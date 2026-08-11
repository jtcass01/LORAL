"""Audit a captured comms dataset before training anything on it.

Runs on a PC against the JSONL written by generate_comms_dataset.py. Answers
the questions that decide whether the data is trainable, and whether the
labels can be trusted:

  * Do captured passes have the right number of pulses? A shortfall means the
    edge detector missed symbols, and those are unrecoverable - CTC can align
    around them but the information is gone.
  * How often does the CURRENT hardcoded decoder get a pass exactly right?
    That is the baseline any model has to beat, and it must be measured on the
    same data the model will be scored on.
  * Where does it go wrong - substitutions, insertions, or deletions? The
    remedy is completely different for each, so this decides the architecture.
  * Are the features separable at all per symbol class?

Deliberately does NOT train anything. Its job is to catch a bad dataset before
hours go into modelling it.
"""
import argparse
import json
import statistics as st
from collections import Counter, defaultdict


def hardcoded_symbol(duration_us: int) -> str:
    """The decoder's current windows, from SingleDiodeRobot.run()."""
    ms = duration_us / 1000
    if 40 < ms < 60:
        return "START"
    if 20 < ms < 30:
        return "ONE"
    if 5 < ms < 15:
        return "ZERO"
    return "BAD"


def align(expected: list, observed: list) -> dict:
    """Levenshtein alignment of observed symbols against expected.

    Counts substitutions, insertions and deletions separately rather than
    reporting one error rate: a decoder that mostly substitutes needs a better
    per-symbol classifier, one that mostly drops or invents symbols needs
    sequence-level framing. Those are different projects.
    """
    n, m = len(expected), len(observed)
    d = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        d[i][0] = i
    for j in range(m + 1):
        d[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            cost = 0 if expected[i - 1] == observed[j - 1] else 1
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + cost)

    i, j = n, m
    sub = ins = dele = match = 0
    while i > 0 or j > 0:
        if i > 0 and j > 0 and d[i][j] == d[i - 1][j - 1] + (expected[i - 1] != observed[j - 1]):
            if expected[i - 1] == observed[j - 1]:
                match += 1
            else:
                sub += 1
            i, j = i - 1, j - 1
        elif i > 0 and d[i][j] == d[i - 1][j] + 1:
            dele += 1
            i -= 1
        else:
            ins += 1
            j -= 1
    return {"match": match, "sub": sub, "ins": ins, "del": dele, "dist": d[n][m]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="experiment_data/C_data_rate_sweep/comms_sweep_v6.jsonl")
    args = ap.parse_args()

    records = [json.loads(l) for l in open(args.input, encoding="utf-8") if l.strip()]
    if not records:
        raise SystemExit(f"{args.input} is empty")

    print(f"{len(records)} passes, {len({r['message'] for r in records})} distinct messages")
    total_syms = sum(r["n_expected"] for r in records)
    print(f"{total_syms:,} expected symbols "
          f"({sum((len(r['message'])+1)*8 for r in records):,} labelled bits)\n")

    # --- 1. capture completeness ---
    print("=== capture completeness ===")
    ratios = [len(r["pulses"]) / r["n_expected"] for r in records]
    short = sum(1 for x in ratios if x < 0.98)
    print(f"  pulses/expected: mean {st.mean(ratios):.3f}  "
          f"min {min(ratios):.3f}  max {max(ratios):.3f}")
    print(f"  passes missing >2% of symbols: {short}/{len(records)}")
    if st.mean(ratios) < 0.98:
        print("  [!] symbols are being MISSED at the detector. CTC can align around")
        print("      them but cannot recover them - fix detection before modelling.")
    elif max(ratios) > 1.02:
        print("  [!] more pulses than symbols: spurious triggers are being inserted.")
    else:
        print("  capture looks complete.")

    # --- 2. baseline decoder ---
    print("\n=== baseline: the CURRENT hardcoded windows ===")
    agg = Counter()
    exact = 0
    per_pass_err = []
    for r in records:
        # BAD is kept in the observed sequence as its own symbol rather than
        # stripped out. Stripping it made every dead-zone pulse reappear as a
        # DELETION in the alignment, so the same 93 events were counted twice
        # and the error mode was misreported as "symbols are being missed" when
        # in fact every pulse was captured and merely fell between two windows.
        obs = [hardcoded_symbol(p["dur"]) for p in r["pulses"]]
        bad = sum(1 for s in obs if s == "BAD")
        a = align(r["expected_symbols"], obs)
        agg.update(a)
        agg["bad"] += bad
        if a["dist"] == 0:
            exact += 1
        per_pass_err.append(a["dist"] / r["n_expected"])

    print(f"  passes decoded EXACTLY right: {exact}/{len(records)} "
          f"({100*exact/len(records):.1f}%)")
    print(f"  symbol error rate: {100*agg['dist']/total_syms:.2f}%")
    print(f"    substitutions {agg['sub']} (of which {agg['bad']} are dead-zone durations)"
          f"   insertions {agg['ins']}   deletions {agg['del']}")

    dead_zone = agg["bad"]
    real_sub = agg["sub"] - dead_zone
    if agg["dist"] == 0:
        print("  no errors at all.")
    elif dead_zone >= max(real_sub, agg["ins"], agg["del"]):
        print("  dominant error mode: DEAD-ZONE durations")
        print("    -> every pulse was captured; they just fell between the hardcoded")
        print("       windows. Soft/learned thresholds alone should recover these,")
        print("       with no sequence modelling required.")
    elif real_sub >= max(agg["ins"], agg["del"]):
        print("  dominant error mode: substitution")
        print("    -> a better per-symbol classifier is the lever.")
    else:
        print("  dominant error mode: insertion/deletion")
        print("    -> sequence-level framing is the lever; a per-pulse classifier")
        print("       cannot fix insertions or deletions.")

    # --- 3. per-class feature separability ---
    print("\n=== features by TRUE symbol class ===")
    by = defaultdict(lambda: defaultdict(list))
    for r in records:
        obs = r["pulses"]
        if len(obs) != r["n_expected"]:
            continue                      # only 1:1 passes can be labelled positionally
        for sym, p in zip(r["expected_symbols"], obs):
            for k in ("dur", "gap", "a_hpf", "b_hpf"):
                if k in p and p[k] is not None and p[k] >= 0:
                    by[sym][k].append(p[k])

    if not by:
        print("  (no 1:1 passes - cannot label positionally)")
    else:
        print(f"  {'class':<7} {'n':>5} {'dur mean':>10} {'dur sd':>8} "
              f"{'gap mean':>10} {'a_hpf':>8} {'b_hpf':>8}")
        for sym in ("START", "ONE", "ZERO"):
            if sym not in by:
                continue
            d = by[sym]
            print(f"  {sym:<7} {len(d['dur']):>5} {st.mean(d['dur']):>10.0f} "
                  f"{st.pstdev(d['dur']):>8.0f} "
                  f"{st.mean(d['gap']) if d['gap'] else 0:>10.0f} "
                  f"{st.mean(d['a_hpf']):>8.0f} {st.mean(d['b_hpf']):>8.0f}")
        durs = {s: by[s]["dur"] for s in by if by[s]["dur"]}
        order = sorted(durs, key=lambda s: st.mean(durs[s]))
        print("\n  duration separation between adjacent classes:")
        for x, y in zip(order, order[1:]):
            mx, my = st.mean(durs[x]), st.mean(durs[y])
            pooled = (st.pstdev(durs[x]) + st.pstdev(durs[y])) / 2
            if pooled:
                print(f"    {x:>5} vs {y:<5} {abs(my-mx)/pooled:8.1f} sd")


if __name__ == "__main__":
    main()

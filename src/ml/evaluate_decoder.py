"""Score the sequence decoder against the hardcoded baseline, per data rate.

Held out BY MESSAGE, never by pass: three passes share a payload, so a
pass-level split would leak the answer into training.

The baseline's duration windows are scaled by rate. Leaving them at their
10/25/50ms settings would make the baseline score zero everywhere above rate 1,
which would be a rigged comparison - the honest question is whether the decoder
beats hand-tuned thresholds that were themselves retuned for each rate.
"""
import argparse
import json
from collections import defaultdict

import decoder as D

BASE_WINDOWS = {"START": (40, 60), "ONE": (20, 30), "ZERO": (5, 15)}


def baseline_decode(pulses, rate):
    """Current firmware behaviour: window match, and drop the byte on ambiguity."""
    out, cur, nbits, reading = [], 0, 0, False
    for p in pulses:
        ms = p["dur"] / 1000
        sym = None
        for name, (lo, hi) in BASE_WINDOWS.items():
            if lo / rate < ms < hi / rate:
                sym = name
                break
        if sym == "START":
            reading, cur, nbits = True, 0, 0
        elif not reading:
            continue
        elif sym in ("ONE", "ZERO"):
            cur |= (1 if sym == "ONE" else 0) << nbits
            nbits += 1
            if nbits == 8:
                out.append(cur)
                reading = False
        else:
            reading = False          # ambiguous pulse: discard the whole byte
    return out


def expected_bytes(message):
    return [ord(c) for c in message + "\n"]


def levenshtein(a, b):
    if a == b:
        return 0
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1]


def score(name, decoded_list, truth_list):
    exact = sum(1 for d, t in zip(decoded_list, truth_list) if d == t)
    berr = sum(levenshtein(d, t) for d, t in zip(decoded_list, truth_list))
    btot = sum(len(t) for t in truth_list)
    return {"name": name, "n": len(truth_list), "exact": exact,
            "pass": exact / len(truth_list) if truth_list else 0.0,
            "byte_errs": berr, "bytes": btot,
            "byte_err": berr / btot if btot else 0.0}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="experiment_data/C_data_rate_sweep/comms_sweep_v6.jsonl")
    ap.add_argument("--test-fraction", type=float, default=0.3)
    ap.add_argument("--out-json", default=None,
                    help="Write per-rate results here so plot_sweep.py can overlay them "
                         "without re-running the decoder.")
    ap.add_argument("--max-passes", type=int, default=0,
                    help="Cap test passes per rate (0 = all). Viterbi is O(states x pulses).")
    args = ap.parse_args()

    by = defaultdict(list)
    dropped = 0
    for line in open(args.input, encoding="utf-8"):
        if not line.strip():
            continue
        r = json.loads(line)
        if any(not isinstance(v, int) for p in r["pulses"] for v in p.values()):
            dropped += 1
            continue
        by[r.get("rate", 1.0)].append(r)
    if dropped:
        print(f"[!] discarded {dropped} pass(es) with malformed pulse fields\n")

    results = {}
    print(f"{'rate':>5} {'bits/s':>7} {'test':>5} {'skipLP':>7} | "
          f"{'baseline':>18} | {'decoder':>18}")
    print(f"{'':>5} {'':>7} {'':>5} {'':>7} | {'pass':>8}{'byte err':>10} | "
          f"{'pass':>8}{'byte err':>10}")

    for rate in sorted(by):
        recs = by[rate]
        msgs = sorted({r["message"] for r in recs})
        n_test = max(1, int(len(msgs) * args.test_fraction))
        test_msgs = set(msgs[:n_test])
        train = [r for r in recs if r["message"] not in test_msgs]
        test = [r for r in recs if r["message"] in test_msgs]
        if args.max_passes:
            test = test[:args.max_passes]
        if not train or not test:
            continue

        emissions = D.fit_emissions(train, rate)
        gap_sigma = D.fit_gap_sigma(train, rate)
        skip_prior = D.fit_skip_prior(train)

        truth = [expected_bytes(r["message"]) for r in test]
        base = [baseline_decode(r["pulses"], rate) for r in test]
        dec = []
        for r in test:
            _, out = D.decode(r["pulses"], emissions, gap_sigma, rate,
                              skip_log_prior=skip_prior)
            dec.append(out if out is not None else [])

        b = score("baseline", base, truth)
        d = score("decoder", dec, truth)
        bits = 8.0 / (((50_000 + 10_000 + 8 * (17_500 + 10_000)) / rate) / 1e6)
        print(f"{rate:>5g} {bits:>7.1f} {len(test):>5} {skip_prior:>7.2f} | "
              f"{100*b['pass']:>7.1f}% {100*b['byte_err']:>9.2f}% | "
              f"{100*d['pass']:>7.1f}% {100*d['byte_err']:>9.2f}%")
        results[str(rate)] = {
            "bits": bits, "n": len(test),
            "baseline_pass": b["pass"], "decoder_pass": d["pass"],
            "baseline_exact": b["exact"], "decoder_exact": d["exact"],
            "baseline_byte_err": b["byte_err"], "decoder_byte_err": d["byte_err"],
            "baseline_byte_errs": b["byte_errs"], "decoder_byte_errs": d["byte_errs"],
            "bytes": b["bytes"],
        }

    if args.out_json:
        with open(args.out_json, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2)
        print(f"\nwrote {args.out_json}")


if __name__ == "__main__":
    main()

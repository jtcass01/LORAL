"""Compare decoders across data rates: hand-tuned windows vs learned models.

Runs on a PC against the JSONL from generate_comms_dataset.py with --rate set.

The comparison is deliberately constructed to be hard on the learned model:

  * The baseline's windows are SCALED BY RATE. Leaving them at their 10/25/50ms
    settings would make the baseline score zero everywhere above rate 1, which
    would be a rigged win. The honest question is whether a fitted model beats
    hand-tuned thresholds that were themselves retuned for each rate.
  * Train/test splits are BY MESSAGE, never by pass. Three passes of the same
    message share their payload, so splitting by pass would leak the answer.
  * Every model is fitted per rate on the training messages only, then scored
    on held-out messages.

Metrics: symbol error rate, and pass success - the fraction of transmissions
decoded with zero errors, which is what actually matters to a link.
"""
import argparse
import json
import statistics as st
from collections import defaultdict

BASE_WINDOWS = {                      # (lo_ms, hi_ms) at rate 1.0
    "START": (40, 60),
    "ONE": (20, 30),
    "ZERO": (5, 15),
}
FEATURES = ("dur", "gap", "a_hpf", "b_hpf", "thr", "bo")


def baseline_symbol(duration_us: float, rate: float) -> str:
    """The current hardcoded decoder, with its windows scaled for this rate."""
    ms = duration_us / 1000
    for name, (lo, hi) in BASE_WINDOWS.items():
        if lo / rate < ms < hi / rate:
            return name
    return "BAD"


def levenshtein(a: list, b: list) -> int:
    if a == b:
        return 0
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1]


def score(records: list, predict) -> dict:
    """Symbol error rate and pass success for a decoder over these passes."""
    errs = total = exact = 0
    for r in records:
        obs = [predict(p) for p in r["pulses"]]
        d = levenshtein(r["expected_symbols"], obs)
        errs += d
        total += r["n_expected"]
        exact += (d == 0)
    return {"ser": errs / total if total else 0.0,
            "pass": exact / len(records) if records else 0.0,
            "n": len(records)}


def fit_nearest_mean(train: list):
    """Class means of duration - the simplest possible learned decoder.

    Included because the rate-1 data showed it recovers 100% of the baseline's
    errors. If it keeps winning at higher rates, nothing more complex is
    justified; where it starts failing is where real modelling begins.
    """
    sums = defaultdict(list)
    for r in train:
        if len(r["pulses"]) != r["n_expected"]:
            continue
        for sym, p in zip(r["expected_symbols"], r["pulses"]):
            sums[sym].append(p["dur"])
    means = {k: st.mean(v) for k, v in sums.items() if v}

    def predict(p):
        return min(means, key=lambda k: abs(p["dur"] - means[k]))
    return predict if means else None


def fit_logistic(train: list):
    """Multinomial logistic regression over all pulse features, both diodes."""
    try:
        import numpy as np
        from sklearn.linear_model import LogisticRegression
        from sklearn.preprocessing import StandardScaler
        from sklearn.pipeline import make_pipeline
    except ImportError:
        return None

    X, y = [], []
    for r in train:
        if len(r["pulses"]) != r["n_expected"]:
            continue
        for sym, p in zip(r["expected_symbols"], r["pulses"]):
            X.append([float(p.get(f, 0) or 0) for f in FEATURES])
            y.append(sym)
    if len(set(y)) < 2:
        return None

    # No multi_class argument: it was removed in scikit-learn 1.9, where
    # multinomial is the default for multiclass problems anyway.
    model = make_pipeline(StandardScaler(), LogisticRegression(max_iter=3000))
    model.fit(np.array(X), np.array(y))

    def predict(p):
        v = np.array([[float(p.get(f, 0) or 0) for f in FEATURES]])
        return model.predict(v)[0]
    return predict


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="experiment_data/F_instrumentation_validation/comms_rate_sweep.jsonl")
    ap.add_argument("--test-fraction", type=float, default=0.3)
    args = ap.parse_args()

    records = [json.loads(l) for l in open(args.input, encoding="utf-8") if l.strip()]
    by_rate = defaultdict(list)
    for r in records:
        by_rate[r.get("rate", 1.0)].append(r)

    print(f"{len(records)} passes across {len(by_rate)} rates\n")
    print(f"{'rate':>5} {'bits/s':>7} {'test':>5} {'capture':>8} | "
          f"{'baseline':>17} | {'nearest-mean':>17} | {'logistic':>17}")
    print(f"{'':>5} {'':>7} {'':>5} {'':>8} | "
          f"{'SER':>8}{'pass':>9} | {'SER':>8}{'pass':>9} | {'SER':>8}{'pass':>9}")

    for rate in sorted(by_rate):
        recs = by_rate[rate]
        msgs = sorted({r["message"] for r in recs})
        n_test = max(1, int(len(msgs) * args.test_fraction))
        test_msgs = set(msgs[:n_test])          # deterministic split, by MESSAGE
        train = [r for r in recs if r["message"] not in test_msgs]
        test = [r for r in recs if r["message"] in test_msgs]
        if not train or not test:
            continue

        cap = st.mean(len(r["pulses"]) / r["n_expected"] for r in recs)
        bits = 8.0 / ((50_000 + 10_000 + 8 * (17_500 + 10_000)) / rate / 1e6)

        base = score(test, lambda p: baseline_symbol(p["dur"], rate))
        nm_fn = fit_nearest_mean(train)
        nm = score(test, nm_fn) if nm_fn else None
        lg_fn = fit_logistic(train)
        lg = score(test, lg_fn) if lg_fn else None

        def cell(s):
            return f"{100*s['ser']:7.2f}%{100*s['pass']:8.1f}%" if s else f"{'n/a':>17}"

        print(f"{rate:>5g} {bits:>7.1f} {base['n']:>5} {cap:>8.3f} | "
              f"{cell(base)} | {cell(nm)} | {cell(lg)}")

    print("\nSER = symbol error rate.  pass = share of transmissions decoded with")
    print("zero errors.  capture = pulses received / symbols sent; below 1.0 means")
    print("the receiver is missing pulses outright, which no decoder can undo.")


if __name__ == "__main__":
    main()

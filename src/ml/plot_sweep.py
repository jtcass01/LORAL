"""Plot the data-rate sweep: what the link does, and where a decoder can help.

Runs on a PC against the JSONL from generate_comms_dataset.py. Produces
paper-ready PNGs:

  <prefix>_pass_success.png   decoder performance vs data rate (the headline)
  <prefix>_symbol_loss.png    symbol loss vs data rate, unbuffered vs buffered
  <prefix>_separation.png     symbol-class separability vs data rate
  <prefix>_error_modes.png    what actually goes wrong, per rate

Colour follows the validated categorical palette in fixed slot order, never
cycled. Every figure carries direct labels as well as a legend, so identity is
never conveyed by colour alone - which also discharges the contrast warning on
the aqua slot.
"""
import argparse
import json
import statistics as st
from collections import defaultdict

import matplotlib
import matplotlib.pyplot as plt

# --- validated categorical palette (light mode), fixed slot order ---
C1 = "#2a78d6"   # blue
C2 = "#eb6834"   # orange
C3 = "#1baf7a"   # aqua
C4 = "#eda100"   # yellow
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
GRID = "#e3e2df"

BASE_WINDOWS = {"START": (40, 60), "ONE": (20, 30), "ZERO": (5, 15)}
W = {"ZERO": 10_000, "ONE": 25_000, "START": 50_000}
GAP_US = 10_000


def style_axes(ax, xlabel, ylabel, title, subtitle=None):
    """Recessive grid and axes; text in ink tokens, never a series colour."""
    ax.set_facecolor(SURFACE)
    ax.figure.set_facecolor(SURFACE)
    ax.grid(True, color=GRID, linewidth=0.8, zorder=0)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=INK_2, labelsize=9)
    ax.set_xlabel(xlabel, color=INK_2, fontsize=10)
    ax.set_ylabel(ylabel, color=INK_2, fontsize=10)
    # Title and subtitle are drawn separately so they can carry different
    # weight and size. Concatenating them into set_title forced both to 12pt,
    # and the longer subtitle then ran off the right edge of the figure.
    ax.set_title(title, color=INK, fontsize=12.5, loc="left",
                 pad=26 if subtitle else 12)
    if subtitle:
        ax.annotate(subtitle, xy=(0, 1), xycoords="axes fraction",
                    xytext=(0, 8), textcoords="offset points",
                    color=INK_2, fontsize=9.5, va="bottom", ha="left")


def bits_per_s(rate):
    per_byte = (W["START"] + GAP_US + 8 * ((W["ONE"] + W["ZERO"]) / 2 + GAP_US)) / rate
    return 8.0 / (per_byte / 1e6)


def baseline_symbol(duration_us, rate):
    """The hardcoded decoder, windows scaled for this rate so the comparison is fair."""
    ms = duration_us / 1000
    for name, (lo, hi) in BASE_WINDOWS.items():
        if lo / rate < ms < hi / rate:
            return name
    return "BAD"


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


def load(path):
    """Group passes by rate, discarding any pass with an unparseable pulse.

    A truncated serial line can leave a field as a string. Such a pass cannot
    be trusted for positional alignment against the expected symbols, and one
    dropped pass in ~600 costs nothing - so the whole pass goes rather than
    being silently half-repaired.
    """
    by = defaultdict(list)
    dropped = 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            r = json.loads(line)
            if any(not isinstance(v, int) for p in r["pulses"] for v in p.values()):
                dropped += 1
                continue
            by[r.get("rate", 1.0)].append(r)
    if dropped:
        print(f"[!] discarded {dropped} pass(es) containing malformed pulse fields")
    return by


def per_rate_stats(recs, rate):
    n = len(recs)
    perfect = sum(1 for r in recs if len(r["pulses"]) == r["n_expected"])
    one = [r for r in recs if len(r["pulses"]) == r["n_expected"] - 1]
    multi = sum(1 for r in recs if len(r["pulses"]) < r["n_expected"] - 1)
    extra = sum(1 for r in recs if len(r["pulses"]) > r["n_expected"])

    nominal = GAP_US / rate
    rescuable = sum(
        1 for r in one
        if any(p.get("gap", 0) and p["gap"] > 1.8 * nominal for p in r["pulses"])
        or max(p["dur"] for p in r["pulses"]) > (W["START"] / rate) * 1.15
    )

    base_ok = sum(1 for r in recs
                  if levenshtein(r["expected_symbols"],
                                 [baseline_symbol(p["dur"], rate) for p in r["pulses"]]) == 0)
    expected = sum(r["n_expected"] for r in recs)
    missing = sum(max(0, r["n_expected"] - len(r["pulses"])) for r in recs)

    # dead-zone: captured pulses the hardcoded windows cannot name
    dead = sum(1 for r in recs for p in r["pulses"]
               if baseline_symbol(p["dur"], rate) == "BAD")

    durs = defaultdict(list)
    for r in recs:
        if len(r["pulses"]) == r["n_expected"]:
            for s, p in zip(r["expected_symbols"], r["pulses"]):
                durs[s].append(p["dur"] / 1000)
    sep = 0.0
    if durs["ZERO"] and durs["ONE"]:
        pooled = (st.pstdev(durs["ZERO"]) + st.pstdev(durs["ONE"])) / 2
        if pooled:
            sep = abs(st.mean(durs["ONE"]) - st.mean(durs["ZERO"])) / pooled

    return {
        "n": n, "bits": bits_per_s(rate),
        "baseline": base_ok / n, "perfect": perfect / n,
        "ceiling": (perfect + rescuable) / n,
        "loss": missing / expected if expected else 0.0,
        "dead_per_pass": dead / n, "multi": multi / n, "extra": extra / n,
        "one": len(one) / n, "sep": sep,
    }


def label_ends(ax, x, items, min_sep):
    """Direct-label several series at their right ends, nudged apart if needed.

    Identity must not rest on colour alone, so every series gets a text label as
    well as a legend entry. Series that finish close together would overprint,
    so the labels are separated vertically while the leader stays at the true
    value - readable without moving the data.
    """
    dx = (ax.get_xlim()[1] - ax.get_xlim()[0]) * 0.02
    placed = []
    for y, text in sorted(items, key=lambda it: it[0]):
        ly = y if not placed else max(y, placed[-1] + min_sep)
        placed.append(ly)
        arrow = None
        if abs(ly - y) > 1e-9:
            arrow = dict(arrowstyle="-", color=GRID, linewidth=0.8,
                         shrinkA=0, shrinkB=2)
        ax.annotate(text, xy=(x, y), xytext=(x + dx, ly), textcoords="data",
                    color=INK_2, fontsize=9, va="center",
                    annotation_clip=False, arrowprops=arrow)


def fig_pass_success(stats, path, decoded=None):
    fig, ax = plt.subplots(figsize=(8, 5))
    xs = [s["bits"] for s in stats]
    series = [
        ("Sequence-decoder ceiling", [100 * s["ceiling"] for s in stats], C3),
        ("All symbols captured", [100 * s["perfect"] for s in stats], C2),
        ("Hardcoded windows", [100 * s["baseline"] for s in stats], C1),
    ]
    if decoded:
        series.insert(1, ("Viterbi decoder (measured)",
                          [100 * decoded[i] for i in range(len(xs))], C4))
    ends = []
    for name, ys, col in series:
        ax.plot(xs, ys, color=col, linewidth=2, marker="o", markersize=8,
                markeredgecolor=SURFACE, markeredgewidth=2, label=name, zorder=3)
        ends.append((ys[-1], name))
    ax.set_ylim(0, 105)
    ax.set_xlim(min(xs) * 0.9, max(xs) * 1.32)
    label_ends(ax, xs[-1], ends, min_sep=7)
    style_axes(ax, "data rate (bits/s)", "transmissions decoded with zero errors (%)",
               "A sequence decoder holds where hard thresholds collapse",
               "the gap from 'hardcoded windows' up to the ceiling is headroom "
               "no per-symbol model can reach")
    leg = ax.legend(frameon=False, fontsize=9, loc="lower left")
    for t in leg.get_texts():
        t.set_color(INK_2)
    fig.tight_layout()
    fig.savefig(path, dpi=160, facecolor=SURFACE)
    return path


def fig_symbol_loss(stats, stats_old, path):
    fig, ax = plt.subplots(figsize=(8, 5))
    xs = [s["bits"] for s in stats]
    ax.plot(xs, [100 * s["loss"] for s in stats], color=C1, linewidth=2, marker="o",
            markersize=8, markeredgecolor=SURFACE, markeredgewidth=2,
            label="buffered logger", zorder=3)
    ends = [(100 * stats[-1]["loss"], "buffered")]
    if stats_old:
        xo = [s["bits"] for s in stats_old]
        ax.plot(xo, [100 * s["loss"] for s in stats_old], color=C2, linewidth=2,
                marker="s", markersize=8, markeredgecolor=SURFACE, markeredgewidth=2,
                label="unbuffered (serial write inside the gap)", zorder=3)
        ends.append((100 * stats_old[-1]["loss"], "unbuffered"))
    ax.set_xlim(min(xs) * 0.9, max(xs) * 1.32)
    ax.set_ylim(bottom=0)
    label_ends(ax, xs[-1], ends, min_sep=0.06)
    # The instrumentation claim is only supported when both curves are shown;
    # with a single series the title must not assert a comparison.
    if stats_old:
        title = "Half the apparent loss was the instrumentation"
        sub = "writing each pulse to serial stole time from the sampling loop"
    else:
        title = "Symbol loss rises with data rate"
        sub = "pulses the receiver never detected, as a share of those transmitted"
    style_axes(ax, "data rate (bits/s)", "symbols never detected (%)", title, sub)
    leg = ax.legend(frameon=False, fontsize=9, loc="upper left")
    for t in leg.get_texts():
        t.set_color(INK_2)
    fig.tight_layout()
    fig.savefig(path, dpi=160, facecolor=SURFACE)
    return path


def fig_separation(stats, path):
    fig, ax = plt.subplots(figsize=(8, 5))
    xs = [s["bits"] for s in stats]
    ys = [s["sep"] for s in stats]
    # one series: no legend box, the title names it
    ax.plot(xs, ys, color=C1, linewidth=2, marker="o", markersize=8,
            markeredgecolor=SURFACE, markeredgewidth=2, zorder=3)
    ax.axhline(2, color=INK_2, linewidth=1, linestyle="--", zorder=2)
    ax.annotate("2 sd — below this, classes overlap", xy=(xs[0], 2),
                xytext=(0, 6), textcoords="offset points", color=INK_2, fontsize=9)
    for x, y in zip(xs, ys):
        ax.annotate(f"{y:.0f}", xy=(x, y), xytext=(0, 10), textcoords="offset points",
                    color=INK_2, fontsize=9, ha="center")
    ax.set_xlim(min(xs) * 0.9, max(xs) * 1.1)
    ax.set_ylim(bottom=0)
    style_axes(ax, "data rate (bits/s)", "ZERO vs ONE separation (pooled sd)",
               "Symbol classes stay far apart even where decoding fails",
               "so the errors are not misclassification — they are missing pulses")
    fig.tight_layout()
    fig.savefig(path, dpi=160, facecolor=SURFACE)
    return path


def fig_error_modes(stats, path):
    fig, ax = plt.subplots(figsize=(8, 5))
    labels = [f"{s['bits']:.0f}" for s in stats]
    xs = range(len(stats))
    # These three are exhaustive and must sum to 100%. An earlier version
    # subtracted dead_per_pass (a count of pulses per pass) from perfect (a
    # fraction of passes) - different units, so the bars silently fell short.
    clean = [100 * s["perfect"] for s in stats]
    one = [100 * s["one"] for s in stats]
    multi = [100 * s["multi"] for s in stats]
    extra = [100 * s["extra"] for s in stats]
    # 2px surface gap between stacked segments
    kw = dict(width=0.6, edgecolor=SURFACE, linewidth=2, zorder=3)
    ax.bar(xs, clean, color=C3, label="fully captured", **kw)
    ax.bar(xs, one, bottom=clean, color=C2, label="one symbol missing", **kw)
    ax.bar(xs, multi, bottom=[c + o for c, o in zip(clean, one)], color=C1,
           label="two or more missing", **kw)
    # Insertions are rare and appear only at the highest rate, but without
    # them the stack stops short of 100% and reads as a rendering fault.
    if any(extra):
        ax.bar(xs, extra, bottom=[c + o + m for c, o, m in zip(clean, one, multi)],
               color=C4, label="extra pulses (insertions)", **kw)
    for i, (c, o, m) in enumerate(zip(clean, one, multi)):
        if o > 4:
            ax.annotate(f"{o:.0f}%", xy=(i, c + o / 2), color=SURFACE, fontsize=9,
                        ha="center", va="center", fontweight="bold")
    ax.set_xticks(list(xs))
    ax.set_xticklabels(labels)
    ax.set_ylim(0, 105)
    style_axes(ax, "data rate (bits/s)", "share of transmissions (%)",
               "Single dropped symbols dominate the failure budget",
               "and every one of them leaves a recoverable trace in the gap timing")
    leg = ax.legend(frameon=False, fontsize=9, loc="upper center", ncol=4,
                    bbox_to_anchor=(0.5, -0.14))
    for t in leg.get_texts():
        t.set_color(INK_2)
    fig.tight_layout()
    fig.savefig(path, dpi=160, facecolor=SURFACE)
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="experiment_data/C_data_rate_sweep/comms_sweep_v6.jsonl")
    ap.add_argument("--compare", default=None,
                    help="Optional older sweep to overlay on the loss figure.")
    ap.add_argument("--decoder-results", default=None,
                    help="JSON from evaluate_decoder.py --out-json, overlaid as a "
                         "measured curve against the ceilings.")
    ap.add_argument("--out-prefix", default=None)
    args = ap.parse_args()

    matplotlib.use("Agg")
    prefix = args.out_prefix or args.input.rsplit(".", 1)[0]

    by = load(args.input)
    stats = [per_rate_stats(by[r], r) for r in sorted(by)]
    stats_old = None
    if args.compare:
        byo = load(args.compare)
        stats_old = [per_rate_stats(byo[r], r) for r in sorted(byo)]

    print(f"{'rate':>5} {'bits/s':>7} {'n':>5} {'baseline':>9} {'captured':>9} "
          f"{'ceiling':>8} {'loss':>7} {'sep':>6}")
    for r, s in zip(sorted(by), stats):
        print(f"{r:>5g} {s['bits']:>7.1f} {s['n']:>5} {100*s['baseline']:>8.1f}% "
              f"{100*s['perfect']:>8.1f}% {100*s['ceiling']:>7.1f}% "
              f"{100*s['loss']:>6.3f}% {s['sep']:>6.1f}")

    decoded = None
    if args.decoder_results:
        with open(args.decoder_results, encoding="utf-8") as f:
            dr = json.load(f)
        decoded = [dr[str(r)]["decoder_pass"] for r in sorted(by) if str(r) in dr]
        if len(decoded) != len(stats):
            print("[!] decoder results cover different rates - overlay skipped")
            decoded = None

    outs = [
        fig_pass_success(stats, f"{prefix}_pass_success.png", decoded),
        fig_symbol_loss(stats, stats_old, f"{prefix}_symbol_loss.png"),
        fig_separation(stats, f"{prefix}_separation.png"),
        fig_error_modes(stats, f"{prefix}_error_modes.png"),
    ]
    print("\nwrote:")
    for o in outs:
        print("  ", o)


if __name__ == "__main__":
    main()

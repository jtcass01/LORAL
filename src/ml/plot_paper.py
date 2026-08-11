"""Paper figures: how the protocol works, how it fails, and what the decoder recovers.

Emits PDF (for LaTeX) and PNG (preview) at IEEE column widths into
docs/IEEE Paper/figures/.

  fig_protocol       one byte on the wire: pulse-width symbols and framing
  fig_recovery       why a dropped symbol is recoverable from gap timing
  fig_model          decoder: framing states, and the trellis Viterbi runs over
  fig_separability   symbol-class separation vs data rate
  fig_results        transmission success and character error, decoder vs baseline
  fig_live           live closed-loop results at two data rates

Palette is the validated categorical set in fixed slot order; series carry
marker shape as well as colour so identity survives greyscale printing.
"""
import argparse
import csv
import json
import math
import pathlib
import statistics as st
from collections import defaultdict

import matplotlib
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

C1 = "#2a78d6"   # blue    - baseline / threshold decoder
C2 = "#eb6834"   # orange  - sequence decoder
C3 = "#1baf7a"   # aqua    - ceiling / start symbol
C4 = "#eda100"   # yellow  - highlight
INK = "#0b0b0b"
INK_2 = "#52514e"
GRID = "#d8d7d4"

W = {"ZERO": 10_000, "ONE": 25_000, "START": 50_000}
GAP_US = 10_000


def style(ax, xlabel, ylabel):
    ax.grid(True, color=GRID, linewidth=0.6, zorder=0)
    ax.set_axisbelow(True)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(GRID)
        ax.spines[s].set_linewidth(0.8)
    ax.tick_params(colors=INK_2, labelsize=8, width=0.8)
    ax.set_xlabel(xlabel, color=INK, fontsize=9)
    ax.set_ylabel(ylabel, color=INK, fontsize=9)


def wilson(k, n, z=1.96):
    if n == 0:
        return 0.0, 0.0
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, c - h), min(1.0, c + h)


def save(fig, base):
    for ext in ("pdf", "png"):
        fig.savefig(f"{base}.{ext}", dpi=300, bbox_inches="tight")
    plt.close(fig)
    return base


# ---------------------------------------------------------------- protocol --

def fig_protocol(base):
    """One byte on the wire. Symbol identity is pulse WIDTH, not amplitude."""
    fig, ax = plt.subplots(figsize=(7.2, 2.2))
    # 'H' = 0x48, transmitted LSB first: 0,0,0,1,0,0,1,0
    bits = [0, 0, 0, 1, 0, 0, 1, 0]
    syms = ["START"] + ["ONE" if b else "ZERO" for b in bits]

    t = 0.0
    xs, ys = [0.0], [0.0]
    for s in syms:
        w = W[s] / 1000.0
        xs += [t, t, t + w, t + w]
        ys += [0, 1, 1, 0]
        t += w + GAP_US / 1000.0
        xs.append(t)
        ys.append(0)
    ax.plot(xs, ys, color=C1, linewidth=1.6, zorder=3)

    # label each symbol under its pulse
    t = 0.0
    for s in syms:
        w = W[s] / 1000.0
        colour = C3 if s == "START" else INK_2
        ax.annotate(f"{w:.0f}", xy=(t + w / 2, -0.22), ha="center", va="top",
                    fontsize=7, color=colour)
        if s == "START":
            ax.annotate("START", xy=(t + w / 2, 1.12), ha="center",
                        fontsize=7.5, color=C3)
        t += w + GAP_US / 1000.0

    ax.annotate("8 data bits, LSB first  (0x48 = 'H')",
                xy=(W["START"] / 1000 + GAP_US / 1000 + 60, 1.12),
                fontsize=7.5, color=INK_2)
    ax.annotate("10 ms gap after every pulse", xy=(t * 0.62, -0.55),
                fontsize=7, color=INK_2)
    ax.set_ylim(-0.75, 1.45)
    ax.set_yticks([])
    ax.set_xlabel("time (ms)", color=INK, fontsize=9)
    ax.spines["left"].set_visible(False)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(colors=INK_2, labelsize=8)
    ax.set_title("One byte: symbol identity is pulse WIDTH, not amplitude",
                 fontsize=10, color=INK, loc="left", pad=8)
    return save(fig, base)


# ---------------------------------------------------------------- recovery --

def fig_recovery(base):
    """A dropped symbol lengthens the following gap by a known amount."""
    fig, axes = plt.subplots(2, 1, figsize=(3.5, 2.9), sharex=True)
    seq = ["ZERO", "ONE", "ZERO"]

    for ax, drop, title in ((axes[0], None, "Transmitted"),
                            (axes[1], 1, "Received: middle symbol undetected")):
        t, xs, ys = 0.0, [0.0], [0.0]
        for i, s in enumerate(seq):
            w = W[s] / 1000.0
            if i == drop:
                t += w + GAP_US / 1000.0        # emitted, never detected
                continue
            xs += [t, t, t + w, t + w]
            ys += [0, 1, 1, 0]
            t += w + GAP_US / 1000.0
            xs.append(t)
            ys.append(0)
        ax.plot(xs, ys, color=C1 if drop is None else C2, linewidth=1.6, zorder=3)
        ax.set_ylim(-0.2, 1.9)
        ax.set_yticks([])
        for s in ("top", "right", "left"):
            ax.spines[s].set_visible(False)
        ax.spines["bottom"].set_color(GRID)
        ax.tick_params(colors=INK_2, labelsize=8)
        ax.set_title(title, fontsize=8, color=INK, loc="left", pad=3)

    # mark the widened gap on the received trace
    g0 = W["ZERO"] / 1000.0
    g1 = g0 + GAP_US / 1000 + W["ONE"] / 1000 + GAP_US / 1000
    axes[1].annotate("", xy=(g0, 1.35), xytext=(g1, 1.35),
                     arrowprops=dict(arrowstyle="<->", color=C4, linewidth=1.2))
    axes[1].annotate("gap = 2G + w(ONE)\nidentifies what was lost",
                     xy=((g0 + g1) / 2, 1.45), ha="center", fontsize=7, color=C4)
    axes[1].set_xlabel("time (ms)", color=INK, fontsize=9)
    fig.tight_layout(pad=0.4)
    return save(fig, base)


# ------------------------------------------------------------------ model --

def _node(ax, x, y, label, colour, fs=8.5, dashed=False):
    """A state box that sizes itself to its label."""
    return ax.text(x, y, label, transform=ax.transAxes, ha="center", va="center",
                   fontsize=fs, color=INK, zorder=4,
                   bbox=dict(boxstyle="round,pad=0.34", facecolor="white",
                             edgecolor=colour, linewidth=1.3,
                             linestyle=":" if dashed else "-"))


def _edge(ax, x0, x1, y, colour, dashed=False, rad=0.0, shrink=13):
    ax.annotate("", xy=(x1, y), xytext=(x0, y),
                xycoords=ax.transAxes, textcoords=ax.transAxes, zorder=2,
                arrowprops=dict(arrowstyle="-|>", color=colour, linewidth=1.3,
                                linestyle=":" if dashed else "-",
                                shrinkA=shrink, shrinkB=shrink,
                                connectionstyle=f"arc3,rad={rad}"))


def fig_model(base):
    """The decoder: what the states are, and where each fitted quantity enters."""
    fig, axes = plt.subplots(1, 2, figsize=(7.2, 3.0))
    for ax in axes:
        ax.set_xticks([]); ax.set_yticks([])
        ax.set_xlim(0, 1); ax.set_ylim(0, 1)
        for s in ("top", "right", "left", "bottom"):
            ax.spines[s].set_visible(False)

    # ---- (a) framing chain -------------------------------------------------
    ax = axes[0]
    ys = 0.70
    xs = [0.09, 0.31, 0.53, 0.72, 0.92]
    for x, lab, c in zip(xs, [r"$S$", r"$B_1$", r"$B_2$", r"$\cdots$", r"$B_8$"],
                         [C3, C1, C1, C1, C1]):
        _node(ax, x, ys, lab, c)
    for a, b in zip(xs, xs[1:]):
        _edge(ax, a, b, ys, INK_2)
    # Edge labels go BELOW the row and the byte-close arc ABOVE it, so the
    # arc's arrowhead landing on S cannot be misread as labelling that edge.
    ax.text((xs[0] + xs[1]) / 2, ys - 0.15, "START", transform=ax.transAxes,
            ha="center", fontsize=7.5, color=C3)
    ax.text((xs[1] + xs[2]) / 2, ys - 0.15, "ZERO / ONE", transform=ax.transAxes,
            ha="center", fontsize=7.5, color=INK_2)
    _edge(ax, xs[-1], xs[0], ys + 0.02, C3, rad=0.40, shrink=15)
    ax.text(0.50, 0.36, "emit byte, only if the completed value\n"
                        "is admissible (0x20–0x7E or 0x0A)",
            transform=ax.transAxes, ha="center", va="top", fontsize=7.5, color=C3)
    ax.text(0.50, 0.04, r"each $B_k$ is $2^{\,k-1}$ states carrying the partial byte;"
                        "\n" r"$256$ states in total",
            transform=ax.transAxes, ha="center", va="bottom", fontsize=7.5,
            color=INK_2)
    ax.set_title("(a)  framing states and transitions, fixed by the protocol",
                 fontsize=8.5, color=INK, loc="left", pad=4)

    # ---- (b) the trellis ---------------------------------------------------
    # The standard HMM decoding picture, which is what makes the skip mechanism
    # legible: advancing TWO framing positions while consuming ONE observation
    # is a double-height step, and nothing else in the lattice looks like it.
    ax = axes[1]
    rows = [r"$S$", r"$B_1$", r"$B_2$", r"$B_3$", r"$B_4$", r"$B_5$"]
    ytop, ybot = 0.94, 0.46
    yr = [ytop - i * (ytop - ybot) / (len(rows) - 1) for i in range(len(rows))]
    xc = [0.16, 0.345, 0.53, 0.715, 0.90]

    for i, lab in enumerate(rows):
        ax.text(0.055, yr[i], lab, transform=ax.transAxes, ha="right",
                va="center", fontsize=7.5, color=INK_2)

    # the lattice: every legal advance of 1..K+1 framing positions
    for a, b in zip(xc, xc[1:]):
        for i in range(len(rows)):
            for step in (1, 2, 3, 4):
                if i + step < len(rows):
                    ax.plot([a, b], [yr[i], yr[i + step]], transform=ax.transAxes,
                            color=GRID, linewidth=0.6, zorder=1)
    for x in xc:
        for y in yr:
            ax.plot([x], [y], "o", transform=ax.transAxes, color=GRID,
                    markersize=3.4, zorder=2)

    # the surviving path; the third step skips a symbol
    path = [(xc[0], yr[0]), (xc[1], yr[1]), (xc[2], yr[2]),
            (xc[3], yr[4]), (xc[4], yr[5])]
    for j, ((x0, y0), (x1, y1)) in enumerate(zip(path, path[1:])):
        skip = (j == 2)
        ax.plot([x0, x1], [y0, y1], transform=ax.transAxes,
                color=C2 if skip else C1, linewidth=2.0,
                linestyle=":" if skip else "-", zorder=4)
    for x, y in path:
        ax.plot([x], [y], "o", transform=ax.transAxes, color=C1, markersize=5.5,
                markeredgecolor="white", markeredgewidth=1.0, zorder=5)

    # observation labels sit between columns, because an edge consumes one pulse
    for j, (a, b) in enumerate(zip(xc, xc[1:]), start=1):
        ax.text((a + b) / 2, 0.375, rf"$o_{j}$", transform=ax.transAxes,
                ha="center", va="center", fontsize=7.5,
                color=C2 if j == 3 else INK_2)
    ax.annotate("", xy=(0.90, 0.315), xytext=(0.16, 0.315),
                xycoords=ax.transAxes, textcoords=ax.transAxes,
                arrowprops=dict(arrowstyle="-|>", color=INK_2, linewidth=0.8))
    ax.text(0.53, 0.255, "detected pulses", transform=ax.transAxes, ha="center",
            va="center", fontsize=7, color=INK_2)

    # Annotations live BELOW the axis and are keyed by colour, so no leader
    # line has to cross the lattice.
    ax.text(0.5, 0.145, r"$o_3$ advances two positions on one pulse:"
                        " a symbol was never detected",
            transform=ax.transAxes, ha="center", va="center", fontsize=7,
            color=C2)
    ax.text(0.5, 0.035, r"every edge scores  $\log\mathcal{N}(d;\mu_s,\sigma_s^2)"
                        r"+\log\mathcal{N}(g;\hat{g}(k,u),((1{+}k)\sigma_g)^2)"
                        r"+k\log\pi$",
            transform=ax.transAxes, ha="center", va="center", fontsize=7,
            color=INK_2)
    ax.set_title("(b)  decoding as a trellis; bold is the surviving path",
                 fontsize=8.5, color=INK, loc="left", pad=4)

    fig.tight_layout(pad=0.4)
    return save(fig, base)


# ------------------------------------------------------------ separability --

def fig_separability(sweep, base):
    import plot_sweep as PS
    by = PS.load(sweep)
    rates = sorted(by)
    stats = [PS.per_rate_stats(by[r], r) for r in rates]
    fig, ax = plt.subplots(figsize=(3.5, 2.6))
    xs = [s["bits"] for s in stats]
    ys = [s["sep"] for s in stats]
    ax.plot(xs, ys, color=C1, marker="o", markersize=5, linewidth=1.6,
            markeredgecolor="white", markeredgewidth=0.6, zorder=3)
    ax.axhline(2, color=INK_2, linewidth=0.9, linestyle="--", zorder=2)
    ax.annotate("2 sd: classes overlap below this", xy=(xs[0], 2.6),
                fontsize=7, color=INK_2)
    ax.set_ylim(0, max(ys) * 1.15)
    style(ax, "Data rate (bit/s)", "ZERO vs ONE separation (pooled sd)")
    fig.tight_layout(pad=0.4)
    return save(fig, base)


# ----------------------------------------------------------------- results --

def held_out(recs, test_fraction=0.3):
    """The same message-level split evaluate_decoder.py uses.

    Must match exactly. The ceiling is a property of the transmissions it is
    computed over, so computing it on all ~120 passes while the decoder and
    baseline points come from the 36 held-out ones compares two different
    populations - which overstated the ceiling by up to 9 points at 85.7 bit/s.
    """
    msgs = sorted({r["message"] for r in recs})
    n_test = max(1, int(len(msgs) * test_fraction))
    test_msgs = set(msgs[:n_test])
    return [r for r in recs if r["message"] in test_msgs]


def fig_results(results, sweep, base):
    with open(results, encoding="utf-8") as f:
        res = json.load(f)
    rates = sorted(res, key=lambda r: res[r]["bits"])
    xs = [res[r]["bits"] for r in rates]
    ns = [res[r]["n"] for r in rates]

    ceiling = None
    try:
        import plot_sweep as PS
        by = PS.load(sweep)
        ceiling = [PS.per_rate_stats(held_out(by[float(r)]), float(r))["ceiling"]
                   for r in rates if float(r) in by]
        if len(ceiling) != len(rates):
            ceiling = None
    except Exception:
        pass

    fig, axes = plt.subplots(1, 2, figsize=(7.2, 2.8))
    ax = axes[0]
    if ceiling:
        ax.plot(xs, [100 * c for c in ceiling], color=C3, marker="^",
                markersize=5, linewidth=1.5, linestyle="--",
                markeredgecolor="white", markeredgewidth=0.6,
                label="Recoverable ceiling", zorder=2)
    for key, colour, marker, label in (
            ("decoder_exact", C2, "o", "Sequence decoder"),
            ("baseline_exact", C1, "s", "Threshold decoder")):
        ks = [res[r][key] for r in rates]
        ys = [100 * k / n for k, n in zip(ks, ns)]
        lo = [100 * (k / n - wilson(k, n)[0]) for k, n in zip(ks, ns)]
        hi = [100 * (wilson(k, n)[1] - k / n) for k, n in zip(ks, ns)]
        ax.errorbar(xs, ys, yerr=[lo, hi], color=colour, marker=marker,
                    markersize=5, linewidth=1.6, capsize=2.5, elinewidth=0.9,
                    markeredgecolor="white", markeredgewidth=0.6,
                    label=label, zorder=3)
    ax.set_ylim(0, 108)
    style(ax, "Data rate (bit/s)", "Transmission success rate (%)")
    ax.set_title("(a)", fontsize=9, color=INK, loc="left")
    leg = ax.legend(frameon=False, fontsize=7.5, loc="lower left",
                    handlelength=1.8, borderaxespad=0.2)
    for t in leg.get_texts():
        t.set_color(INK)

    ax = axes[1]
    for key, colour, marker, label in (
            ("decoder_byte_err", C2, "o", "Sequence decoder"),
            ("baseline_byte_err", C1, "s", "Threshold decoder")):
        ax.plot(xs, [100 * res[r][key] for r in rates], color=colour,
                marker=marker, markersize=5, linewidth=1.6,
                markeredgecolor="white", markeredgewidth=0.6, label=label,
                zorder=3)
    for x, r in zip(xs, rates):
        if res[r]["decoder_byte_err"] == 0:
            ax.annotate("no errors", xy=(x, 0), xytext=(4, 10),
                        textcoords="offset points", color=C2, fontsize=7)
    ax.set_ylim(-0.3, 7.2)
    style(ax, "Data rate (bit/s)", "Character error rate (%)")
    ax.set_title("(b)", fontsize=9, color=INK, loc="left")
    leg = ax.legend(frameon=False, fontsize=7.5, loc="upper right",
                    handlelength=1.8, borderaxespad=0.2)
    for t in leg.get_texts():
        t.set_color(INK)
    fig.tight_layout(pad=0.5)
    return save(fig, base)


# -------------------------------------------------------------------- live --

def fig_live(csvs, base):
    """Closed-loop results measured on hardware, decoder vs baseline."""
    groups = []
    for label, path in csvs:
        if not pathlib.Path(path).exists():
            continue
        rows = list(csv.DictReader(open(path)))
        if not rows:
            continue
        n = len(rows)
        d = sum(int(r["decoder_exact"]) for r in rows)
        b = sum(int(r["baseline_exact"]) for r in rows)
        groups.append((label, n, d, b))
    if not groups:
        return None

    fig, ax = plt.subplots(figsize=(3.5, 2.6))
    idx = range(len(groups))
    wbar = 0.34
    for off, key, colour, lab in ((-wbar / 2, 3, C1, "Threshold decoder"),
                                  (wbar / 2, 2, C2, "Sequence decoder")):
        ys = [100 * g[key] / g[1] for g in groups]
        errs = [[100 * (g[key] / g[1] - wilson(g[key], g[1])[0]) for g in groups],
                [100 * (wilson(g[key], g[1])[1] - g[key] / g[1]) for g in groups]]
        ax.bar([i + off for i in idx], ys, wbar, color=colour, label=lab,
               edgecolor="white", linewidth=1.2, zorder=3,
               yerr=errs, capsize=3, error_kw=dict(elinewidth=0.9, ecolor=INK_2))
        for i, y in zip(idx, ys):
            ax.annotate(f"{y:.0f}%", xy=(i + off, y), xytext=(0, 3),
                        textcoords="offset points", ha="center",
                        fontsize=7.5, color=INK)
    ax.set_xticks(list(idx))
    ax.set_xticklabels([f"{g[0]}\n(n={g[1]})" for g in groups], fontsize=8)
    ax.set_ylim(0, 118)
    style(ax, "", "Transmission success rate (%)")
    leg = ax.legend(frameon=False, fontsize=7.5, loc="upper right",
                    handlelength=1.5, borderaxespad=0.2)
    for t in leg.get_texts():
        t.set_color(INK)
    fig.tight_layout(pad=0.4)
    return save(fig, base)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sweep", default="experiment_data/C_data_rate_sweep/comms_sweep_v6.jsonl")
    ap.add_argument("--results", default="experiment_data/D_offline_decoder_evaluation/decoder_results.json")
    ap.add_argument("--outdir", default="docs/IEEE Paper/figures")
    args = ap.parse_args()

    matplotlib.use("Agg")
    matplotlib.rcParams["pdf.fonttype"] = 42
    matplotlib.rcParams["font.family"] = "sans-serif"

    out = pathlib.Path(args.outdir)
    out.mkdir(parents=True, exist_ok=True)

    made = [
        fig_protocol(str(out / "fig_protocol")),
        fig_recovery(str(out / "fig_recovery")),
        fig_model(str(out / "fig_model")),
        fig_separability(args.sweep, str(out / "fig_separability")),
        fig_results(args.results, args.sweep, str(out / "fig_results")),
        fig_live([("28.6 bit/s", "experiment_data/E_live_closed_loop/live_decode.csv"),
                  ("114.3 bit/s", "experiment_data/E_live_closed_loop/live_decode_rate4.csv")],
                 str(out / "fig_live")),
    ]
    print("wrote:")
    for m in made:
        if m:
            print(f"   {m}.pdf / .png")


if __name__ == "__main__":
    main()

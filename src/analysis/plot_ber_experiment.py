"""Plot bit error rate experiment results from a CSV produced by run_ber_experiment.py.

Produces PNG figures: cumulative BER over time and cumulative packet success
rate over time (paper-ready), plus a diagnostics figure (resync errors,
missed start pulses, edge threshold) for your own reference.
"""
import argparse
import csv

import matplotlib.pyplot as plt


def load_rows(csv_path: str) -> list[dict]:
    with open(csv_path, "r", newline="") as f:
        return list(csv.DictReader(f))


def to_float(value: str, default: float = float("nan")) -> float:
    return float(value) if value not in (None, "") else default


def main() -> None:
    parser = argparse.ArgumentParser(description="Plot BER experiment results from run_ber_experiment.py's CSV.")
    parser.add_argument("csv_path", help="Path to the CSV written by run_ber_experiment.py")
    parser.add_argument(
        "--out-prefix", default=None, help="Prefix for saved PNG files (default: derived from csv_path)"
    )
    parser.add_argument("--no-show", action="store_true", help="Save PNGs without opening interactive windows")
    args = parser.parse_args()

    rows = load_rows(args.csv_path)
    if not rows:
        raise SystemExit(f"No data in {args.csv_path}")

    prefix = args.out_prefix or args.csv_path.rsplit(".", 1)[0]

    elapsed_min = [to_float(r["elapsed_s"]) / 60.0 for r in rows]
    cumulative_ber = [to_float(r["cumulative_ber"]) * 100 for r in rows]
    cumulative_success = [to_float(r["cumulative_packet_success_rate"]) * 100 for r in rows]
    resync_errors = [to_float(r["resync_errors"]) for r in rows]
    missed_start_pulses = [to_float(r["missed_start_pulses"]) for r in rows]
    edge_threshold = [to_float(r["edge_threshold"]) for r in rows]

    # --- Figure 1: cumulative bit error rate over time ---
    plt.figure(figsize=(8, 4.5))
    plt.plot(elapsed_min, cumulative_ber, color="#d9534f", linewidth=1.5)
    plt.xlabel("Elapsed time (minutes)")
    plt.ylabel("Cumulative bit error rate (%)")
    plt.title("IR Link Bit Error Rate Over Time")
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(f"{prefix}_ber.png", dpi=200)

    # --- Figure 2: cumulative packet success rate over time ---
    plt.figure(figsize=(8, 4.5))
    plt.plot(elapsed_min, cumulative_success, color="#5cb85c", linewidth=1.5)
    plt.xlabel("Elapsed time (minutes)")
    plt.ylabel("Cumulative packet success rate (%)")
    plt.title("IR Link Packet Success Rate Over Time")
    plt.ylim(0, 105)
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(f"{prefix}_packet_success.png", dpi=200)

    # --- Figure 3: diagnostics (resync errors, missed start pulses, edge threshold) ---
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(8, 6), sharex=True)
    ax1.plot(elapsed_min, resync_errors, label="Resync errors", color="#f0ad4e")
    ax1.plot(elapsed_min, missed_start_pulses, label="Missed start pulses", color="#5bc0de")
    ax1.set_ylabel("Cumulative count")
    ax1.legend()
    ax1.grid(True, alpha=0.3)

    ax2.plot(elapsed_min, edge_threshold, color="#292b2c")
    ax2.set_xlabel("Elapsed time (minutes)")
    ax2.set_ylabel("Edge threshold")
    ax2.grid(True, alpha=0.3)

    fig.suptitle("Receiver Diagnostics Over Time")
    fig.tight_layout()
    fig.savefig(f"{prefix}_diagnostics.png", dpi=200)

    print(f"Saved {prefix}_ber.png")
    print(f"Saved {prefix}_packet_success.png")
    print(f"Saved {prefix}_diagnostics.png")

    if not args.no_show:
        plt.show()


if __name__ == "__main__":
    main()

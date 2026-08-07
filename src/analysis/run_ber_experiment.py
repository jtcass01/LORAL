"""Run a timed IR comms bit error rate experiment and log results to CSV.

Runs on a PC. Repeatedly polls the beacon (ground truth) and robot (decoded
messages) status pages for a fixed duration, computing bit error rate and
packet success rate over time. Each poll only counts messages that are new
since the last poll — the robot's status page only ever shows the last few
decoded messages, so re-reading the whole window every poll would double
count a message that's still sitting there because it hasn't been pushed out
yet.

Writes one row per poll to a CSV file. See plot_ber_experiment.py to turn the
CSV into figures.
"""
import argparse
import csv
import sys
import time
from datetime import datetime

import requests

from diagnose_comms import compute_bit_error_stats, fetch_beacon_message, fetch_robot_status

CSV_FIELDS = [
    "elapsed_s",
    "timestamp",
    "ground_truth",
    "estimated_distance",
    "new_messages",
    "poll_bit_errors",
    "poll_bits_sent",
    "poll_ber",
    "cumulative_bit_errors",
    "cumulative_bits_sent",
    "cumulative_ber",
    "cumulative_messages",
    "cumulative_exact_matches",
    "cumulative_packet_success_rate",
    "resync_errors",
    "missed_start_pulses",
    "edge_threshold",
]


def find_new_messages(previous_window: list[str], current_window: list[str]) -> list[str]:
    """The robot's status page exposes a rolling FIFO of the last few decoded
    messages. Given the previous poll's window and this poll's window, return
    only the messages that are new since the last poll, by finding the
    longest suffix of the old window that matches a prefix of the new one
    (the FIFO overlap) and returning everything after it.
    """
    if not previous_window:
        return current_window
    max_overlap = min(len(previous_window), len(current_window))
    for overlap in range(max_overlap, -1, -1):
        if overlap == 0 or previous_window[-overlap:] == current_window[:overlap]:
            return current_window[overlap:]
    return current_window


def run_experiment(robot_url: str, beacon_url: str, duration_s: float, interval_s: float, csv_path: str) -> None:
    previous_window: list[str] = []
    cumulative_bit_errors = 0
    cumulative_bits_sent = 0
    cumulative_messages = 0
    cumulative_exact_matches = 0
    cumulative_ber = 0.0
    cumulative_success = 0.0

    start = time.monotonic()
    next_poll = start

    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()

        while True:
            elapsed = time.monotonic() - start
            if elapsed >= duration_s:
                break

            try:
                current_window, distance, resync_errors, missed_start_pulses, _, edge_threshold = (
                    fetch_robot_status(robot_url)
                )
                ground_truth = fetch_beacon_message(beacon_url)
            except requests.RequestException as exc:
                print(f"[{elapsed:6.1f}s] Failed to reach robot/beacon: {exc}", file=sys.stderr)
            else:
                new_messages = find_new_messages(previous_window, current_window)
                previous_window = current_window

                poll_stats = compute_bit_error_stats(ground_truth, new_messages)
                cumulative_bit_errors += poll_stats["total_bit_errors"]
                cumulative_bits_sent += poll_stats["total_bits_sent"]
                cumulative_messages += poll_stats["message_count"]
                cumulative_exact_matches += poll_stats["exact_matches"]

                cumulative_ber = (cumulative_bit_errors / cumulative_bits_sent) if cumulative_bits_sent else 0.0
                cumulative_success = (
                    (cumulative_exact_matches / cumulative_messages) if cumulative_messages else 0.0
                )

                writer.writerow(
                    {
                        "elapsed_s": round(elapsed, 1),
                        "timestamp": datetime.now().isoformat(timespec="seconds"),
                        "ground_truth": ground_truth,
                        "estimated_distance": distance,
                        "new_messages": poll_stats["message_count"],
                        "poll_bit_errors": poll_stats["total_bit_errors"],
                        "poll_bits_sent": poll_stats["total_bits_sent"],
                        "poll_ber": poll_stats["bit_error_rate"] if poll_stats["message_count"] else "",
                        "cumulative_bit_errors": cumulative_bit_errors,
                        "cumulative_bits_sent": cumulative_bits_sent,
                        "cumulative_ber": cumulative_ber,
                        "cumulative_messages": cumulative_messages,
                        "cumulative_exact_matches": cumulative_exact_matches,
                        "cumulative_packet_success_rate": cumulative_success,
                        "resync_errors": resync_errors,
                        "missed_start_pulses": missed_start_pulses,
                        "edge_threshold": edge_threshold,
                    }
                )
                f.flush()

                print(
                    f"[{elapsed:6.1f}s] +{poll_stats['message_count']} msgs  "
                    f"cumulative BER={cumulative_ber:.4%}  "
                    f"packet success={cumulative_success:.1%}  "
                    f"resync={resync_errors}  missed_start={missed_start_pulses}  "
                    f"threshold={edge_threshold}"
                )

            next_poll += interval_s
            sleep_for = next_poll - time.monotonic()
            if sleep_for > 0:
                time.sleep(sleep_for)

    print(f"\nDone. Wrote {csv_path}")
    print(f"Final cumulative BER: {cumulative_ber:.4%}")
    print(f"Final packet success rate: {cumulative_success:.1%}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run a timed IR comms bit error rate experiment and log results to CSV."
    )
    parser.add_argument("--robot-host", default="192.168.0.228", help="Robot (receiver) IP address")
    parser.add_argument("--beacon-host", default="192.168.0.131", help="Beacon (transmitter) IP address")
    parser.add_argument(
        "--duration", type=float, default=600.0, help="Experiment duration in seconds (default: 600 = 10 min)"
    )
    parser.add_argument("--interval", type=float, default=3.0, help="Seconds between polls (default: 3)")
    parser.add_argument(
        "--output", default=None, help="CSV output path (default: ber_experiment_<timestamp>.csv)"
    )
    args = parser.parse_args()

    csv_path = args.output or f"ber_experiment_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"

    robot_url = f"http://{args.robot_host}/"
    beacon_url = f"http://{args.beacon_host}/"

    print(f"Running {args.duration:.0f}s experiment, polling every {args.interval:.1f}s -> {csv_path}")
    try:
        run_experiment(robot_url, beacon_url, args.duration, args.interval, csv_path)
    except KeyboardInterrupt:
        print(f"\nInterrupted - partial results saved to {csv_path}")


if __name__ == "__main__":
    main()

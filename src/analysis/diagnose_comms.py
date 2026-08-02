"""Poll the robot and beacon HTTP status pages and compute IR comms bit error stats.

Runs on a PC (not the Pico W boards). Fetches the beacon's currently-broadcast
message (ground truth) and the robot's recently-decoded message log, then
computes a deterministic bit error rate and packet success rate from the diff
between them.
"""
import argparse
import difflib
import re
import sys
import time

import requests

MESSAGE_LOG_RE = re.compile(r"<pre>(.*?)</pre>", re.DOTALL)
DISTANCE_RE = re.compile(r"Estimated Distance:</b>\s*<span[^>]*>([^<]*)</span>")
RESYNC_ERRORS_RE = re.compile(r"Resync Errors:</b>\s*<span[^>]*>([^<]*)</span>")
BROADCASTING_RE = re.compile(r"Currently Broadcasting:\s*<b>([^<]*)</b>")

# Placeholder the robot's shared_data starts with before any message is decoded —
# not an actual decode result, so it's excluded from bit error analysis.
INIT_PLACEHOLDER = "System initialized. Awaiting messages..."


def bit_hamming_distance(a: str, b: str) -> int:
    return bin(ord(a) ^ ord(b)).count("1")


def compute_bit_error_stats(ground_truth: str, decoded_messages: list[str]) -> dict:
    """Diff each decoded message against the beacon's ground-truth broadcast.

    Uses a character-level alignment (difflib) rather than a naive positional
    compare, since a single dropped or inserted byte would otherwise shift
    every character after it out of alignment. Substituted characters are
    scored by their actual bit Hamming distance (XOR + popcount); dropped or
    inserted characters count as a full lost/spurious byte (8 bits).
    """
    per_message = []
    total_bits_sent = 0
    total_bit_errors = 0
    exact_matches = 0

    for decoded in decoded_messages:
        matcher = difflib.SequenceMatcher(a=ground_truth, b=decoded, autojunk=False)
        bit_errors = 0

        for op, a_start, a_end, b_start, b_end in matcher.get_opcodes():
            if op == "equal":
                continue
            if op == "replace":
                length = max(a_end - a_start, b_end - b_start)
                for i in range(length):
                    in_a = a_start + i < a_end
                    in_b = b_start + i < b_end
                    if in_a and in_b:
                        bit_errors += bit_hamming_distance(ground_truth[a_start + i], decoded[b_start + i])
                    else:
                        bit_errors += 8  # length mismatch within this replace block
            else:  # "delete" (dropped byte) or "insert" (spurious byte)
                bit_errors += 8 * max(a_end - a_start, b_end - b_start)

        total_bits_sent += len(ground_truth) * 8
        total_bit_errors += bit_errors
        exact_matches += decoded == ground_truth
        per_message.append({"decoded": decoded, "bit_errors": bit_errors})

    message_count = len(decoded_messages)
    return {
        "per_message": per_message,
        "message_count": message_count,
        "total_bit_errors": total_bit_errors,
        "total_bits_sent": total_bits_sent,
        "bit_error_rate": (total_bit_errors / total_bits_sent) if total_bits_sent else 0.0,
        "packet_success_rate": (exact_matches / message_count) if message_count else 0.0,
    }


def fetch_robot_status(url: str) -> tuple[list[str], str, str]:
    response = requests.get(url, timeout=5)
    response.raise_for_status()
    html = response.text

    message_match = MESSAGE_LOG_RE.search(html)
    distance_match = DISTANCE_RE.search(html)
    resync_match = RESYNC_ERRORS_RE.search(html)

    log_text = message_match.group(1).strip() if message_match else ""
    messages = [m for m in log_text.splitlines() if m and m != INIT_PLACEHOLDER]
    distance = distance_match.group(1).strip() if distance_match else "(unknown)"
    resync_errors = resync_match.group(1).strip() if resync_match else "(unknown)"
    return messages, distance, resync_errors


def fetch_beacon_message(url: str) -> str:
    response = requests.get(url, timeout=5)
    response.raise_for_status()
    match = BROADCASTING_RE.search(response.text)
    return match.group(1).strip() if match else "(unknown)"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compute IR comms bit error rate and packet success rate from the beacon/robot status pages."
    )
    parser.add_argument("--robot-host", default="192.168.0.228", help="Robot (receiver) IP address")
    parser.add_argument("--beacon-host", default="192.168.0.131", help="Beacon (transmitter) IP address")
    parser.add_argument("--interval", type=float, default=10.0, help="Seconds between polls (default: 10)")
    parser.add_argument("--once", action="store_true", help="Poll a single time, then exit")
    args = parser.parse_args()

    robot_url = f"http://{args.robot_host}/"
    beacon_url = f"http://{args.beacon_host}/"

    while True:
        try:
            decoded_messages, distance, resync_errors = fetch_robot_status(robot_url)
            ground_truth = fetch_beacon_message(beacon_url)
        except requests.RequestException as exc:
            print(f"Failed to reach robot/beacon: {exc}", file=sys.stderr)
        else:
            stats = compute_bit_error_stats(ground_truth, decoded_messages)
            print(f"\n=== Bit error stats ({time.strftime('%H:%M:%S')}) ===")
            print(f"Ground truth:         {ground_truth!r}")
            print(f"Estimated distance:   {distance}")
            print(f"Messages compared:    {stats['message_count']}")
            print(f"Bit error rate:       {stats['bit_error_rate']:.4%}")
            print(f"Packet success rate:  {stats['packet_success_rate']:.2%}")
            print(f"Resync errors:        {resync_errors}")
            for m in stats["per_message"]:
                print(f"  {m['bit_errors']:3d} bit errors  <- {m['decoded']!r}")

        if args.once:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()

"""Collect labeled ADC training data over USB serial for distance-regression training.

Runs on a PC. Connects to the Pico's USB serial port (running
src/robot/serial_pulse_logger.py), reads the raw binary PULSE packets it
writes via sys.stdout.buffer.write, tags each with the target distance you
supply for this session, and appends rows to a CSV. Run once per physical
distance you want a calibration point for - the CSV accumulates labeled
samples across sessions.
"""
import argparse
import csv
import os
import struct

import serial

CSV_FIELDS = ["duration_us", "peak_amplitude", "target_distance_in"]

SYNC_BYTE = 0xAA
PACKET_FORMAT = "<BIH"  # sync byte, duration_us (uint32), peak_amplitude (uint16)
PACKET_SIZE = struct.calcsize(PACKET_FORMAT)


def read_pulse_packet(ser: serial.Serial) -> tuple[int, int] | None:
    """Read one binary PULSE packet from the serial stream.

    Scans byte-by-byte for the sync byte before reading the fixed-size
    payload, so the parser can recover framing if it starts listening
    mid-stream (or a byte was ever dropped on the wire) rather than reading
    misaligned garbage forever. Returns (duration_us, peak_amplitude), or
    None on a read timeout.
    """
    while True:
        sync = ser.read(1)
        if not sync:
            return None  # read timeout, no data right now
        if sync[0] == SYNC_BYTE:
            break

    payload = ser.read(PACKET_SIZE - 1)
    if len(payload) != PACKET_SIZE - 1:
        return None  # timed out mid-packet

    _, duration_us, peak_amplitude = struct.unpack(PACKET_FORMAT, sync + payload)
    return duration_us, peak_amplitude


def collect(port: str, baud: int, distance_m: float, csv_path: str) -> None:
    need_header = not os.path.exists(csv_path) or os.path.getsize(csv_path) == 0

    with serial.Serial(port, baud, timeout=1) as ser, open(csv_path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if need_header:
            writer.writeheader()

        print(f"Connected to {port}. Collecting samples at distance={distance_m}m. Ctrl+C to stop.")
        count = 0
        try:
            while True:
                packet = read_pulse_packet(ser)
                if packet is None:
                    continue
                duration_us, peak_amplitude = packet

                writer.writerow(
                    {
                        "duration_us": duration_us,
                        "peak_amplitude": peak_amplitude,
                        "target_distance_in": distance_m,
                    }
                )
                f.flush()
                count += 1
                if count % 10 == 0:
                    print(f"  {count} samples collected...")
        except KeyboardInterrupt:
            pass

        print(f"\nStopped. Collected {count} samples at distance={distance_m}m -> {csv_path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Collect labeled ADC pulse data (duration, peak amplitude, target distance) over USB serial."
    )
    parser.add_argument("--port", required=True, help="Serial port, e.g. COM5 (Windows) or /dev/ttyACM0 (Linux)")
    parser.add_argument("--baud", type=int, default=115200, help="Baud rate (default: 115200)")
    parser.add_argument(
        "--distance",
        type=float,
        required=True,
        help="Known target distance in meters for this collection session",
    )
    parser.add_argument(
        "--output", default="training_data.csv", help="CSV output path (default: training_data.csv)"
    )
    args = parser.parse_args()

    collect(args.port, args.baud, args.distance, args.output)


if __name__ == "__main__":
    main()

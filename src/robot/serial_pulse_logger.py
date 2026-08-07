"""Stream per-pulse ADC data over USB serial (raw binary) for distance-regression training.

Runs standalone on the Pico W - no Wi-Fi, no HTTP server. Reuses the same
edge-detection logic as SingleDiodeRobot (single_diode_robot.py must be on
the device alongside this file) to detect IR pulses, and writes one fixed-size
binary packet per detected pulse straight to USB serial via
sys.stdout.buffer.write - no print(), no text encoding, no newlines:

    SYNC (1 byte, 0xAA) + duration_us (uint32, little-endian) + peak_amplitude (uint16, little-endian)

The sync byte lets the PC-side reader recover framing if it starts listening
mid-stream. Pair with src/ml/collect_training_data.py, which reads this
serial stream, tags each pulse with a target distance you supply, and logs
rows to a CSV for later model training.
"""
import sys
import uasyncio as asyncio
import ustruct as struct
from machine import ADC

from single_diode_robot import SingleDiodeRobot

SYNC_BYTE = 0xAA
PACKET_FORMAT = "<BIH"  # sync byte, duration_us (uint32), peak_amplitude (uint16)


async def main():
    receiver_adc = ADC(0)
    robot = SingleDiodeRobot(receiver_adc=receiver_adc, edge_threshold=2000)
    while True:
        duration_us, peak_amplitude = await robot.listen_for_pulse()
        packet = struct.pack(PACKET_FORMAT, SYNC_BYTE, duration_us, peak_amplitude)
        sys.stdout.buffer.write(packet)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass

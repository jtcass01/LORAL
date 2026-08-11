"""Stream per-pulse ADC data over USB serial (line-based text) for distance training.

Runs standalone on the Pico W - no Wi-Fi, no HTTP server. Reuses the same
edge-detection logic as SingleDiodeRobot (single_diode_robot.py must be on the
device alongside this file) to detect IR pulses, and prints one line per event.

The protocol is human-readable text, one record per line, `KIND key=value ...`:

    LORAL 3 serial_pulse_logger ready adc=0 thr=2000
    PULSE dur=25100 peak=43440 trough=42000 base=42000 hpf=2880 thr=1150 bo=0
    IDLE thr=1150 raw=42013 pulses=12 since_ms=2000
    ERR <message>

This used to be a packed binary struct with a 0xAA sync byte. Text is a better
fit and the reasons are worth recording, because the binary version cost real
debugging time:

  * Throughput is a non-issue. INTER_SAMPLE_MS caps this at ~2 records/second,
    about 150 bytes/s against 11 KB/s of available bandwidth. The binary
    encoding was optimising a resource that was never scarce.
  * Binary made the device un-printable: stdout was the packet channel, so any
    diagnostic print corrupted the stream, and a crashed script emitted a
    traceback that the PC parser could only see as garbage. Now a traceback is
    just another line the collector can show you.
  * A firmware/collector version skew silently produced misframed nonsense.
    key=value parsing is order-independent and tolerant of added or missing
    fields, so a mismatch degrades instead of exploding - and the LORAL banner
    states the version outright.

IDLE lines are the point of the exercise: without them a device that is alive
but detecting nothing is indistinguishable from a dead one. They report the
current ADC level and adapted threshold, which together say whether the signal
is absent, too weak, or present but below the trigger.
"""
import sys
import uasyncio as asyncio
from machine import ADC
from utime import ticks_ms, ticks_diff

from single_diode_robot import SingleDiodeRobot

# Bump when the line format changes in a way the collector must know about.
# collect_training_data.py checks this against its own PROTOCOL_VERSION.
PROTOCOL_VERSION = 3

# Gap between recorded pulses. The beacon emits a burst of pulses per message;
# sleeping between them keeps consecutive CSV rows from being near-duplicates
# of the same transmission, which would otherwise make a random train/test
# split look far better than the model really is.
INTER_SAMPLE_MS = 500

# How often to emit an IDLE heartbeat. Independent of pulse detection, so the
# device keeps talking even when it is hearing nothing at all.
IDLE_REPORT_MS = 2000

_stats = {"pulses": 0, "last_pulse_ms": 0}


async def report_idle(robot, adc):
    """Emit a heartbeat so silence on the wire always means a dead device.

    Reads the ADC directly rather than going through robot.sample_hpf(), which
    would corrupt the high-pass filter's previous-sample memory and fabricate
    an edge. A bare read_u16() touches no detector state.
    """
    while True:
        await asyncio.sleep_ms(IDLE_REPORT_MS)
        thr, backoffs = robot.detector_state()
        since = ticks_diff(ticks_ms(), _stats["last_pulse_ms"])
        print(f"IDLE thr={thr} raw={adc.read_u16()} bo={backoffs} "
              f"pulses={_stats['pulses']} since_ms={since}")


async def main():
    receiver_adc = ADC(0)
    robot = SingleDiodeRobot(receiver_adc=receiver_adc, edge_threshold=2000)
    # Seed the filter from the real ADC level before the first sample. Without
    # this, _prev_raw starts at 0, so the very first sample_hpf reports
    # raw - 0 (~42000) as an edge: the first PULSE is garbage, and worse, that
    # bogus magnitude feeds _adapt_threshold and slams the threshold up to
    # ~3800, which then takes several real pulses to decay back down - missing
    # pulses and mismeasuring their durations the whole way.
    robot.prime_filter()
    _stats["last_pulse_ms"] = ticks_ms()

    print(f"LORAL {PROTOCOL_VERSION} serial_pulse_logger ready adc=0 thr=2000")
    asyncio.create_task(report_idle(robot, receiver_adc))

    while True:
        duration_us, peak, trough, baseline, peak_hpf = await robot.listen_for_pulse()
        thr, backoffs = robot.detector_state()
        _stats["pulses"] += 1
        _stats["last_pulse_ms"] = ticks_ms()
        print(f"PULSE dur={duration_us} peak={peak} trough={trough} base={baseline} "
              f"hpf={int(peak_hpf)} thr={thr} bo={backoffs}")
        await asyncio.sleep_ms(INTER_SAMPLE_MS)
        # We stopped sampling for the whole sleep above, so the high-pass
        # filter's previous-sample memory is now stale by INTER_SAMPLE_MS.
        # Without this the first sample after waking looks like an enormous
        # edge and immediately fires a false pulse - which is recorded as a
        # real one, with a meaningless duration and swing.
        robot.prime_filter()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except Exception as e:
        # Surface the failure in the protocol's own vocabulary before the
        # interpreter's traceback follows it out the same serial port.
        print(f"ERR {type(e).__name__}: {e}")
        raise

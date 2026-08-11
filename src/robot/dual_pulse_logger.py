"""Stream per-pulse two-channel ADC data over USB serial, for bearing training.

Runs standalone on the Pico - no Wi-Fi, no HTTP server. Needs
double_diode_robot.py on the device alongside it. One line per event:

    LORAL 4 dual_pulse_logger ready adc_a=0 adc_b=1 thr=2000
    PULSE dur=25110 a_peak=42218 a_trough=39577 a_base=39755 a_hpf=1887
                    b_peak=41902 b_trough=39610 b_base=39702 b_hpf=1204
                    thr=1078 bo=0
    IDLE thr=1078 a_raw=40276 b_raw=40188 bo=0 pulses=12 since_ms=200
    ERR <message>

(PULSE is one physical line; wrapped above only for readability.)

Protocol 4 adds the second channel. The collector parses key=value pairs, so
it is order-independent and tolerant of fields appearing or disappearing - a
version skew degrades rather than producing misframed nonsense.

DO NOT switch this back to a packed binary format. Text costs ~300 B/s against
11 KB/s available, and it is what makes the device debuggable: a crash prints a
traceback the collector can show you, instead of garbage a binary parser cannot
interpret.

IDLE lines report BOTH raw levels. That is the fastest saturation check there
is - if a_raw and b_raw sit at a fixed ceiling no matter how the robot is
aimed, the MCP6002 is clipping and no amount of differencing will recover a
bearing. Back the beacon off until they move.
"""
import uasyncio as asyncio
from machine import ADC
from utime import ticks_ms, ticks_diff

from double_diode_robot import DoubleDiodeRobot

PROTOCOL_VERSION = 4

# ADC channel numbers for the two photodiode circuits.
# ADC(0)=GP26, ADC(1)=GP27, ADC(2)=GP28. Change to match your wiring.
ADC_A = 0
ADC_B = 1

INTER_SAMPLE_MS = 500
IDLE_REPORT_MS = 2000

_stats = {"pulses": 0, "last_pulse_ms": 0}


async def report_idle(robot, adc_a, adc_b):
    """Heartbeat, so silence on the wire always means a dead device.

    Reads the ADCs directly rather than through robot.sample(), which would
    corrupt the high-pass filters' previous-sample memory and fabricate an edge.
    """
    while True:
        await asyncio.sleep_ms(IDLE_REPORT_MS)
        thr, backoffs = robot.detector_state()
        since = ticks_diff(ticks_ms(), _stats["last_pulse_ms"])
        print(f"IDLE thr={thr} a_raw={adc_a.read_u16()} b_raw={adc_b.read_u16()} "
              f"bo={backoffs} pulses={_stats['pulses']} since_ms={since}")


async def main():
    adc_a = ADC(ADC_A)
    adc_b = ADC(ADC_B)
    robot = DoubleDiodeRobot(adc_a=adc_a, adc_b=adc_b, edge_threshold=2000)

    # Seed the filters from real ADC levels before the first sample. Without
    # this, prev_raw starts at 0 and the first sample reports raw - 0 as an
    # enormous edge: the first PULSE is garbage, and that bogus magnitude feeds
    # the threshold adaptation and takes several real pulses to decay back out.
    robot.prime_filter()
    _stats["last_pulse_ms"] = ticks_ms()

    print(f"LORAL {PROTOCOL_VERSION} dual_pulse_logger ready "
          f"adc_a={ADC_A} adc_b={ADC_B} thr=2000")
    asyncio.create_task(report_idle(robot, adc_a, adc_b))

    while True:
        p = await robot.listen_for_pulse()
        thr, backoffs = robot.detector_state()
        _stats["pulses"] += 1
        _stats["last_pulse_ms"] = ticks_ms()
        print(f"PULSE dur={p['dur']} "
              f"a_peak={p['a_peak']} a_trough={p['a_trough']} a_base={p['a_base']} "
              f"a_hpf={p['a_hpf']} "
              f"b_peak={p['b_peak']} b_trough={p['b_trough']} b_base={p['b_base']} "
              f"b_hpf={p['b_hpf']} thr={thr} bo={backoffs}")
        await asyncio.sleep_ms(INTER_SAMPLE_MS)
        robot.prime_filter()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except Exception as e:
        print(f"ERR {type(e).__name__}: {e}")
        raise

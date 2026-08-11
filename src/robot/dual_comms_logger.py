"""Stream every pulse from both diodes, continuously, for decoder training.

Runs standalone on the Pico. Needs double_diode_robot.py alongside it.

Line format (protocol 6), one per pulse:

    LORAL 6 dual_comms_logger ready adc_a=0 adc_b=1
    P dur=25110 gap=10050 a_hpf=1887 b_hpf=1204 a_pk=42218 b_pk=41902 thr=1078 bo=0
    FLUSH n=234 dropped=0

WHY THIS BUFFERS
----------------
Protocol 5 printed each pulse as it was detected, and that turned out to be the
dominant error source at high data rates. The sampling loop stops for the whole
serial write, and the inter-bit gap it has to fit inside shrinks with the rate:

    rate 1  gap 10000us   a 1ms write costs 10% of the gap
    rate 4  gap  2500us   a 1ms write costs 40% of the gap
    rate 6  gap  1667us   a 1ms write costs 60% of the gap

Measured symbol loss tracked that almost linearly - 0.21% at rate 1 rising to
0.88% at rate 6 - which is the signature of a fixed per-pulse dead time, not of
a degrading optical link. The experiment was measuring its own instrumentation.

So the in-gap work is now one tuple append, and all formatting and I/O happens
in a single write during the beacon's 500ms inter-message rest, via the
idle_cb hook in listen_for_pulse. Nothing is written while a pass is in flight.

The FLUSH line reports the batch size and any pulses lost to a full buffer, so
a buffer overrun can never masquerade as a channel error in the analysis.
"""
from array import array

import uasyncio as asyncio
from machine import ADC
from utime import ticks_ms, ticks_diff

from double_diode_robot import DoubleDiodeRobot

PROTOCOL_VERSION = 6

ADC_A = 0
ADC_B = 1

# Idle time before the buffer is flushed. Must sit above the largest inter-bit
# gap (10ms at rate 1) and below the beacon's 500ms inter-message rest.
IDLE_FLUSH_US = 100_000

# Longest pass we will buffer. The beacon caps messages at 32 chars = 33 bytes
# = 297 symbols; 320 leaves a little headroom.
MAX_BUFFER = 320

# Lines emitted per write during a flush. Formatting all ~300 at once needed a
# contiguous ~19KB allocation and reliably raised MemoryError on the Pico.
# Chunking keeps each allocation near 1.5KB while still avoiding 300 separate
# writes. Only ever runs while the line is idle, so the extra calls are free.
FLUSH_CHUNK = 16

# Fixed-size columns, allocated ONCE at import. The record path then does no
# allocation at all - it only assigns into preallocated slots. That matters
# twice over: a MicroPython heap allocation between pulses costs time in the
# gap we are trying to protect, and a garbage collection pause there would
# drop pulses outright.
_dur = array('i', [0] * MAX_BUFFER)
_gap = array('i', [0] * MAX_BUFFER)
_ahpf = array('i', [0] * MAX_BUFFER)
_bhpf = array('i', [0] * MAX_BUFFER)
_apk = array('i', [0] * MAX_BUFFER)
_bpk = array('i', [0] * MAX_BUFFER)
_thr = array('i', [0] * MAX_BUFFER)
_bo = array('i', [0] * MAX_BUFFER)

_count = 0
_dropped = 0


def _record(p, thr, backoffs, gap):
    """In-gap work: eight indexed stores, no allocation, no formatting."""
    global _count, _dropped
    i = _count
    if i >= MAX_BUFFER:
        _dropped += 1
        return
    _dur[i] = p["dur"]
    _gap[i] = gap
    _ahpf[i] = p["a_hpf"]
    _bhpf[i] = p["b_hpf"]
    _apk[i] = p["a_peak"]
    _bpk[i] = p["b_peak"]
    _thr[i] = thr
    _bo[i] = backoffs
    _count = i + 1


def make_flush(robot, adc_a, adc_b):
    """Build the idle callback: emit the batch, or a heartbeat if there is none.

    Emitting SOMETHING even when the buffer is empty is the whole point. An
    earlier version returned silently in that case, which meant a receiver that
    was powered and sampling but hearing no pulses produced no output at all -
    byte-for-byte identical to an unplugged board. The heartbeat carries the
    two values that separate those cases, the raw ADC levels and the adapted
    threshold, so "no data" always comes with a reason.
    """
    def flush():
        global _count, _dropped
        n = _count
        if n:
            chunk = []
            for i in range(n):
                chunk.append("P dur=%d gap=%d a_hpf=%d b_hpf=%d a_pk=%d b_pk=%d thr=%d bo=%d"
                             % (_dur[i], _gap[i], _ahpf[i], _bhpf[i],
                                _apk[i], _bpk[i], _thr[i], _bo[i]))
                if len(chunk) >= FLUSH_CHUNK:
                    print("\n".join(chunk))
                    chunk = []
            if chunk:
                print("\n".join(chunk))
            print("FLUSH n=%d dropped=%d" % (n, _dropped))
            _count = 0
        else:
            thr, backoffs = robot.detector_state()
            print("IDLE thr=%d a_raw=%d b_raw=%d bo=%d dropped=%d"
                  % (thr, adc_a.read_u16(), adc_b.read_u16(), backoffs, _dropped))
        _dropped = 0
    return flush


async def main():
    adc_a = ADC(ADC_A)
    adc_b = ADC(ADC_B)
    robot = DoubleDiodeRobot(adc_a=adc_a, adc_b=adc_b, edge_threshold=2000)

    # Seed the filters from real ADC levels before the first sample, or the
    # first pulse reports raw - 0 as an enormous edge and drags the adaptive
    # threshold up for several pulses afterwards.
    robot.prime_filter()

    print("LORAL %d dual_comms_logger ready adc_a=%d adc_b=%d" %
          (PROTOCOL_VERSION, ADC_A, ADC_B))

    flush = make_flush(robot, adc_a, adc_b)
    prev_end_us = None

    while True:
        p = await robot.listen_for_pulse(idle_cb=flush, idle_us=IDLE_FLUSH_US)
        thr, backoffs = robot.detector_state()

        if prev_end_us is None:
            gap = -1                     # first pulse: no previous end to measure from
        else:
            gap = ticks_diff(p["start_us"], prev_end_us)
        prev_end_us = p["end_us"]

        _record(p, thr, backoffs, gap)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except Exception as e:
        print("ERR %s: %s" % (type(e).__name__, e))
        raise

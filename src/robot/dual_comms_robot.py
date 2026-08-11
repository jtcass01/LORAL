"""Receiver application: stream pulses, take decoded messages back from the PC.

Runs standalone on the Pico. Needs double_diode_robot.py alongside it.

WHY THE DECODER IS NOT ON THE DEVICE
------------------------------------
The Viterbi decoder carries 256 states and a backpointer per state per
observation - tens of thousands of entries for one message. This board already
raised MemoryError trying to allocate 19KB for a single formatted batch, so the
lattice is out of reach by more than an order of magnitude. The decode runs on
the PC and the result comes back over the same USB serial link.

That leaves the device doing what it is actually good at: sampling fast enough
not to miss pulses.

PROTOCOL (extends the logger's, protocol 7)
-------------------------------------------
Device -> PC   P dur=.. gap=.. a_hpf=.. b_hpf=.. a_pk=.. b_pk=.. thr=.. bo=..
               FLUSH n=.. dropped=..
               IDLE thr=.. a_raw=.. b_raw=.. bo=.. dropped=..
               MSGLOG n=<count> last=<text>
PC -> device   MSG <decoded text>

All device-side I/O - the flush AND the stdin poll - happens inside the idle
window, never between pulses. Reading stdin costs time exactly like writing
stdout does, and at 4x rate the inter-symbol gap is 2.5ms.
"""
from array import array

import uasyncio as asyncio
import sys
import uselect
from machine import ADC
from utime import ticks_ms, ticks_diff

from double_diode_robot import DoubleDiodeRobot

PROTOCOL_VERSION = 7

ADC_A = 0
ADC_B = 1

IDLE_FLUSH_US = 100_000
MAX_BUFFER = 320
FLUSH_CHUNK = 16
MAX_LOG = 5

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
_messages = []
_received = 0


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


def make_idle_task(robot, adc_a, adc_b):
    """Everything slow happens here: emit the batch, then read any reply.

    Both directions are deliberately confined to the idle window. An earlier
    version wrote per pulse and lost measurable symbols to it; a blocking read
    between pulses would cost exactly the same way.
    """
    poller = uselect.poll()
    poller.register(sys.stdin, uselect.POLLIN)

    def idle():
        global _count, _dropped, _received

        if _count:
            n = _count
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

        # Drain whatever the PC has sent back. Non-blocking: poll(0) returns
        # immediately when nothing is waiting, so a PC that is slow or absent
        # never stalls sampling.
        while poller.poll(0):
            line = sys.stdin.readline()
            if not line:
                break
            line = line.strip()
            if line.startswith("MSG "):
                text = line[4:]
                _messages.append(text)
                if len(_messages) > MAX_LOG:
                    _messages.pop(0)
                _received += 1
                print("MSGLOG n=%d last=%s" % (_received, text))

    return idle


async def main():
    adc_a = ADC(ADC_A)
    adc_b = ADC(ADC_B)
    robot = DoubleDiodeRobot(adc_a=adc_a, adc_b=adc_b, edge_threshold=2000)
    robot.prime_filter()

    print("LORAL %d dual_comms_robot ready adc_a=%d adc_b=%d" %
          (PROTOCOL_VERSION, ADC_A, ADC_B))

    idle = make_idle_task(robot, adc_a, adc_b)
    prev_end_us = None

    while True:
        p = await robot.listen_for_pulse(idle_cb=idle, idle_us=IDLE_FLUSH_US)
        thr, backoffs = robot.detector_state()
        gap = -1 if prev_end_us is None else ticks_diff(p["start_us"], prev_end_us)
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

"""Run the REAL DoubleDiodeRobot against simulated diodes at +/-15 degrees,
capture the REAL logger's output, and feed it to the REAL collector."""
import sys, types, threading, io, asyncio, contextlib, math, os, csv, tempfile, builtins

# ---- stub MicroPython ----
_t = {'us': 0}
utime = types.ModuleType('utime')
utime.ticks_us = lambda: (_t.__setitem__('us', _t['us'] + 10), _t['us'])[1]
utime.ticks_ms = lambda: _t['us'] // 1000
utime.ticks_diff = lambda a, b: a - b
utime.sleep = lambda s: None
ua = types.ModuleType('uasyncio')
ua.sleep = asyncio.sleep
async def _sleep_ms(ms): await asyncio.sleep(0)
ua.sleep_ms = _sleep_ms
ua.run, ua.create_task = asyncio.run, asyncio.create_task
ua.CancelledError = asyncio.CancelledError
mach = types.ModuleType('machine')
for n, m in [('utime', utime), ('uasyncio', ua), ('machine', mach)]:
    sys.modules[n] = m

sys.path.insert(0, 'src/robot')
from double_diode_robot import DoubleDiodeRobot

DC, GAIN = 40000, 6000.0
BORESIGHT = {'a': +15.0, 'b': -15.0}   # diodes aimed 15 deg either side
SIGMA = 20.0                            # beam/acceptance width, degrees
state = {'angle': 0.0, 'i': -1}


def response(diode):
    """Gaussian angular response of one diode to the beacon."""
    off = state['angle'] - BORESIGHT[diode]
    return GAIN * math.exp(-(off ** 2) / (2 * SIGMA ** 2))


class DiodeADC:
    """Square pulse train; amplitude set by this diode's angular response."""
    def __init__(self, which): self.which = which
    def read_u16(self):
        if self.which == 'a':
            state['i'] += 1
        phase = (state['i'] // 1) % 600
        on = phase >= 400
        return int(DC + (response(self.which) if on else 0))


print("=== 1. firmware: does ndiff track angle? ===")
print(f"{'angle':>7} {'a_hpf':>8} {'b_hpf':>8} {'ndiff':>9}")


async def one_pulse(robot):
    await robot.listen_for_pulse()          # settle
    return await robot.listen_for_pulse()


ndiffs = []
for ang in (-30, -20, -10, 0, 10, 20, 30):
    state['angle'], state['i'] = float(ang), -1
    r = DoubleDiodeRobot(adc_a=DiodeADC('a'), adc_b=DiodeADC('b'),
                         edge_threshold=300, settle_discard=False)
    r.prime_filter()
    p = asyncio.run(one_pulse(r))
    a, b = p['a_hpf'], p['b_hpf']
    nd = (a - b) / (a + b) if (a + b) else 0
    ndiffs.append(nd)
    print(f"{ang:>7} {a:>8} {b:>8} {nd:>9.4f}")

assert all(x < y for x, y in zip(ndiffs, ndiffs[1:])), \
    f"ndiff must be monotonic in angle, got {ndiffs}"
print("\nOK: ndiff is strictly monotonic in bearing, and crosses ~0 at boresight")

# ---- 2. logger -> collector round trip ----
print("\n=== 2. real logger output -> real collector parser ===")
mach.ADC = lambda ch: DiodeADC('a' if ch == 0 else 'b')
sys.path.insert(0, 'src/robot')
import dual_pulse_logger as LOG
state['angle'], state['i'] = 20.0, -1

captured = io.StringIO()
async def drive():
    task = asyncio.ensure_future(LOG.main())
    await asyncio.sleep(0.30)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
with contextlib.redirect_stdout(captured):
    asyncio.run(drive())

wire = captured.getvalue()
lines = wire.splitlines()
print("  " + lines[0])
pulse_lines = [l for l in lines if l.startswith("PULSE")]
print(f"  {pulse_lines[0]}")
print(f"  ({len(pulse_lines)} PULSE lines, {len(lines)} total)")

sys.path.insert(0, 'src/ml')
import collect_training_data as C
assert LOG.PROTOCOL_VERSION == 4


class Ser:
    def __init__(self, d): self.b = io.BytesIO(d)
    def reset_input_buffer(self): pass
    def readline(self): return self.b.readline()


ser = Ser(wire.encode())
seen = {}
while True:
    rec = C.read_record(ser)
    if rec is None:
        break
    kind, fields, text = rec
    seen[kind] = seen.get(kind, 0) + 1
    if kind == "PULSE":
        mode = C.detect_mode(fields)
        assert mode == "dual", f"mode detected as {mode}"
        p = C.pulse_values(fields, mode)
        assert p is not None, f"could not parse: {text}"
        d = C.derive_features(p, mode)
assert "?" not in seen, f"collector failed on a line the logger emitted: {seen}"
print(f"  parsed kinds: {seen}")
print(f"  last pulse -> ndiff={d['ndiff_hpf']:.4f} sum={d['sum_hpf']} "
      f"a_swing={d['a_swing']} b_swing={d['b_swing']}")
print("OK: dual mode auto-detected and every line parsed")

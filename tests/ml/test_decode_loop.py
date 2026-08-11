"""Closed-loop test: real robot code -> real daemon logic -> back to robot.

Replays a captured pass through the daemon's decode path, then feeds the
resulting MSG line into the robot's actual stdin handler, confirming the
message reaches the device's log.
"""
import sys, types, json, io

# ---- stub MicroPython for the robot module -------------------------------
ua = types.ModuleType('uasyncio')
ua.sleep = None
mach = types.ModuleType('machine'); mach.ADC = object
ut = types.ModuleType('utime')
ut.ticks_ms = lambda: 0; ut.ticks_us = lambda: 0; ut.ticks_diff = lambda a, b: a - b

STDIN_LINES = []
class FakePoll:
    def register(self, *a): pass
    def poll(self, t): return [1] if STDIN_LINES else []
usel = types.ModuleType('uselect'); usel.poll = lambda: FakePoll(); usel.POLLIN = 1

class FakeStdin:
    def readline(self): return STDIN_LINES.pop(0) if STDIN_LINES else ""
fake_sys = types.ModuleType('sys'); fake_sys.stdin = FakeStdin()

ddr = types.ModuleType('double_diode_robot')
class _R:
    def detector_state(self): return (1100, 0)
ddr.DoubleDiodeRobot = _R
for n, m in [('uasyncio', ua), ('machine', mach), ('utime', ut),
             ('uselect', usel), ('double_diode_robot', ddr)]:
    sys.modules[n] = m

sys.path.insert(0, 'src/robot')
import dual_comms_robot as ROBOT
ROBOT.sys = fake_sys                      # patch the module's sys.stdin

sys.path.insert(0, 'src/ml')
import decoder as D
from decode_daemon import split_passes, parse_line

# ---- 1. daemon decodes a real captured pass ------------------------------
recs = [json.loads(l) for l in open('experiment_data/C_data_rate_sweep/comms_sweep_v6.jsonl', encoding='utf-8') if l.strip()]
recs = [r for r in recs if r.get('rate') == 1.0
        and all(isinstance(v, int) for p in r['pulses'] for v in p.values())]
em = D.fit_emissions(recs, 1.0); gs = D.fit_gap_sigma(recs, 1.0); sp = D.fit_skip_prior(recs)

target = recs[3]
_, out = D.decode(target['pulses'], em, gs, 1.0, skip_log_prior=sp)
decoded = bytes(out or []).decode('ascii', 'replace').rstrip('\n')
print(f"daemon decoded: {decoded!r}")
print(f"ground truth  : {target['message']!r}")
assert decoded == target['message'], "decode mismatch"

# ---- 2. that MSG line goes back into the real robot handler --------------
class ADC:
    def read_u16(self): return 41500

idle = ROBOT.make_idle_task(_R(), ADC(), ADC())
STDIN_LINES.append("MSG " + decoded + "\n")

buf = io.StringIO()
_stdout = sys.stdout
sys.stdout = buf
idle()
sys.stdout = _stdout
emitted = buf.getvalue().strip().splitlines()

print(f"\ndevice emitted: {emitted}")
print(f"device log    : {ROBOT._messages}")
assert ROBOT._messages == [decoded], ROBOT._messages
assert any(l.startswith("MSGLOG n=1") for l in emitted), emitted
print("\nOK: pass decoded on the PC and the message reached the device log")

# ---- 3. the stdin drain must not block when nothing is waiting -----------
STDIN_LINES.clear()
buf = io.StringIO(); sys.stdout = buf; idle(); sys.stdout = _stdout
print(f"empty-stdin idle emitted: {buf.getvalue().strip()!r}")
assert ROBOT._messages == [decoded], "log changed with no input"
print("OK: idle with no reply waiting is a no-op beyond the heartbeat")

# ---- 4. pass splitting on a multi-pass batch -----------------------------
two = target['pulses'] + [dict(recs[4]['pulses'][0], gap=510000)] + recs[4]['pulses'][1:]
parts = split_passes(two, 1.0)
print(f"\nbatch of 2 passes -> split into {len(parts)} ({[len(x) for x in parts]})")
assert len(parts) == 2, [len(x) for x in parts]
print("OK: rest gap splits batches into individual messages")

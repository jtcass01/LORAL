"""The flush callback must fire in the 500ms rest and NEVER in an inter-bit gap.

Runs the real DoubleDiodeRobot against a simulated beacon that emits a short
burst of pulses separated by inter-bit gaps, then a long rest, repeatedly.
"""
import sys, types, asyncio, threading

# stubbed ticks advance by a controllable virtual clock, so gap sizes are exact
CLK = {'us': 0}
utime = types.ModuleType('utime')
utime.ticks_us = lambda: CLK['us']
utime.ticks_ms = lambda: CLK['us'] // 1000
utime.ticks_diff = lambda a, b: a - b
utime.sleep = lambda s: None
ua = types.ModuleType('uasyncio')
async def _s(x=0): return None
ua.sleep = _s; ua.sleep_ms = _s; ua.run = asyncio.run
ua.create_task = asyncio.ensure_future
ua.CancelledError = asyncio.CancelledError
mach = types.ModuleType('machine'); mach.ADC = object
for n, m in [('utime', utime), ('uasyncio', ua), ('machine', mach)]:
    sys.modules[n] = m
sys.path.insert(0, 'src/robot')
from double_diode_robot import DoubleDiodeRobot

RATE = 6
GAP_US = int(10_000 / RATE)          # 1667us inter-bit gap at rate 6
REST_US = 500_000                    # beacon rest, unscaled
PULSE_US = int(10_000 / RATE)
SAMPLE_US = 25                       # per virtual ADC sample
BURST = 9                            # pulses per "byte"


class SimADC:
    """Square pulses with inter-bit gaps, and a long rest every BURST pulses."""
    def __init__(self, which):
        self.which = which
        self.emitted = 0
        self.phase_us = 0

    def read_u16(self):
        if self.which == 'a':
            CLK['us'] += SAMPLE_US
            self.phase_us += SAMPLE_US
        gap = REST_US if (self.emitted and self.emitted % BURST == 0) else GAP_US
        cycle = gap + PULSE_US
        pos = self.phase_us % cycle
        if self.which == 'a' and self.phase_us >= cycle:
            self.phase_us -= cycle
            self.emitted += 1
        return 40000 + (3000 if pos >= gap else 0)


adc_a, adc_b = SimADC('a'), SimADC('b')
robot = DoubleDiodeRobot(adc_a=adc_a, adc_b=adc_b, edge_threshold=500,
                         settle_discard=False)
robot.prime_filter()

flushes = []          # (idle_us_at_fire,) for each callback invocation
search_starts = []


async def run(n_pulses):
    for _ in range(n_pulses):
        t0 = CLK['us']
        search_starts.append(t0)
        await robot.listen_for_pulse(
            idle_cb=lambda: flushes.append(CLK['us'] - t0),
            idle_us=100_000)


asyncio.run(run(30))

print(f"simulated rate {RATE}x: inter-bit gap {GAP_US}us, rest {REST_US}us")
print(f"idle_us threshold = 100000us\n")
print(f"pulses detected: {len(search_starts)}")
print(f"flush callbacks fired: {len(flushes)}")
if flushes:
    print(f"  idle time when each fired: {sorted(set(flushes))[:6]} ...")

# The callback must never fire during an inter-bit gap.
bad = [f for f in flushes if f < GAP_US * 2]
print(f"\nfired inside an inter-bit gap ({GAP_US}us): {len(bad)}   <- must be 0")
assert not bad, bad

# It must fire during rests. 30 pulses at BURST=9 -> at least 2 rests.
print(f"fired during a rest (>100ms idle): {len(flushes)}   <- expect >= 2")
assert len(flushes) >= 2, f"expected flushes during rests, got {len(flushes)}"

print("\nOK: flush lands only in the rest, never in an inter-bit gap")

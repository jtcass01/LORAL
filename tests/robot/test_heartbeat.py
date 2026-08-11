"""A receiver that hears nothing must keep saying so.

listen_for_pulse never returns while the line is silent, so a single-shot idle
callback left the board permanently quiet after one call - indistinguishable
from unplugged. This checks the callback repeats.
"""
import sys, types, asyncio

CLK = {'us': 0}
utime = types.ModuleType('utime')
utime.ticks_us = lambda: CLK['us']
utime.ticks_ms = lambda: CLK['us'] // 1000
utime.ticks_diff = lambda a, b: a - b
utime.sleep = lambda s: None
ua = types.ModuleType('uasyncio')
ua.sleep = asyncio.sleep          # must genuinely yield
ua.sleep_ms = asyncio.sleep
ua.run = asyncio.run
ua.CancelledError = asyncio.CancelledError
mach = types.ModuleType('machine'); mach.ADC = object
for n, m in [('utime', utime), ('uasyncio', ua), ('machine', mach)]:
    sys.modules[n] = m
sys.path.insert(0, 'src/robot')
from double_diode_robot import DoubleDiodeRobot


class Done(Exception):
    pass


class DeadADC:
    """Beacon off: flat DC, no pulses. Advances the virtual clock, then stops
    the run by raising, since listen_for_pulse would otherwise never return."""
    def __init__(self, budget):
        self.n, self.budget = 0, budget

    def read_u16(self):
        CLK['us'] += 500
        self.n += 1
        if self.n > self.budget:
            raise Done()
        return 40000


r = DoubleDiodeRobot(adc_a=DeadADC(6000), adc_b=DeadADC(10**9),
                     edge_threshold=500, settle_discard=False)
r.prime_filter()

fires = []


async def go():
    try:
        await r.listen_for_pulse(idle_cb=lambda: fires.append(CLK['us']),
                                 idle_us=100_000, idle_repeat_us=500_000)
    except Done:
        pass


asyncio.run(go())

print(f"silent line, {CLK['us']/1e6:.2f}s of virtual time elapsed")
print(f"heartbeat fired {len(fires)} times, at (ms): {[round(f/1000) for f in fires]}")
assert len(fires) >= 4, f"only {len(fires)} beats - the single-shot bug is back"
gaps = [fires[i+1] - fires[i] for i in range(len(fires)-1)]
print(f"interval between beats: {[round(g/1000) for g in gaps]} ms  (expect ~500)")
assert all(400_000 <= g <= 600_000 for g in gaps), gaps
print("\nOK: a receiver hearing nothing keeps reporting that it is alive")

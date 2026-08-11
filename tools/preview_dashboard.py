"""Populate a DashboardState with realistic data and serve it for a look."""
import sys, time, random
sys.path.insert(0, 'src/ml')
from dashboard import DashboardState, start_dashboard

random.seed(7)
state = DashboardState()
state.started = time.time() - 96          # as if ~96s into a session

TRUE = {3.0: 4000, 4.0: 2250, 5.0: 1440, 6.0: 1000, 7.0: 735, 8.0: 562}
for rnd in range(2):
    for d, sw in TRUE.items():
        for _ in range(8):
            swing = int(random.gauss(sw, sw * 0.04))
            base = 42000 + random.randint(-30, 30)
            thr = 1150 + random.randint(-40, 40)
            backoffs = 0 if sw > 800 else random.choice([0, 0, 1, 2])
            dur = random.choice([10100, 25150, 50300])
            state.record_pulse(d, dur, base + swing, base - random.randint(0, 20),
                               base, swing * 2, thr, backoffs)
state.set_distance(8.0)

port = start_dashboard(state, 8742, 2.0)
print(f'serving on http://127.0.0.1:{port}', flush=True)
time.sleep(180)

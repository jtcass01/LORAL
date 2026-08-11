"""Does prime_filter() stop the post-sleep false trigger?

Tests sample_hpf directly. The pulse-detection loop can't be timed faithfully
here (the stubbed ticks_us advances per call, not per ADC sample), but the
false trigger is a property of sample_hpf alone: it reports raw - prev_raw, so
a prev_raw left stale across a sleep fabricates an edge that never happened.
"""
import sys, types, threading

utime = types.ModuleType('utime')
utime.ticks_us = lambda: 0; utime.ticks_diff = lambda a, b: a - b; utime.sleep = lambda s: None
uasyncio = types.ModuleType('uasyncio')
async def _sleep(s): return None
uasyncio.sleep = _sleep; uasyncio.sleep_ms = _sleep; uasyncio.run = lambda c: None
uasyncio.CancelledError = type('CancelledError', (Exception,), {})
machine = types.ModuleType('machine'); machine.ADC = object
network = types.ModuleType('network'); network.WLAN = lambda *a: None; network.STA_IF = 0
socket = types.ModuleType('socket')
_thread = types.ModuleType('_thread'); _thread.allocate_lock = threading.Lock
wifi = types.ModuleType('wifi_config'); wifi.WIFI_SSID = ''; wifi.WIFI_PASSWORD = ''
for n, m in [('utime', utime), ('uasyncio', uasyncio), ('machine', machine), ('network', network),
             ('socket', socket), ('_thread', _thread), ('wifi_config', wifi)]:
    sys.modules[n] = m
sys.path.insert(0, 'src/robot')
from single_diode_robot import SingleDiodeRobot

THRESHOLD = 500


class StepADC:
    """Sits at `level`; set .level to move it."""
    def __init__(self, level): self.level = level
    def read_u16(self): return self.level


def run(prime: bool, level_after_gap: int, label: str):
    adc = StepADC(42000)
    r = SingleDiodeRobot(receiver_adc=adc, edge_threshold=THRESHOLD)
    for _ in range(50):                 # settle on a quiet baseline
        r.sample_hpf()
    settled = r.sample_hpf()[1]

    # --- the logger's 500ms sleep: we stop sampling; the world moves on ---
    adc.level = level_after_gap
    if prime:
        r.prime_filter()

    raw, hpf = r.sample_hpf()           # first sample after waking
    fires = hpf > THRESHOLD
    print(f'  {label:<34} settled_hpf={settled:7.1f}  first_hpf_after_gap={hpf:9.1f}  '
          f'{"TRIGGERS (false pulse)" if fires else "no trigger"}')
    return fires


print('Signal is HIGH when we wake (pulse already in progress, edge missed):')
bad1 = run(False, 43440, 'without prime_filter()')
ok1 = run(True, 43440, 'with prime_filter()')

print('\nSignal unchanged, but DC pedestal drifted during the gap:')
bad2 = run(False, 42800, 'without prime_filter()')
ok2 = run(True, 42800, 'with prime_filter()')

assert bad1 and bad2, 'expected a false trigger without priming'
assert not ok1 and not ok2, 'priming should suppress it'
print('\nOK: prime_filter() suppresses the fabricated edge in both cases.')

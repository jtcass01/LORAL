"""Exercise the new listen_for_pulse on a PC by stubbing MicroPython modules."""
import sys, types, asyncio, threading

clock = {'t': 0}
def ticks_us():
    clock['t'] += 10
    return clock['t']
def ticks_diff(a, b):
    return a - b

utime = types.ModuleType('utime')
utime.ticks_us, utime.ticks_diff, utime.sleep = ticks_us, ticks_diff, lambda s: None
uasyncio = types.ModuleType('uasyncio')
async def _sleep(s):
    return None
uasyncio.sleep = _sleep
uasyncio.sleep_ms = _sleep
uasyncio.run = lambda c: None
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


class FakeADC:
    """DC pedestal + an optical pulse train of the given swing."""
    def __init__(self, dc, swing, idle=400, on=200):
        self.dc, self.swing, self.idle, self.on, self.i = dc, swing, idle, on, -1

    def read_u16(self):
        self.i += 1
        phase = self.i % (self.idle + self.on)
        return int(self.dc + (self.swing if phase >= self.idle else 0))


async def run_one(dc, swing):
    robot = SingleDiodeRobot(receiver_adc=FakeADC(dc, swing), edge_threshold=500)
    await robot.listen_for_pulse()          # first pulse warms up the baseline EMA
    return await robot.listen_for_pulse()


print('Simulated beacon, DC pedestal 42000, swing following inverse-square:')
print(f'{"dist":>5} {"true swing":>11} | {"peak":>7} {"trough":>7} {"baseline":>9} | {"meas swing":>11} {"pk-pk":>7}')
for d, sw in [(3, 4000), (4, 2250), (5, 1440), (6, 1000), (8, 562)]:
    dur, peak, trough, base, phpf = asyncio.run(run_one(42000, sw))
    print(f'{d:5d} {sw:11d} | {peak:7d} {trough:7d} {base:9d} | {peak - base:11d} {peak - trough:7d}')

print('\nWith a pedestal that DRIFTS between sessions (the artifact that faked the 4in class):')
for dc in [42000, 43500, 40500]:
    dur, peak, trough, base, phpf = asyncio.run(run_one(dc, 1440))
    print(f'  pedestal={dc}: raw peak={peak} (tracks the drift) -> swing={peak - base} (immune)')

print('\nInverted polarity (light pulls the reading DOWN):')
dur, peak, trough, base, phpf = asyncio.run(run_one(42000, -1440))
print(f'  peak={peak} trough={trough} baseline={base}')
print(f'  swing_from_baseline={peak - base} (collapses)   swing_peak_to_trough={peak - trough} (still recovers it)')

"""End-to-end test of collect() + dashboard over the TEXT protocol."""
import sys, io, os, builtins, csv, tempfile, urllib.request, re

sys.path.insert(0, 'src/ml')
import collect_training_data as C

TRUE_SWING = {3.0: 4000, 5.0: 1440, 8.0: 562}
# preflight reads before the first prompt, so a distance must be set up front
st = {'d': 3.0, 'n': 0, 'sent_banner': False}


class FakeSerial:
    """Emits the device's text protocol, including IDLE heartbeats, a banner,
    an occasional read timeout, and one device traceback line."""
    def __init__(self, *a, **k):
        self.buf = b''

    def _next_line(self):
        st['n'] += 1
        if not st['sent_banner']:
            st['sent_banner'] = True
            return b"LORAL 3 serial_pulse_logger ready adc=0 thr=2000\r\n"
        if st['n'] % 7 == 0:
            return b"IDLE thr=1650 raw=40800 bo=0 pulses=%d since_ms=180\r\n" % st['n']
        if st['n'] % 23 == 0:                      # device says something unexpected
            return b"AttributeError: no attribute 'prime_filter'\r\n"
        sw = TRUE_SWING[st['d']]
        base = 40800
        return (b"PULSE dur=25150 peak=%d trough=%d base=%d hpf=%d thr=1650 bo=0\r\n"
                % (base + sw, base - 900, base, sw))

    def reset_input_buffer(self):
        self.buf = b''

    def readline(self):
        st['n'] += 0
        if st['n'] % 11 == 0:                      # simulate a serial read timeout
            st['n'] += 1
            return b''
        return self._next_line()

    def __enter__(self): return self
    def __exit__(self, *a): return False


# The prompt no longer names the distance, so follow the known block order.
SEQ = [3.0, 5.0, 8.0] * 2
st['idx'] = -1


def fake_input(prompt=''):
    st['idx'] += 1
    st['d'] = SEQ[st['idx']]
    print(prompt + '<Enter>')
    return ''


C.serial.Serial = FakeSerial
builtins.input = fake_input
C.STALL_WARN_S = 0.0

out = os.path.join(tempfile.mkdtemp(), 'train.csv')
C.collect('FAKE', 115200, [3.0, 5.0, 8.0], samples=4, rounds=2,
          csv_path=out, dashboard_port=8733)

print('\n--- CSV ---')
rows = list(csv.DictReader(open(out)))
print(','.join(rows[0].keys()))
print(','.join(rows[0].values()))
assert len(rows) == 2 * 3 * 4, len(rows)
assert sorted({r['target'] for r in rows}) == ['3.0', '5.0', '8.0']
assert rows[0]['edge_threshold'] == '1650'
means = {}
for r in rows:
    means.setdefault(r['target'], []).append(int(r['swing_from_baseline']))
assert {k: v[0] for k, v in means.items()} == {'3.0': 4000, '5.0': 1440, '8.0': 562}, means
print(f'{len(rows)} rows; swing per distance {[(k, v[0]) for k, v in sorted(means.items())]}')
print('OK: text protocol -> CSV, schema unchanged from the binary era')

html = urllib.request.urlopen('http://127.0.0.1:8733/', timeout=5).read().decode()
# The falloff table dropped the " in" suffix and gained an sd column when the
# dashboard became mode-aware; columns are now label, n, mean, sd, ...
rowspat = re.findall(r'<tr><td>([\d.-]+)</td><td>(\d+)</td><td>([\d.e+-]+)</td>', html)
print('\ndashboard falloff rows (label, n, mean feature):', rowspat)
assert [r[2] for r in rowspat] == ['4000', '1440', '562'], rowspat
print('OK: dashboard still renders correct per-label means')

"""End-to-end collect() in DUAL mode, with dashboard."""
import sys, io, os, builtins, csv, tempfile, math, urllib.request, random

sys.path.insert(0, 'src/ml')
import collect_training_data as C

ANGLES = [-30.0, 0.0, 30.0]
SEQ = ANGLES * 2
st = {'idx': -1, 'ang': ANGLES[0], 'n': 0, 'banner': False}
random.seed(3)


def resp(off):
    return 6000 * math.exp(-(off ** 2) / (2 * 20.0 ** 2))


def pulse_line(ang):
    a = int(resp(ang - 15) * random.uniform(.97, 1.03))
    b = int(resp(ang + 15) * random.uniform(.97, 1.03))
    return (f"PULSE dur=25110 a_peak={40000+a} a_trough=39500 a_base=40000 a_hpf={a} "
            f"b_peak={40000+b} b_trough=39500 b_base=40000 b_hpf={b} "
            f"thr=1200 bo=0\r\n").encode()


class Ser:
    def __init__(self, *a, **k): pass
    def reset_input_buffer(self): pass
    def readline(self):
        st['n'] += 1
        if not st['banner']:
            st['banner'] = True
            return b"LORAL 4 dual_pulse_logger ready adc_a=0 adc_b=1 thr=2000\r\n"
        if st['n'] % 9 == 0:
            return b"IDLE thr=1200 a_raw=40010 b_raw=40004 bo=0 pulses=%d since_ms=200\r\n" % st['n']
        return pulse_line(st['ang'])
    def __enter__(self): return self
    def __exit__(self, *a): return False


def fake_input(prompt=''):
    st['idx'] += 1
    st['ang'] = SEQ[st['idx']]
    return ''


C.serial.Serial = Ser
builtins.input = fake_input

out = os.path.join(tempfile.mkdtemp(), 'bearing.csv')
C.collect('FAKE', 115200, ANGLES, samples=10, rounds=2, csv_path=out,
          dashboard_port=8755, units='deg', noun='the robot')

rows = list(csv.DictReader(open(out)))
print("\n--- CSV ---")
print(','.join(rows[0].keys()))
print(','.join(rows[0].values()))
assert len(rows) == 2 * 3 * 10, len(rows)
assert set(rows[0].keys()) == set(C.DUAL_CSV_FIELDS), "wrong schema selected"
assert {r['target_units'] for r in rows} == {'deg'}

by = {}
for r in rows:
    by.setdefault(float(r['target']), []).append(float(r['ndiff_hpf']))
means = {k: sum(v)/len(v) for k, v in sorted(by.items())}
print(f"\nmean ndiff by angle: { {k: round(v,3) for k,v in means.items()} }")
ordered = [means[a] for a in sorted(means)]
assert all(x < y for x, y in zip(ordered, ordered[1:])), f"not monotonic: {ordered}"
print("OK: dual schema written, units recorded, ndiff monotonic in angle")

html = urllib.request.urlopen('http://127.0.0.1:8755/', timeout=5).read().decode()
for needle in ['Bearing feature vs label', 'ndiff_hpf', 'sep from prev', 'sum_hpf',
               'a_hpf', 'b_hpf']:
    assert needle in html, f"dashboard missing: {needle}"
print("OK: dashboard rendered in dual mode with all bearing columns")

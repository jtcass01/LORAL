"""Stale buffered pulses must never be recorded under the new distance label."""
import sys, io, os, builtins, csv, tempfile
sys.path.insert(0, 'src/ml')
import collect_training_data as C

# hpf identifies which distance a pulse REALLY came from
HPF_AT = {3.0: 4000, 8.0: 560}
# The prompt no longer names the distance (it's in a preceding print), so the
# fake follows the known block order instead of parsing the prompt text.
SEQ = [3.0, 8.0]
st = {'idx': -1, 'real': 3.0, 'buffered': [], 'flushes': 0}


def line(hpf, bo=0, dur=25110):
    return (b"PULSE dur=%d peak=42218 trough=39577 base=39755 hpf=%d thr=1078 bo=%d\r\n"
            % (dur, hpf, bo))


class Ser:
    """Models a real port: bytes accumulate in a buffer while nobody reads."""
    def __init__(self, *a, **k): pass

    def reset_input_buffer(self):
        st['flushes'] += 1
        st['buffered'].clear()

    def readline(self):
        if st['buffered']:
            return st['buffered'].pop(0)
        # nothing stale left -> device emits a fresh pulse at the REAL distance
        return line(HPF_AT[st['real']])

    def __enter__(self): return self
    def __exit__(self, *a): return False


def fake_input(prompt=''):
    """User repositions the beacon. While they do, the port fills with pulses
    from the OLD distance, plus a few mid-move ones with backoffs."""
    st['idx'] += 1
    old, new = st['real'], SEQ[st['idx']]
    # 40 stale pulses buffered at the OLD distance during the reposition
    st['buffered'] = [line(HPF_AT[old]) for _ in range(40)]
    # then a few at the new distance while the threshold is still catching up
    st['buffered'] += [line(HPF_AT[new], bo=2) for _ in range(4)]
    st['real'] = new
    return ''


C.serial.Serial = Ser
builtins.input = fake_input

out = os.path.join(tempfile.mkdtemp(), 't.csv')
C.collect('FAKE', 115200, [3.0, 8.0], samples=10, rounds=1, csv_path=out)

rows = list(csv.DictReader(open(out)))
print(f"\nflushes performed: {st['flushes']}")
bad = []
for r in rows:
    d, hpf = float(r['target']), int(r['peak_hpf'])
    if hpf != HPF_AT[d]:
        bad.append((d, hpf))

by_d = {}
for r in rows:
    by_d.setdefault(r['target'], []).append(int(r['peak_hpf']))
for d, v in sorted(by_d.items()):
    print(f"  {d}in -> peak_hpf values all {set(v)}  (correct value {HPF_AT[float(d)]})")

assert not bad, f"MISLABELLED rows (distance, hpf): {bad}"
assert len(rows) == 20, len(rows)
print("\nOK: no stale pulse was recorded under the wrong distance label")

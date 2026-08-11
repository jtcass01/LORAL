"""--no-prompt must never call input(), and long pulses must be KEPT."""
import sys, io, os, builtins, csv, tempfile
sys.path.insert(0, 'src/ml')
import collect_training_data as C

st = {'n': 0}


class Ser:
    def __init__(self, *a, **k): pass

    def reset_input_buffer(self): pass

    def readline(self):
        st['n'] += 1
        n = st['n']
        if n % 9 == 0:
            return b"IDLE thr=1050 raw=40276 bo=0 pulses=%d since_ms=200\r\n" % n
        if n % 13 == 0:      # noise trigger - must be DROPPED
            return b"PULSE dur=69 peak=38937 trough=38937 base=40866 hpf=1686 thr=990 bo=0\r\n"
        if n % 17 == 0:      # over-long - must be KEPT
            return b"PULSE dur=217868 peak=43114 trough=38857 base=40540 hpf=2100 thr=1050 bo=0\r\n"
        return b"PULSE dur=25110 peak=42218 trough=39577 base=39755 hpf=2000 thr=1078 bo=0\r\n"

    def __enter__(self): return self
    def __exit__(self, *a): return False


C.serial.Serial = Ser
def boom(prompt=''):
    raise AssertionError("--no-prompt still called input()!")
builtins.input = boom

out = os.path.join(tempfile.mkdtemp(), 't.csv')
C.collect('FAKE', 115200, [3.0, 8.0], samples=12, rounds=1,
          csv_path=out, prompt_between_blocks=False)

rows = list(csv.DictReader(open(out)))
durs = [int(r['duration_us']) for r in rows]
hpfs = [int(r['peak_hpf']) for r in rows]
print(f"\n{len(rows)} rows written")
print(f"  any dur=69 noise triggers kept?  {69 in durs}   (must be False)")
print(f"  any dur=217868 long pulses kept? {217868 in durs}  (must be True)")
assert 69 not in durs, "noise trigger leaked into the CSV"
assert 217868 in durs, "long pulse was dropped - that biases the dataset by distance"
assert len(rows) == 24, len(rows)
print("\nOK: --no-prompt ran unattended; degenerate dropped, long-but-usable kept")

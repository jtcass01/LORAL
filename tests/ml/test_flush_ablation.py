"""Same scenario with and without flush+settle, to show the fix is load-bearing."""
import sys, os, csv, tempfile, builtins
sys.path.insert(0, 'src/ml')
import collect_training_data as C

HPF_AT = {3.0: 4000, 8.0: 560}
SEQ = [3.0, 8.0]


def line(hpf, bo=0):
    return (b"PULSE dur=25110 peak=42218 trough=39577 base=39755 hpf=%d thr=1078 bo=%d\r\n"
            % (hpf, bo))


def run(with_fix: bool):
    st = {'idx': -1, 'real': 3.0, 'buffered': []}

    class Ser:
        def __init__(self, *a, **k): pass
        def reset_input_buffer(self): st['buffered'].clear()
        def readline(self):
            return st['buffered'].pop(0) if st['buffered'] else line(HPF_AT[st['real']])
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_input(prompt=''):
        st['idx'] += 1
        old, new = st['real'], SEQ[st['idx']]
        st['buffered'] = [line(HPF_AT[old]) for _ in range(40)]
        st['buffered'] += [line(HPF_AT[new], bo=2) for _ in range(4)]
        st['real'] = new
        return ''

    real_flush, real_settle = C.flush_and_resync, C.settle
    if not with_fix:
        C.flush_and_resync = lambda ser: None
        C.settle = lambda ser, **k: 0
    C.serial.Serial = Ser
    builtins.input = fake_input

    out = os.path.join(tempfile.mkdtemp(), 't.csv')
    import io, contextlib
    with contextlib.redirect_stdout(io.StringIO()):
        C.collect('FAKE', 115200, SEQ, samples=10, rounds=1, csv_path=out)

    C.flush_and_resync, C.settle = real_flush, real_settle

    rows = list(csv.DictReader(open(out)))
    bad = [(float(r['target']), int(r['peak_hpf'])) for r in rows
           if int(r['peak_hpf']) != HPF_AT[float(r['target'])]]
    return rows, bad


rows_no, bad_no = run(with_fix=False)
rows_yes, bad_yes = run(with_fix=True)

print(f"WITHOUT flush+settle: {len(bad_no):>2} of {len(rows_no)} rows mislabelled")
print(f"WITH    flush+settle: {len(bad_yes):>2} of {len(rows_yes)} rows mislabelled")

assert bad_no, "expected mislabelling without the fix - the test is not exercising the bug"
assert not bad_yes, f"fix failed: {bad_yes}"
print("\nThe fix is load-bearing: stale buffered pulses would otherwise be")
print("recorded under the wrong distance label.")

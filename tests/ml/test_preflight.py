import sys, io, builtins, os, tempfile
sys.path.insert(0, 'src/ml')
import collect_training_data as C


class Ser:
    """Replays a fixed set of lines, then returns b'' (read timeout) forever.
    A leading filler line absorbs the resync discard in flush_and_resync."""
    def __init__(self, lines):
        self.lines = [b"(resync filler)\r\n"] + list(lines)
    def reset_input_buffer(self): pass
    def readline(self):
        return self.lines.pop(0) if self.lines else b''
    def __enter__(self): return self
    def __exit__(self, *a): return False


BANNER = b"LORAL 3 serial_pulse_logger ready adc=0 thr=2000\r\n"
IDLE = b"IDLE thr=1750 raw=40800 bo=0 pulses=0 since_ms=2000\r\n"
PULSE = b"PULSE dur=25150 peak=43460 trough=39308 base=40789 hpf=3524 thr=1750 bo=0\r\n"
OLD = b"LORAL 2 serial_pulse_logger ready adc=0 thr=2000\r\n"
CRASH = b"AttributeError: 'SingleDiodeRobot' object has no attribute 'prime_filter'\r\n"

for title, lines, expect in [
    ("Pico silent (the current situation)", [], None),
    ("Alive but detecting nothing", [BANNER] + [IDLE] * 3, "dual"),
    ("Healthy", [BANNER, IDLE] + [PULSE] * 6, "single"),
    ("Stale firmware version", [OLD] + [PULSE] * 3, "single"),
    ("Device crashed", [CRASH], None),
]:
    print("=" * 74)
    print(f"CASE: {title}")
    print("=" * 74)
    ok = C.preflight(Ser(lines), seconds=0.4)
    print(f"--> preflight returned {ok} (expected {expect})")
    assert ok == expect, (title, ok, expect)
    print()

# and confirm collect() aborts before ever prompting when preflight fails
print("=" * 74)
print("CASE: collect() must abort BEFORE the beacon prompt")
print("=" * 74)
C.serial.Serial = lambda *a, **k: Ser([])
def boom(prompt=''):
    raise AssertionError("collect() prompted the user despite a dead device")
builtins.input = boom
out = os.path.join(tempfile.mkdtemp(), 't.csv')
C.collect('FAKE', 115200, [3.0], samples=4, rounds=1, csv_path=out)
print("\n--> collect() returned without prompting. Correct.")

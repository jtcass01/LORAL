"""Replay the user's REAL captured COM7 stream through the diagnostic + parser."""
import sys, io
sys.path.insert(0, 'src/ml')
import collect_training_data as C

# exactly the lines from the --raw capture (mid-run: no banner)
REAL = b"""IDLE thr=1136 raw=41194 bo=0 pulses=294 since_ms=469\r
PULSE dur=25110 peak=42218 trough=39577 base=39755 hpf=1887 thr=1078 bo=0\r
PULSE dur=25102 peak=42314 trough=38921 base=39714 hpf=2027 thr=1059 bo=0\r
PULSE dur=50101 peak=42090 trough=38953 base=39270 hpf=2079 thr=1053 bo=0\r
PULSE dur=69 peak=38937 trough=38937 base=40866 hpf=1686 thr=990 bo=0\r
IDLE thr=990 raw=39273 bo=0 pulses=298 since_ms=170\r
PULSE dur=25142 peak=42250 trough=38889 base=40647 hpf=2022 thr=997 bo=0\r
PULSE dur=25136 peak=42154 trough=39305 base=40630 hpf=2623 thr=1091 bo=0\r
PULSE dur=25155 peak=42170 trough=39449 base=40616 hpf=1853 thr=1042 bo=0\r
IDLE thr=1042 raw=40745 bo=0 pulses=301 since_ms=476\r
PULSE dur=10217 peak=42202 trough=39449 base=40276 hpf=2358 thr=1083 bo=0\r
"""

print("### 1. the --raw analyser on a mid-run capture (no banner) ###")
C._analyse_raw(REAL)

print("\n\n### 2. parse + validity filter ###")


class Ser:
    def __init__(self, d): self.b = io.BytesIO(d)
    def readline(self): return self.b.readline()


ser = Ser(REAL)
kept, skipped = [], []
while True:
    rec = C.read_record(ser)
    if rec is None:
        break
    kind, fields, text = rec
    if kind != "PULSE":
        continue
    v = C.pulse_values(fields, C.detect_mode(fields))
    assert v is not None, text
    why = C.pulse_is_valid(v['dur'], v['peak'], v['trough'])
    (skipped if why else kept).append((v['dur'], v['hpf'], why))

print(f"kept {len(kept)}, rejected {len(skipped)}")
for dur, hpf, why in skipped:
    print(f"  rejected: dur={dur} hpf={hpf}  <- {why}")
print(f"\nkept peak_hpf values: {[h for _, h, _ in kept]}")
mean = sum(h for _, h, _ in kept) / len(kept)
print(f"mean peak_hpf = {mean:.0f}")
assert len(skipped) == 1 and skipped[0][0] == 69, skipped
print("\nOK: the dur=69 glitch is rejected, every real pulse kept")

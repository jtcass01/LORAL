"""Check expected_symbols mirrors the beacon, and that pass splitting works."""
import sys, io, random
sys.path.insert(0, 'src/ml')
import generate_comms_dataset as G

# --- 1. does expected_symbols match Beacon.send_byte exactly? ---
def beacon_reference(message):
    """Transcribed independently from src/beacon/beacon.py send_byte/broadcast_loop."""
    syms = []
    for c in message + "\n":
        syms.append("START")                 # 50ms pulse, then gap
        byte_val = ord(c)
        for i in range(8):                   # LSB first
            bit = (byte_val >> i) & 1
            syms.append("ONE" if bit == 1 else "ZERO")
    return syms

for m in ["A", "Hi", "The Quick Brown Fox 12345", "~U*", ""]:
    assert G.expected_symbols(m) == beacon_reference(m), m
print("OK: expected_symbols matches the beacon's send_byte for every test message")

m = "Hi"
syms = G.expected_symbols(m)
print(f"\n'{m}' -> {len(syms)} symbols ({len(m)+1} bytes x 9)")
print("  first 18:", syms[:18])
# 'H' = 0x48 = 0100 1000 -> LSB first: 0,0,0,1,0,0,1,0
assert syms[1:9] == ["ZERO","ZERO","ZERO","ONE","ZERO","ZERO","ONE","ZERO"], syms[1:9]
print("  'H'=0x48 LSB-first bits verified")

# --- 2. timing estimate sanity ---
print(f"\npass_duration_s('The Quick Brown Fox 12345') = "
      f"{G.pass_duration_s('The Quick Brown Fox 12345'):.1f}s (worst case, all bits 1)")

# --- 3. pass splitting on a simulated stream ---
rng = random.Random(1)
def synth(message, n_passes, drop_rate=0.0):
    """Build a pulse stream the way the beacon would emit it."""
    pulses = []
    for pi in range(n_passes):
        syms = G.expected_symbols(message)
        for si, s in enumerate(syms):
            if rng.random() < drop_rate:
                continue                      # simulate a missed pulse
            dur = {"START": 50_000, "ONE": 25_000, "ZERO": 10_000}[s]
            first = (si == 0)
            gap = (G.REST_MS * 1000 if first and pi > 0 else 10_000)
            pulses.append({"dur": dur, "gap": gap, "a_hpf": 1800, "b_hpf": 1200,
                           "a_pk": 42000, "b_pk": 41800, "thr": 1100, "bo": 0})
    return pulses

msg = "Hello"
exp = len(G.expected_symbols(msg))
for drop in (0.0, 0.02, 0.10):
    stream = synth(msg, 4, drop)
    passes = G.split_into_passes(stream, exp)
    lens = [len(p) for p in passes]
    print(f"\ndrop_rate {drop:.0%}: {len(stream)} pulses -> {len(passes)} usable passes {lens} "
          f"(expected {exp}/pass)")
    for p in passes:
        assert 0.75*exp <= len(p) <= 1.25*exp

# A message change: one pass of the old message, then passes of the new one.
# Only the correctly-sized pass may survive. The rest gap must be inserted at
# the boundary, exactly as the beacon emits it between passes.
old_pass = synth("Hello", 1)
new_pass = synth("A much longer message here", 1)
new_pass[0] = dict(new_pass[0], gap=G.REST_MS * 1000)
mixed = old_pass + new_pass
kept = G.split_into_passes(mixed, exp)
print(f"\nmessage-change stream: {len(kept)} pass kept of 2 "
      f"(the wrong-length one must be dropped)")
assert len(kept) == 1 and len(kept[0]) == exp, [len(p) for p in kept]

# And with no rest gap at all, the two merge into one oversized run that is
# rejected outright - dropping data is the right failure, mislabelling is not.
merged = G.split_into_passes(old_pass + new_pass[1:], exp)
print(f"no rest gap at boundary: {len(merged)} passes kept "
      f"(merged run is too long, correctly discarded)")
assert merged == []
print("OK: pass splitting rejects mixed, partial and merged passes")

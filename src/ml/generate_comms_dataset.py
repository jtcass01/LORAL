"""Drive the beacon over HTTP and capture labelled pulse streams for decoder training.

Runs on a PC. Sets the beacon's broadcast message via its web form, captures the
robot's pulse stream over USB serial (running src/robot/dual_comms_logger.py),
and writes one JSONL record per captured message pass:

    {"message": "...", "expected_symbols": [...], "pulses": [{...}, ...]}

The transmitted message IS the label, so supervision is free and exact - no
hand alignment, and no risk of deriving labels from the same duration feature a
model is then trained on.

Two timing facts drive the design, both read off src/beacon/beacon.py:

  * The beacon re-reads its message at the START of each pass, so a message set
    mid-pass does not take effect until the next one. The pass in flight when
    we change it is therefore a mix and must be discarded.
  * A pass is ~280ms per byte (50ms start pulse + 10ms gap + 8 bits of 20-35ms),
    then a 500ms rest. A 26-byte message takes ~7.8s.

Message boundaries are found in the stream itself rather than by wall clock:
the beacon appends '\\n' to every message, so a decoded 0x0A marks the end of a
pass. Capturing whole passes between those markers keeps records aligned even
when the serial capture starts mid-stream.
"""
import argparse
import json
import random
import string
import sys
import time
import urllib.parse
import urllib.request

import serial

PROTOCOL_VERSION = 5

# Beacon symbol timing, from Beacon.__init__ / send_byte in src/beacon/beacon.py.
START_US = 50_000
ONE_US = 25_000
ZERO_US = 10_000
GAP_US = 10_000
REST_MS = 500


def expected_symbols(message: str) -> list:
    """The exact symbol sequence the beacon will emit for this message.

    Mirrors Beacon.send_byte: a 50ms start pulse per character, then 8 data
    bits LSB-first. The beacon appends '\\n' itself, so it is appended here too.
    """
    syms = []
    for ch in message + "\n":
        syms.append("START")
        v = ord(ch)
        for i in range(8):
            syms.append("ONE" if (v >> i) & 1 else "ZERO")
    return syms


def pass_duration_s(message: str, rate: float = 1.0) -> float:
    """Worst-case seconds for one full transmission pass (all bits = 1).

    Symbol widths scale by 1/rate, but the beacon's inter-pass rest does NOT -
    broadcast_loop's sleep_ms(500) is unconditional. That is convenient: the
    rest stays 500ms while the inter-bit gap shrinks, so the boundary between
    passes gets MORE distinct at high rates, not less.
    """
    per_byte = (START_US + GAP_US + 8 * (ONE_US + GAP_US)) / rate
    return (len(message) + 1) * per_byte / 1e6 + REST_MS / 1000


def set_beacon_rate(base_url: str, rate: float, timeout: float = 8.0) -> str:
    """Set the beacon's speed multiplier; return the rate it reports back."""
    url = f"{base_url.rstrip('/')}/?rate={rate}"
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        html = resp.read().decode("utf-8", errors="replace")
    marker = "Rate: <b>"
    if marker in html:
        return html.split(marker, 1)[1].split("</b>", 1)[0]
    return ""


def set_beacon_message(base_url: str, message: str, timeout: float = 8.0) -> str:
    """Set the broadcast message; return what the beacon reports back.

    The beacon parses `query.split('&')[0][4:]` after 'msg=', so every character
    is percent-encoded with an empty safe set - an unencoded '&' would truncate
    the message and silently mislabel the whole capture.
    """
    url = f"{base_url.rstrip('/')}/?msg={urllib.parse.quote(message, safe='')}"
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        html = resp.read().decode("utf-8", errors="replace")

    marker = "Currently Broadcasting: <b>"
    if marker in html:
        return html.split(marker, 1)[1].split("</b>", 1)[0]
    return ""


def settle_detector(ser: serial.Serial, min_clean: int = 5, timeout_s: float = 20.0) -> int:
    """Wait for the robot's adaptive threshold to re-converge before recording.

    Matters most when attenuation changes: the threshold is still tuned for the
    previous signal strength, so right after tape goes on, weak pulses fail to
    trigger while strong ones still do. Recording through that transient would
    put biased, partly-empty passes at the start of every condition - and since
    attenuation is the independent variable, that bias would land exactly where
    it does the most damage.

    Watches the device's own backoff counter: bo=0 means the pulse was found
    without the detector timing out and clawing its threshold down.
    """
    clean = seen = 0
    deadline = time.time() + timeout_s
    while clean < min_clean and time.time() < deadline:
        rec = read_pulse(ser)
        if rec is None:
            continue
        kind, fields = rec
        if kind != "P":
            continue
        seen += 1
        clean = clean + 1 if fields.get("bo", 0) == 0 else 0
    return seen


def read_pulse(ser: serial.Serial):
    """One line -> ('P', fields) / ('IDLE', fields) / ('other', text) / None."""
    line = ser.readline()
    if not line:
        return None
    text = line.decode("utf-8", errors="replace").strip()
    if not text:
        return None
    kind, _, rest = text.partition(" ")
    fields = {}
    malformed = False
    for tok in rest.split():
        k, sep, v = tok.partition("=")
        if sep:
            try:
                fields[k] = int(v)
            except ValueError:
                fields[k] = v
                malformed = True

    if kind not in ("P", "IDLE", "LORAL", "ERR", "FLUSH"):
        return "other", {"text": text}

    # A pulse whose fields did not all parse is unusable, and keeping it would
    # put a string where every downstream consumer expects an int. Seen once in
    # 91k pulses - a serial line truncated mid-value - so it is rare, but it
    # crashed the analysis rather than degrading it.
    if kind == "P" and malformed:
        return "other", {"text": text}
    return kind, fields


def capture_pulses(ser: serial.Serial, seconds: float) -> tuple:
    """Collect raw pulse records for a wall-clock window. Returns (pulses, dropped).

    The device buffers a whole pass and emits it in one burst during the rest
    gap, so the window must be long enough to include the FLUSH that follows
    the last pass of interest - the pulses do not arrive as they happen.

    `dropped` is the device's own count of pulses it could not buffer. It is
    surfaced rather than ignored so a buffer overrun cannot be mistaken for a
    channel error later.
    """
    out, dropped = [], 0
    deadline = time.time() + seconds
    while time.time() < deadline:
        rec = read_pulse(ser)
        if rec is None:
            continue
        kind, fields = rec
        if kind == "P":
            out.append(fields)
        elif kind == "FLUSH":
            dropped += int(fields.get("dropped", 0) or 0)
    return out, dropped


def split_into_passes(pulses: list, expect_len: int, tolerance: float = 0.5) -> list:
    """Cut the pulse stream into transmission passes.

    Splits on the long inter-pass rest gap rather than trying to decode, so this
    stays independent of the thing we are training a decoder to do. The rest is
    500ms against a 10ms inter-bit gap, so the boundary is unambiguous even with
    pulses missing.

    Passes whose length is far from the expected symbol count are dropped: they
    are partial captures at the ends of the window, or passes that straddled a
    message change and carry a mix of two labels.

    Tolerance is deliberately loose (+/-50%). At high data rates the receiver
    genuinely starts missing pulses, and a tight bound would reject exactly the
    degraded passes the rate sweep exists to measure - throwing away the result
    instead of recording it. The captured/expected ratio is stored per record
    so the analysis can filter afterwards on evidence rather than up front.
    """
    if not pulses:
        return []

    rest_threshold = (REST_MS * 1000) * 0.6
    passes, current = [], []
    for p in pulses:
        gap = p.get("gap", -1)
        if gap is not None and gap > rest_threshold and current:
            passes.append(current)
            current = []
        current.append(p)
    if current:
        passes.append(current)

    lo, hi = expect_len * (1 - tolerance), expect_len * (1 + tolerance)
    return [p for p in passes if lo <= len(p) <= hi]


def random_message(rng: random.Random, min_len: int, max_len: int) -> str:
    """A printable-ASCII payload.

    Deliberately mixes letters, digits, punctuation and spaces so the byte
    values - and therefore the bit patterns - stay varied. Note the ceiling
    this imposes: printable ASCII is 0x20-0x7E, so bit 7 is ALWAYS 0 and the
    decoder never sees a 1 in that position. That matches how the link is
    actually used, but it means the model is only trained over the byte range
    it will meet.
    """
    alphabet = string.ascii_letters + string.digits + " .,:;!?-_+*/=()[]{}#@$%"
    n = rng.randint(min_len, max_len)
    return "".join(rng.choice(alphabet) for _ in range(n))


# Connected English prose, sliced into payload-sized chunks. Natural text is
# what makes a character-level prior worth having: it has the bigram and word
# structure a language model can exploit to reconstruct a dropped symbol.
# Random ASCII has none, which is exactly why the dataset keeps some of both -
# comparing the two is what separates "the prior helped" from "the framing
# constraint helped".
CORPUS = (
    "the quick brown fox jumps over the lazy dog and then runs back home again "
    "we hold these truths to be self evident that all men are created equal "
    "it was the best of times it was the worst of times it was the age of wisdom "
    "call me ishmael some years ago never mind how long precisely having little "
    "in the beginning the universe was created this made a lot of people angry "
    "all happy families are alike each unhappy family is unhappy in its own way "
    "it is a truth universally acknowledged that a single man in possession of "
    "a good fortune must be in want of a wife however little known the feelings "
    "space is big really big you just wont believe how vastly hugely mindbogglingly "
    "big it is i mean you may think its a long way down the road to the chemist "
    "the sky above the port was the color of television tuned to a dead channel "
    "many years later as he faced the firing squad colonel aureliano buendia was "
    "to remember that distant afternoon when his father took him to discover ice "
)


def text_messages(rng: random.Random, count: int, min_len: int, max_len: int) -> list:
    """Slice the corpus into payloads, cutting on word boundaries where possible."""
    out, pos = [], 0
    while len(out) < count:
        n = rng.randint(min_len, max_len)
        if pos + n >= len(CORPUS):
            pos = 0
        chunk = CORPUS[pos:pos + n]
        cut = chunk.rfind(" ")
        if cut > min_len:                    # prefer a word boundary
            chunk = chunk[:cut]
        chunk = chunk.strip()
        if len(chunk) >= min_len:
            out.append(chunk)
        pos += len(chunk) + 1
    return out


def structured_messages() -> list:
    """Edge-case payloads worth guaranteeing in the dataset.

    Random text under-samples the extremes: runs of identical bits, and bytes
    whose set bits sit at the ends of the LSB-first sequence. Those are exactly
    where a decoder's framing is most likely to slip.
    """
    return [
        "AAAAAAAAAAAAAAAA",      # 0x41: repeating, sparse bits
        "~~~~~~~~~~~~~~~~",      # 0x7E: nearly all ones
        " " * 16,                # 0x20: single set bit, high position
        "!" * 16,                # 0x21: bits at both ends
        "UUUUUUUUUUUUUUUU",      # 0x55: alternating 10101010
        "*" * 16,                # 0x2A: alternating, offset
        "The Quick Brown Fox 12345",
        "0123456789",
    ]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", required=True, help="Robot serial port, e.g. COM7")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--beacon", default="http://192.168.0.131",
                    help="Beacon base URL (default: http://192.168.0.131)")
    ap.add_argument("--messages", type=int, default=50,
                    help="How many distinct messages to cycle through (default: 50)")
    ap.add_argument("--passes", type=int, default=2,
                    help="Transmission passes to keep per message (default: 2). More than "
                         "one is useful: same bits, different noise.")
    ap.add_argument("--min-len", type=int, default=8)
    ap.add_argument("--max-len", type=int, default=28,
                    help="Beacon form caps the message at 32 chars.")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--text-fraction", type=float, default=0.7,
                    help="Share of messages drawn from natural English rather than random "
                         "ASCII (default 0.7). Keeping BOTH is deliberate: comparing them is "
                         "what separates a language prior's contribution from the framing "
                         "constraint's.")
    ap.add_argument("--rate", type=float, default=None,
                    help="Beacon speed multiplier to set for this run (1.0 = the original "
                         "10/25/50ms symbols, 2.0 = twice as fast). Recorded with every pass.")
    ap.add_argument("--condition", default="baseline",
                    help="Label recorded with every pass, identifying the link condition "
                         "this run was collected under (default: baseline).")
    ap.add_argument("--output", default="comms_dataset.jsonl")
    ap.add_argument("--restore", default=None,
                    help="Message to set on the beacon when finished, e.g. the one it "
                         "was broadcasting before this run.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Show the message plan and timing estimate, touch nothing.")
    args = ap.parse_args()

    if args.max_len > 32:
        ap.error("--max-len cannot exceed 32; the beacon form caps it there")

    rng = random.Random(args.seed)
    plan = structured_messages()[:args.messages]
    remaining = args.messages - len(plan)
    n_text = int(remaining * args.text_fraction)
    text_pool = set(text_messages(rng, n_text, args.min_len, args.max_len))
    plan += sorted(text_pool)
    while len(plan) < args.messages:
        plan.append(random_message(rng, args.min_len, args.max_len))
    # Interleave so text and random are spread evenly through the run - if the
    # link drifts partway through, a block layout would confound payload type
    # with time.
    rng.shuffle(plan)

    rate = args.rate if args.rate else 1.0
    est = sum((pass_duration_s(m, rate) * (args.passes + 1) + 2.0) for m in plan)
    print(f"{len(plan)} messages x {args.passes} kept passes")
    print(f"estimated wall-clock: {est/60:.1f} min (one extra pass per message is "
          f"discarded across the change)")
    print(f"supervision: ~{sum(len(m)+1 for m in plan)*8*args.passes:,} labelled bits\n")

    if args.dry_run:
        for m in plan[:12]:
            print(f"  {pass_duration_s(m):5.1f}s  {m!r}")
        if len(plan) > 12:
            print(f"  ... and {len(plan)-12} more")
        return

    if args.rate is not None:
        echoed = set_beacon_rate(args.beacon, args.rate)
        print(f"beacon rate set to {echoed}x (requested {args.rate})", flush=True)
        if echoed and abs(float(echoed) - args.rate) > 1e-6:
            print(f"[!] beacon clamped the rate to {echoed} - continuing at that value",
                  flush=True)
            rate = float(echoed)
        # The rate only takes effect at the next pass boundary, so let the
        # in-flight pass finish at the OLD timing before anything is recorded.
        time.sleep(pass_duration_s("x" * args.max_len, 1.0) + 1.0)

    kept = 0
    with serial.Serial(args.port, args.baud, timeout=1) as ser, \
            open(args.output, "a", encoding="utf-8") as out:
        ser.reset_input_buffer()
        ser.readline()

        warm = settle_detector(ser)
        print(f"detector settled after {warm} pulses\n", flush=True)
        if warm == 0:
            print("[!] no pulses at all during warm-up - is the beacon transmitting?",
                  flush=True)

        for i, msg in enumerate(plan, 1):
            echoed = set_beacon_message(args.beacon, msg)
            if echoed != msg:
                print(f"[{i}/{len(plan)}] beacon echoed {echoed!r}, expected {msg!r} - skipping")
                continue

            # Discard the pass that was in flight when the message changed: the
            # beacon only picks up a new message at a pass boundary, so that
            # one is a mix of the old label and the new.
            ser.reset_input_buffer()
            ser.readline()
            window = pass_duration_s(msg, rate) * (args.passes + 1)
            pulses, dev_dropped = capture_pulses(ser, window)

            syms = expected_symbols(msg)
            passes = split_into_passes(pulses, len(syms))
            usable = passes[1:1 + args.passes] if len(passes) > 1 else []

            for p in usable:
                out.write(json.dumps({
                    "message": msg,
                    "expected_symbols": syms,
                    "n_expected": len(syms),
                    "condition": args.condition,
                    "rate": rate,
                    "captured_ratio": round(len(p) / len(syms), 4),
                    "is_text": msg in text_pool,
                    "pulses": p,
                }) + "\n")
                out.flush()
            kept += len(usable)

            # flush=True because a run is minutes long and stdout is block
            # buffered whenever it is not a terminal - without this the
            # progress log stays invisible until the process exits, which is
            # exactly when it stops being useful.
            drop_note = f" [device dropped {dev_dropped}]" if dev_dropped else ""
            print(f"[{i}/{len(plan)}] {msg[:24]!r:28} {len(pulses):4d} pulses -> "
                  f"{len(passes)} passes, kept {len(usable)} "
                  f"(expected {len(syms)} symbols/pass){drop_note}", flush=True)

    print(f"\nwrote {kept} labelled passes -> {args.output}")

    if args.restore:
        echoed = set_beacon_message(args.beacon, args.restore)
        print(f"beacon restored to {echoed!r}")


if __name__ == "__main__":
    main()

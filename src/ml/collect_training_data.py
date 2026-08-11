"""Collect labeled ADC training data over USB serial for distance-regression training.

Runs on a PC. Connects to the Pico's USB serial port (running
src/robot/serial_pulse_logger.py), reads the line-based text records it prints,
tags each pulse with the target distance, and appends rows to a CSV. IDLE
heartbeats, the boot banner, device errors and anything unrecognised are echoed
to the terminal rather than dropped, so a crashed or stale device explains
itself instead of just going quiet.

Two things about this pipeline are easy to get wrong, and both silently
produce a dataset no model can learn from:

1. The feature is the SWING, not the peak. The raw ADC peak is dominated by
   the photodiode's DC bias level (~42000 counts on this rig) and moves less
   than 1% between 3in and 8in. peak - baseline is the part that actually
   follows the 1/d^2 falloff. Both are logged so a drifting baseline stays
   visible rather than being folded invisibly into the feature.

2. Distances must be INTERLEAVED, not collected one session per distance.
   With one session per distance, any per-session drift (ambient light, gain,
   temperature) correlates perfectly with the label, and a model will happily
   learn the drift instead of the distance. This script cycles through all
   distances each round and records round_index so that artifact stays
   detectable after the fact.
"""
import argparse
import csv
import os
import sys
import time

import serial

# Two device protocols are supported. Which one is in use is detected from the
# fields a PULSE record actually carries, not from the banner version, so a
# capture that starts mid-run still resolves correctly.
SINGLE_CSV_FIELDS = [
    "timestamp",
    "round_index",
    "duration_us",
    "peak_amplitude",
    "trough_amplitude",
    "baseline_amplitude",
    "peak_hpf",
    "edge_threshold",
    "backoffs",
    "swing_from_baseline",
    "swing_peak_to_trough",
    "target",
    "target_units",
]

# ndiff_hpf = (a - b) / (a + b) is the bearing feature: monotonic in angle near
# boresight, and it divides out everything common to both channels - distance,
# transmit power, amplifier gain, ambient DC. sum_hpf is kept alongside it
# because it is the saturation witness: if sum stops responding while the robot
# is re-aimed, the front end is clipping and ndiff is no longer trustworthy.
DUAL_CSV_FIELDS = [
    "timestamp",
    "round_index",
    "duration_us",
    "a_peak", "a_trough", "a_base", "a_hpf",
    "b_peak", "b_trough", "b_base", "b_hpf",
    "sum_hpf", "diff_hpf", "ndiff_hpf", "ratio_hpf",
    "a_swing", "b_swing",
    "edge_threshold",
    "backoffs",
    "target",
    "target_units",
]

SINGLE_PULSE_FIELDS = ("dur", "peak", "trough", "base", "hpf", "thr", "bo")
DUAL_PULSE_FIELDS = ("dur", "a_peak", "a_trough", "a_base", "a_hpf",
                     "b_peak", "b_trough", "b_base", "b_hpf", "thr", "bo")


def detect_mode(fields: dict) -> str:
    """'dual' or 'single', from what the record carries rather than a version."""
    return "dual" if "a_hpf" in fields else "single"


def csv_fields_for(mode: str) -> list:
    return DUAL_CSV_FIELDS if mode == "dual" else SINGLE_CSV_FIELDS

# Must match PROTOCOL_VERSION in src/robot/serial_pulse_logger.py. Reported in
# the device's LORAL banner; a mismatch is warned about, not fatal, because
# key=value parsing tolerates fields being added or missing.
PROTOCOL_VERSION = 3

# Legacy binary sync byte. The wire format is line-based text now, but the raw
# diagnostic still recognises the old framing so a device running stale
# firmware gets named rather than dismissed as noise.
LEGACY_SYNC_BYTE = 0xAA
LEGACY_PACKET_SIZES = {
    7: "the ORIGINAL binary firmware (duration + peak only)",
    15: "the SWING-ERA binary firmware (no detector state)",
    18: "the FINAL binary firmware (pre-text-protocol)",
}

# Seconds of silence before the collector starts saying it's heard nothing.
# The device emits IDLE every 2s, so real silence is now genuinely anomalous.
STALL_WARN_S = 3.0
# Ceiling on throughput, set by INTER_SAMPLE_MS in src/robot/serial_pulse_logger.py
# (500ms). Reported alongside the measured rate so "slow" is distinguishable
# from "dropping pulses". Keep in sync if you change the device-side sleep.
EXPECTED_RATE_HZ = 2.0


# The beacon's symbols are ~10ms, ~25ms and ~50ms (start pulse 40-60ms).
#
# Only genuinely degenerate pulses are dropped. A too-short trigger fires on
# noise and exits before the ADC moves (peak == trough), so every field
# including peak_hpf is meaningless.
#
# An over-long pulse is NOT dropped, deliberately. It means the falling edge
# was missed, which inflates peak/trough/swing - but peak_hpf is measured at
# the RISING edge and is unaffected by how long the window then runs. Since
# peak_hpf is the feature we train on, discarding these would throw away good
# data. Worse, it would bias the dataset by distance: a weaker signal at 8in
# is likelier to miss its falling edge than a strong one at 3in, so a duration
# cutoff would preferentially delete far samples and flatten the very falloff
# we are trying to measure. They are counted and reported instead.
MIN_PULSE_US = 5_000
LONG_PULSE_US = 70_000


def pulse_is_valid(duration_us: int, peak: int, trough: int) -> str | None:
    """Return None if the pulse is usable, else why it must be dropped."""
    if duration_us < MIN_PULSE_US:
        return f"{duration_us}us too short (noise trigger)"
    if peak <= trough:
        return "no ADC excursion (peak <= trough)"
    return None


def pulse_is_suspect(duration_us: int) -> bool:
    """Usable for peak_hpf, but its swing/pk-pk fields are inflated."""
    return duration_us > LONG_PULSE_US


def read_record(ser: serial.Serial) -> tuple[str, dict, str] | None:
    """Read one line and split it into (kind, fields, raw_text).

    Returns None on a read timeout. Lines are `KIND key=value ...`; unparseable
    ones come back with kind "?" and are surfaced verbatim rather than dropped,
    which is how a MicroPython traceback from the device reaches your terminal
    instead of vanishing into a parser that only understood one format.
    """
    line = ser.readline()
    if not line:
        return None

    text = line.decode("utf-8", errors="replace").strip()
    if not text:
        return None

    kind, _, rest = text.partition(" ")
    fields = {}
    for token in rest.split():
        key, sep, value = token.partition("=")
        if sep:
            fields[key] = value

    if kind not in ("PULSE", "IDLE", "LORAL", "ERR"):
        return "?", {}, text
    return kind, fields, text


def pulse_values(fields: dict, mode: str = "single") -> dict | None:
    """Coerce a PULSE record to ints, or None if any field is missing/bad.

    Returns a dict rather than a positional tuple: the dual record carries
    eleven numbers and a positional unpack of that length is a bug waiting to
    happen the next time a field is added.
    """
    names = DUAL_PULSE_FIELDS if mode == "dual" else SINGLE_PULSE_FIELDS
    try:
        return {n: int(fields[n]) for n in names}
    except (KeyError, ValueError):
        return None


def derive_features(p: dict, mode: str) -> dict:
    """Compute the trained-on features from one pulse record."""
    if mode == "single":
        return {
            "swing_from_baseline": p["peak"] - p["base"],
            "swing_peak_to_trough": p["peak"] - p["trough"],
        }

    a, b = p["a_hpf"], p["b_hpf"]
    total = a + b
    return {
        # Echoed so the returned dict is self-contained for display; they are
        # identical to the raw fields already headed for the CSV.
        "a_hpf": a,
        "b_hpf": b,
        "sum_hpf": total,
        "diff_hpf": a - b,
        # Guard the degenerate case: if both channels read zero there is no
        # bearing to report, and 0/0 would otherwise kill the whole run.
        "ndiff_hpf": round((a - b) / total, 6) if total else 0.0,
        "ratio_hpf": round(a / b, 6) if b else 0.0,
        "a_swing": p["a_peak"] - p["a_base"],
        "b_swing": p["b_peak"] - p["b_base"],
    }


def primary_feature(p: dict, derived: dict, mode: str) -> float:
    """The value the live summaries judge separability by."""
    return derived["ndiff_hpf"] if mode == "dual" else p["hpf"]


def raw_dump(port: str, baud: int, seconds: float) -> None:
    """Print exactly what arrives on the serial port, parsing nothing.

    When the collector reports no packets, the question is which half of the
    pipeline is at fault, and the parser can't answer it - a device that never
    transmits and a device transmitting something unparseable both look like
    silence from inside the line reader. This shows the bytes themselves.
    """
    print(f"Listening on {port} at {baud} baud for {seconds:.0f}s.")
    print("Parsing nothing - this is exactly what the Pico is putting on the wire.\n")

    chunks = []
    deadline = time.time() + seconds
    try:
        with serial.Serial(port, baud, timeout=0.5) as ser:
            while time.time() < deadline:
                data = ser.read(4096)
                if data:
                    chunks.append(data)
                    print(f"  +{len(data)} bytes (total {sum(len(c) for c in chunks)})")
    except serial.SerialException as e:
        print(f"\nCould not open {port}: {e}")
        print("\nUsually means the port name is wrong, or another program is holding it open -")
        print("Thonny, mpremote, or a serial monitor will each claim the port exclusively.")
        return
    except KeyboardInterrupt:
        print("\n  stopped early.")

    _analyse_raw(b"".join(chunks))


def _no_data_guidance() -> None:
    """Why a Pico goes quiet, in rough order of likelihood."""
    print("  1. The logger isn't RUNNING on the Pico. Thonny's %Run (and the green Run")
    print("     button) executes the script only for as long as that Thonny session lives -")
    print("     it does not persist. The moment Thonny disconnects, the Pico drops back to")
    print("     the REPL and stops transmitting. To run it standalone: save the file to the")
    print("     DEVICE as main.py, close Thonny, then reset the Pico.")
    print("  2. Thonny is still attached and holding the port, so packets go to ITS shell")
    print("     rather than here. Only one program can own a COM port at a time - though in")
    print("     that case opening the port here would normally have failed outright.")
    print("  3. The device is running its old main.py (the Wi-Fi robot) instead.")
    print("  4. Wrong COM port, or the Pico needs a reset after reflashing.")
    print("\n  To see the bytes for yourself:")
    print("    python src/ml/collect_training_data.py --port COM7 --raw")


def flush_and_resync(ser: serial.Serial) -> None:
    """Drop everything buffered, then re-align to a line boundary.

    The OS keeps buffering while we sit at a prompt, so without this the first
    pulses of a block are stale ones captured at the PREVIOUS distance (or
    while the beacon was physically being moved) - and they would be written to
    the CSV labelled with the new distance. Silent mislabelling, the worst kind.

    The single discarded readline is deliberate: reset_input_buffer cuts the
    stream at an arbitrary byte, so the next bytes to arrive are the tail of a
    line already half-sent. That fragment parses as garbage.
    """
    ser.reset_input_buffer()
    ser.readline()


def settle(ser: serial.Serial, max_discard: int = 12, min_clean: int = 3,
           timeout_s: float = 15.0) -> int:
    """Discard pulses until the detector has re-adapted to the new distance.

    Flushing alone isn't enough. The edge threshold is still tuned for where
    the beacon just was, and moving FARTHER leaves it too high: weak pulses
    fail to trigger while strong ones still do, so the pulses that survive are
    biased high. That inflates peak_hpf at exactly the far distances whose
    falloff we are trying to measure - it would flatten the 1/d^2 curve and
    make the data look untrainable for a purely procedural reason.

    Since blocks run 3,4,5,6,7,8 inches, every transition but the wrap moves
    farther, so this is the common case rather than an edge case.

    Rather than guessing a fixed warmup, this watches the device's own backoff
    counter: bo=0 means that pulse was found without the detector timing out
    and clawing its threshold down. A few consecutive clean pulses means it has
    caught up. A strong signal settles in the minimum 3 and costs ~1.5s.
    """
    clean = discarded = 0
    deadline = time.time() + timeout_s

    while discarded < max_discard and clean < min_clean and time.time() < deadline:
        record = read_record(ser)
        if record is None:
            continue
        kind, fields, _ = record
        if kind != "PULSE":
            continue
        # Read bo directly: this runs before the mode is necessarily known, and
        # bo is present in both protocols.
        try:
            backoffs = int(fields["bo"])
        except (KeyError, ValueError):
            continue
        discarded += 1
        clean = clean + 1 if backoffs == 0 else 0

    return discarded


def preflight(ser: serial.Serial, seconds: float = 4.0) -> str | None:
    """Confirm the device is talking, and report which mode it speaks.

    Returns 'dual', 'single', or None if the device is unusable.
    """
    # Without this the first thing collection does is block on input(), so a
    # dead device isn't discovered until after you've positioned the beacon and
    # hit Enter - and then it looks like a collection failure rather than a
    # device that was never running. The banner and IDLE heartbeat make this
    # cheap: a healthy device says something within two seconds, unprompted.
    #
    # The device may also have been running for hours; judge it on what it is
    # saying now, not on a backlog the driver accumulated before we attached.
    flush_and_resync(ser)

    print(f"Listening {seconds:.0f}s to check the device is alive...")
    kinds: dict[str, int] = {}
    banner = None
    mode = None
    deadline = time.time() + seconds

    while time.time() < deadline:
        record = read_record(ser)
        if record is None:
            continue
        kind, fields, text = record
        kinds[kind] = kinds.get(kind, 0) + 1
        if kind == "LORAL":
            banner = text
        elif kind == "PULSE" and mode is None:
            mode = detect_mode(fields)
        elif kind in ("ERR", "?"):
            print(f"  [device] {text}")

    if not kinds:
        print("\nNo data at all - the port is open but the device is silent.\n")
        _no_data_guidance()
        return None

    if banner:
        print(f"  [device] {banner}")
        version = banner.split()[1] if len(banner.split()) > 1 else "?"
        if version != str(PROTOCOL_VERSION):
            print(f"  [!] device speaks protocol {version}, collector expects {PROTOCOL_VERSION}.")

    if kinds.get("PULSE"):
        label = "TWO-diode (bearing)" if mode == "dual" else "single-diode"
        print(f"  device is alive and detecting pulses ({kinds['PULSE']} in {seconds:.0f}s).")
        print(f"  mode: {label}. Ready.\n")
        return mode

    if kinds.get("IDLE"):
        print(f"\n  Device is ALIVE ({kinds['IDLE']} heartbeats) but detected NO pulses in "
              f"{seconds:.0f}s.")
        print("  It's sampling and hearing nothing. Check the beacon is powered and aimed,")
        print("  and watch the raw values in the idle lines: if they don't move when the")
        print("  beacon fires, that's a wiring or signal problem rather than a threshold one.")
        print("  Cannot tell single from dual without a PULSE, so assuming dual.")
        print("  Continuing anyway - collection will wait for pulses.\n")
        return "dual"

    print(f"\n  Received {kinds} but no recognised records. Try --raw to see the bytes.\n")
    return None


def _hexdump(blob: bytes, limit: int = 256) -> str:
    lines = []
    for off in range(0, min(len(blob), limit), 16):
        row = blob[off:off + 16]
        hexpart = " ".join(f"{b:02x}" for b in row).ljust(47)
        asciipart = "".join(chr(b) if 32 <= b < 127 else "." for b in row)
        lines.append(f"    {off:04x}  {hexpart}  |{asciipart}|")
    if len(blob) > limit:
        lines.append(f"    ... {len(blob) - limit} more bytes")
    return "\n".join(lines)


def _analyse_raw(blob: bytes) -> None:
    print(f"\n--- {len(blob)} bytes received ---")

    if not blob:
        print("\nNOTHING arrived. The PC opened the port fine, so the device isn't transmitting.\n")
        _no_data_guidance()
        return

    print(_hexdump(blob))

    printable = sum(1 for b in blob if b in (9, 10, 13) or 32 <= b < 127)
    text_ratio = printable / len(blob)

    if text_ratio > 0.85:
        text = blob.decode("utf-8", errors="replace")
        kinds = {}
        for line in text.splitlines():
            head = line.strip().split(" ")[0] if line.strip() else ""
            if head:
                kinds[head] = kinds.get(head, 0) + 1

        # Identify the protocol by the records themselves, NOT by the banner.
        # The banner prints once at boot, so any capture of an already-running
        # device has none - and treating that as "not our protocol" reports a
        # perfectly healthy device as broken.
        ours = sum(kinds.get(k, 0) for k in ("PULSE", "IDLE", "LORAL", "ERR"))
        if ours >= 2:
            banner = next((l for l in text.splitlines() if l.startswith("LORAL ")), None)
            print("\nThis is the text protocol - the device is running the right firmware.")
            if banner:
                version = banner.split()[1] if len(banner.split()) > 1 else "?"
                print(f"  banner: {banner.strip()}")
                if version != str(PROTOCOL_VERSION):
                    print(f"  [!] device speaks protocol {version}, collector expects {PROTOCOL_VERSION}.")
            else:
                print("  (no banner in this capture - expected, it only prints at boot and the")
                print("   device was already running. Reset the Pico to see it.)")
            print(f"  line kinds seen: {kinds}")
            if kinds.get("PULSE"):
                print(f"\nPULSE lines are arriving ({kinds['PULSE']} of them) - the device is")
                print("working and ready to collect. Just run the normal collection command.")
            elif kinds.get("IDLE"):
                print("\nIDLE heartbeats but no PULSE lines: the device is alive and sampling,")
                print("but never crosses its edge threshold. Check the 'raw' and 'thr' values")
                print("above - if raw barely moves when the beacon fires, it's a signal or")
                print("wiring problem; if raw swings but thr sits above it, the threshold is")
                print("adapted too high and the beacon is too far or misaligned.")
            return

        if "Traceback" in text or "Error" in text:
            print("\nThat's a MicroPython traceback - the logger script crashed. Full text:\n")
            print("    " + text.replace("\n", "\n    "))
            print("If it names a missing attribute (detector_state, prime_filter), then")
            print("single_diode_robot.py on the device is the OLD copy. Both files must be")
            print("reflashed together - the logger calls methods only the new one defines.")
        else:
            print("\nText, but not our protocol and no traceback - almost certainly the old")
            print("main.py robot code auto-running at boot instead of serial_pulse_logger.py.")
            print("Text seen:\n")
            print("    " + text[:800].replace("\n", "\n    "))
        return

    # Binary means stale firmware: the current protocol is text. Name which one.
    syncs = [i for i, b in enumerate(blob) if b == LEGACY_SYNC_BYTE]
    print(f"\nThis is BINARY, but the current protocol is text - so the Pico is running old")
    print(f"firmware. Found {len(syncs)} occurrences of the legacy 0xAA sync byte.")
    if len(syncs) < 2:
        print("Too few to identify which version.")
        return

    gaps = {}
    for a, b in zip(syncs, syncs[1:]):
        gaps[b - a] = gaps.get(b - a, 0) + 1
    stride, count = max(gaps.items(), key=lambda kv: kv[1])
    print(f"Most common gap between sync bytes: {stride} bytes ({count} times).")
    if stride in LEGACY_PACKET_SIZES:
        print(f"That is {LEGACY_PACKET_SIZES[stride]}.")
    print("\nReflash BOTH src/robot/serial_pulse_logger.py and src/robot/single_diode_robot.py,")
    print("then reset the Pico. The new logger announces itself with a LORAL banner line.")


def check_csv_schema(csv_path: str, fields: list) -> bool:
    """Return True if the file needs a header written.

    Refuses to append to a CSV written under a different schema - including a
    single-diode file when the device is now dual. Appending would interleave
    rows with incompatible meanings under one header.
    """
    if not os.path.exists(csv_path) or os.path.getsize(csv_path) == 0:
        return True

    with open(csv_path, newline="") as f:
        existing = next(csv.reader(f), [])

    CSV_FIELDS = fields
    if existing != CSV_FIELDS:
        sys.exit(
            f"ERROR: {csv_path} has an incompatible header.\n"
            f"  found:    {existing}\n"
            f"  expected: {CSV_FIELDS}\n\n"
            "That file was written before the amplitude fix (it logs the raw ADC peak,\n"
            "which is the DC bias level, not the pulse swing). Its rows can't be mixed\n"
            "with the new ones. Archive it and collect fresh:\n"
            f"  mv {csv_path} {csv_path}.old\n"
        )
    return False


def collect_block(
    ser: serial.Serial,
    distance_in: float,
    samples: int,
    round_index: int,
    writer: csv.DictWriter,
    f,
    state=None,
    mode: str = "single",
    units: str = "in",
) -> list[float]:
    """Collect `samples` pulses at one label value. Returns the primary feature.

    Dual mode judges by ndiff_hpf, the normalised channel difference, because
    it divides out distance, transmit power and gain drift. Single mode judges
    by peak_hpf: measured on hardware at a fixed distance its within-distance
    cv was 5.2% against swing's 21.5%, since the idle baseline wanders with
    where in the beacon's burst the search phase sat. Every field still reaches
    the CSV either way; this only sets what the live summaries report.

    Prints a line per pulse. At the device's INTER_SAMPLE_MS of 500 this is
    about two lines a second - slow enough to read, and the point is that
    silence is then unambiguous: no line means no pulse arrived, not that the
    script is buffering.
    """
    swings: list[float] = []
    rejected: list[str] = []
    suspect: list[int] = []
    print(f"  collecting {samples} samples at {distance_in}{units} "
          f"(Ctrl+C to cut this block short)")
    if mode == "dual":
        print(f"    {'#':>7} {'ndiff':>8} {'a_hpf':>7} {'b_hpf':>7} {'sum':>7} "
              f"{'a_peak':>7} {'b_peak':>7} {'thresh':>7} {'b/o':>4} {'dur':>9}")
    else:
        print(f"    {'#':>7} {'swing':>7} {'pk-pk':>7} {'peak':>7} {'base':>7} "
              f"{'thresh':>7} {'b/o':>4} {'dur':>9}")

    block_start = time.time()
    last_packet = block_start
    next_warn_at = STALL_WARN_S
    try:
        while len(swings) < samples:
            record = read_record(ser)
            if record is None:
                # Serial read timed out. The device emits IDLE every 2s, so
                # this now means genuine silence, not merely "no pulse".
                idle = time.time() - last_packet
                if idle >= next_warn_at:
                    print(f"    ...silence for {idle:.0f}s - not even an IDLE heartbeat. "
                          f"Is serial_pulse_logger.py running on the Pico?")
                    next_warn_at = idle + STALL_WARN_S
                continue

            kind, fields, text = record
            last_packet = time.time()
            next_warn_at = STALL_WARN_S

            if kind != "PULSE":
                # IDLE heartbeats, the boot banner, errors and anything
                # unrecognised (a device traceback) all get shown as-is.
                _report_non_pulse(kind, fields, text)
                continue

            p = pulse_values(fields, mode)
            if p is None:
                print(f"    [!] malformed PULSE, skipped: {text}")
                continue

            if mode == "dual":
                # Validity is judged on the summed channel: a pulse arriving
                # far off-axis is legitimately weak on one diode, and testing
                # them separately would discard exactly the large-bearing
                # samples that carry the most angular information.
                peak_for_check = max(p["a_peak"], p["b_peak"])
                trough_for_check = min(p["a_trough"], p["b_trough"])
            else:
                peak_for_check, trough_for_check = p["peak"], p["trough"]

            reason = pulse_is_valid(p["dur"], peak_for_check, trough_for_check)
            if reason is not None:
                rejected.append(reason)
                print(f"    [skip] {reason}")
                continue
            if pulse_is_suspect(p["dur"]):
                suspect.append(p["dur"])

            derived = derive_features(p, mode)
            row = {"timestamp": f"{time.time():.3f}", "round_index": round_index,
                   "duration_us": p["dur"], "edge_threshold": p["thr"],
                   "backoffs": p["bo"], "target": distance_in, "target_units": units}
            if mode == "dual":
                row.update({k: p[k] for k in DUAL_PULSE_FIELDS
                            if k not in ("dur", "thr", "bo")})
            else:
                row.update({
                    "peak_amplitude": p["peak"], "trough_amplitude": p["trough"],
                    "baseline_amplitude": p["base"], "peak_hpf": p["hpf"],
                })
            row.update(derived)

            writer.writerow(row)
            f.flush()

            feature = primary_feature(p, derived, mode)
            swings.append(feature)
            if state is not None:
                state.record_pulse(distance_in, p["dur"], feature, derived, mode,
                                   p["thr"], p["bo"])

            if mode == "dual":
                print(f"    {len(swings):>3}/{samples:<3} {derived['ndiff_hpf']:>8.4f} "
                      f"{p['a_hpf']:>7} {p['b_hpf']:>7} {derived['sum_hpf']:>7} "
                      f"{p['a_peak']:>7} {p['b_peak']:>7} {p['thr']:>7} {p['bo']:>4} "
                      f"{p['dur'] / 1000:>7.1f}ms")
            else:
                print(f"    {len(swings):>3}/{samples:<3} {derived['swing_from_baseline']:>7} "
                      f"{derived['swing_peak_to_trough']:>7} {p['peak']:>7} {p['base']:>7} "
                      f"{p['thr']:>7} {p['bo']:>4} {p['dur'] / 1000:>7.1f}ms")
    except KeyboardInterrupt:
        print("\n  block cut short.")

    _print_block_summary(swings, time.time() - block_start, rejected, suspect, mode)
    return swings


def _report_non_pulse(kind: str, fields: dict, text: str) -> None:
    """Surface everything the device says that isn't a pulse.

    IDLE is the one that earns its keep during debugging: raw shows whether the
    ADC is reading a sane level at all, and thr shows where the adaptive
    threshold has settled, so "no pulses" resolves into no signal, weak signal,
    or signal present but under the trigger.
    """
    if kind == "IDLE":
        # Dual reports a_raw/b_raw, single reports raw. Show whichever exists -
        # these are the saturation witness, so they must not go missing.
        if "a_raw" in fields:
            levels = f"a_raw={fields['a_raw']} b_raw={fields.get('b_raw', '?')}"
        else:
            levels = f"adc={fields.get('raw', '?')}"
        print(f"    [idle] {levels} threshold={fields.get('thr', '?')} "
              f"backoffs={fields.get('bo', '?')} pulses_so_far={fields.get('pulses', '?')}")
    elif kind == "LORAL":
        version = text.split()[1] if len(text.split()) > 1 else "?"
        print(f"    [device] {text}")
        if version != str(PROTOCOL_VERSION):
            print(f"    [!] device speaks protocol {version}, this collector expects "
                  f"{PROTOCOL_VERSION}. Reflash src/robot/ if fields look wrong.")
    elif kind == "ERR":
        print(f"    [!] device error: {text}")
    else:
        print(f"    [device] {text}")


def _print_block_summary(swings: list[float], elapsed: float,
                         rejected: list[str] = (), suspect: list[int] = (),
                         mode: str = "single") -> None:
    feat = "ndiff_hpf" if mode == "dual" else "peak_hpf"
    if rejected:
        share = len(rejected) / (len(rejected) + len(swings))
        print(f"  dropped {len(rejected)} degenerate pulses ({share:.0%} of what arrived)")
        if share > 0.25:
            print("    That is a lot - noise triggers, meaning the edge threshold has")
            print("    adapted below the noise floor. Check beacon alignment.")
    if suspect:
        print(f"  {len(suspect)} pulses ran long (>{LONG_PULSE_US//1000}ms, missed falling edge). "
              f"KEPT - peak_hpf is still valid;")
        print(f"    only their swing/pk-pk columns are inflated.")

    if not swings:
        print("  done: 0 samples - no usable pulses received.\n")
        return

    rate = len(swings) / elapsed if elapsed > 0 else 0.0
    mean = sum(swings) / len(swings)
    print(f"  done: {len(swings)} samples in {elapsed:.1f}s = {rate:.2f}/s "
          f"(device caps this at {EXPECTED_RATE_HZ:.1f}/s), mean {feat} = {mean:.4g} "
          f"(cv {_cv(swings):.1f}%), range {min(swings):.4g} to {max(swings):.4g}")
    if rate < EXPECTED_RATE_HZ / 2:
        print(f"    NOTE: that is well under the {EXPECTED_RATE_HZ:.1f}/s ceiling, so pulses are "
              f"being missed rather than merely rate-limited.\n"
              f"    Usual causes: beacon out of alignment, or the edge threshold adapted too high.")
    print()


def collect(port: str, baud: int, labels: list[float], samples: int, rounds: int,
            csv_path: str, dashboard_port: int | None = None,
            prompt_between_blocks: bool = True, units: str = "deg",
            noun: str = "the target") -> None:
    summary: dict[float, list[float]] = {d: [] for d in labels}

    state = None
    if dashboard_port is not None:
        try:
            from dashboard import DashboardState, start_dashboard   # run as a script
        except ImportError:
            from .dashboard import DashboardState, start_dashboard  # run as -m src.ml....
        state = DashboardState()
        bound = start_dashboard(state, dashboard_port, EXPECTED_RATE_HZ)
        print(f"Dashboard: http://127.0.0.1:{bound}  (refreshes once a second)")

    with serial.Serial(port, baud, timeout=1) as ser:
        print(f"Connected to {port}.")
        # Mode has to be known before the CSV header is written, since the two
        # protocols produce different column sets.
        mode = preflight(ser)
        if mode is None:
            return

        fields = csv_fields_for(mode)
        need_header = check_csv_schema(csv_path, fields)

        with open(csv_path, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            if need_header:
                writer.writeheader()

            print(f"Plan: {rounds} round(s) x {len(labels)} position(s) x {samples} samples "
                  f"= {rounds * len(labels) * samples} rows total.\n")

            for round_index in range(rounds):
                for label in labels:
                    print(f"[round {round_index + 1}/{rounds}] {label:g}{units}")
                    if prompt_between_blocks:
                        # Loud on purpose. This blocks on input(), so the program
                        # goes quiet here - and a quiet collector is easily read
                        # as a broken one, especially right after a device problem.
                        print( "  +------------------------------------------------------------+")
                        print(f"  |  Set {noun} to {label:g} {units.upper()}.")
                        print( "  |  Then PRESS ENTER here to start collecting this block.")
                        print( "  |  (Nothing is recorded until you do. Ctrl+C to stop.)")
                        print( "  +------------------------------------------------------------+")
                        try:
                            input("  waiting for ENTER >>> ")
                        except KeyboardInterrupt:
                            print("\nAborted.")
                            _print_summary(summary, mode, units)
                            return

                    # Order matters: flush what buffered while the rig was being
                    # moved, THEN let the detector re-adapt, and only then record.
                    flush_and_resync(ser)
                    dropped = settle(ser)
                    print(f"  flushed stale buffer, discarded {dropped} pulses while the "
                          f"detector re-adapted")

                    if state is not None:
                        state.set_distance(label)
                    swings = collect_block(ser, label, samples, round_index, writer, f,
                                           state, mode, units)
                    summary[label].extend(swings)

    print(f"\nCollection complete -> {csv_path}")
    _print_summary(summary, mode, units)


def _cv(vals: list[int]) -> float:
    """Coefficient of variation as a percentage. Within one distance this is
    pure noise, so it sets the floor on how finely distances can be told apart."""
    if len(vals) < 2:
        return 0.0
    mean = sum(vals) / len(vals)
    if not mean:
        return 0.0
    var = sum((v - mean) ** 2 for v in vals) / len(vals)
    return 100 * var ** 0.5 / abs(mean)


def _print_summary(summary: dict[float, list[float]], mode: str = "single",
                   units: str = "in") -> None:
    """Per-label mean of the primary feature, with separability.

    The separation column is what decides whether this is trainable: it reports
    each label's gap from its neighbour in pooled standard deviations. Under
    about 2 sd and those two classes will overlap badly no matter which model
    is fitted. A large mean spread is worthless if within-label noise is the
    same size.
    """
    populated = {d: v for d, v in summary.items() if v}
    if not populated:
        return

    feat = "ndiff_hpf" if mode == "dual" else "peak_hpf"
    if mode == "dual":
        print(f"\n  mean {feat} by {units} (expect a MONOTONIC sweep through zero at boresight):")
    else:
        print(f"\n  mean {feat} by {units} (expect a clear falloff):")

    order = sorted(populated)
    prev = None
    for d in order:
        vals = populated[d]
        mean = sum(vals) / len(vals)
        cv = _cv(vals)
        sd = (sum((v - mean) ** 2 for v in vals) / len(vals)) ** 0.5
        line = f"    {d:7.2f}{units}  n={len(vals):4d}  mean={mean:10.4g}  sd={sd:9.4g}"
        if prev is not None:
            pm, psd = prev
            pooled = (sd + psd) / 2
            neighbour = order[order.index(d) - 1]
            if pooled:
                line += f"  [{abs(mean - pm) / pooled:.1f} sd from {neighbour:g}{units}]"
            else:
                line += f"  [no spread yet vs {neighbour:g}{units}]"
        print(line)
        prev = (mean, sd)

    if mode == "dual":
        print("\n  ndiff should move smoothly and monotonically with angle, crossing zero")
        print("  where the two diodes see the beacon equally. If it is flat, check that")
        print("  a_peak/b_peak actually differ - equal channels mean the diodes are aimed")
        print("  the same way, or the front end is saturated and both are pinned.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Collect labeled ADC pulse data (duration, swing, target distance) over USB serial."
    )
    parser.add_argument("--port", required=True, help="Serial port, e.g. COM5 (Windows) or /dev/ttyACM0 (Linux)")
    parser.add_argument("--baud", type=int, default=115200, help="Baud rate (default: 115200)")
    parser.add_argument(
        "--label",
        type=float,
        nargs="+",
        help="Label value(s) to collect at, e.g. --label -30 -20 -10 0 10 20 30 for bearing. "
             "Cycled once per round. Required unless --raw is given.",
    )
    parser.add_argument(
        "--units", default="deg",
        help="Units for --label, used in prompts and the CSV (default: deg).",
    )
    parser.add_argument(
        "--noun", default="the robot",
        help="What the prompt tells you to move (default: 'the robot').",
    )
    parser.add_argument(
        "--distance", type=float, nargs="+",
        help="Back-compat alias for '--label ... --units in --noun \"the beacon\"'.",
    )
    parser.add_argument(
        "--samples", type=int, default=25, help="Samples to collect per distance per round (default: 25)"
    )
    parser.add_argument(
        "--rounds",
        type=int,
        default=4,
        help="Times to cycle through the distance list (default: 4). More rounds spread each "
             "distance across more of the session, so drift can't masquerade as distance.",
    )
    parser.add_argument(
        "--output", default="training_data.csv", help="CSV output path (default: training_data.csv)"
    )
    parser.add_argument(
        "--dashboard",
        nargs="?",
        type=int,
        const=8000,
        default=None,
        metavar="PORT",
        help="Serve a live monitor on 127.0.0.1:PORT (default port 8000 if the flag is given "
             "with no value). Shows rate, edge threshold, backoffs and swing-vs-distance as "
             "collection runs.",
    )
    parser.add_argument(
        "--no-prompt",
        action="store_true",
        help="Don't wait for Enter between blocks - start collecting immediately and run "
             "straight through. Use for a single fixed distance, or an unattended run.",
    )
    parser.add_argument(
        "--raw",
        nargs="?",
        type=float,
        const=10.0,
        default=None,
        metavar="SECONDS",
        help="Diagnostic: dump raw serial bytes for SECONDS (default 10) and report what the "
             "device is actually sending, instead of collecting. Use this first when no packets arrive.",
    )
    args = parser.parse_args()

    if args.raw is not None:
        raw_dump(args.port, args.baud, args.raw)
        return

    labels, units, noun = args.label, args.units, args.noun
    if args.distance:
        if labels:
            parser.error("pass either --label or --distance, not both")
        labels, units, noun = args.distance, "in", "the beacon"
    if not labels:
        parser.error("--label (or --distance) is required unless --raw is given")

    collect(args.port, args.baud, labels, args.samples, args.rounds,
            args.output, args.dashboard, not args.no_prompt, units, noun)


if __name__ == "__main__":
    main()

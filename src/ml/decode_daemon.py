"""Live decoder: read the robot's pulse stream, decode, send the message back.

Runs on a PC against a Pico running src/robot/dual_comms_robot.py. Closes the
loop that the offline evaluation only simulated - the robot ends up holding
decoded messages it could not have decoded itself.

    robot --P/FLUSH--> daemon --Viterbi--> daemon --MSG--> robot

The model is fitted once at startup from a captured dataset rather than being
learned online. Emission means and the gap sigma are properties of the link's
timing, not of the payload, so they do not drift within a session; refitting
per message would only add variance.

If the beacon URL is supplied the daemon also polls it for ground truth and
reports live accuracy for the decoder against the hardcoded baseline, which is
the same comparison the offline evaluation makes but on unseen traffic.
"""
import argparse
import csv
import html as html_mod
import json
import re
import threading
import time
import urllib.request
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import serial

import decoder as D
from evaluate_decoder import baseline_decode, levenshtein

REST_FRACTION = 0.6          # of the beacon's 500ms rest, for pass splitting
REST_MS = 500
MAX_LOG = 5                  # matches the robot's log depth

# The page below is deliberately shaped to satisfy the regexes in
# src/analysis/diagnose_comms.py - two <pre> blocks in order (message log, then
# diagnostics) and four labelled spans. That lets the existing BER tooling work
# against this daemon unchanged: point --robot-host at host:port and
# run_ber_experiment.py measures the DECODER, while /baseline exposes the same
# traffic decoded by the hardcoded windows for a like-for-like comparison.
PAGE = """HTTP/1.0 200 OK\r\nContent-type: text/html\r\n\r\n
<!DOCTYPE html>
<html><head><title>LORAL {which} status</title>
<meta http-equiv="refresh" content="2">
<style>body{{font-family:sans-serif;margin:2rem;background:#f4f4f9}}
.c{{background:#fff;padding:20px;border-radius:8px;max-width:640px;
box-shadow:0 2px 4px rgba(0,0,0,.1)}}
pre{{background:#1e1e1e;color:#0f0;padding:12px;border-radius:5px;
overflow:auto;max-height:260px}}</style></head><body><div class="c">
<h2>LORAL receiver &mdash; {which}</h2>
<p><b>Estimated Distance:</b> <span>{distance}</span></p>
<p><b>Resync Errors:</b> <span>{resync}</span></p>
<p><b>Missed Start Pulses:</b> <span>{missed}</span></p>
<p><b>Current Edge Threshold:</b> <span>{threshold}</span></p>
<h3>Message Log (last {n})</h3>
<pre>{messages}</pre>
<h3>Diagnostics</h3>
<pre>{diagnostics}</pre>
</div></body></html>"""


def fit_model(path, rate):
    """Fit emissions, gap sigma and skip prior from a captured dataset."""
    recs = []
    for line in open(path, encoding="utf-8"):
        if not line.strip():
            continue
        r = json.loads(line)
        if r.get("rate", 1.0) != rate:
            continue
        if any(not isinstance(v, int) for p in r["pulses"] for v in p.values()):
            continue
        recs.append(r)
    if not recs:
        raise SystemExit(f"no rate-{rate:g} passes in {path}")
    return (D.fit_emissions(recs, rate),
            D.fit_gap_sigma(recs, rate),
            D.fit_skip_prior(recs),
            len(recs))


def parse_line(text):
    kind, _, rest = text.partition(" ")
    fields = {}
    ok = True
    for tok in rest.split():
        k, sep, v = tok.partition("=")
        if sep:
            try:
                fields[k] = int(v)
            except ValueError:
                ok = False
    return kind, fields, ok


def split_passes(pulses, rate):
    """Cut a batch on the inter-message rest, which does not scale with rate."""
    out, cur = [], []
    threshold = REST_MS * 1000 * REST_FRACTION
    for p in pulses:
        if cur and p.get("gap", -1) > threshold:
            out.append(cur)
            cur = []
        cur.append(p)
    if cur:
        out.append(cur)
    return out


class Status:
    """Shared state between the serial loop and the HTTP server."""

    def __init__(self):
        self.lock = threading.Lock()
        self.decoder_msgs = []
        self.baseline_msgs = []
        self.diagnostics = []
        self.threshold = "(unknown)"
        self.device_dropped = 0
        self.baseline_failures = 0
        self.passes = 0

    def add(self, dec_txt, base_txt):
        with self.lock:
            for log, txt in ((self.decoder_msgs, dec_txt),
                             (self.baseline_msgs, base_txt)):
                log.append(txt)
                del log[:-MAX_LOG]
            self.passes += 1

    def note(self, line):
        with self.lock:
            self.diagnostics.append(line)
            del self.diagnostics[:-MAX_LOG]

    def render(self, which):
        with self.lock:
            msgs = self.decoder_msgs if which == "decoder" else self.baseline_msgs
            body = "\n".join(html_mod.escape(m) for m in msgs) or \
                "System initialized. Awaiting messages..."
            diag = "\n".join(html_mod.escape(d) for d in self.diagnostics) or "(none)"
            return PAGE.format(
                which=which, n=MAX_LOG, messages=body, diagnostics=diag,
                # Kept only because diagnose_comms.py parses this field; the
                # decoder does not estimate range.
                distance="n/a (decoder mode)",
                # Closest analogue to the firmware's counter: passes the
                # hardcoded decoder failed to decode exactly.
                resync=self.baseline_failures,
                missed=self.device_dropped,
                threshold=self.threshold,
            )


def start_http(status, port):
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            which = "baseline" if self.path.rstrip("/").endswith("baseline") \
                else "decoder"
            body = status.render(which)
            head, _, page = body.partition("\r\n\r\n")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(page.encode("utf-8"))

        def log_message(self, *a):
            pass                    # the page self-refreshes; logs would bury the output

    # Loopback, not 0.0.0.0: this is a local debug view of a running
    # experiment and has no reason to be on the network.
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    except OSError as exc:
        # Windows reserves scattered port ranges (Hyper-V/WinNAT), so a
        # perfectly ordinary port can fail to bind with a permission error.
        # Falling back to an OS-assigned port keeps the run alive; the caller
        # prints whatever we actually got.
        print(f"[!] could not bind port {port} ({exc}); using an ephemeral port")
        srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv.server_address[1]


def fetch_truth(url):
    try:
        with urllib.request.urlopen(url, timeout=3) as r:
            html = r.read().decode("utf-8", "replace")
        m = re.search(r"Currently Broadcasting: <b>([^<]*)</b>", html)
        return m.group(1) if m else None
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", required=True)
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--train", default="experiment_data/C_data_rate_sweep/comms_sweep_v6.jsonl",
                    help="Captured dataset to fit the decoder model from.")
    ap.add_argument("--rate", type=float, default=1.0,
                    help="Beacon rate the robot is receiving; selects the model.")
    ap.add_argument("--beacon", default=None,
                    help="Beacon URL. If given, live accuracy is reported against it.")
    ap.add_argument("--http-port", type=int, default=8080,
                    help="Serve a diagnose_comms-compatible status page here. "
                         "'/' shows the decoder's messages, '/baseline' the "
                         "hardcoded decoder's, so run_ber_experiment.py can "
                         "measure either without modification.")
    ap.add_argument("--csv", default=None,
                    help="Write one row per decoded transmission here. Pairs each "
                         "pass with the ground truth current at decode time, which "
                         "run_ber_experiment cannot do - its dedup identifies "
                         "messages by text, so a repeated payload is invisible to it.")
    ap.add_argument("--duration", type=float, default=0.0,
                    help="Stop after this many seconds (0 = run until Ctrl+C).")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    emissions, gap_sigma, skip_prior, n_train = fit_model(args.train, args.rate)
    print(f"model: {n_train} training passes at rate {args.rate:g}, "
          f"skip log-prior {skip_prior:.2f}", flush=True)
    print(f"       emissions {({k: round(v[0]) for k, v in emissions.items()})}", flush=True)

    truth = fetch_truth(args.beacon) if args.beacon else None
    if truth is not None:
        print(f"ground truth: {truth!r}", flush=True)

    csv_f = csv_w = None
    if args.csv:
        csv_f = open(args.csv, "w", newline="", encoding="utf-8")
        csv_w = csv.writer(csv_f)
        csv_w.writerow([
            "elapsed_s", "timestamp", "ground_truth", "decoded", "baseline",
            "decoder_exact", "baseline_exact",
            "decoder_char_errs", "baseline_char_errs", "chars",
            "cum_decoder_exact_rate", "cum_baseline_exact_rate",
            "cum_decoder_char_err", "cum_baseline_char_err",
            "edge_threshold", "device_dropped",
        ])

    status = Status()
    bound = start_http(status, args.http_port)
    print(f"status page: http://127.0.0.1:{bound}/  (decoder)", flush=True)
    print(f"             http://127.0.0.1:{bound}/baseline  (hardcoded)", flush=True)
    print(f"  measure with: python src/analysis/run_ber_experiment.py "
          f"--robot-host 127.0.0.1:{bound} --beacon-host 192.168.0.131\n", flush=True)

    stats = defaultdict(int)
    pending = []
    last_truth_poll = 0.0

    with serial.Serial(args.port, args.baud, timeout=1) as ser:
        ser.reset_input_buffer()
        ser.readline()
        print(f"listening on {args.port}\n", flush=True)
        t_start = time.time()

        deadline = time.time() + args.duration if args.duration else None
        while deadline is None or time.time() < deadline:
            raw = ser.readline()
            if not raw:
                continue
            text = raw.decode("utf-8", "replace").strip()
            if not text:
                continue
            kind, fields, ok = parse_line(text)

            if kind == "P":
                if ok:
                    pending.append(fields)
                    # Take the threshold from pulses, not only from IDLE. IDLE
                    # is emitted only when the buffer is empty, which almost
                    # never happens under continuous traffic - so the status
                    # page reported "(unknown)" for an entire run.
                    status.threshold = fields.get("thr", status.threshold)
                else:
                    stats["malformed"] += 1
                continue
            if kind != "FLUSH":
                if kind == "IDLE" and "thr" in fields:
                    status.threshold = fields["thr"]
                if kind in ("ERR", "MSGLOG"):
                    status.note(text)
                if not args.quiet and kind in ("IDLE", "ERR", "MSGLOG", "LORAL"):
                    print(f"  [device] {text}", flush=True)
                continue

            # A FLUSH closes a batch; it may hold more than one pass.
            batch, pending = pending, []
            status.device_dropped += fields.get("dropped", 0)
            if args.beacon and time.time() - last_truth_poll > 10:
                truth = fetch_truth(args.beacon) or truth
                last_truth_poll = time.time()

            for p in split_passes(batch, args.rate):
                if len(p) < 9:                       # shorter than one byte
                    continue
                _, out = D.decode(p, emissions, gap_sigma, args.rate,
                                  skip_log_prior=skip_prior)
                base = baseline_decode(p, args.rate)
                dec_txt = bytes(out or []).decode("ascii", "replace").rstrip("\n")
                base_txt = bytes(base).decode("ascii", "replace").rstrip("\n")

                ser.write(("MSG " + dec_txt + "\n").encode("ascii", "replace"))
                stats["passes"] += 1
                # Feeds the status page. Without this the served message log
                # stays empty and every BER poll reports zero new messages,
                # which reads as a perfect 0% error rate over zero traffic.
                status.add(dec_txt, base_txt)

                note = ""
                if truth:
                    d_ok = dec_txt == truth
                    b_ok = base_txt == truth
                    stats["dec_ok"] += d_ok
                    stats["base_ok"] += b_ok
                    stats["dec_err"] += levenshtein(list(dec_txt), list(truth))
                    stats["base_err"] += levenshtein(list(base_txt), list(truth))
                    stats["chars"] += len(truth)
                    if not b_ok:
                        status.baseline_failures += 1
                    note = (f"   decoder {'OK ' if d_ok else 'BAD'}"
                            f"  baseline {'OK ' if b_ok else 'BAD'}")

                    if csv_w:
                        n = stats["passes"]
                        chars = max(stats["chars"], 1)
                        csv_w.writerow([
                            f"{time.time() - t_start:.1f}",
                            time.strftime("%Y-%m-%dT%H:%M:%S"),
                            truth, dec_txt, base_txt, int(d_ok), int(b_ok),
                            levenshtein(list(dec_txt), list(truth)),
                            levenshtein(list(base_txt), list(truth)), len(truth),
                            f"{stats['dec_ok'] / n:.4f}", f"{stats['base_ok'] / n:.4f}",
                            f"{stats['dec_err'] / chars:.5f}",
                            f"{stats['base_err'] / chars:.5f}",
                            status.threshold, status.device_dropped,
                        ])
                        csv_f.flush()
                if not args.quiet:
                    print(f"[{stats['passes']:>4}] {dec_txt!r}{note}", flush=True)

            if truth and stats["passes"] and stats["passes"] % 5 == 0:
                n = stats["passes"]
                print(f"       running: decoder {100*stats['dec_ok']/n:5.1f}% exact "
                      f"({100*stats['dec_err']/max(stats['chars'],1):.2f}% char err)"
                      f"  |  baseline {100*stats['base_ok']/n:5.1f}% exact "
                      f"({100*stats['base_err']/max(stats['chars'],1):.2f}%)",
                      flush=True)

    if truth:
        summarise(stats)


def summarise(stats):
    """Final tally. Keys must match those accumulated in main(): dec_* / base_*."""
    n = stats["passes"]
    if not n:
        print("\nno complete passes decoded")
        return
    chars = max(stats["chars"], 1)
    print(f"\n  over {n} live transmissions:")
    print(f"  {'':10}{'exact':>9}{'char err':>11}")
    for prefix, label in (("dec", "decoder"), ("base", "baseline")):
        print(f"  {label:<10}{100*stats[prefix+'_ok']/n:>8.1f}%"
              f"{100*stats[prefix+'_err']/chars:>10.2f}%")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nstopped")

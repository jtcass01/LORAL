"""Live HTTP dashboard for training-data collection.

Served from the PC, not the Pico. The device could host this itself - there is
already an HTTP server in single_diode_robot.py - but MicroPython's _thread has
a GIL, so an HTTP handler on core 1 steals bytecode cycles from the sampling
loop on core 0, and the Pico W's WiFi stack steals more. Since the open question
during collection is usually "are we sampling fast enough to catch every pulse",
hosting the debug view on the device would perturb the thing being measured.
The PC receives every packet anyway and has cycles to spare.

Start it with start_dashboard() and feed it with record_pulse(); it runs on a
daemon thread and never blocks collection.
"""
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

RECENT_PULSES = 25

CSS = """
body { font-family: system-ui, sans-serif; margin: 1.5rem; background: #f4f4f9; color: #222; }
.card { background: #fff; padding: 1rem 1.25rem; border-radius: 8px; margin-bottom: 1rem;
        box-shadow: 0 2px 4px rgba(0,0,0,.1); }
h2 { margin: 0 0 .75rem; } h3 { margin: 0 0 .5rem; font-size: 1rem; }
table { border-collapse: collapse; width: 100%; font-variant-numeric: tabular-nums; }
th, td { padding: .3rem .5rem; text-align: right; border-bottom: 1px solid #eee; }
th:first-child, td:first-child { text-align: left; }
th { font-weight: 600; color: #555; }
.tiles { display: flex; gap: 1rem; flex-wrap: wrap; }
.tile { flex: 1 1 9rem; }
.tile .v { font-size: 1.6rem; font-weight: 600; }
.tile .l { font-size: .8rem; color: #666; }
.ok { color: #2e7d32; } .warn { color: #ef6c00; } .bad { color: #c62828; }
.log { background: #1e1e1e; color: #d4d4d4; padding: .75rem; border-radius: 5px;
       overflow: auto; max-height: 20rem; }
.log table { color: #d4d4d4; } .log th { color: #888; }
.log th, .log td { border-bottom: 1px solid #333; }
.note { font-size: .85rem; color: #666; margin-top: .5rem; }
"""


class DashboardState:
    """Thread-safe store of what collection has seen so far."""

    def __init__(self):
        self._lock = threading.Lock()
        self.started = time.time()
        self.last_packet_at = None
        self.total = 0
        self.recent = []                 # newest first
        self.by_distance = {}            # distance -> list of swings
        self.current_distance = None
        self.edge_threshold = 0
        self.backoffs = 0
        self.mode = "single"

    def set_distance(self, distance_in):
        with self._lock:
            self.current_distance = distance_in

    def record_pulse(self, label, duration_us, feature, derived, mode,
                     edge_threshold, backoffs):
        """Record one pulse. `feature` is whatever the mode judges by
        (ndiff_hpf for dual, peak_hpf for single); `derived` carries the rest."""
        with self._lock:
            self.last_packet_at = time.time()
            self.total += 1
            self.mode = mode
            self.edge_threshold = edge_threshold
            self.backoffs = backoffs

            bucket = self.by_distance.setdefault(label, {"feature": [], "extra": []})
            bucket["feature"].append(feature)
            bucket["extra"].append(derived)

            row = {"t": self.last_packet_at, "d": label, "dur": duration_us,
                   "feature": feature, "thr": edge_threshold, "bo": backoffs}
            row.update(derived)
            self.recent.insert(0, row)
            del self.recent[RECENT_PULSES:]

    def snapshot(self):
        with self._lock:
            return {
                "started": self.started,
                "last_packet_at": self.last_packet_at,
                "total": self.total,
                "recent": list(self.recent),
                "by_distance": {d: {k: list(v) for k, v in b.items()}
                                for d, b in self.by_distance.items()},
                "current_distance": self.current_distance,
                "edge_threshold": self.edge_threshold,
                "backoffs": self.backoffs,
                "mode": self.mode,
            }


def _cv(vals):
    """Coefficient of variation, as a percentage. Within one distance this is
    pure noise, so it sets the floor on how finely distances can be told apart."""
    if len(vals) < 2:
        return 0.0
    mean = sum(vals) / len(vals)
    if not mean:
        return 0.0
    var = sum((v - mean) ** 2 for v in vals) / len(vals)
    return 100 * var ** 0.5 / abs(mean)


def _sd(vals):
    if len(vals) < 2:
        return 0.0
    m = sum(vals) / len(vals)
    return (sum((v - m) ** 2 for v in vals) / len(vals)) ** 0.5


def _falloff_table(by_distance, mode):
    """Per-label mean of the primary feature, with separation from the neighbour.

    Separation in pooled standard deviations is the number that decides
    trainability. Under about 2 sd, two labels overlap badly regardless of what
    model is fitted, so a large mean spread means nothing on its own.
    """
    populated = {d: b for d, b in by_distance.items() if b["feature"]}
    if not populated:
        return "<p class='note'>No samples yet.</p>"

    dual = mode == "dual"
    feat = "ndiff_hpf" if dual else "peak_hpf"
    rows, prev = [], None
    for d in sorted(populated):
        vals = populated[d]["feature"]
        extra = populated[d]["extra"]
        mean, sd = sum(vals) / len(vals), _sd(vals)

        sep_cell = "<td>&mdash;</td>"
        if prev is not None:
            pm, psd = prev
            pooled = (sd + psd) / 2
            if pooled:
                sep = abs(mean - pm) / pooled
                cls = "ok" if sep >= 2 else ("warn" if sep >= 1 else "bad")
                sep_cell = f"<td class='{cls}'>{sep:.1f} sd</td>"
        prev = (mean, sd)

        if dual and extra:
            a = sum(e["sum_hpf"] for e in extra) / len(extra)
            side = f"<td>{a:.0f}</td>"
        elif extra:
            a = sum(e["swing_from_baseline"] for e in extra) / len(extra)
            side = f"<td>{a:.0f}</td>"
        else:
            side = "<td>&mdash;</td>"

        rows.append(
            f"<tr><td>{d:g}</td><td>{len(vals)}</td><td>{mean:.4g}</td>"
            f"<td>{sd:.3g}</td>{sep_cell}{side}</tr>"
        )

    side_hdr = "sum_hpf" if dual else "swing"
    note = (
        "<b>ndiff_hpf</b> = (a&minus;b)/(a+b) should sweep smoothly and monotonically with "
        "angle, crossing zero where both diodes see the beacon equally. It divides out "
        "distance, transmit power and gain drift. If it is flat, check that the two channels "
        "actually differ &mdash; equal channels mean the diodes are aimed alike, or the front "
        "end is saturated and both are pinned, in which case <b>sum_hpf</b> will also stop "
        "responding."
        if dual else
        "peak_hpf should fall off with distance. A flat column is the signature of a saturating "
        "front end &mdash; a hardware problem, not a modeling one."
    )
    return (
        f"<table><tr><th>label</th><th>n</th><th>mean {feat}</th><th>sd</th>"
        f"<th>sep from prev</th><th>{side_hdr}</th></tr>"
        + "".join(rows) + "</table>"
        f"<p class='note'>{note} <b>Separation</b> under 2 sd means those two labels will "
        "overlap badly whatever model you fit.</p>"
    )


def _recent_table(recent, mode):
    if not recent:
        return "<p class='note'>Waiting for the first pulse&hellip;</p>"

    if mode == "dual":
        cols = [("label", "d"), ("ndiff", "ndiff_hpf"), ("a_hpf", "a_hpf"),
                ("b_hpf", "b_hpf"), ("sum", "sum_hpf"), ("a_swing", "a_swing"),
                ("b_swing", "b_swing"), ("thresh", "thr"), ("b/o", "bo")]
    else:
        cols = [("label", "d"), ("peak_hpf", "feature"), ("swing", "swing_from_baseline"),
                ("pk-pk", "swing_peak_to_trough"), ("thresh", "thr"), ("b/o", "bo")]

    rows = []
    for p in recent:
        cells = f"<td>{time.strftime('%H:%M:%S', time.localtime(p['t']))}</td>"
        for _, key in cols:
            v = p.get(key) if key else None
            if v is None:
                v = "&mdash;"
            elif isinstance(v, float):
                v = f"{v:.4g}"
            cells += f"<td>{v}</td>"
        cells += f"<td>{p['dur'] / 1000:.1f} ms</td>"
        rows.append(f"<tr>{cells}</tr>")

    hdr = "<th>time</th>" + "".join(f"<th>{n}</th>" for n, _ in cols) + "<th>dur</th>"
    return ("<div class='log'><table><tr>" + hdr + "</tr>"
            + "".join(rows) + "</table></div>")


def render(state, expected_rate_hz):
    s = state.snapshot()
    now = time.time()

    if s["last_packet_at"] is None:
        age, age_cls, age_txt = None, "bad", "no packets yet"
    else:
        age = now - s["last_packet_at"]
        age_cls = "ok" if age < 2 else ("warn" if age < 5 else "bad")
        age_txt = f"{age:.1f}s ago"

    elapsed = now - s["started"]
    rate = s["total"] / elapsed if elapsed > 0 else 0
    rate_cls = "ok" if rate >= expected_rate_hz * 0.75 else ("warn" if rate >= expected_rate_hz * 0.4 else "bad")
    bo_cls = "ok" if s["backoffs"] == 0 else ("warn" if s["backoffs"] < 3 else "bad")

    dist = s["current_distance"]
    dist_txt = f"{dist:g} in" if dist is not None else "&mdash;"

    return f"""<!DOCTYPE html><html><head><title>LORAL collection monitor</title>
<meta http-equiv="refresh" content="1"><style>{CSS}</style></head><body>
<h2>LORAL collection monitor</h2>

<div class="card"><div class="tiles">
  <div class="tile"><div class="v {age_cls}">{age_txt}</div><div class="l">last pulse</div></div>
  <div class="tile"><div class="v {rate_cls}">{rate:.2f}/s</div><div class="l">rate (cap {expected_rate_hz:.1f}/s)</div></div>
  <div class="tile"><div class="v">{s['total']}</div><div class="l">pulses this session</div></div>
  <div class="tile"><div class="v">{dist_txt}</div><div class="l">collecting at</div></div>
  <div class="tile"><div class="v">{s['edge_threshold']}</div><div class="l">edge threshold</div></div>
  <div class="tile"><div class="v {bo_cls}">{s['backoffs']}</div><div class="l">backoffs before last pulse</div></div>
</div>
<p class="note">Backoffs above zero mean the detector timed out waiting and clawed its threshold
down to find that pulse &mdash; the direct signature of pulses being missed rather than merely rate-limited.</p>
</div>

<div class="card"><h3>{'Bearing feature vs label' if s['mode'] == 'dual' else 'Signal vs label'}</h3>
{_falloff_table(s['by_distance'], s['mode'])}</div>
<div class="card"><h3>Last {RECENT_PULSES} pulses</h3>{_recent_table(s['recent'], s['mode'])}</div>
</body></html>"""


def start_dashboard(state, port, expected_rate_hz):
    """Start the dashboard on a daemon thread. Returns the bound port."""
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = render(state, expected_rate_hz).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass  # the page self-refreshes once a second; access logs would bury the collector output

    # Bound to loopback: this is a local debug view of an in-progress experiment,
    # and there is no reason to expose it to the rest of the network.
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server.server_address[1]

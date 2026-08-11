import uasyncio as asyncio
import network
import socket
import _thread
from machine import ADC
from utime import ticks_us, ticks_diff, sleep
from wifi_config import WIFI_SSID, WIFI_PASSWORD

# --- Shared State for Inter-Core Communication ---
data_lock = _thread.allocate_lock()
MAX_MESSAGES = 5 # Changed to keep the last 5 messages
MAX_MISSED_PULSE_LOG = 5

# Fixed-point shift for the idle-baseline accumulator (see SingleDiodeRobot).
BASELINE_SHIFT = 6

shared_data = {
    "messages": ["System initialized. Awaiting messages..."],
    "estimated_distance": "Pending ML Model (TBD)",
    "resync_errors": 0,
    "missed_start_pulses": 0,
    "missed_pulse_log": [],
    "edge_threshold": 0
}

# --- Wi-Fi Setup ---
def connect_wifi(ssid, password):
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    wlan.connect(ssid, password)
    
    print("Connecting to WiFi...")
    while not wlan.isconnected():
        sleep(1)
        
    print("Connected! IP Address:", wlan.ifconfig()[0])

# --- Core 1: HTTP Server ---
def core1_http_server():
    """Runs continuously on Core 1 to serve HTTP requests."""
    print("Starting HTTP Server on Core 1...")
    
    addr = socket.getaddrinfo('0.0.0.0', 80)[0][-1]
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(addr)
    s.listen(1)
    
    while True:
        try:
            cl, addr = s.accept()
            request = cl.recv(1024)
            
            # Safely read the shared data
            with data_lock:
                message_log = "\n".join(shared_data["messages"])
                dist = shared_data["estimated_distance"]
                resync_errors = shared_data["resync_errors"]
                missed_start_pulses = shared_data["missed_start_pulses"]
                missed_pulse_log = "\n".join(shared_data["missed_pulse_log"])
                edge_threshold = shared_data["edge_threshold"]
            
            # Build the HTML response
            html = f"""HTTP/1.1 200 OK\r\nContent-type: text/html\r\n\r\n
            <!DOCTYPE html>
            <html>
                <head>
                    <title>Robot Receiver Status</title>
                    <meta http-equiv="refresh" content="2">
                    <style>
                        body {{ font-family: sans-serif; margin: 2rem; background-color: #f4f4f9; }}
                        .container {{ background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
                        .log-box {{ background: #1e1e1e; color: #00ff00; padding: 15px; border-radius: 5px; overflow-y: auto; height: 300px; }}
                        h3 {{ margin-top: 0; }}
                    </style>
                </head>
                <body>
                    <div class="container">
                        <h2>Robot Receiver Status</h2>
                        <p><b>Estimated Distance:</b> <span style="font-size: 1.2em; color: #d9534f;">{dist}</span></p>
                        <p><b>Resync Errors:</b> <span style="font-size: 1.2em; color: #d9534f;">{resync_errors}</span></p>
                        <p><b>Missed Start Pulses:</b> <span style="font-size: 1.2em; color: #d9534f;">{missed_start_pulses}</span></p>
                        <p><b>Current Edge Threshold:</b> <span style="font-size: 1.2em; color: #d9534f;">{edge_threshold}</span></p>

                        <h3>Message Log (Last {MAX_MESSAGES})</h3>
                        <div class="log-box">
<pre>{message_log}</pre>
                        </div>

                        <h3>Missed Start Pulses (Last {MAX_MISSED_PULSE_LOG})</h3>
                        <div class="log-box">
<pre>{missed_pulse_log}</pre>
                        </div>
                    </div>
                </body>
            </html>
            """
            cl.send(html.encode('utf-8'))
            cl.close()
        except Exception as e:
            print("HTTP Server error:", e)
            if 'cl' in locals():
                cl.close()

# --- Core 0: Receiver ---
class SingleDiodeRobot:
    def __init__(
        self,
        receiver_adc: ADC,
        edge_threshold: int = 2000,
        min_edge_threshold: int = 300,
        max_edge_threshold: int = 8000,
        threshold_margin: float = 0.5,
        search_timeout_ms: int = 1000,
    ):
        self._adc: ADC = receiver_adc
        # edge_threshold is now just the STARTING guess; it's adapted at runtime
        # based on the actual signal strength we see (see _adapt_threshold and
        # the search-timeout backoff in listen_for_pulse), so it doesn't need to
        # be hand-tuned per test distance.
        self._edge_threshold: float = edge_threshold
        self._min_edge_threshold: float = min_edge_threshold
        self._max_edge_threshold: float = max_edge_threshold
        self._threshold_margin: float = threshold_margin
        self._search_timeout_us: int = search_timeout_ms * 1000
        self._prev_raw: int = 0
        self._filtered_val: float = 0.0
        self._alpha: float = 0.85
        # Slow EMA of the raw ADC level while we're idle between pulses, i.e.
        # the DC operating point of the photodiode bias network (ambient light +
        # circuit bias). The raw peak during a pulse is this pedestal PLUS the
        # optical signal, and the pedestal dominates it by ~100x - so the peak
        # alone says almost nothing about distance. Subtracting this baseline is
        # what recovers the part that actually scales with received power.
        #
        # Kept as a fixed-point INTEGER accumulator (baseline << BASELINE_SHIFT)
        # rather than a float EMA. This is updated on every ADC sample in the
        # detection loop, and that loop's speed is the effective sampling rate -
        # a float multiply-add there is slow enough on MicroPython to cost real
        # pulses. Shift of 6 gives a ~64-sample time constant: tracks ambient
        # drift, far too slow to be dragged around by a single pulse.
        self._baseline_acc = None
        # Search-timeout backoffs sat through while waiting for the current
        # pulse, and the count for the pulse most recently returned. Reported
        # by detector_state() - see listen_for_pulse.
        self._backoff_count: int = 0
        self._last_backoffs: int = 0

    def sample_hpf(self) -> tuple[int, float]:
        raw = self._adc.read_u16()
        self._filtered_val = (raw - self._prev_raw) + (self._alpha * self._filtered_val)
        self._prev_raw = raw
        return raw, self._filtered_val

    def _adapt_threshold(self, peak_hpf: float) -> None:
        """Nudge the edge threshold toward a margin below the strongest edge we
        just saw, so detection stays sensitive as distance (and therefore signal
        strength) changes, instead of being tuned for one fixed distance.
        Smoothed (70/30) so a single unusually strong or weak pulse doesn't yank
        the threshold around.
        """
        target = max(self._min_edge_threshold, min(self._max_edge_threshold, peak_hpf * self._threshold_margin))
        self._edge_threshold = 0.7 * self._edge_threshold + 0.3 * target

    async def listen_for_pulse(self):
        """Detect one pulse and return its timing plus the raw levels needed to
        recover its amplitude.

        Returns (duration_us, peak_raw, trough_raw, baseline_raw, peak_hpf).

        peak_raw on its own is NOT a distance feature - it is dominated by the
        DC pedestal (see _baseline_acc). The amplitude signal lives in
        peak_raw - baseline_raw (or peak_raw - trough_raw, which is immune to
        the circuit's polarity). All four are returned rather than a single
        pre-computed swing so a drifting baseline stays visible in the logs
        instead of being silently folded into the feature.
        """
        search_start = ticks_us()
        while True:
            raw, hpf = self.sample_hpf()
            # Updated before the threshold check so the baseline is always
            # initialised, even if a pulse triggers on the very first sample.
            if self._baseline_acc is None:
                self._baseline_acc = raw << BASELINE_SHIFT
            else:
                self._baseline_acc += raw - (self._baseline_acc >> BASELINE_SHIFT)
            if hpf > self._edge_threshold:
                baseline_at_start = self._baseline_acc >> BASELINE_SHIFT
                start_time = ticks_us()
                break
            # Nothing has crossed the current threshold in well over the
            # beacon's normal ~500ms gap between messages. The threshold is
            # likely tuned above the actual signal (e.g. beacon moved farther
            # away) - back it off and keep searching instead of waiting forever.
            if ticks_diff(ticks_us(), search_start) > self._search_timeout_us:
                self._edge_threshold = max(self._min_edge_threshold, self._edge_threshold * 0.9)
                self._backoff_count = min(255, self._backoff_count + 1)
                search_start = ticks_us()
            await asyncio.sleep(0)

        # Reset only once a pulse is actually found, so the count reported with
        # this pulse says how many timeouts we sat through waiting for it. Zero
        # means the beacon was heard immediately; a rising count is the detector
        # telling you it is starving and clawing the threshold down to cope.
        self._last_backoffs = self._backoff_count
        self._backoff_count = 0

        peak_amplitude = 0
        # Tracked alongside the peak so the swing can be measured without
        # assuming the pulse drives the ADC upward - if the front end is wired
        # so light pulls the reading down, peak_raw - baseline_raw collapses to
        # noise while peak_raw - trough_raw still captures the full excursion.
        trough_amplitude = 65535
        peak_hpf = hpf

        while True:
            raw, hpf = self.sample_hpf()
            if raw > peak_amplitude:
                peak_amplitude = raw
            if raw < trough_amplitude:
                trough_amplitude = raw
            if hpf > peak_hpf:
                peak_hpf = hpf
            if hpf < -self._edge_threshold:
                end_time = ticks_us()
                break
            await asyncio.sleep(0)

        self._adapt_threshold(peak_hpf)

        duration = ticks_diff(end_time, start_time)
        return duration, peak_amplitude, trough_amplitude, baseline_at_start, peak_hpf

    def detector_state(self) -> tuple[int, int]:
        """Adaptive-detector state as of the last returned pulse:
        (edge_threshold, backoffs_waited).

        Deliberately separate from listen_for_pulse's return value rather than a
        sixth and seventh element on it: that tuple describes the PULSE, this
        describes the DETECTOR that found it. They're read together by the
        logger but they answer different questions, and folding them into one
        seven-element unpack makes both harder to get right.
        """
        return round(self._edge_threshold), self._last_backoffs

    def prime_filter(self) -> None:
        """Re-seed the high-pass filter from the current ADC level.

        sample_hpf is a differentiator: it reports raw - prev_raw. After any gap
        where we stopped sampling (e.g. the logger's sleep between recorded
        pulses), prev_raw is stale by that whole gap, so the very first sample
        afterwards reports a huge bogus edge and fires an immediate false
        trigger. Call this after a deliberate pause to throw that one sample
        away instead of logging it as a pulse.
        """
        self._prev_raw = self._adc.read_u16()
        self._filtered_val = 0.0

    async def run(self):
        print("Robot Receiver Started on Core 0 (Async Mode).")

        with data_lock:
            shared_data["edge_threshold"] = round(self._edge_threshold)

        # State variables for assembling characters and strings
        reading_bits = False
        current_byte = 0
        bit_count = 0
        message_buffer = ""

        try:
            while True:
                duration_us, peak_amplitude, _trough, baseline_amplitude, _peak_hpf = await self.listen_for_pulse()
                duration_ms = duration_us / 1000.0
                swing = peak_amplitude - baseline_amplitude

                # Check for Start Pulse (40-60ms)
                if 40 < duration_ms < 60:
                    reading_bits = True
                    current_byte = 0
                    bit_count = 0
                    
                # If we are reading bits, parse 1s and 0s
                elif reading_bits:
                    bit_val = -1
                    if 20 < duration_ms < 30:
                        bit_val = 1
                    elif 5 < duration_ms < 15:
                        bit_val = 0
                    
                    if bit_val != -1:
                        # Shift the bit into place (Transmitter sends LSB first)
                        current_byte |= (bit_val << bit_count)
                        bit_count += 1

                        # We have collected a full 8-bit character
                        if bit_count == 8:
                            char = chr(current_byte)

                            # End of message
                            if char == '\n':
                                if message_buffer.strip(): # Ignore completely empty payloads
                                    print(f"Full message received: {message_buffer}")

                                    # Update shared data
                                    with data_lock:
                                        shared_data["messages"].append(message_buffer)
                                        if len(shared_data["messages"]) > MAX_MESSAGES:
                                            shared_data["messages"].pop(0)
                                        shared_data["edge_threshold"] = round(self._edge_threshold)

                                # Clear the buffer for the next message
                                message_buffer = ""
                            else:
                                # Append character to buffer
                                message_buffer += char

                            # Reset bit reader state machine, wait for next START PULSE
                            reading_bits = False
                    else:
                        # Pulse duration didn't match a start pulse, a '1', or a '0'.
                        # Bit_count would otherwise silently fail to advance, shifting
                        # every remaining bit in this byte (and desyncing the rest of
                        # the message). Discard the in-progress byte and resync on the
                        # next start pulse instead of building on a corrupted position.
                        print(f"Resync: ambiguous pulse ({duration_ms:.1f}ms), byte discarded")
                        with data_lock:
                            shared_data["resync_errors"] += 1
                            shared_data["edge_threshold"] = round(self._edge_threshold)
                        reading_bits = False

                else:
                    # Idle (no byte in progress) and this pulse wasn't a valid start
                    # pulse either. There's no recovery for this case: the whole byte
                    # this pulse belonged to (its real start pulse plus all 8 data
                    # bits) is skipped entirely, since we never entered reading_bits
                    # for it. Log the duration and the pulse swing so we can tell
                    # misses that cluster just outside the 40-60ms window (a timing
                    # fix) from misses with a weak swing (a threshold/signal fix).
                    # Swing, not peak: peak is dominated by the DC pedestal and is
                    # near-identical for a strong and a barely-detected pulse.
                    print(f"Missed start pulse: duration={duration_ms:.1f}ms swing={swing}")
                    with data_lock:
                        shared_data["missed_start_pulses"] += 1
                        shared_data["missed_pulse_log"].append(
                            f"{duration_ms:.1f}ms (swing {swing})"
                        )
                        if len(shared_data["missed_pulse_log"]) > MAX_MISSED_PULSE_LOG:
                            shared_data["missed_pulse_log"].pop(0)
                        shared_data["edge_threshold"] = round(self._edge_threshold)

        except asyncio.CancelledError:
            print("Receiver task cancelled.")
        except Exception as e:
            print(f"Receiver error: {e}")

async def main():
    receiver_adc = ADC(0) 
    test_robot = SingleDiodeRobot(receiver_adc=receiver_adc, edge_threshold=2000)
    await test_robot.run()

if __name__ == "__main__":
    # 1. Connect to Network (Fill in your credentials)
    connect_wifi(WIFI_SSID, WIFI_PASSWORD)
    
    # 2. Start HTTP server on Core 1
    _thread.start_new_thread(core1_http_server, ())
    
    # 3. Start Async loop on Core 0
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nProgram interrupted by user.")
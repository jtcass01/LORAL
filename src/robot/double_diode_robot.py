"""Two-photodiode receiver for bearing estimation.

Runs on the Pico. Samples two BPW34/MCP6002 channels and reports, per detected
pulse, the amplitude seen by each diode independently.

Why two diodes gives you an angle when one gave you nothing:

    D = (A - B) / (A + B)

With the diodes aimed slightly apart, D is monotonic in bearing near boresight
and cancels every term common to both channels - distance, transmit power,
amplifier gain drift, ambient DC. Those common-mode nuisances are exactly what
swamped the single-diode amplitude, so the differential removes the problem
rather than fighting it.

The one thing the ratio CANNOT survive is saturation. If both channels clip,
A and B are both pinned, D collapses to 0, and the bearing information is gone
- so this must be operated far enough back that the MCP6002 stays linear. Watch
a_peak/b_peak: if they sit at a fixed ceiling regardless of aim, back off.

Kept deliberately free of network imports so a data-collection run doesn't drag
the Wi-Fi stack and HTTP server onto the device alongside the sampling loop.
"""
import uasyncio as asyncio
from utime import ticks_us, ticks_diff

# Fixed-point shift for the idle-baseline accumulators. Integer, not float:
# this updates on every ADC sample and the loop's speed IS the sampling rate.
BASELINE_SHIFT = 6


class DoubleDiodeRobot:
    def __init__(
        self,
        adc_a,
        adc_b,
        edge_threshold: int = 2000,
        min_edge_threshold: int = 300,
        max_edge_threshold: int = 8000,
        threshold_margin: float = 0.5,
        search_timeout_ms: int = 1000,
        settle_discard: bool = True,
    ):
        self._adc_a = adc_a
        self._adc_b = adc_b

        # The RP2350 has ONE SAR converter behind an input mux, so reading A
        # then B leaves charge from the previous channel on the sample-and-hold
        # and each reading is pulled slightly toward the other. Harmless when
        # you only care about one channel; corrupting when the whole
        # measurement is the DIFFERENCE between them. Throwing away the first
        # conversion after each mux switch lets the S/H settle. Costs 2x the
        # conversions, which is affordable for ms-scale pulses.
        self._settle_discard = settle_discard

        self._edge_threshold: float = edge_threshold
        self._min_edge_threshold: float = min_edge_threshold
        self._max_edge_threshold: float = max_edge_threshold
        self._threshold_margin: float = threshold_margin
        self._search_timeout_us: int = search_timeout_ms * 1000

        # Independent high-pass state per channel - they see different signal
        # levels, so a shared filter would smear one into the other.
        self._prev_a: int = 0
        self._prev_b: int = 0
        self._filt_a: float = 0.0
        self._filt_b: float = 0.0
        self._alpha: float = 0.85

        self._base_acc_a = None
        self._base_acc_b = None

        self._backoff_count: int = 0
        self._last_backoffs: int = 0

    def _read_pair(self):
        if self._settle_discard:
            self._adc_a.read_u16()
            raw_a = self._adc_a.read_u16()
            self._adc_b.read_u16()
            raw_b = self._adc_b.read_u16()
        else:
            raw_a = self._adc_a.read_u16()
            raw_b = self._adc_b.read_u16()
        return raw_a, raw_b

    def sample(self):
        """One sample of both channels: (raw_a, raw_b, hpf_a, hpf_b)."""
        raw_a, raw_b = self._read_pair()
        self._filt_a = (raw_a - self._prev_a) + (self._alpha * self._filt_a)
        self._filt_b = (raw_b - self._prev_b) + (self._alpha * self._filt_b)
        self._prev_a, self._prev_b = raw_a, raw_b
        return raw_a, raw_b, self._filt_a, self._filt_b

    def _adapt_threshold(self, peak_sum_hpf: float) -> None:
        target = max(self._min_edge_threshold,
                     min(self._max_edge_threshold, peak_sum_hpf * self._threshold_margin))
        self._edge_threshold = 0.7 * self._edge_threshold + 0.3 * target

    def prime_filter(self) -> None:
        """Re-seed both high-pass filters from the current ADC levels.

        sample() is a differentiator reporting raw - prev_raw, so after any gap
        where we stopped sampling, prev_raw is stale by that whole gap and the
        next sample reports a huge fabricated edge. Call after any deliberate
        pause, and once before the first pulse.
        """
        raw_a, raw_b = self._read_pair()
        self._prev_a, self._prev_b = raw_a, raw_b
        self._filt_a = self._filt_b = 0.0

    def detector_state(self):
        """(edge_threshold, backoffs_waited) as of the last returned pulse."""
        return round(self._edge_threshold), self._last_backoffs

    async def listen_for_pulse(self, idle_cb=None, idle_us: int = 100_000,
                               idle_repeat_us: int = 500_000):
        """Detect one pulse; return a dict of per-channel measurements.

        Triggering is on the SUM of the two high-pass outputs, not on either
        channel alone. Off to one side the far diode may barely register, and a
        per-channel trigger would then silently drop exactly the large-bearing
        samples that carry the most angular information - flattening the very
        curve we are trying to measure.

        idle_cb fires after idle_us of finding nothing, then every
        idle_repeat_us for as long as the search continues. It exists so a
        caller can do something slow - serial output, most obviously - at the
        only moment when doing so is free. Anything slow done between pulses
        steals time from this loop, and the loop's speed IS the sampling rate:
        at 6x the inter-bit gap is 1.67ms, so a 1ms write costs 60% of it and
        pulses start going missing. idle_us of 100ms is far longer than any
        inter-bit gap (10ms at rate 1, less above) but well inside the beacon's
        500ms inter-message rest, so the callback only ever lands in real slack.

        It REPEATS rather than firing once because this method never returns
        while the line is silent. A single-shot callback meant a receiver that
        was alive but hearing nothing went permanently quiet after one call,
        which is indistinguishable from a dead board - the precise ambiguity
        the heartbeat exists to remove.
        """
        search_start = ticks_us()
        # Separate clock from search_start on purpose. The search-timeout
        # backoff below RESETS search_start every second, so sharing it made
        # the idle deadline unreachable after the first reset and the heartbeat
        # died two beats in - silently, and only on a line that stayed quiet.
        idle_start = ticks_us()
        next_idle_us = idle_us
        while True:
            raw_a, raw_b, hpf_a, hpf_b = self.sample()

            if idle_cb is not None:
                if ticks_diff(ticks_us(), idle_start) > next_idle_us:
                    next_idle_us += idle_repeat_us
                    idle_cb()

            if self._base_acc_a is None:
                self._base_acc_a = raw_a << BASELINE_SHIFT
                self._base_acc_b = raw_b << BASELINE_SHIFT
            else:
                self._base_acc_a += raw_a - (self._base_acc_a >> BASELINE_SHIFT)
                self._base_acc_b += raw_b - (self._base_acc_b >> BASELINE_SHIFT)

            if hpf_a + hpf_b > self._edge_threshold:
                base_a = self._base_acc_a >> BASELINE_SHIFT
                base_b = self._base_acc_b >> BASELINE_SHIFT
                start_time = ticks_us()
                break

            if ticks_diff(ticks_us(), search_start) > self._search_timeout_us:
                self._edge_threshold = max(self._min_edge_threshold,
                                           self._edge_threshold * 0.9)
                self._backoff_count = min(255, self._backoff_count + 1)
                search_start = ticks_us()
            await asyncio.sleep(0)

        self._last_backoffs = self._backoff_count
        self._backoff_count = 0

        peak_a = peak_b = 0
        trough_a = trough_b = 65535
        peak_hpf_a, peak_hpf_b = hpf_a, hpf_b
        peak_sum = hpf_a + hpf_b

        while True:
            raw_a, raw_b, hpf_a, hpf_b = self.sample()

            if raw_a > peak_a:
                peak_a = raw_a
            if raw_b > peak_b:
                peak_b = raw_b
            if raw_a < trough_a:
                trough_a = raw_a
            if raw_b < trough_b:
                trough_b = raw_b
            if hpf_a > peak_hpf_a:
                peak_hpf_a = hpf_a
            if hpf_b > peak_hpf_b:
                peak_hpf_b = hpf_b
            if hpf_a + hpf_b > peak_sum:
                peak_sum = hpf_a + hpf_b

            if hpf_a + hpf_b < -self._edge_threshold:
                end_time = ticks_us()
                break
            await asyncio.sleep(0)

        self._adapt_threshold(peak_sum)

        return {
            "dur": ticks_diff(end_time, start_time),
            # Absolute rising-edge time. Decoding needs the GAP between pulses,
            # not just their widths: the 10ms inter-bit gap and the longer gap
            # after a byte are what carry framing, and a spurious noise trigger
            # shows up at an anomalous gap even when its width looks plausible.
            "start_us": start_time,
            "end_us": end_time,
            "a_peak": peak_a, "a_trough": trough_a, "a_base": base_a,
            "a_hpf": int(peak_hpf_a),
            "b_peak": peak_b, "b_trough": trough_b, "b_base": base_b,
            "b_hpf": int(peak_hpf_b),
        }

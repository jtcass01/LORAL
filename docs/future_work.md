# Future Work

Ordered by expected value per unit effort. Each entry states the evidence
motivating it, so the list stays falsifiable rather than aspirational.

## 1. Character-level language prior in the decoder

**Evidence.** At 114 bit/s the decoder fails 65 of 118 live transmissions, and
almost every failure is a *single character substitution*: `Brovn` for `Brown`,
`Qudck` for `Quick`, `Quick$Brown` for `Quick Brown`. When a symbol is dropped
the decoder must infer the missing bit; gap timing localises it and the ASCII
constraint narrows the candidates, but `Brovn` is valid ASCII so the constraint
cannot reject it.

**Change.** Add an n-gram prior `P(char | history)` as a fourth evidence term
in the Viterbi cost. Cheap to fit, cheap to evaluate, and it targets exactly the
observed error profile.

**Caveat.** Only helps payloads with linguistic structure. The dataset is 70%
natural text and 30% random ASCII precisely so this contribution can be
measured rather than assumed — compare the two subsets before and after.

## 2. Amplitude features in the emission model

**Evidence.** Emissions currently use pulse width alone. Both photodiodes are
now linear and gain-matched (ratio 1.15 after the feedback resistor was
corrected from 1 M\Omega to 100 k\Omega), so `a_hpf`, `b_hpf`, `sum_hpf` and
`ndiff_hpf` are all recorded and all unused.

**Change.** Extend the emission model from univariate Gaussian over duration to
a diagonal multivariate Gaussian including amplitude. Channel disagreement is a
free per-symbol confidence signal: when the two diodes imply different symbols,
that pulse deserves a flatter posterior entering the lattice.

**Expected gain.** Modest for classification — class separation is already
12–40 sd — but potentially useful for detecting which pulses are unreliable.

## 3. Close the remaining gap to the recovery ceiling

**Evidence.** Offline at 57–171 bit/s the decoder reaches 53–72% packet success
against a computed ceiling of 85–96%. Roughly half the recoverable
transmissions are still missed, and neither `MAX_SKIP=3` nor a fitted skip
prior moved the number — both were measured as exact no-ops.

**Change.** The untested levers are a two-pass decode (forward-backward
posteriors rather than a single Viterbi path), and modelling *insertions*
explicitly. Insertions appear only at the highest rate but are currently
unhandled.

## 4. Reduce framing overhead

**Evidence.** Every byte carries its own 50 ms start pulse: 60 ms of the ~280 ms
byte period, or **21% of airtime**, spent on framing. Errors compound across
nine pulses per byte, so a per-symbol error rate of 1.4% becomes a 12% byte
loss rate.

**Change.** Frame per *message* rather than per byte, or replace the start
pulse with a shorter distinguishable symbol. Fewer pulses per byte reduces both
airtime and the compounding.

**Interaction.** This changes the protocol the decoder models, so the state
machine in `decoder.py` must change with it.

## 5. Forward error correction

**Evidence.** The link has no parity, checksum, or retransmission. A single
ambiguous pulse currently discards an entire byte.

**Change.** Even a parity bit per byte would let the decoder detect — and often
locate — a residual error the Viterbi path got wrong. A Hamming code would
correct single-bit errors outright, addressing the same substitution failures as
item 1 but without depending on payload structure.

**Cost.** Directly trades throughput for reliability, which is worth measuring
as a curve rather than assuming.

## 6. Get the front end into its linear region

**Evidence.** Received amplitude is flat with distance across 3–8 inches —
under 1% variation where inverse-square predicts a 7x change. Peak sits at
~42000 of 65535 ADC counts while the swing is only ~2200, consistent with the
MCP6002 output railing near 2.1 V. Measured, not inferred.

**Change.** Lower transimpedance gain, or operate at the range the gain was
chosen for. Until then no amplitude-based distance estimate is possible,
because the amplitude carries no distance information.

## 7. Bearing estimation with the dual-diode receiver

**Evidence.** `ndiff = (a-b)/(a+b)` cancels distance, transmit power and gain
drift, and simulation confirms it is strictly monotonic in bearing across
±30°. But on hardware it reads +0.041 ± 0.035 — the two diodes see nearly the
same signal, so the spread is comparable to the mean.

**Change.** Angle the diodes meaningfully apart (±15° or more) and back the
beacon off into the linear region. Both are prerequisites; neither is satisfied
today. The collection pipeline already supports it (`--label` with `--units
deg`), and the dual-mode CSV and dashboard are already built and tested.

## 8. Run the decoder on the device

**Evidence.** The Viterbi lattice is 256 states with a backpointer per state
per observation — tens of thousands of entries for one message. The Pico raised
`MemoryError` allocating 19 KB for a single formatted batch, so this is out of
reach by more than an order of magnitude. The decode currently runs on a PC over
USB serial.

**Change.** A fixed-lag decoder (commit decisions N symbols behind rather than
at end-of-message) bounds memory to N states and would fit. This is the
difference between a tethered demonstration and an autonomous receiver.

## 9. Measurement infrastructure

Two known limitations in the existing tooling:

- **`run_ber_experiment.py` cannot measure repeated payloads.** It identifies a
  new message by text, so a beacon repeating one string is invisible to it —
  it reported 0% BER over 48 polls of perfectly good traffic. Either dedup on a
  sequence number, or use the daemon's `--csv`, which pairs each pass with the
  ground truth current at decode time.
- **Payload length confounds cross-experiment comparison.** Because symbol
  widths differ between ZERO and ONE, throughput and failure probability both
  depend on the payload. The offline suite averaged 153 symbols per message and
  the live beacon sends 234, which alone accounts for most of the apparent gap
  between the two (56.8% vs 41.9% predicted intact). Fix the payload length
  across experiments, or always report it.

## Descoped from the original proposal

Recorded for completeness; none of this was implemented or measured.

| Item | Status |
|---|---|
| Three frequency-multiplexed beacons (10/18/27 kHz) | Not built — one beacon, baseband, no carrier |
| Goertzel spectral pipeline at 100 kS/s | Not built — pulse-width detection in a MicroPython loop |
| Trilateration to <10 cm in 3 m x 3 m | Not attempted |
| Orientation estimation to <5 degrees | Not attempted — see item 7 |
| Q-learning for servo/orientation optimisation | Not attempted |
| BNO085 IMU ground truth | Not integrated |
| Operation over 0.5-3 m | Not tested; all data at 0.08-0.20 m |

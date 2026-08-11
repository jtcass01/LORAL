# LORAL Link Protocol

## Modulation

Baseband on–off keying of an infrared emitter (TSAL6200, switched by a 2N2222
stage from one GPIO). Information is carried in pulse *duration*, not amplitude
or carrier frequency — a pulse-width variant of the intensity-modulation /
direct-detection schemes surveyed by Kahn and Barry [1].

| Symbol | Width | Follows with |
|---|---|---|
| ZERO | 10 ms | 10 ms idle |
| ONE | 25 ms | 10 ms idle |
| START | 50 ms | 10 ms idle |

Time-domain encoding was the load-bearing decision. The measured amplitude
response is effectively flat — received peak varies under 1% between 3 and 8
inches, the front end being saturated across that range — so any
amplitude-discriminating scheme would fail. Pulse widths stayed separable by
43.7 pooled standard deviations at nominal timing.

## Framing

One START pulse per byte, then eight data bits, LSB first: nine pulses per
byte. Messages terminate with `\n`, then idle 500 ms and repeat, so a receiver
may join mid-stream and synchronise on the next START. The rest is 50× the
inter-symbol gap, keeping message boundaries unambiguous under symbol loss.

A byte occupies ~280 ms (~28.6 bit/s). Framing costs 21% of airtime. Because
ZERO and ONE differ in width, throughput is payload-dependent: 220 ms for an
all-zero byte against 340 ms for all-ones.

## Receiver

Two BPW34 photodiodes, each into an MCP6002 transimpedance stage (100 kΩ
feedback), sampled on separate ADC channels — an angle-diversity receiver in
the sense of Carruthers and Kahn [2], though here the second aperture serves
detection robustness rather than multipath rejection.

Each channel carries independent high-pass state,
`y[n] = (x[n] − x[n−1]) + αy[n−1]`, α = 0.85, so detection responds to
transitions rather than to the DC bias that dominates absolute level. Edges are
declared on the **sum** of both channels: off-axis, the far diode may barely
register, and a per-channel trigger would discard exactly the geometries of
interest.

The RP2350 multiplexes one SAR converter across both inputs, so charge from the
previous channel biases each reading toward the other. The first conversion
after each mux switch is discarded — negligible when reading one channel,
material when the measurement is a difference between two.

Threshold adaptation tracks a fixed margin below the strongest recent edge,
smoothed against single-pulse displacement, with a timeout that relaxes it when
no edge appears. No per-range tuning is required.

Serial output is buffered into preallocated arrays and flushed only during the
500 ms inter-message rest. Writing per pulse cost measurable symbols: the
sampling loop halts for the write, and at 4× rate a 1 ms write consumes 40% of
the inter-symbol gap. Buffering halved observed symbol loss (0.76% → 0.38%).

## Rate control

All four timing constants divide by a common multiplier, set over HTTP and
applied at message boundaries. 0.5×–8×, spanning 14.3–228.6 bit/s, but only
1×–6× were characterised. The ceiling is the sampling loop: fitting the
periodicity of 90,870 measured pulse widths gives a loop period of 262.4 µs
(3.81 kiloiterations/s, consistent to 0.5% across all five rates), so a ZERO
spans 38 iterations at 1×, 6.4 at 6×, and only 4.8 at 8×.

## Limitations

No parity, checksum, or retransmission. A duration outside every acceptance
window discards the whole partially assembled byte and resynchronises on the
next START. Since a byte spans nine pulses, a 1.4% symbol error rate becomes a
12% byte loss rate and near-certain corruption of a 25-character message. This
amplification — not symbol misclassification — bounds end-to-end reliability,
and motivates maximum-likelihood sequence detection over the protocol's
framing constraints [3] rather than better per-symbol classification.

## References

Citations below are given by author, title and venue; **verify volume, issue
and page numbers against the originals before submission.**

1. J. M. Kahn and J. R. Barry, "Wireless Infrared Communications,"
   *Proceedings of the IEEE*, 1997.
2. J. B. Carruthers and J. M. Kahn, "Angle Diversity for Nondirected Wireless
   Infrared Communication," *IEEE Transactions on Communications*, 2000.
3. G. D. Forney, "The Viterbi Algorithm," *Proceedings of the IEEE*, 1973.
4. F. R. Gfeller and U. Bapst, "Wireless In-House Data Communication via
   Diffuse Infrared Radiation," *Proceedings of the IEEE*, 1979.
5. IEEE Std 802.15.7, "Short-Range Wireless Optical Communication Using
   Visible Light," 2011. — OOK and variable pulse-position modulation as
   standardised; useful contrast with the pulse-width scheme used here.

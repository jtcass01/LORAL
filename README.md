# LORAD

An infrared optical link between two microcontrollers, and a sequence decoder
that recovers pulses the receiver never detected.

Symbols are encoded in pulse **width**, not amplitude. When the receiver misses
a pulse, the gap that follows is longer by that pulse's width plus one
inter-symbol gap — so the loss is not silent, and a Viterbi decoder over the
protocol's byte framing can reconstruct it. Measured on hardware, **100% of
single-omission transmissions carried that signature**, at every data rate
tested.

Coursework for Application of Sensing Systems, Johns Hopkins University.

## Result

Transmission success rate — the fraction of messages decoded with zero errors.

| | 28.6 bit/s | 114.3 bit/s |
|---|---|---|
| Threshold decoder (offline, held out) | 72.2% | 36.1% |
| Sequence decoder (offline, held out) | **100.0%** | **69.4%** |
| Threshold decoder (live, in the loop) | 69.2% | 5.9% |
| Sequence decoder (live, in the loop) | **100.0%** | **44.9%** |

No change to the transmitter, the receiver electronics, or the modulation. The
gain comes entirely from information the existing receiver already captured and
discarded.

## The finding behind it

The link does not fail by misclassifying symbols. Across 67,581 symbols at
28.6 bit/s there were **zero** substitution errors — pulse-width classes are
separated by 12–40 pooled standard deviations even at the highest rate. It
fails by *never detecting* some pulses at all: 0.371% of symbols at 114.3 bit/s,
which compounds over the nine pulses in a byte into a majority of transmissions
arriving corrupt.

A better per-symbol classifier cannot help, because the missing observation was
never produced. That is the whole argument for sequence decoding here.

## Layout

```
src/beacon/       transmitter firmware (MicroPython)
src/robot/        receiver firmware and device-side loggers
src/ml/           decoder, dataset generation, evaluation, plotting
src/analysis/     earlier BER tooling (superseded)
experiment_data/  captured data, one directory per paper subsection
tests/            15 standalone tests; python tests/run_all.py
tools/            paper checks and dev utilities
docs/IEEE Paper/  the manuscript
```

`experiment_data/README.md` maps every data file to the paper subsection it
backs.

## Reproducing

Requires Python 3 with `matplotlib`, `numpy`, `pyserial`. No hardware needed —
the captured data is in the repo.

```bash
pip install -r requirements.txt
```

```bash
python src/ml/evaluate_decoder.py
```

```bash
python src/ml/plot_paper.py --outdir "docs/IEEE Paper/figures"
```

```bash
python tests/run_all.py
```

Building the PDF needs a LaTeX toolchain; `docs/IEEE Paper/build.ps1` drives
MiKTeX on Windows without Perl.

## Hardware

Two Raspberry Pi Pico 2 W (RP2350). Transmitter: TSAL6200 emitter through a
2N2222 switch. Receiver: two BPW34 photodiodes, each into an MCP6002
transimpedance stage with 100 kΩ feedback, on ADC0/ADC1. The decoder runs on a
host PC — the Viterbi lattice does not fit in the receiver's memory, so the
device streams pulse observations over USB serial and receives decoded text
back.

## Limitations

Worth reading before drawing conclusions from any of the above.

- **The symbol loss is largely self-inflicted.** The detection loop runs at
  262.4 µs per iteration in MicroPython — roughly 3% of what the RP2350's ADC
  can sustain. Its high-pass filter's time constant is 1.61 ms, which is about
  one ZERO symbol at the highest rate tested. Loss is zero where symbols span
  ~6 time constants and appears as soon as they fall below ~3. A faster
  sampling path would likely remove much of the problem the decoder solves.
- **One operating point.** A single fixed geometry at ~0.1 m, ordinary indoor
  lighting, one hardware unit. Only the data rate was varied.
- **Small samples.** About 36 held-out transmissions per rate, giving ±15
  percentage point confidence intervals. The two decoders separate at four of
  five rates; at 171.4 bit/s the intervals overlap.
- **The lunar framing is motivation, not content.** No vacuum, thermal cycling,
  dust, or radiation testing. 28.6–171.4 bit/s is orders of magnitude below any
  operational proximity link.
- **The mechanism is not novel.** Symbol omission is a deletion channel, and
  hidden Markov models with deletion transitions are the established tool for
  it. The contribution here is empirical: a measurement that a pulse-width
  physical layer supplies, for free, the synchronization evidence that
  watermark codes insert redundancy to obtain.

## Paper

`docs/IEEE Paper/main.tex` — build with `build.ps1`, or any LaTeX toolchain.
Supporting notes in `docs/`: `protocol.md`, `decoder_model.md`, `future_work.md`.

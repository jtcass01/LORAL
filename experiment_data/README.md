# Experiment data

Captured data backing the paper, one directory per subsection of
**Section IV (Experiments)** in `docs/IEEE Paper/`. Directory letters match the
subsection letters in the compiled PDF, so a number in the paper can be traced
to the file it came from.

Every file here is raw capture output. Nothing in this tree is derived from
anything else in it.

| Dir | Paper subsection | File | Contents |
|---|---|---|---|
| `B_front_end_characterization/` | IV-B Front-End and Error-Mode Characterization | `comms_dataset_baseline.jsonl` | 437 transmissions at 28.6 bit/s, 67,581 symbols, every symbol detected. Backs the amplitude/saturation figures and the zero-substitution result. |
| `C_data_rate_sweep/` | IV-C Data-Rate Sweep | `comms_sweep_v6.jsonl` | 597 transmissions across 1×–6× (28.6–171.4 bit/s), ~91,000 pulses, buffered logger. **The paper's primary dataset**: every figure and table not otherwise attributed comes from this file. |
| `D_offline_decoder_evaluation/` | IV-D Offline Decoder Evaluation | `decoder_results.json` | Per-rate scores for both decoders on the held-out split. Derived from `C`, not a separate capture. |
| `E_live_closed_loop/` | IV-E Live Closed-Loop Operation | `live_decode.csv` | 39 live transmissions at 28.6 bit/s, decoder in the loop. |
| | | `live_decode_rate4.csv` | 118 live transmissions at 114.3 bit/s. |
| `F_instrumentation_validation/` | IV-F Instrumentation Validation | `comms_rate_sweep.jsonl` | 555 transmissions, **unbuffered** logger (one serial write per pulse). The "before" half of the dead-time comparison. |
| | | `comms_rate4_buffered.jsonl` | 120 transmissions at 114.3 bit/s, buffered. The rate-4 "after" measurement. |
| `not_cited/` | — | `comms_dataset.jsonl` | 38-transmission pilot capture at 28.6 bit/s. Superseded by `B`; no paper claim rests on it. |
| | | `ber_baseline.csv`, `ber_decoder.csv` | Output of `src/analysis/run_ber_experiment.py`, which cannot measure repeated payloads and reported 0% BER over 48 polls of good traffic (see `docs/future_work.md`). Kept as the record of that defect. |

## Two things worth knowing

**`D` is the one derived directory.** It captures nothing itself: it reads
`C_data_rate_sweep/comms_sweep_v6.jsonl`, holds out 30% of messages by payload,
and scores both decoders on the remainder. Delete it and it rebuilds; delete
anything else here and the measurement is gone.

**IV-A (Automated Data Collection) has no directory**: it describes the capture
method common to B, C and F rather than a separate experiment.

## Regenerating results from this data

Run from the repo root; the defaults already point into this tree.

```bash
python src/ml/evaluate_decoder.py --out-json experiment_data/D_offline_decoder_evaluation/decoder_results.json
```

```bash
python src/ml/plot_paper.py --outdir "docs/IEEE Paper/figures"
```

New captures from `generate_comms_dataset.py` still default to
`comms_dataset.jsonl` in the working directory; move them in here and record
which subsection they belong to.

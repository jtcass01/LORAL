# Tests

```bash
python tests/run_all.py          # everything
python tests/run_all.py robot    # one group
python tests/run_all.py -v       # show output from failures
```

No test framework. Each file is a standalone script that asserts its own
invariants and exits non-zero on failure. The runner sets the working directory
to the project root, so tests reference source as `src/robot` and datasets by
bare filename.

## tests/robot — device code, MicroPython stubbed

These import the real firmware classes with `uasyncio`, `machine` and `utime`
replaced by stubs, so device logic runs and is asserted on a PC. A simulated
ADC drives a virtual clock, which makes pulse timing exactly reproducible.

| Test | Guards against |
|---|---|
| `test_listen_for_pulse` | Swing recovery, immunity to a drifting DC pedestal, and correct behaviour when the front end's polarity is inverted |
| `test_prime_filter` | The stale-filter false trigger after a sampling pause. Without priming, the first sample after a gap reports a fabricated edge |
| `test_heartbeat` | A receiver hearing nothing must keep saying so. A single-shot idle callback went permanently quiet after one call — indistinguishable from a dead board |
| `test_flush_timing` | Serial writes must land in the inter-message rest, never in an inter-bit gap. At 6x rate that gap is 1.67 ms |
| `test_dual_diode` | `ndiff` is monotonic in bearing and the two-channel logger round-trips through the collector |

## tests/ml — PC-side pipeline

| Test | Guards against |
|---|---|
| `test_collector` | Collection end to end: text protocol into CSV, schema, dashboard rendering |
| `test_preflight` | Silent / alive-but-deaf / healthy / stale-firmware / crashed device states, and that collection aborts before prompting when the device is dead |
| `test_flush_labeling` | Stale buffered pulses must never be recorded under the wrong label |
| `test_flush_ablation` | Proves the flush fix is load-bearing: with it disabled, half the rows are mislabelled |
| `test_generator` | `expected_symbols` matches the beacon's `send_byte` exactly, including LSB-first ordering; pass splitting rejects mixed, partial and merged passes |
| `test_raw_dump` | The diagnostic names each failure mode correctly — silent port, device traceback, wrong program, stale binary firmware, current text protocol with and without a banner |
| `test_real_stream` | A real captured device stream parses, and the degenerate `dur=69` noise trigger is rejected |
| `test_noprompt` | `--no-prompt` never blocks on input; degenerate pulses dropped, long-but-usable ones kept |
| `test_dual_collect` | Dual-mode schema, units recorded, `ndiff` monotonic in the label |
| `test_decode_loop` | Closed loop: a captured pass decodes on the PC and the message reaches the device's log |

## Notes

Several tests encode bugs that were found the hard way and would be silent if
reintroduced — `test_heartbeat`, `test_flush_timing` and `test_flush_ablation`
in particular. They assert behaviour that is invisible in normal operation and
only shows up as degraded data.

`test_decode_loop` needs `experiment_data/C_data_rate_sweep/comms_sweep_v6.jsonl`; the runner skips rather than
fails it when the dataset is absent, since that is not a code defect.

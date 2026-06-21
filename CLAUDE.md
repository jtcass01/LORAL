# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## Authority Hierarchy

When sources conflict, follow the higher-ranked file:

1. **CLAUDE.md** (this file) — system truth and constraints
2. **[docs/system_overview.md](docs/system_overview.md)** — architecture details
3. **[docs/experiment_plan.md](docs/experiment_plan.md)** — experimental procedure
4. **[src/](src/)** — implementation
5. **[docs/IEEE Paper/](docs/IEEE%20Paper/)** — derived publication artifact, never authoritative

---

## Project Goal

LORAL (Lunar Optical Relay And Localization) investigates whether IR beacon networks can simultaneously provide reliable short-range OOK communication and robot pose estimation (position + orientation) in a controlled low-light lunar-like environment.

This is an **experimental systems research platform** running on Raspberry Pi Pico 2W hardware, not a simulation.

---

## Development Workflow

**Language:** MicroPython (Raspberry Pi Pico 2W target)

**IDE setup:** VS Code with the [Pico-W-Go extension](https://marketplace.visualstudio.com/items?itemName=paulober.pico-w-go) (`paulober.pico-w-go`). The `.micropico` file marks this as a Pico project. MicroPython stubs are configured in [.vscode/settings.json](.vscode/settings.json).

**Deploying to hardware:** Upload files via the Pico-W-Go extension (right-click → "Upload project to Pico") or use `mpremote` from the command line:
```
mpremote connect COM6 cp src/main.py :main.py
mpremote connect COM6 run src/main.py
```

**Running REPL:** `mpremote connect <PORT>` opens an interactive MicroPython REPL on the Pico.

There is no build step — MicroPython is interpreted. All firmware lives in [src/](src/).

---

## Hardware Platform

| Component | Part | Interface |
|-----------|------|-----------|
| MCU | Raspberry Pi Pico 2W | — |
| IR Receiver | BPW34 photodiode + MCP6002 op-amp | ADC (100 kHz) |
| IR Transmitter | TSAL6200 LED + 2N2222 transistor | GPIO → PWM carrier |
| Servo | SG90 | PWM (GPIO) |
| IMU | BNO085 | UART |

Power: 5 V 2 A rail for Pico/LED/motor; 3.3 V rail (from Pico) for sensors.

Circuit diagrams are in [circuits/](circuits/) as Fritzing `.fzz` files.

---

## Signal Processing Pipeline

```
IR Beacons (3 towers @ 10 kHz, 18 kHz, 27 kHz OOK)
    → BPW34 + MCP6002 (analog)
    → ADC @ 100 kHz (digital samples)
    → Goertzel algorithm (per-frequency amplitude)
    → Feature extraction (per-beacon signal strength vector)
    → Localization engine (x, y) + IMU fusion (θ)
    → Q-learning agent → Servo (SG90) orientation control
```

**Modulation:** On-Off Keying (OOK) — carrier present = `1`, carrier absent = `0`. Each beacon has a unique carrier frequency for frequency-division identification.

**Goertzel:** Preferred over FFT for embedded, single-frequency detection at known target frequencies.

---

## Firmware Constraints (src/)

- IMU (BNO085) is **orientation reference only** — never used for position estimation
- Servo scanning uses Q-learning to optimize signal reception direction

---

## Localization System

- **Primary:** Multi-beacon RSSI-like signal strength comparison + geometric triangulation in a 3 m × 3 m workspace
- **Outputs:** position `(x, y)` and orientation `θ`
- **No** GPS, LiDAR, SLAM, or ROS unless explicitly requested
- **No** black-box ML for localization — all methods must be physically interpretable and map to measurable data

---

## Q-Learning (Servo Optimization)

Used **only** to optimize sensing direction — not for localization.

- **State:** discretized servo angle + relative beacon signal strengths
- **Actions:** rotate left, rotate right, hold
- **Reward:** signal stability + beacon detection strength + packet success rate

---

## Experimental Data Requirements

Every experiment must log structured output in **CSV or JSON** with timestamps:

- ADC time series
- Decoded bitstreams
- Goertzel magnitude vectors
- Servo angle logs
- IMU orientation logs
- Localization estimates `(x, y, θ)`

---

## Performance Targets

| Metric | Target |
|--------|--------|
| Data rate | ≥ 10 kbps |
| Position error | < 10 cm |
| Orientation error | < 5° |
| BER | Minimize across 0.1–3 m range |

---

## IEEE Paper (docs/IEEE Paper/)

LaTeX sections go in [docs/IEEE Paper/sections/](docs/IEEE%20Paper/sections/). The paper is a **derived artifact**:
- Never redefine system architecture in LaTeX — reflect what the system actually does
- Figures must be generated only from logged experimental data
- Write sections in-place; do not create intermediate documents

---

## Design Philosophy

- Prefer signal processing over machine learning
- Keep models physically interpretable
- Prefer minimal working implementations; avoid speculative features
- Every method must tie to: signal model → experiment → metric

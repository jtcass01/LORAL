# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.
p
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

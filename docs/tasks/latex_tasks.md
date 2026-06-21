## Task 1 — Paper Consistency Review + Related Works Rewrite

### Files to modify
- docs/IEEE Paper/sections/01_introduction.tex
- docs/IEEE Paper/sections/02_related_work.tex
- docs/IEEE Paper/mybibfile.bib

---

## Objectives

Perform a **joint consistency pass across Introduction and Related Works** with the goal of eliminating redundancy, improving narrative flow, and enforcing IEEE-style separation of motivation vs literature review.

---

## 1. Consistency Review (Global Across Both Sections)

Audit both sections for:

- Duplicate explanations of:
  - FSO communication
  - optical localization methods
  - IM/DD / OOK concepts
  - photodiode-based sensing
  - embedded spectral processing (Goertzel/FFT)
- Repeated or inconsistent definitions of acronyms or system terms
- Terminology drift (e.g., IM/DD vs OOK vs intensity modulation used inconsistently)
- Any “system explanation” leaking into Related Works

### Hard Rule:
- **Each concept is defined/explained once in the paper**
  - Prefer Introduction for definitions
  - Related Works must NOT redefine anything already introduced

---

## 2. Introduction Refinement

Ensure the Introduction:

- Clearly defines the problem and gap
- Contains a single, consolidated definition of:
  - LORAL system
  - IR beacon concept
  - OOK + frequency multiplexing (only once)
- Ends with:
  - Explicit contribution statement
  - Clear paper roadmap (section outline)

### Important:
- Remove any literature-review-style expansion from the Introduction
- Keep citations minimal and only for motivation, not deep comparison

---

## 3. Related Works Rewrite

Rewrite `02_related_work.tex` as a **pure literature synthesis section**.

### Structure requirement:

Each subsection must:

- Begin with a **1–2 sentence framing paragraph**
  - explains what this subsection covers
  - does NOT repeat Introduction content

Example pattern:
> “This section reviews prior work in free-space optical communication systems relevant to long-range IM/DD links.”

Then subsections:

- Free-space optical communication (FSO / space systems)
- IR LED / VLC intensity-modulated communication
- Optical localization using RSSI / photodiodes
- Frequency-multiplexed optical beacon networks
- Embedded spectral detection (Goertzel vs FFT)

---

### Hard constraints for Related Works:

- Do NOT re-explain what LORAL is
- Do NOT restate system architecture
- Do NOT restate the research gap (it belongs in Introduction only)
- Do NOT introduce implementation details
- Focus only on:
  - prior art
  - established methods
  - what those systems achieve

---

## 4. Flow Optimization Between Sections

Ensure clean progression:

### Introduction ends with:
- Problem → gap → contribution → paper outline

### Related Works begins with:
- Neutral framing sentence (no overlap with Introduction)

### Conceptual flow across sections must follow:

FSO (space systems)
→ VLC / IR LED comms
→ optical localization (RSSI/AOA/TDOA)
→ frequency multiplexing
→ embedded spectral processing

---

## 5. Bibliography Update

Update `mybibfile.bib` only if needed:

- Ensure all cited works are correctly categorized
- Remove unused references
- Add missing canonical citations for:
  - LLCD / NASA optical comm systems
  - VLC surveys
  - Goertzel / narrowband detection references

---

## Constraints

- IEEE citation style only
- SLAM / LiDAR only as contrast (never focus)
- Keep emphasis on signal-processing-based optical systems
- Maintain physically grounded interpretation of all methods
- Avoid redundancy across all sections

---

## Output Expectation

After completion:
- Introduction reads as a **self-contained motivation + contribution section**
- Related Works reads as a **pure literature review with no system leakage**
- No concept is explained twice unless necessary for clarity
- Section transitions feel intentional and non-repetitive
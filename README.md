# TwinForge — Digital Twin for a Vehicle Assembly Line

**Accenture Innovation Challenge 2026 · Round 2 · Problem Statement 4**
Team TwinForge (IIT Madras)

A live digital twin of a fixed 9-station assembly network, presented as four
IDE-style workspaces:

- **Telemetry** — a Grafana-style dashboard of the running line: per-station
  expected-vs-actual throughput, cycle time, and *heterogeneous* sensors (some
  stations have temperature, some voltage, fastening stations have torque, manual
  stations are dark). Click any station to drill down. Amber ⚠ marks flag risk
  *before* failure. A case adjuster injects faults to simulate cases.
- **Prevent** — ML what-if. The forecast flags a station trending to constrain at
  T+5 *before it fails*; you perturb its telemetry (add operator / cool / throttle
  / service) and the **same model** is re-scored to see if the risk drops. Not a
  simulation.
- **Diagnose** — defect root-cause. A drifting tool makes out-of-spec units with
  **zero throughput change**, caught late at QA; the twin traces them back to the
  origin tool using genealogy × torque telemetry (a station on the bad path but
  with an in-spec tool is exonerated).
- **Resolve** — counterfactual re-simulation for a bottleneck that actually
  formed: the line is re-run under identical conditions, current vs simulated.

Two problems, honest split: locating a bottleneck is the arithmetic blocked/
starved walk (no ML); the ML lives in the **forecast** (prevent) and the
**defect backtrace** (diagnose). The twin advises; it never writes to line control.

---

## Run it

```powershell
./run_demo.ps1
```

Then open **http://localhost:8000**. One FastAPI process serves both the API and
the (dependency-free) web UI. Python **3.12**, deps in `requirements.txt`
(installed into the repo-root venv `E:\Code\AIC\.venv`).

To regenerate data + retrain the model from scratch:

```bash
python -m scripts.generate_and_train --regen
```

---

## What's inside

| Area | File | What it does |
|---|---|---|
| Topology | `src/twin/factory.py` | The fixed 9-station DAG (routes, buffers, tiers) — single source of truth. |
| Simulation | `src/twin/simulator.py` | Discrete-event sim over the DAG. Conservation-correct, CRN-seeded, no deadlocks. |
| Ground truth | `src/twin/ground_truth.py` | **Economic** bottleneck via paired counterfactual re-simulation (never an identity). |
| Detector | `src/twin/detector.py` | Current constraint by effective cycle time + blocked/starved signature. |
| Forecast | `src/twin/forecast.py` | Logistic regression predicting the constraint at **T+5 min**. |
| Diagnostic | `src/twin/diagnostic.py` | Backward-graph root cause: a posterior probability per upstream station. |
| Counterfactual | `src/twin/counterfactual.py` | Real paired re-simulation of an intervention (cars gained). |
| Live loop | `src/twin/loop.py` | Ingest → detect → forecast → emit, + twin-drift re-calibration. |
| Sparse sensing | `src/twin/observability.py` | Measured / inferred / unknown per station, with confidence. |
| Genealogy | `src/twin/genealogy.py` | Containment list: which vehicles were built on a drifting station. |
| API + UI | `src/api/main.py`, `web/` | FastAPI + SSE stream; vanilla-JS / inline-SVG front end. |

Design rationale and measured results: **[DESIGN.md](DESIGN.md)**.

## Headline numbers (from `data/processed/metrics.json`)

- Healthy line flows at **~291 UPH** (design takt 300), std **1.6** across 30 seeds.
- Paired-comparison **noise floor: ~1 car**.
- Detector **top-1 = 1.00**, regret **0.0 cars** vs **0.62** for utilisation ranking.
- Forecast holdout accuracy **92.5%** (predicting economic truth at T+5).
- Sparse-sensing: detector still finds the constraint on **74%** of faulted runs
  from the sensor-poor view.

<div align="center">

# TwinForge — A Live Digital Twin for Vehicle Assembly Lines

**Predictive · Diagnostic · Prescriptive manufacturing intelligence on a discrete-event twin**

Accenture Innovation Challenge 2026 · Round 2 · Problem Statement 4 — *DigitalTwin.ai*
Team **TwinForge** · IIT Madras

`Python 3.12` · `Discrete-Event Simulation` · `NumPy · SciPy` · `scikit-learn` · `FastAPI + Server-Sent Events` · `Zero-dependency JS/SVG UI`

</div>

---

## TL;DR

TwinForge is a **live digital twin** of a mixed-model vehicle assembly line that does three things a dashboard cannot: it **prevents** forming bottlenecks with a T+5-minute ML forecast, **diagnoses** late-surfacing defects by tracing them backward through a probabilistic root-cause graph, and **prescribes** fixes via **paired counterfactual re-simulation**. It runs on a **conservation-exact, Common-Random-Numbers discrete-event simulator** of a 9-station DAG with **heterogeneous, real-world sensor coverage** — some stations richly instrumented, some fully manual (dark) — exactly the "patchwork of legacy and modern equipment" the brief describes.

> **We do not mock a dashboard. We simulate a factory, use it as ground truth to train ML, and re-simulate every fix — end to end, with injectable cases.**

The design discipline is one line: **compute what you can, train only what you must.** Locating today's bottleneck is arithmetic; ML is reserved for the forecast and the defect backtrace, and both are trained against **economic ground truth**, never a self-referential statistic.

## Headline results (reproducible bit-for-bit from fixed seeds)

| Metric | Result | What it means |
|---|---:|---|
| Healthy throughput | **≈291 UPH** (σ 1.6 / 30 seeds) | Line flows at design takt; deadlock-free, low variance |
| Paired-comparison **noise floor** | **≈1 car** | Common Random Numbers make counterfactuals truly paired |
| Bottleneck detector **top-1** | **1.00** | Locates the constraint every time (arithmetic, zero training) |
| Detector **regret** | **0.0 cars** | Ranks by *effective cycle time*, not the "utilisation trap" |
| Forecast **holdout accuracy** (T+5) | **≈89%** | Predicts the *economic* constraint 5 min ahead — a real forecast, not an identity |
| **Sparse-sensing** agreement | **96%** | Still finds the constraint from the sensor-poor view |
| Defect **origin stations** | **6** | Weld / coat / torque / cell quality faults across the network |
| Throughput cost of a defect | **0 UPH** | Quality faults have *no flow signature* — caught late at QA |
| Training corpus | **128 simulated shifts · 6,400 windowed samples** | Split **by run** (5,150 train / 1,250 holdout) to prevent leakage |
| Invariant test suite | **10 / 10 passing** | Conservation, deadlock-freedom, CRN, regret |

*Numbers are model-generated on illustrative data, as Round 2 encourages; every figure regenerates with one command (`python -m scripts.generate_and_train --regen`).*

---

## The end-to-end pipeline (how it works)

TwinForge is a single pipeline you can read left to right. Nothing is hand-waved between the stages:

```
  PROBLEM (PS4)      FIX A FACTORY        SIMULATE IT          ECONOMIC             TRAIN ML
  see bottlenecks →  9-station DAG,   →   per-second DES,  →   GROUND TRUTH    →    90-feature
  predict defects    uneven sensors       CRN, back-pressure   paired counterfactual multinomial logit
                                                │                                        │
                          ┌─────────────────────┼───────────────────────┐                │
                          ▼                      ▼                       ▼                ▼
                     04 RESOLVE             03 DIAGNOSE               (feeds)         02 PREVENT
                counterfactual re-sim   backward probability graph                 ML forecast, re-scored
                (pure DES, no ML)       (genealogy × telemetry)                    on live telemetry
```

1. **Fix a factory.** A declarative 9-station DAG (`src/twin/factory.py`) with merges, splits, finite buffers and a per-station sensor list. Topology is **data**, not code — point the engines at another line by swapping the graph.
2. **Simulate it.** A conservation-exact discrete-event simulator (`src/twin/simulator.py`) runs the line one second at a time, with real back-pressure and physically-grounded sensor signals — *including the disruptions* (slowdowns, outages, overheating, tool wear, tool drift).
3. **Label it economically.** For every station we **re-run the same shift with that station sped up** and measure the cars actually gained (`src/twin/ground_truth.py`). That paired counterfactual is the label — the truth is *economic*, never the detector's own formula.
4. **Train ML on it.** A multinomial logistic regression (`src/twin/forecast.py`) learns to predict the **economic constraint five minutes ahead** from a 90-feature telemetry window.
5. **Act.** Three engines on one injectable case — **Prevent** (ML), **Resolve** (pure re-simulation), **Diagnose** (probability graph).

---

## The four workspaces

TwinForge is an **IDE-style single-page app**: four purpose-built workspaces over **one global, injectable Case** (a fault you inject once from the top ribbon — station, type, severity, complication). Each tab is its own view and toolset.

### `01 · Telemetry` — read the living line
A **Grafana-grade operational dashboard**: an animated process-flow DAG (state colour, WIP, buffer occupancy), a per-station **expected-vs-actual** table (throughput, cycle time), and **heterogeneous sensors** — each station exposes only the channels it physically has. **Click any node** to drill into its full flow + sensor record with **measured/inferred provenance**. An amber ⚠ pre-failure marker lights up *before* a station constrains. Streamed live over **Server-Sent Events**.

### `02 · Prevent` — forecast and fix, pre-failure
A **multinomial logistic-regression** model over a **90-feature vector** (9 stations × 10 channels) predicts the **economic constraint at T+5 minutes** from telemetry drift — *while the line is still healthy*. You test an intervention (add operator / cool machine / throttle release / service tool) and the **same model is re-scored on perturbed telemetry in milliseconds** — an **ML sensitivity analysis, not a simulation**. Fixes are **coherent by cause**: cooling only helps a genuinely overheating station (60% → 33%); servicing only helps a worn tool; interventions are **gated to the station's real sensors**.

### `03 · Diagnose` — trace a defect with no flow signature
A drifting tool produces **out-of-spec units with zero throughput change**, caught dozens of stations later at final QA — so the blocked/starved walk is **blind**. Diagnose builds a **backward probability graph**: candidates are the **producer plus downstream handlers** (a coating reject could be a bad application at Paint *or* a scuff at Marriage/Trim), fused from **genealogy lift × quality telemetry** into a **tempered posterior distributed over every candidate** (e.g. 82% / 10% / 8% — never a false 98%), then a **genealogy containment list** of every affected VIN.

### `04 · Resolve` — price the fix with a real counterfactual
When a bottleneck has actually formed, the line is **re-simulated under Common Random Numbers** with the fix applied — **current vs simulated on identical draws**, so **cars-gained is measured, not asserted** (de-bottleneck the true constraint → **+34 cars**; the wrong station → **+1**). The flow graph **visibly re-runs**. Options include the free levers — **"do nothing"** and **"reduce release rate" (CONWIP)**. This is **pure discrete-event simulation, decoupled from ML.**

---

## Architecture & the honest ML split

```
Discrete-Event Simulator ─▶ Feature windows + Economic ground truth ─▶ Engines ─▶ Live loop ─▶ FastAPI + SSE ─▶ Web UI
   (9-station DAG, CRN)        (paired counterfactual labels)        (4 below)   (twin drift)    (SSE stream)     (JS + SVG)
```

**We train where it helps and compute where it doesn't** — the design principle a mentored jury respects:

| Capability | Method | Trained? |
|---|---|:--:|
| Locate the current bottleneck | Effective cycle time (processing ÷ availability) + **blocked/starved boundary walk** | **No** — arithmetic |
| Economic ground truth | **Paired counterfactual** re-simulation (cars a speed-up actually yields) | **No** — measured |
| Forecast the T+5 constraint (Prevent) | **Multinomial logistic regression**, 90 features, class-balanced | **Yes** |
| Attribute a late defect (Diagnose) | **Genealogy lift × quality telemetry**, tempered softmax posterior | **No** — inference |
| Price a fix (Resolve) | **CRN paired discrete-event re-simulation** | **No** — simulation |

The forecast is trained against **economic ground truth** (which station's speed-up actually makes more cars), **never the detector's own statistic** — so a good score is a genuine forecast, not a self-fulfilling identity.

---

## Deep dive — the simulator (`src/twin/simulator.py`)

A discrete-event simulation stepped one second at a time. The same `Simulator` drives both the live loop (`.step()` + `.snapshot()`) and batch data generation (`.run()`).

- **Flow & back-pressure.** Units are injected at the three sources on a takt, assigned one of five routes, and flow along it. A station **starves** when its input buffers are empty and **blocks** when its downstream buffer is full (capacity 8), so constraints *propagate* rather than being absorbed. Stations are stepped **downstream-first** so a freed slot is usable upstream in the same tick.
- **Conservation is exact.** `injected = produced + in-line WIP + source backlog`, asserted in the test suite. A unit is created at a source and destroyed at the sink, never duplicated or lost.
- **Cycle time.** Sampled per unit as **lognormal(μ, σ = 0.08)**, with the noise term **fixed per (unit, station)** so a counterfactual perturbs *only* the intended lever (see CRN below). Effective cycle time reported to the detector is **work-only processing ÷ availability** — never wall-clock, which would double-count downtime.
- **Common Random Numbers (CRN).** Randomness is a deterministic function of *(unit, station)*, and background failures are **pre-scheduled per station** from an independent stream. So a speed-scale counterfactual does not reshuffle failures or draws — the comparison is *paired*, and the noise floor drops to ≈1 car.
- **Signals as ground truth.** Each station emits `motor_current`, `vibration`, `temperature` (plus `torque` on fastening stations, `voltage` on HV stations, and a quality index on quality stations). Crucially, a **mechanical slowdown moves cycle time only** — it does not heat a motor. Health signals rise **only from a complication** (overheating → temp; tool wear → vibration + current), and the complication's signal **leads the cycle-time drag** (rising to full over ~40% of the ramp) — which is exactly what makes it a **leading indicator** the forecast can act on early. A small **lagged thermal coupling** (≈180 s) carries heat downstream, the physical cascade the diagnostic recovers.

## Deep dive — economic ground truth (`src/twin/ground_truth.py`)

The single most important discipline in the project: **never label the bottleneck with the same statistic the detector computes** — that measures an identity, not a result (an earlier version scored a meaningless 95% doing exactly this). Here the truth is **economic**: the constraint is the station whose **speed-up (to 0.75× cycle time) actually produces more cars**, measured by re-running the same shift under CRN with that one station accelerated. A margin rule reports **NONE** when no station clears a few cars over the runner-up (the line is genuinely balanced). Only knowable offline, so it is used to **label** training data and **score** the detector — never inside the live loop.

## Deep dive — the forecast model (`src/twin/forecast.py`, `features.py`)

- **Input:** a per-station feature window flattened to a **90-vector** = 9 stations × 10 channels (`frac_working/starved/blocked/down`, `proc_time_mean`, `effective_ct`, `queue_in_mean`, `current_mean`, `vib_mean`, `temp_mean`) aggregated over a trailing **5-minute** window.
- **Model:** `StandardScaler → LogisticRegression(multinomial softmax, C=0.5, class_weight="balanced", max_iter=2000)` over classes `{NONE, S1…S9}`. Logistic regression on purpose — trained fast, calibrated, inspectable (per-feature weights), hard to attack in Q&A.
- **Target:** the **economic constraint at T+5 minutes**. Samples are split **by run** (whole shifts held out) so no window from a training run leaks into holdout.
- **What-if:** an intervention perturbs the telemetry features and **re-scores the same model** — a millisecond sensitivity analysis, gated so a fix can only pull a signal *toward* its healthy baseline (cooling an already-cool station is a no-op).

## Deep dive — backward-graph diagnosis (`src/twin/diagnostic.py`)

A QA reject has **no throughput signature**, so a flow-based detector is blind. And it does not point at one station — a coating fault could be a bad application at the producer *or* a downstream station that handled (and scuffed) it. So the candidate set for a defect **attribute** is the **producer plus its plausible handlers** (`factory.ATTRIBUTE_HANDLERS`), and probability is distributed from three signals:

```
posterior(k) ∝ prior(k) × (0.25 + genealogy_lift(k)) × quality_evidence(k) ,  then tempered by γ = 0.55
```

- **genealogy lift** — how over-represented candidate *k* is on the defective units' build paths vs all units (a handler off the bad units' path is cleared).
- **quality evidence** — how far *k*'s own quality channel is out of spec (strong for a producer; a handler with nominal tooling rests on prior + genealogy only).
- **tempering** (γ < 1) spreads the posterior so it reads like a real ranked worklist — **82% / 10% / 8%**, not a false 98% / 1% / 1%.

The engine also emits the **defect corridor** (edges the bad units travelled, for the graph to light) and a **containment list** — every VIN built on the most-likely origin since it drifted (e.g. 92 vehicles), the difference between holding a dozen cars and recalling a shift.

## Deep dive — counterfactual resolve (`src/twin/counterfactual.py`)

The intervention is applied to a **copy** of the current shift and run under CRN against the untouched baseline, so *cars gained* is a **paired measurement**, not an assertion. The menu deliberately includes **"do nothing"** and **"reduce release rate" (CONWIP)** — both frequently correct and neither an operator's instinct. Output is **Current vs Simulated** with a cumulative-cars curve.

## Sparse sensing & provenance (`src/twin/observability.py`)

The brief's hardest clause. Each station carries an instrumentation tier — **HIGH** (all channels measured), **MEDIUM** (flow scanned, internal signals inferred), **LOW** (only boundary scans; even state inferred from the neighbour signature — input empty → starved, output full → blocked — and internal signals left **UNKNOWN**, never hallucinated). Every value carries **provenance** (measured / inferred / unknown) and a confidence; a measured and an inferred value never look identical. From this degraded, sensor-poor view the detector still agrees with the full-sensor constraint on **96%** of faulted runs.

---

## Engineering honesty — bugs we caught (and what they taught)

Building the simulator honestly surfaced four defects, each with a lesson worth more than a green test:

1. **Resume-after-outage deadlock** — a station that failed mid-unit resumed as `starved` while still holding the unit, freezing forever. *A state machine must resume the work it was doing.*
2. **Blocked-latch deadlock** — a blocked station never retried its offload, so one background outage cascaded into a permanent whole-line freeze; healthy runs never blocked, so it hid until a random failure hit. Fixing it took healthy-run variance from **±61 → ±1.6 cars**. *Every blocked resource must keep retrying.*
3. **CRN desync** — a shared RNG meant changing one station's speed reshuffled every downstream draw, so a counterfactual on a *healthy* line appeared to gain **68 cars**. Making randomness a deterministic function of (unit, station) and pre-scheduling failures per station cut the **noise floor to ≈1 car**.
4. **Effective-CT double-counting** — recording wall-clock (which includes downtime) as processing time and *then* dividing by availability counted downtime twice, making an idle station read as a 275 s bottleneck. Recording work-only cycle time fixed the detector from **top-1 0.62 → 1.00**.

---

## What we simulate — faults, each mapped to a distinct signal and fix

| Fault | Moves | The fix that works |
|---|---|---|
| Gradual / sudden slowdown | cycle time only | add operator (capacity) |
| Outage (station down) | availability | planned stop / reroute |
| **Overheating** (complication) | temperature ↑ (leading), then cycle | **cool the machine** |
| **Tool wear** (complication) | vibration + current ↑, then cycle | **service the tool** |
| **Tool drift** (quality) | quality channel out of spec — **no flow change** | re-calibrate the tool |

A mechanical slowdown does **not** heat a motor — health signals rise only from a **complication**, and each complication is a **leading indicator** (signal first, slowdown second), which is exactly what lets Prevent act early *and* keeps the fixes physically coherent.

---

## Quickstart

```powershell
# Python 3.12; install deps into a venv
pip install -r requirements.txt

# launch the twin (single FastAPI process serves API + UI)
./run_demo.ps1        # then open http://localhost:8000
```

```bash
# regenerate the simulated corpus + retrain the forecast (bit-for-bit reproducible)
python -m scripts.generate_and_train --regen

# run the invariant test suite
python -m pytest -q
```

The whole UI is **one page of vanilla JavaScript + inline SVG** — **no Node build, no bundler** — so it runs anywhere the Python backend does.

### Try the flagship demo
1. **Telemetry** → *Edit case* → inject **S4 · gradual slowdown**, watch it develop.
2. **Prevent** → it's flagged at ~48% risk *before failure* → *Cool the machine* is a **no-op** (mechanical), *Add operator* **averts** it. Now add an **Overheating** complication → *Cool the machine* **averts** it. Coherent by cause.
3. **Resolve** → let it become a bottleneck → *de-bottleneck S4* → **+34 cars** vs doing nothing.
4. **Diagnose** → pick **S3 · coat drift** → *Trace back* → **S3 82% / S8 10% / S7 8%** with the defect corridor lit and a containment list.

## Repository structure

```
src/twin/        factory.py · simulator.py · ground_truth.py · detector.py · forecast.py · features.py
                 diagnostic.py · counterfactual.py · loop.py · observability.py · genealogy.py
src/api/main.py  FastAPI: REST + Server-Sent-Events stream + static serving
web/             index.html · app.js · factory.js · styles.css   (vanilla JS + SVG)
scripts/         generate_and_train.py (data + model) · build_pitch.py (deck)
data/processed/  trained model + baselines + layout + metrics (runs out of the box)
tests/           test_twin.py — 10 invariant tests
TwinForge_Pitch.pptx   the pitch deck
```

## How it maps to Problem Statement 4

| PS4 requirement | TwinForge answer |
|---|---|
| See bottlenecks forming | Blocked/starved walk + **T+5 forecast** (Telemetry ⚠ + Prevent) |
| Predict defects before they surface | **Quality-channel drift detection** + backward attribution (Diagnose) |
| Uneven / no sensor coverage | **Heterogeneous sensors** + neighbour inference with **provenance & confidence** |
| Multi-causal, intermittent roots | **Distributed posterior** over producer + handler candidates |
| Defect uncaught for many units | **Genealogy containment** — every VIN built on the suspect since drift |
| No live-control modification | The twin **advises; it never writes** set-points to PLCs |
| Three stakeholder views | Supervisor (real-time) · Manager (forecast/trends) · Leadership (ROI) |
| Validate vs outcomes / trust | **Economic ground truth**, **regret**, **twin-drift** re-calibration |
| Scale across lines & vintages | Topology is **data**; engines are **graph algorithms** |

## What we do **not** claim

- We did not invent the primitives — effective-cycle-time / active-period ranking (Roser et al.), Theory of Constraints (Goldratt 1984), CUSUM-style drift, and the model/shadow/twin taxonomy (Kritzinger et al. 2018) are established; naming them correctly is what makes the rest credible.
- The twin **advises, never writes** to line control — a safety-certified change no plant grants a prototype.
- Sparse-station internal readings are **estimated, not measured**, and shown as such — we never hallucinate a sensor value.
- Numbers are from a simulator; they demonstrate the mechanism on illustrative data, exactly what Round 2 asks for.

## Roadmap

- **Scale** to 30–50 stations across body / paint / final with segment-varying sensor coverage.
- **Costed sensor-retrofit optimiser** — where to add k sensors for maximum observability, phased by maintenance window.
- **Failure-mode classifier** (wear vs sensor-drift vs material-lot) on scale-invariant ratios.
- **MLOps** — drift monitoring, held-out recalibration, audit-grade replay of the decision surface.

---

<div align="center">

**Simulate · Forecast · Trace · Resolve.**
Team TwinForge — IIT Madras

</div>

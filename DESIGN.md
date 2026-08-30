# TwinForge — Design Document

**Accenture Innovation Challenge 2026 · Round 2 · Problem Statement 4 — DigitalTwin.ai**
Team TwinForge (IIT Madras)

This is the source of truth for the prototype: what it models, how each engine
works, the numbers it produces, and the boundaries it will not cross. Every
quantitative claim here is either **measured** (with the code that produces it)
or **cited** from established literature. Nothing is asserted.

---

## 0. The product in one page (what is actually built)

TwinForge is a factory-line **simulator** with an AI layer, presented as four IDE
workspaces. A single **Case** (a fault you inject from the top ribbon) is the
simulation everything runs against.

**The logical flow (the story the demo tells):**

1. **Telemetry** — the line runs the active Case. A Grafana-style dashboard shows
   per-station expected-vs-actual throughput/cycle-time and *heterogeneous*
   sensors (some stations have temperature, some voltage, fastening stations have
   torque, manual stations are dark). Click any node/row to drill down. As a flow
   fault ramps, an amber ⚠ appears on the at-risk station **before** it constrains,
   and the **Prevent** tab lights up.
2. **Prevent** — *ML, pre-failure.* The forecast flags the station trending to
   constrain at T+5 (e.g. "risk 48% at ~min 9, not failed yet"). You test an
   action (add operator / cool machine / throttle / service) — the telemetry is
   perturbed and the **same model is re-scored**; cooling the machine drops the
   risk 48%→0%. **This is ML sensitivity, not a simulation.**
3. **Resolve** — *pure simulation, post-failure.* If prevention was missed and the
   bottleneck actually formed, re-simulate the line with a fix under identical
   draws: current vs simulated, cars gained (fix the real constraint → +34 cars;
   fix the wrong station → ~0). The flow graph animates the fixed line. **No ML.**
4. **Diagnose** — *detached, for defects.* A drifting tool makes out-of-spec units
   with **zero throughput change**, caught late at QA. Pick a defect case and
   "Trace back": the engine builds a **probability graph** — stations + connectors,
   no flow, a posterior **on every node** and the defect corridor lit — from
   genealogy × quality telemetry. A station on the corridor but in spec is
   exonerated (S4 drops to ~2% when S7's tool is the one drifting). Plus the
   containment list of affected VINs.

Two "what-if" engines, never conflated: **Prevent = perturb telemetry → model**
(ML); **Resolve = re-simulate the line** (no ML). Locating a bottleneck at all is
the arithmetic blocked/starved walk — no ML there either. The ML lives exactly
where it earns its place: the T+5 forecast (Prevent) and the defect backtrace
(Diagnose).

### Coherence rules that make the story hang together

- **A mechanical slowdown moves cycle time only** — it does not, by itself, heat
  a motor or shake a tool. Health signals (temperature, vibration, current) rise
  only from a **complication** — a sensor-specific cause layered on the case
  (overheating needs a temp probe, tool-wear needs a vibration/current probe).
  The complication's signal is a **leading indicator**: it rises early, then the
  cycle-time drag follows, so the forecast can act before the constraint forms.
- **An intervention only helps the fault it targets.** In Prevent, an action can
  pull a signal *toward its healthy baseline, never past it*, and only if that
  signal is genuinely elevated. So **cooling an overheating machine averts the
  risk; cooling a machine that is merely busy does nothing** — and cooling is not
  even offered on a station with no temperature sensor. Adding an operator (more
  capacity) always helps a constraint; a health fix helps only when its
  complication is present.
- **A defect points at more than one station.** A coating reject could be a bad
  application at S3 *or* a downstream station that scuffed it, so Diagnose scores
  the **producer plus the plausible handlers** and returns a *distributed*,
  tempered posterior (e.g. 64% / 22% / 14%), never a false 98%.

---

## 1. What the problem asks, and our answer

The brief asks for a *live* digital twin of a vehicle assembly line that shows
where bottlenecks are forming and predicts defects before they happen — working
with **uneven sensor coverage**, across **multi-causal** faults, **without
touching live line control**, surfacing a **late-inspection containment**
problem, serving **three stakeholders**, and validated against real outcomes so
false alarms don't erode trust.

TwinForge answers it as **two coupled intelligence problems on one live twin**:

| Brief clause | TwinForge answer |
|---|---|
| Bottleneck ripples downstream | **Prevent** — effective-cycle-time detector + T+5 forecast over the process graph |
| Predict problems before they surface | **Diagnose** — backward-graph root cause with a probability per station |
| Move from visualising to predicting | **Prescribe** — paired counterfactual re-simulation of interventions |
| Uneven / no sensor coverage | Observability tiers + neighbour inference, every value tagged measured/inferred |
| Defect uncaught for many vehicles | Genealogy containment list from the unit build record |
| Three stakeholders | Supervisor / Plant Manager / Leadership framings of one record stream |
| Validated, trust-preserving | Economic ground truth, held-out forecast, regret, twin-drift re-calibration |
| No live-control modification | The twin **advises**; it never writes setpoints |

### Positioning

We are the only one of the three PS4 entries with a **running, end-to-end**
system: a real discrete-event simulator, a trained model against economic truth,
a live loop, and an interactive UI. Our design discipline is deliberately
borrowed from the strongest ideas in the field — and we say so, because an
unattributed rediscovery reads worse than a cited one.

---

## 2. The fixed factory (topology)

`src/twin/factory.py` defines one fixed **9-station DAG** — deliberately not a
straight line, so that merges and splits make bottleneck propagation (and the
backward causal graph) worth drawing.

```
 S1 Body Framing ─┬─▶ S4 Powertrain ─┐
                  │                   ├─▶ S7 Marriage ─┐
 S2 Chassis Prep ─┘                   │                 ├─▶ S9 Final/QA ─▶ OUT
 S3 Paint Shop ─┬─▶ S5 Interior ──────┼─▶ S8 Trim ──────┘
                └─▶ S6 Battery/HV ─────┘
```

Edges (finite-capacity buffers): `S1→S4, S1→S5, S2→S4, S3→S5, S3→S6, S4→S7,
S6→S7, S5→S8, S7→S9, S8→S9`. Five source→sink routes, each length 4, so every
unit is processed by exactly four stations and the line stays balanced.

**Load** (routes through a station) drives which station is the natural
constraint: `S9=5` (all flow), `S7=3`, most `=2`, `S2` and `S6 =1`. Cycle times
are tuned so that at the design takt (one unit / 12 s → 300 UPH) **every station
runs below capacity** — the healthy line flows — yet a single fault can push one
station over its own limit and create a real, propagating bottleneck. S9 is the
busiest by raw utilisation (0.83), which is exactly the **utilisation trap** the
detector must not fall for.

Stations also carry an **instrumentation tier** (HIGH / MEDIUM / LOW) used by the
sparse-sensing engine (§8).

---

## 3. The simulator

`src/twin/simulator.py` is a discrete-event simulation stepped one second at a
time. The same `Simulator` drives both the live loop (`.step()` + `.snapshot()`)
and batch data generation (`.run()`).

- **Flow & back-pressure.** Units are injected at the sources on a takt, assigned
  a route, and flow along it. A station **starves** when its inputs are empty and
  **blocks** when its downstream buffer is full — so constraints propagate.
- **Conservation is exact.** Injected = produced + in-line WIP + source backlog,
  verified in tests. A unit is created at a source and destroyed at the sink,
  never duplicated or lost.
- **Faults.** `degrade_ramp`, `degrade_step`, `station_down`, applied to a chosen
  station, plus rare pre-scheduled background failures (MTBF/MTTR) that give the
  availability term meaning.
- **Signals.** Each station emits `motor_current`, `vibration`, `temperature`
  that respond to its own state and fault, plus a small **lagged thermal coupling
  from upstream** — the physical ground truth the diagnostic recovers.

### Bugs we caught (and what they taught us)

Building the simulator honestly surfaced four defects, each with a lesson:

1. **Resume-after-outage deadlock** — a station that failed mid-unit resumed as
   `starved` while still holding the unit, freezing forever. *A state machine
   must resume the work it was doing.*
2. **Blocked-latch deadlock** — a blocked station never retried its offload, so a
   single outage cascaded into a permanent, whole-line freeze. Healthy runs never
   blocked, so it hid until a background failure hit. *Every blocked resource must
   keep retrying.* Fixing this took healthy-run variance from **±61 → ±1.6 cars**.
3. **CRN desync** — a shared RNG meant changing one station's speed reshuffled
   every downstream random draw, so a counterfactual on a *healthy* line appeared
   to gain **68 cars**. Making randomness a deterministic function of (unit,
   station) and pre-scheduling failures per station cut the **noise floor to ~1
   car**. (This is exactly the desync IIT KGP flagged in their own code.)
4. **Effective-CT double-counting** — recording wall-clock (which includes
   downtime) as processing time, *then* dividing by availability, counted downtime
   twice and made an idle station read as a 275 s bottleneck. Recording work-only
   cycle time fixed the detector from **top-1 0.62 → 1.00**.

---

## 4. Economic ground truth (the anti-identity rule)

`src/twin/ground_truth.py`. The most important discipline in the whole project:
**never label the bottleneck with the same statistic the detector computes.** The
old prototype labelled it with a formula and then trained a model to predict that
formula, scoring a meaningless 95% — an identity, not a measurement.

Here the truth is **economic**: the constraint is the station whose speed-up
actually produces more cars, measured by re-running the exact same shift under
Common Random Numbers with that one station sped up (a paired counterfactual).
This is only knowable offline, so it is used to **label** training data and to
**score** the detector — never inside the live loop.

The **noise floor** (apparent cars gained by speeding a station on a *healthy*
line) is ~1 car, so any economic gain above a few cars is real signal.

---

## 5. Prevent — detector + forecast

**Current constraint (arithmetic, no training)** — `src/twin/detector.py`.
Ranks stations by **effective cycle time** (`processing ÷ availability`) weighted
by the **blocked/starved signature**: the constraint is pinned working (rarely
starved) while its upstream blocks and downstream starves. Below a margin the
line is reported **balanced (NONE)**, matching economic truth. Utilisation is
kept only as a contrast the demo shows being wrong.

> Measured: detector **top-1 = 1.00**, **regret = 0.0 cars** vs **0.62** for
> utilisation, on held-out faulted runs.

**Forecast (trained)** — `src/twin/forecast.py`. A multinomial **logistic
regression** over the rolling-window telemetry predicts the economic constraint
**T+5 minutes** ahead (classes: NONE + the nine stations). Logistic regression is
chosen on purpose: easily trained, calibrated, inspectable, and hard to attack in
Q&A. Because it is trained against economic truth (not the detector's formula), a
good score is a real forecast.

> Measured: forecast **holdout accuracy 92.5%** (88 runs, ~4,400 windowed
> samples, split by run to avoid leakage).

---

## 6. Diagnose — backward-graph root cause

`src/twin/diagnostic.py`. Given a symptom (an anomalous signal at a station), the
engine walks **backward through the process DAG** and assigns every upstream
station — and the symptom station itself — a probability of being the root cause:

```
posterior(k) ∝ prior(k) × anomaly(k) × propagation(k → symptom)
```

- **anomaly(k)** — how far k's own signals sit from its healthy baseline
  (`data/processed/baselines.json`), capped so one near-deterministic channel
  can't dominate, and reported to the operator as **% deviation** (readable).
- **propagation(k)** — is there a plausible, corroborated path k → symptom: a
  proximity decay times the fraction of the path that is *also* anomalous (a real
  cascade lights up the whole chain).

The posteriors are normalised over candidates, so the UI draws the backward graph
with a probability on **each node** and lights the carrying edges — the root cause
can be the symptom station itself (a local fault). The engine also emits ranked
hypotheses with evidence and a recommended action.

> Example: symptom "S9 temperature", scenario S7-degrading → root cause **S7
> (66–74%)**, then S4, then S6, with the S4→S7→S9 path lit.

---

## 7. Prescribe — counterfactual intervention

`src/twin/counterfactual.py`. The old prototype faked physics
(`temperature *= old/new`). This one **re-simulates**: the chosen action is
applied to a copy of the current shift and run under Common Random Numbers
against the untouched baseline, so *cars gained* is a paired measurement. The
action menu deliberately includes **"do nothing"** and **"reduce release rate"**
(CONWIP) — both frequently correct and neither an operator's instinct.

> Example: de-bottleneck S7 → **+52 cars**; the same action on the non-constraint
> S6 → **0 cars** ("an hour saved at a non-bottleneck is a mirage", Goldratt 1984
> — emergent here from the topology).

The UI shows this as **Current vs Simulated** side by side, with a cumulative-cars
chart.

---

## 8. The live loop & twin drift

`src/twin/loop.py`. What makes it a *twin* and not a report: **ingest → update →
detect → forecast → emit**, on a timer, with the *same* code path for a live feed
and a replay. It also carries **twin drift** — the divergence between the
throughput the twin expects and what it observes over a trailing window. When
drift exceeds threshold the twin **re-calibrates** (re-fits its expectation) and
logs the event — the difference between a twin that tracks the line and a
snapshot that quietly goes stale.

The loop streams to the browser over **Server-Sent Events**; the frontend
animates each snapshot.

---

## 9. Sparse sensing & provenance

`src/twin/observability.py`. The brief's hardest clause. Each station has a tier:

- **HIGH** — every channel measured.
- **MEDIUM** — flow (state, queue, cycle) scanned; internal signals (temp,
  current, vibration) inferred at reduced confidence.
- **LOW** — only boundary scans; even the station's state is inferred from the
  **neighbour signature** (input empty → starved; output full → blocked), and
  internal signals are left **UNKNOWN** rather than hallucinated.

Every value carries **provenance** (measured / inferred / unknown) and a
**confidence**; a measured and an inferred value never look identical. The
detector then runs on this degraded view.

> Measured: from the sensor-poor view the detector still agrees with the
> full-sensor constraint on **74%** of faulted runs — graceful degradation, and
> an honest number (not 100%, because dark stations genuinely lose information).

The observability map (measured/inferred/unknown per station + confidence) is a
first-class output, surfaced in the Leadership view and drives a future costed
sensor-retrofit recommendation.

---

## 10. Genealogy & containment

`src/twin/genealogy.py`. A defect introduced early surfaces late, by which time
many downstream units carry it. Every unit carries its full station path
(recorded by the simulator), so once the diagnostic names a drifting station and
when it started, genealogy returns the exact **containment list** — which
vehicles were built on that station since the drift — to hold and inspect.

> Example: **112 vehicles** built on S7 since drift onset (VINs listed).

---

## 11. Three stakeholder views

One record stream, three framings (in Monitor's **Audience** control):

- **Floor Supervisor** — real-time: what is constraining now, the ranked
  candidates, and a jump to Diagnose.
- **Plant Manager** — the T+5 forecast and twin-health / drift.
- **Leadership** — throughput, the observability map (retrofit case), and a
  pointer to Simulate for cars-valued ROI.

---

## 12. Front end

Dependency-free (no Node): vanilla JS + inline SVG served by FastAPI, so it runs
anywhere the Python backend does. A dark, high-contrast, Apple-restrained design —
one accent, semantic colour only on station states and severity. Three clear
modes over one shared factory graph:

- **Monitor** streams the live line and annotates the constraint + forecast.
- **Diagnose** animates the backward trace across the graph edges, revealing a
  probability on each node as the reasoning flows.
- **Simulate** re-runs the shift and shows Current vs Simulated.

---

## 13. Measured results (`data/processed/metrics.json`)

| Metric | Value |
|---|---|
| Healthy throughput | ~291 UPH (design 300), std 1.6 over 30 seeds |
| Paired-comparison noise floor | ~1 car |
| Detector top-1 / regret | **1.00 / 0.0 cars** (utilisation regret 0.62) |
| Forecast holdout accuracy (T+5) | **92.5%** |
| Sparse-sensing agreement | **74%** of faulted runs |

All reproducible bit-for-bit from fixed seeds via
`python -m scripts.generate_and_train --regen`.

---

## 14. What we do **not** claim

- We did not invent the primitives. Effective-cycle-time / active-period ranking
  (Roser et al.), Theory of Constraints (Goldratt 1984), CUSUM-style drift, and
  the model/shadow/twin taxonomy (Kritzinger et al. 2018) are established; naming
  them correctly is what makes the rest credible.
- The twin **advises, never writes** to line control — a safety-certified change
  no plant grants a prototype.
- Sparse-station internal readings are **estimated, not measured**, and shown as
  such; we do not hallucinate a sensor value.
- Numbers are from a simulator; they demonstrate the mechanism on illustrative
  data, exactly what Round 2 asks for.

---

## 15. Scale & roadmap

The prototype is 9 stations for legibility; nothing in the engines assumes that.
The topology is data (`factory.py`), the detector and diagnostic are graph
algorithms, and the forecast is a flat feature vector per station — all scale to
the brief's **30–50 stations across body / paint / final**. Next steps: expand to
three segments with segment-varying sensor coverage, add the costed sensor-retrofit
optimiser (Tier D), and a defect-mode classifier on scale-invariant ratios.

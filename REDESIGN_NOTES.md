# TwinForge — Redesign Notes (thinking step, v2)

> No code has been changed. This is the shared thinking doc: what exists today,
> where it diverges from the intended vision, and the concrete changes proposed
> before we touch anything. Decisions still open are at the end.

---

## 0. TL;DR — the one reframe that changes everything

The current build treats a **scenario as a fault that has already bitten**, and
**Monitor as a constraint alarm** ("S4 IS THE CONSTRAINT", red, now). The vision
is the inverse:

- **Monitor is the live telemetry twin of a *running, not-failed* line.** You read
  the fundamentals per station and click a node for detail.
- **Prevention is a light overlay on that healthy line** — subtle "might fail"
  halos from the telemetry ensemble, *before* anything fails. Click → why.
- **Diagnosis is reactive** — a symptom is observed (a station's output/throughput
  dropping) and we build the causal graph **backward** to assign blame
  probabilities.
- **Scenario is the sandbox** — an explicit tab to configure the *case* (inject a
  failure, set its severity) and confirm it, not a hidden dropdown.

Everything below follows from that.

---

## 1. Current system (what exists today)

### Backend — solid, keep as-is
Discrete-event simulator over the fixed 9-station DAG (conservation-exact,
CRN-seeded, no deadlocks); economic ground truth via paired counterfactual;
effective-cycle-time detector; logistic **forecast** of the constraint at T+5;
**backward-graph diagnostic** (posterior per station); **counterfactual**
re-simulation; **live loop** + twin drift; **observability** (measured/inferred);
**genealogy** containment. Metrics: healthy ~291 UPH (std 1.6), noise floor ~1
car, detector top-1 1.00 / regret 0.0, forecast holdout 92.5%, sparse agreement
74%. **The engines already support the vision below — the gap is presentation.**

### Frontend — the part being reworked
Three modes over one shared graph:
- **Monitor** — live stream; shows the *current* constraint (red pulse) + a
  forecast bar list + an Audience toggle (Supervisor/Manager/Leadership).
- **Diagnose** — pick symptom station+signal → animated backward trace + ranked
  hypotheses.
- **Simulate** — pick an intervention → Current vs Simulated re-simulation.
- **Scenario** — a dropdown in the header (healthy / S4 degrading / …).

---

## 2. The gap — where today diverges from the vision

| # | Vision | Today | Gap |
|---|---|---|---|
| A | Monitor = telemetry dashboard; click a station for input/output/expected/temp | Nodes show only a WIP badge; panel shows a constraint alarm | You can't read anything off the dashboard; no per-station detail |
| B | Prevention = subtle pre-failure risk overlay on a healthy line; click ⚠ → prediction + drivers | The "constraint now" fires red even mid-ramp / on healthy lines; forecast is a bare bar list | Reactive framing, not preventive; no "at-risk vs failed" distinction; no per-station "why" |
| C | Diagnose = observe a symptom (e.g. S7 output dropping) → backward probability graph | Backward trace works but is only in the side panel; can't inspect nodes; symptom = raw signal dropdown | Present but not legible; missing "throughput dropping" as a first-class symptom |
| D | Scenario = a tab; configure the case (which station fails, severity) + confirm | Header dropdown of fixed presets | Confusing; hides that this is a *simulation of cases*; no severity control |
| E | Healthy line must look healthy | "Healthy line" shows S4 red "constraint, margin 11%" | Bug — undermines the whole preventive story |
| F | — | Leadership → observability map stuck "Loading…" | Bug — race in the render |

---

## 3. Proposed changes (concrete, still no code)

### 3.1 Information architecture
Top nav becomes four clear destinations:

```
  SCENARIO   |   MONITOR (telemetry + prevent)   |   DIAGNOSE   |   SIMULATE
```

- **Scenario** — opens a config panel (see 3.5). This is where a "case" is set up.
- **Monitor** — the telemetry twin, with a **Prevention** toggle (see 3.3).
- **Diagnose** / **Simulate** — as today, but clearer (3.4).

(Audience — Supervisor/Manager/Leadership — stays as a small control inside
Monitor; it reframes the same telemetry, satisfying the brief's 3-view clause.)

### 3.2 Station detail popup — the fundamentals (applies everywhere)
Clicking any station node opens a compact popup / side card:

- **Row 1 (flow):** current input rate · current output · **expected output** ·
  cycle time (vs nominal) · state.
- **Row 2 (telemetry):** temperature, motor current, vibration, WIP/queue — each
  vs its healthy baseline, with a small sparkline if cheap.
- **Row 3 (provenance):** measured / inferred / unknown + confidence (ties in the
  sparse-sensing story).
- If the station is flagged by Prevention or Diagnosis, the popup also shows the
  **risk/why** block.

All of this is already in the live snapshot + `baselines.json`; likely just one
small `/api/station` (or reuse the streamed state) — no new modelling.

### 3.3 Prevention as a pre-failure overlay (the big one)
- Monitor shows the line in a **neutral/healthy** palette by default.
- A **"Prevention"** toggle (filter/button) overlays **risk halos**: stations the
  forecast/ensemble flags as *trending toward* becoming a constraint — an **amber
  ⚠**, sized by risk, appearing *before* the station is the actual constraint.
- Three distinct visual states, never conflated:
  - **Healthy** (green/neutral) — nothing to do.
  - **At risk** (amber ⚠, pre-failure) — prevention: "may constrain in ~T+5".
  - **Constraining now** (red) — reactive fact, high margin only.
- Click the ⚠ → the station popup's **risk block**: predicted risk %, horizon,
  and the **driving signals** ("temperature +18%, cycle time +9%, inflow rising")
  — i.e. *what* the model is reacting to. Realistically the plant hasn't failed;
  this is the preventive value.

### 3.4 Diagnose — make the backward graph legible
- Add **"Throughput / output dropping"** as a first-class symptom (your "new input
  variable"): pick a station whose *output is falling* and trace back.
- Keep the thick animated backward edges (you like them), but:
  - put the probability **on the node** *and* echo it in the popup on click;
  - clearly mark **symptom node** vs **root-cause node** vs candidates;
  - the ranked hypotheses stay in the panel but link to node popups.
- Message stays honest: root cause can be the symptom station itself.

### 3.5 Scenario builder (the sandbox)
A dedicated Scenario tab/panel:
- Pick a station, a **failure type** (gradual degrade / step / outage), and a
  **severity slider**, and an onset.
- A **"Run this case"** confirm button that applies it and returns to Monitor,
  with a clear banner: "Case active: S4 gradual degrade, +90%, from min 5."
- A **"Healthy line"** default (no case) that reads genuinely healthy.
- Optionally: preset cases as one-click chips, but always visible/editable.

### 3.6 Bugs to fix alongside
- **Healthy line shows a red constraint** — raise the "balanced" margin threshold
  and/or only show **red** when the detector is confident *and* a case is active;
  a healthy line should read healthy (green, "balanced").
- **Observability map "Loading…"** — fix the render race so the Leadership map
  populates.

---

## 4. What explicitly stays

- All backend engines and the measured numbers.
- The shared factory-graph canvas, the thick trace lines, the dark high-contrast
  look, the live SSE animation.
- Diagnose and Simulate mechanics (backward graph; Current vs Simulated).

---

## 5. Traceability — this maps to the PS and our deck

- **PS4 Round 2** asks for: *see where bottlenecks are forming* (→ Prevention
  overlay, pre-failure), *predict problems before they happen* (→ forecast risk),
  *uneven sensor coverage* (→ station popup provenance + observability),
  *late-inspection containment* (→ genealogy from a diagnosed station), *three
  stakeholder views* (→ Audience), *validated / trust* (→ economic truth, twin
  drift). The reframe makes each clause **visible**, not just implemented.
- **Our deck ("Predict + Diagnose + Prescribe")**: Prevention = the Forecast
  Engine, Diagnose = the Diagnostic Engine's backward dependency graph, Simulate =
  the Counterfactual layer. The redesign realigns the UI to the deck's own three
  engines instead of a generic "constraint dashboard".
- **IIT KGP's decision-surface / alert-contract idea** supports the station popup:
  every surfaced value should carry provenance + confidence, and an alert should
  state its evidence and the cost of not acting — that's exactly the popup's
  risk/why block.

---

## 6. Open questions — decide before we build

1. **Prevention placement:** a toggle/overlay *inside* Monitor (my recommendation),
   or its own top-level tab? You leaned toward "a small filter/button on the
   telemetry dashboard" → overlay.
2. **Station detail:** popup over the node, or a docked side-card that updates on
   click? (Popup feels lighter; side-card holds more.)
3. **Scenario severity:** expose full control (station + type + severity + onset),
   or a simpler "pick a station + how bad" with sensible defaults?
4. **Healthy-line behaviour:** should Monitor ever show a red "constraint now" when
   no case is active, or only ever amber "at risk" until a case is running?
5. **Audience toggle:** keep all three framings, or is that adding noise now that
   Prevention/Diagnose/Simulate are the clear stars?

---

## 7. The story we are telling (decided with the team)

> **A line runs fine. The twin prevents what it can, and when a fault slips
> through, it finds the real culprit fast and prices the fix.**

- **Act 0 — Telemetry (Monitor).** Line running, healthy. Click a station → current
  in/out, expected out, temp, cycle time. A twin you can *read*.
- **Act 1 — Prevent (before / forward / probabilistic).** The ensemble watches every
  station's telemetry and raises an amber ⚠ on a *not-yet-failed* station: "trending
  to constrain in ~5 min — temp +18%, cycle creeping." Catch it → **Simulate this
  fix** → +N cars → done. *No failure ever happened* = the win.
- **Act 2 — It slips through.** Unforeseeable faults (step/outage) or an ignored
  warning. The station actually degrades; output falls; the ripple reaches the sink.
  Now there is a **real failure with real evidence**.
- **Act 3 — Diagnose (after / backward / evidential).** Prior (graph structure) ×
  evidence (post-failure telemetry) → **posterior blame per station**. Sees through
  the starved victims to the degraded origin — especially a **dark station** that
  could not have been watched directly.
- **Act 4 — Prescribe (Simulate).** Act on the blamed/at-risk station: paired
  re-simulation, "+N cars vs nothing." Loop closes; twin re-reads the line.

Prevent = *before, forward, probabilistic*. Diagnose = *after, backward,
evidential*. Simulate = *the action both hand off to*. = Predict → Diagnose →
Prescribe from our deck, made legible.

## 8. Why the backward graph is real (symptom ≠ cause)

A fault throws **two waves**: a **starvation wave downstream** (everyone after it
outputs less) and a **blocking wave upstream** (everyone before it backs up). So
"output dropped" is seen at the **cause and every downstream victim** — ranking
"who slowed" cannot separate them.

The discriminator is **who slowed for their own reason**:
- **Cause** = own process abnormal (cycle time / temp / current up) **with input
  still available**.
- **Victim** = normal process, slowdown fully explained by **starved input** or
  **blocked output**.

The graph walks against the flow from the symptom and scores each station on
*origin vs pass-through*. Top probability = the **origin**, which for a downstream
symptom is **upstream of the alarm**, not the symptom station (unless the symptom
station really is the origin — also a valid answer).

**Why we can catch the symptom where we can't see the cause (real cases):**
1. **Dark-station cause** — a manual/legacy station (LOW tier, no sensors)
   degrades; we only see the instrumented downstream starve, and infer back. *The
   strongest demo case, and straight from the PS.*
2. **Different-signal symptom** — an upstream motor overheats; the temperature
   alarm trips *downstream*, not at the fault. The instrument you'd watch is not
   where the fault is.
3. **Buffer-absorbed cause** — the cause's output hasn't visibly dropped yet, but
   its process already drifted and the next station is already starving.

Implication for the engine (refinement, not new science): explicitly
**down-weight a station whose slowdown is flow-explained** (high starved/blocked
share) and up-weight **process-anomaly-with-available-input**. Feature the hard
case (dark S6 as cause, or the thermal case) in the demo so the graph is visibly
non-trivial. Multiple simultaneous causes → posterior spreads (honest).

## 9. Simulate = the shared action arm (not a floating tab)

Keep its internals (Current vs Simulated). Change how it's *entered*:
- From **Prevent**: "S4 at risk → **Simulate this fix**" (act before failure).
- From **Diagnose**: "root cause S4 → **Simulate the fix**" (act after).
- Station **pre-filled** from the sending tab; plus the one global lever —
  **release rate**, tied to the line entry (S1/S2/S3).
It always answers: "if I act *here*, how many cars vs doing nothing?"

## 10. Honest split — where is the ML, actually? (credibility)

Locating a bottleneck on an *observable* line is **not ML** — it is the
blocked/starved boundary walk (Roser active-period / Li Turning Point): walk up
from the most-starved downstream and down from the most-blocked upstream; they
meet at the constraint. Pure arithmetic. **We say this out loud** — "don't train
what you can compute" is a strength, and claiming a model finds an obvious
bottleneck is what loses a mentored round.

| Problem | Method | ML? |
|---|---|---|
| Locate the current bottleneck | blocked/starved boundary walk | **No** (arithmetic) |
| **Predict** it ~5 min early (Prevent) | telemetry-drift forecast | **Yes** — the real model |
| Diagnose a bottleneck | the walk + inference where a station is dark | No |
| Diagnose a **defect** (no flow signature) | telemetry drift + genealogy backtrace + attribution | **Yes** — the backward graph's true home |

Key realisation: **output monitoring at every station removes the need for a model
on the FLOW problem.** The model's real jobs are (1) Prevent — call it before the
flow signature exists, and (2) Defects — a quality fault with *no* throughput
change that the walk cannot see at all.

## 11. Decision — do we add the defect/quality dimension?

Today the sim models only bottlenecks (cycle-time degrade → throughput), so
bottleneck-Diagnose is admittedly mostly common sense. Two paths:

- **(A) Bottleneck-only** — present Diagnose as the transparent walk + dark-station
  inference; ML lives in Prevent. Honest, simpler, but Diagnose is "just" the walk.
- **(B) Add defects (recommended)** — model tool drift that makes bad units with
  **no throughput impact**, surfacing late at final QA. The backward trace +
  genealogy then does what no flow walk can: attribute a late defect to its origin
  station in time+space. Covers the *other half* of PS4 ("predict defects before
  they happen", "a defect uncaught for dozens of vehicles", late-inspection
  containment) that we currently ignore, and makes the genealogy we already built
  matter.

## 12. Sensor personalisation (Data tab)

Each station gets a sensor profile; **two low-impact, rarely-failing stations
(S2 Chassis Prep, S6 Battery) have no internal sensors** — only boundary scans.
This is what forces genuine inference in Diagnose (state/telemetry estimated with
confidence), and it is straight from the PS ("some stations rely entirely on
manual checks"). Editable in the Data/Scenario tab so the demo can show coverage
changing the confidence.

## 13. TWO different "what-if" engines (do not conflate)

- **Prevention what-if = ML sensitivity, NOT simulation.** Perturb the current
  telemetry (cool machine → temp↓, add operator → cycle↓, throttle input →
  inflow↓) and push the modified feature vector back through the **same trained
  forecast model** → new failure probability. "Does this action drop the predicted
  risk below threshold?" Fast, pre-failure, no line re-run.
- **Bottleneck resolution what-if = real counterfactual simulation.** The jam is
  already real → re-run the DES line under CRN with the change → true cars-gained.
  **No ML** — pure simulation + the blocked/starved algorithm.

The old build used one engine (re-simulation) for both. Split them: perturbation
+ model for prevention, re-simulation for resolution.

## 14. The window/tab system (Blender-style, not one graph + side panel)

Each tab is a distinct **workspace** with its own layout and tools — NOT a shared
flowchart with a swapping right panel.

**① LINE — Telemetry + Prediction** (the flow view)
DAG + live telemetry + throughput. **Case adjuster** across the top with a "Run
case" button. ML prediction overlays a ⚠ on a station *before* the jam manifests.
Click a station → its numbers (in / out / expected / temp / provenance). Click ⚠
→ window ②. Purpose: flow & bottlenecks.

**② PREVENT / PRESCRIBE — ML what-if** (opened from a ⚠; no flow graph)
"Model predicts S4 constrains in ~5 min." Levers (add operator / cool machine /
slow input) → perturb telemetry → same-model re-inference → new risk %. Shows
whether the action averts the predicted failure. ML, not simulation.

**③ DEFECT DIAGNOSIS — backward traceback** (different layout; throughput graphs
gone, stations read 0)
Selector: "Defect found at [station]." → **Trace back** → model uses per-station
telemetry drift + defect evidence + genealogy → ranks stations by probability in
**check-first** order (symptom station is NOT always top — a real ML problem).
Shows ranked probability + evidence + the "N units built on the suspect since it
drifted" containment list. Half-page, its own view.

**④ RESOLVE — Counterfactual Simulation** (a bottleneck that actually formed)
Change something → **re-simulate** the line → does it clear, and +N cars. Pure
sim + blocked/starved walk, no ML.

| Window | Situation | Engine | ML? |
|---|---|---|---|
| ① Line | live | telemetry + forecast | prediction = ML |
| ② Prevent/Prescribe | before failure | perturb telemetry → same model | **ML** |
| ③ Defect Diagnosis | defect caught late | backward trace + genealogy | **ML** |
| ④ Resolve | bottleneck is real | re-simulate the line | **No** (sim + algo) |

## 15. Decision — chosen: ADD DEFECTS (option B)

Diagnosis tab is **for defects**, not bottlenecks. Input = where the defect was
caught; output = probabilistic origin from telemetry + genealogy. Needs a
tool/quality channel in the sim (drift that makes bad units with **no throughput
impact**, surfacing late at QA) + a drift detector. Bottleneck localisation stays
the arithmetic walk, inside Line/Resolve.

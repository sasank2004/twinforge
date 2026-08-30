"""
TwinForge - FastAPI backend (P2/P4).

Serves the dependency-free frontend and exposes the two engines:
  * Prevent  - a live Server-Sent-Events stream of the twin loop (animated
    factory + detector + T+5 forecast + twin drift).
  * Diagnose - backward-graph probabilistic root cause for a chosen symptom.
  * Prescribe- real paired-counterfactual re-simulation of an intervention.

No external services; the whole UI is one page of vanilla JS + inline SVG.
"""

from __future__ import annotations

import os
import json
import time

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src.twin import factory as F
from src.twin import features as FT
from src.twin.simulator import SimConfig, FaultSpec
from src.twin.loop import run_stream, window_state_from_rows
from src.twin.detector import detect
from src.twin.diagnostic import diagnose
from src.twin.counterfactual import evaluate, INTERVENTIONS, apply
from src.twin.simulator import Simulator
from src.twin import forecast as FC
from src.twin.observability import observe, observability_map
from src.twin.genealogy import containment
from src.twin.diagnostic import diagnose_defect
from src.twin.forecast import prevent_whatif, PREVENT_ACTIONS
from src.twin.simulator import NOMINAL_TORQUE

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WEB = os.path.join(ROOT, "web")
PROC = os.path.join(ROOT, "data", "processed")

app = FastAPI(title="TwinForge Digital Twin")

# --- Scenario presets -------------------------------------------------------

def _sc(seed, faults):
    return {"seed": seed, "faults": faults}


SCENARIOS = {
    "healthy":     {"label": "Healthy line",              "seed": 3, "fault": None,
                    "kind": "flow"},
    "s7_marriage": {"label": "S7 Marriage degrading",     "seed": 3,
                    "fault": ("S7", "degrade_ramp", 300, 0.9, 600), "kind": "flow"},
    "s4_power":    {"label": "S4 Powertrain degrading",   "seed": 3,
                    "fault": ("S4", "degrade_ramp", 300, 0.9, 600), "kind": "flow"},
    "s6_battery":  {"label": "S6 Battery slowing (minor)", "seed": 3,
                    "fault": ("S6", "degrade_ramp", 300, 1.2, 600), "kind": "flow"},
    "s2_down":     {"label": "S2 Chassis outage",         "seed": 3,
                    "fault": ("S2", "station_down", 600, 0.0, 600), "kind": "flow"},
    "s4_tooldrift": {"label": "S4 Powertrain — torque drift", "seed": 3,
                     "fault": ("S4", "tool_drift", 600, 0.35, 600), "kind": "defect"},
    "s7_tooldrift": {"label": "S7 Marriage — torque drift", "seed": 3,
                     "fault": ("S7", "tool_drift", 600, 0.35, 600), "kind": "defect"},
    "s8_tooldrift": {"label": "S8 Trim — torque drift", "seed": 3,
                     "fault": ("S8", "tool_drift", 600, 0.35, 600), "kind": "defect"},
    "s1_tooldrift": {"label": "S1 Body Framing — weld drift", "seed": 3,
                     "fault": ("S1", "tool_drift", 600, 0.35, 600), "kind": "defect"},
    "s3_tooldrift": {"label": "S3 Paint — coat drift", "seed": 3,
                     "fault": ("S3", "tool_drift", 600, 0.35, 600), "kind": "defect"},
    "s6_tooldrift": {"label": "S6 Battery — cell drift", "seed": 3,
                     "fault": ("S6", "tool_drift", 600, 0.35, 600), "kind": "defect"},
}


def _fault_from(spec):
    st, kind, onset, mag, ramp = spec
    if kind == "station_down":
        return FaultSpec(st, kind, onset, duration_s=1200)
    return FaultSpec(st, kind, onset, magnitude=mag, ramp_s=ramp)


def scenario_cfg(name: str, duration_s: int = 3600, speed_scale=None,
                 takt_scale: float = 1.0, custom=None) -> SimConfig:
    if custom:                       # case adjuster: {station,kind,magnitude,onset,complication}
        st = custom["station"]; kind = custom.get("kind", "none")
        onset = int(custom.get("onset_s", 600)); mag = float(custom.get("magnitude", 0.35))
        faults = []
        if kind and kind != "none":
            faults.append(_fault_from((st, kind, onset, mag, 600)))
        comp = custom.get("complication")
        if comp and comp != "none":  # a sensor-specific cause layered on top
            faults.append(FaultSpec(st, comp, onset, magnitude=max(0.8, mag), ramp_s=600))
        seed = int(custom.get("seed", 3))
    else:
        sc = SCENARIOS.get(name, SCENARIOS["healthy"])
        faults = [_fault_from(sc["fault"])] if sc["fault"] else []
        seed = sc["seed"]
    return SimConfig(seed=seed, duration_s=duration_s, faults=faults,
                     speed_scale=speed_scale or {}, takt_scale=takt_scale)


# --- API --------------------------------------------------------------------

@app.get("/api/layout")
def layout():
    return F.to_dict()


@app.get("/api/metrics")
def metrics():
    p = os.path.join(PROC, "metrics.json")
    if os.path.exists(p):
        with open(p) as f:
            return json.load(f)
    return {}


@app.get("/api/scenarios")
def scenarios():
    return {
        "scenarios": [{"id": k, "label": v["label"], "kind": v.get("kind", "flow"),
                       "fault": v["fault"]} for k, v in SCENARIOS.items()],
        "interventions": INTERVENTIONS,
        "prevent_actions": PREVENT_ACTIONS,
        "fault_kinds": {
            "degrade_ramp": "Gradual slowdown",
            "degrade_step": "Sudden slowdown",
            "station_down": "Outage",
        },
        "complications": {
            "none":        {"label": "None", "needs": None},
            "overheating": {"label": "Overheating", "needs": "temp"},
            "tool_wear":   {"label": "Tool wear", "needs": "vibration"},
        },
        "fastening": sorted(NOMINAL_TORQUE),
        "stations": [{"id": s.id, "name": s.name, "sensors": list(s.sensors)} for s in F.STATIONS],
    }


class DefectReq(BaseModel):
    scenario: str = "s4_tooldrift"
    custom: dict | None = None
    duration_s: int = 3600


@app.post("/api/defect_diagnose")
def api_defect_diagnose(req: DefectReq):
    cfg = scenario_cfg(req.scenario, req.duration_s, custom=req.custom)
    sim = Simulator(cfg); sim.run()
    qual_dev = {k: abs(sim.qual[k] / F.QUALITY[k]["nominal"] - 1.0) for k in F.QUALITY}
    return diagnose_defect(sim.completed_units, qual_dev)


def _actionable_window(res, station):
    """
    The pre-failure window Prevent acts on. Among windows where the station's
    risk is rising but not yet saturated, pick the one closest to ~0.6 rather
    than the first crossing - a moment or two later, the rolling window is fully
    past the fault onset so the leading signal is established, not diluted by
    pre-fault baseline. Falls back to the peak-risk window.
    """
    from src.twin import forecast as FC
    cands, best = [], (None, None, 0.0)
    max_min = int(res.features.minute.max())
    for m in range(6, min(max_min, 30)):
        st = FT.window_state(res.features, m - 5, m)
        if not st:
            continue
        risk = FC.forecast_state(st)["station_probs"].get(station, 0.0)
        if 0.4 <= risk <= 0.9:
            cands.append((m, st, risk))
        if risk > best[2]:
            best = (m, st, risk)
    if cands:
        return min(cands, key=lambda c: abs(c[2] - 0.6))
    return best


class PreventStatusReq(BaseModel):
    scenario: str = "s4_power"
    custom: dict | None = None
    station: str = "S4"


@app.post("/api/prevent_status")
def api_prevent_status(req: PreventStatusReq):
    cfg = scenario_cfg(req.scenario, duration_s=2400, custom=req.custom)
    res = Simulator(cfg).run()
    m, state, risk = _actionable_window(res, req.station)
    # driving telemetry at that window vs expected
    drivers = {}
    if state and req.station in state:
        st = state[req.station]
        for ch, key in [("temp_mean", "temp"), ("current_mean", "current"),
                        ("vib_mean", "vibration")]:
            if ch in st:
                drivers[key] = round(st[ch], 1)
    return {"station": req.station, "at_minute": m or 0, "risk": round(risk, 3),
            "drivers": drivers, "actionable": 0.35 <= risk <= 0.92}


class PreventReq(BaseModel):
    scenario: str = "s4_power"
    custom: dict | None = None
    station: str = "S4"
    action: str = "add_operator"
    at_minute: int = 20


@app.post("/api/prevent_whatif")
def api_prevent_whatif(req: PreventReq):
    cfg = scenario_cfg(req.scenario, duration_s=2400, custom=req.custom)
    res = Simulator(cfg).run()
    _m, state, _risk = _actionable_window(res, req.station)   # act at the pre-failure window
    if not state:
        state = FT.window_state(res.features, 15, 20)
    return prevent_whatif(state, req.station, req.action)


def _sse(gen, speed: float, emit_every_s: int):
    """Wrap the loop generator as paced Server-Sent Events."""
    def stream():
        real_per_emit = emit_every_s / max(1.0, speed)
        for snap in gen:
            yield f"data: {json.dumps(snap)}\n\n"
            time.sleep(real_per_emit)
        yield "event: done\ndata: {}\n\n"
    return StreamingResponse(stream(), media_type="text/event-stream")


@app.get("/api/stream")
def stream(scenario: str = "healthy", speed: float = 40.0,
           duration_s: int = 3600, emit_every_s: int = 10,
           intervene: str = "", station: str = "",
           fault_station: str = "", fault_kind: str = "",
           fault_mag: float = 0.35, fault_onset: int = 600, complication: str = ""):
    # a case-adjuster case overrides the named scenario
    custom = None
    if fault_station and ((fault_kind and fault_kind != "none") or (complication and complication != "none")):
        custom = {"station": fault_station, "kind": fault_kind or "none",
                  "magnitude": fault_mag, "onset_s": fault_onset,
                  "complication": complication or "none"}
    cfg = scenario_cfg(scenario, duration_s, custom=custom)
    if intervene:
        cfg = apply(cfg, intervene, station or None)
    gen = run_stream(cfg, emit_every_s=emit_every_s)
    return _sse(gen, speed, emit_every_s)


class DiagnoseReq(BaseModel):
    scenario: str = "s7_marriage"
    symptom_station: str = "S9"
    symptom_signal: str | None = None
    at_minute: int = 45
    state: dict | None = None


@app.post("/api/diagnose")
def api_diagnose(req: DiagnoseReq):
    if req.state:
        state = req.state
    else:
        cfg = scenario_cfg(req.scenario, duration_s=max(3600, (req.at_minute + 5) * 60))
        res = Simulator(cfg).run()
        state = FT.window_state(res.features, req.at_minute - 5, req.at_minute)
    return diagnose(state, req.symptom_station, req.symptom_signal)


class CounterfactualReq(BaseModel):
    scenario: str = "s7_marriage"
    custom: dict | None = None
    intervention: str = "add_operator"
    station: str | None = "S7"
    duration_s: int = 3600


@app.post("/api/counterfactual")
def api_counterfactual(req: CounterfactualReq):
    cfg = scenario_cfg(req.scenario, req.duration_s, custom=req.custom)
    window = None
    if req.custom:
        window = (int(req.custom.get("onset_s", 600)) + 600, req.duration_s)
    else:
        sc = SCENARIOS.get(req.scenario)
        if sc and sc["fault"]:
            window = (sc["fault"][2] + 600, req.duration_s)
    return evaluate(cfg, req.intervention, req.station, window=window)


@app.get("/api/observability")
def api_observability(scenario: str = "s7_marriage", at_minute: int = 30):
    cfg = scenario_cfg(scenario, duration_s=max(3600, (at_minute + 5) * 60))
    res = Simulator(cfg).run()
    true_state = FT.window_state(res.features, at_minute - 5, at_minute)
    obs, records = observe(true_state)
    return {"map": observability_map(records), "records": records}


class GenealogyReq(BaseModel):
    scenario: str = "s7_marriage"
    station: str = "S7"
    since_minute: int = 5
    duration_s: int = 3600


@app.post("/api/genealogy")
def api_genealogy(req: GenealogyReq):
    cfg = scenario_cfg(req.scenario, req.duration_s)
    res = Simulator(cfg).run()
    return containment(res.completed, req.station, req.since_minute * 60)


# --- static frontend --------------------------------------------------------

@app.get("/")
def index():
    return FileResponse(os.path.join(WEB, "index.html"))


if os.path.isdir(WEB):
    app.mount("/web", StaticFiles(directory=WEB), name="web")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("src.api.main:app", host="0.0.0.0", port=8000, reload=False)

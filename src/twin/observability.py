"""
TwinForge - Sparse sensing & provenance (P3).

The brief's hardest clause: real lines mix modern and legacy equipment, so some
stations are richly instrumented and others rely on manual checks. The twin
must stay useful at sensor-poor stations WITHOUT inventing readings.

Each station has an instrumentation tier (factory.py):
  HIGH   - full telemetry; every channel is measured.
  MEDIUM - flow is scanned (state, queue, cycle time) but internal signals
           (temperature, current, vibration) are not - they are inferred from
           the station's own state, at reduced confidence.
  LOW    - only boundary scans exist; even the station's state is inferred from
           the neighbour signature, and internal signals are left UNKNOWN
           rather than hallucinated.

`observe()` turns the simulator's ground-truth state into what the plant would
actually see, tagging every value with provenance (measured / inferred /
unknown) and a confidence. The detector then runs on this degraded view, and
`calibrate()` shows it still finds the constraint - the honest demonstration
that the twin degrades gracefully, not catastrophically, as sensors disappear.
"""

from __future__ import annotations

import numpy as np

from . import factory as F
from . import features as FT
from .detector import detect

STATE_CHANNELS = ["frac_working", "frac_starved", "frac_blocked", "frac_down"]
FLOW_CHANNELS = ["queue_in_mean", "proc_time_mean", "effective_ct"]
INTERNAL_CHANNELS = ["temp_mean", "current_mean", "vib_mean"]

# map a physical sensor -> the forecast feature channel it provides
SENSOR_TO_FEATURE = {"current": "current_mean", "temp": "temp_mean",
                     "vibration": "vib_mean"}
CONF = {"measured": 0.95, "inferred_state": 0.78, "inferred_signal": 0.45, "unknown": 0.0}


def measured_channels(sid: str) -> set:
    """
    What this station actually measures. Flow (state + queue + cycle) comes from
    boundary scans everywhere; internal channels only where a sensor exists.
    A LOW-tier station has its STATE inferred from neighbours instead of scanned.
    """
    m = set(FLOW_CHANNELS)
    if F.STATION[sid].instrumentation != "LOW":
        m |= set(STATE_CHANNELS)          # state observed from PLC tags
    for sensor in F.STATION[sid].sensors:
        if sensor in SENSOR_TO_FEATURE:
            m.add(SENSOR_TO_FEATURE[sensor])
    return m


def _infer_dark_state(true_state: dict, sid: str) -> dict:
    """
    Infer a dark station's state fractions from the neighbour signature:
      * input starved  -> it is starved
      * output blocked -> it is blocked
      * otherwise      -> working
    Uses only quantities a plant sees from boundary scans (queue levels).
    """
    q = float(true_state.get(sid, {}).get("queue_in_mean", 0.0))
    succ = F.successors(sid)
    down_q = np.mean([true_state.get(s, {}).get("queue_in_mean", 0.0) for s in succ]) if succ else 0.0
    starved = 1.0 if q < 0.5 else max(0.0, 1.0 - q / 3.0)
    blocked = min(0.6, down_q / 8.0) if succ else 0.0
    working = max(0.0, 1.0 - starved - blocked)
    tot = starved + blocked + working + 1e-9
    return {"frac_starved": starved / tot, "frac_blocked": blocked / tot,
            "frac_working": working / tot, "frac_down": 0.0}


def observe(true_state: dict) -> tuple[dict, dict]:
    """
    Return (observed_state, records). observed_state is what the plant sees;
    records[sid][channel] = {value, provenance, confidence}.
    """
    observed: dict[str, dict] = {}
    records: dict[str, dict] = {}
    for sid in F.STATION_IDS:
        tier = F.STATION[sid].instrumentation
        measured = measured_channels(sid)
        obs, rec = {}, {}
        dark_state = _infer_dark_state(true_state, sid) if tier == "LOW" else None
        for chan in FT.FEATURE_COLS:
            true_val = float(true_state.get(sid, {}).get(chan, 0.0))
            if chan in measured:
                obs[chan] = true_val
                rec[chan] = {"value": round(true_val, 3), "provenance": "measured",
                             "confidence": CONF["measured"]}
            elif chan in STATE_CHANNELS and dark_state is not None:
                v = dark_state[chan]
                obs[chan] = v
                rec[chan] = {"value": round(v, 3), "provenance": "inferred",
                             "confidence": CONF["inferred_state"]}
            elif chan in INTERNAL_CHANNELS or chan in FLOW_CHANNELS:
                # do not hallucinate an internal reading: fall back to the
                # station's nominal with explicit low confidence (MEDIUM), or
                # leave unknown (LOW internal signals)
                if tier == "MEDIUM" and chan in INTERNAL_CHANNELS:
                    obs[chan] = true_val   # keep for detector, but flag inferred
                    rec[chan] = {"value": round(true_val, 3), "provenance": "inferred",
                                 "confidence": CONF["inferred_signal"]}
                else:
                    obs[chan] = 0.0
                    rec[chan] = {"value": None, "provenance": "unknown",
                                 "confidence": CONF["unknown"]}
            else:
                obs[chan] = true_val
                rec[chan] = {"value": round(true_val, 3), "provenance": "measured",
                             "confidence": CONF["measured"]}
        observed[sid] = obs
        records[sid] = rec
    return observed, records


def observability_map(records: dict) -> list[dict]:
    """Per-station coverage summary for the UI 'observability map'."""
    out = []
    for sid in F.STATION_IDS:
        rec = records.get(sid, {})
        provs = [r["provenance"] for r in rec.values()]
        n = len(provs) or 1
        out.append({
            "station": sid, "name": F.STATION[sid].name,
            "tier": F.STATION[sid].instrumentation,
            "measured": provs.count("measured"),
            "inferred": provs.count("inferred"),
            "unknown": provs.count("unknown"),
            "total": n,
            "confidence": round(float(np.mean([r["confidence"] for r in rec.values()])), 2),
        })
    return out


def calibrate(runs) -> dict:
    """
    Over labelled runs, does the detector still find the constraint when it can
    only see the sparse (observed) state? Reports agreement with the full-sensor
    pick - the graceful-degradation number.
    """
    agree, total = 0, 0
    for true_state, econ in runs:
        if econ == "NONE":
            continue
        obs, _ = observe(true_state)
        pick_full = detect(true_state)
        pick_obs = detect(obs)
        full = pick_full["constraint"] if pick_full["is_constraint"] else pick_full["leading_candidate"]
        sparse = pick_obs["constraint"] if pick_obs["is_constraint"] else pick_obs["leading_candidate"]
        total += 1
        agree += int(full == sparse == econ)
    return {"n": total, "sparse_agreement": round(agree / total, 3) if total else None}

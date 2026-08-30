"""
TwinForge - Diagnostic engine: backward-graph root-cause (P5).

Given a symptom (an anomalous signal at a station), walk BACKWARD through the
process DAG and assign every upstream station - and the symptom station itself
- a probability of being the root cause. The disturbance a plant sees late
(e.g. a temperature rise at the final station) usually started upstream and
propagated with a lag; this engine recovers where.

Method (transparent, no training):
  posterior(k) proportional to  prior(k) x anomaly(k) x propagation(k -> symptom)
  * anomaly(k)      - how far k's own signals are from its healthy baseline (z).
  * propagation(k)  - does a plausible, corroborated path k -> symptom exist:
                      proximity decay x fraction of the path that is also
                      anomalous (a real cascade lights up the whole chain).
The posteriors are normalised over candidates, so the frontend can draw the
backward graph with a probability on each node and light the carrying edges.
The root cause CAN be the symptom station itself (a local fault).
"""

from __future__ import annotations

import os
import json
from collections import deque

import numpy as np

from . import factory as F

BASELINE_PATH = os.path.join(os.path.dirname(__file__), "..", "..",
                             "data", "processed", "baselines.json")


DIAG_TEMPER = 0.55     # <1 softens the posterior so it distributes realistically


def diagnose_defect(completed: list[dict], qual_dev: dict[str, float]) -> dict:
    """
    Defect root-cause (the Diagnose tab). A QA reject has NO throughput
    signature, so the blocked/starved walk is blind. And it does not point at
    one station: a coating fault could be a bad application at S3 OR a downstream
    station that scuffed it. So the candidate set for a defect ATTRIBUTE is the
    station that PRODUCES it plus the downstream stations that HANDLE it, and
    probability is DISTRIBUTED across them from three signals:

      * QUALITY telemetry - which station's own channel is actually out of spec
        (strong evidence for a producer; a handler with nominal tooling can only
        be implicated by prior + genealogy).
      * GENEALOGY lift - which candidates the defective units actually passed
        through (a handler off the bad units' path is cleared).
      * PRIOR - producers are likelier than handlers, but a handler is never 0.

    The posterior is tempered (gamma<1) so it reads like a real ranked worklist -
    e.g. 64% / 22% / 14% - not a false 98% / 1% / 1%.
    """
    total = len(completed) or 1
    defective = [u for u in completed if u.get("defective")]
    n_def = len(defective)

    # what KIND of defect QA is seeing (the attribute); default to the worst channel
    attr_ct: dict[str, int] = {}
    for u in defective:
        for a in u.get("defect_attrs", []):
            attr_ct[a] = attr_ct.get(a, 0) + 1
    if attr_ct:
        attribute = max(attr_ct, key=attr_ct.get)
    else:
        attribute = max(F.QUALITY, key=lambda k: qual_dev.get(k, 0))
        attribute = F.QUALITY[attribute]["channel"]

    producers = [k for k in sorted(F.QUALITY) if F.QUALITY[k]["channel"] == attribute]
    handlers = [h for h in F.ATTRIBUTE_HANDLERS.get(attribute, []) if h not in producers]
    candidates = producers + handlers

    def passes(units, k):
        return sum(1 for u in units if any(p[0] == k for p in u.get("path", [])))

    raw, info = {}, {}
    for k in candidates:
        through_all = passes(completed, k) / total
        through_def = (passes(defective, k) / n_def) if n_def else 0.0
        lift = through_def / (through_all + 1e-9)
        is_prod = k in producers
        own = F.QUALITY.get(k)
        dev = qual_dev.get(k, 0.0) if own else 0.0
        oos = own is not None and dev > own["spec"]
        if is_prod:
            evidence = 1.0 + 5.0 * max(0.0, dev / own["spec"] - 1.0)   # own channel out of spec
            prior = 1.0
            role, chan = "producer", own["channel"]
        else:
            # a handler cannot be confirmed by the missing channel; a rough tool
            # of its own (out of spec) raises suspicion, otherwise prior only.
            evidence = 1.0 + 2.0 * max(0.0, dev / own["spec"] - 1.0) if own else 1.0
            prior = 0.5
            role, chan = "handler", (own["channel"] if own else "—")
        raw[k] = prior * (0.25 + lift) * evidence
        info[k] = {"channel": chan, "role": role, "dev_pct": round(dev * 100, 1),
                   "out_of_spec": oos, "genealogy_lift": round(lift, 2), "on_path": through_def > 0.05}

    # tempered posterior (distributes probability, no false certainty)
    tw = {k: v ** DIAG_TEMPER for k, v in raw.items()}
    ssum = sum(tw.values()) or 1.0
    posterior = {k: tw[k] / ssum for k in candidates}
    ranked = sorted(posterior.items(), key=lambda kv: -kv[1])
    root = ranked[0][0] if ranked else None

    edge_ct: dict[tuple, int] = {}
    for u in defective:
        p = [x[0] for x in u.get("path", [])]
        for a, b in zip(p[:-1], p[1:]):
            edge_ct[(a, b)] = edge_ct.get((a, b), 0) + 1
    mx = max(edge_ct.values()) if edge_ct else 1
    corridor = [{"src": a, "dst": b, "weight": round(c / mx, 2)} for (a, b), c in edge_ct.items()]

    contained = [u["vin"] for u in defective
                 if root and any(x[0] == root for x in u.get("path", []))]

    hyps = []
    for rank, (k, p) in enumerate(ranked, 1):
        d = info[k]
        if d["role"] == "producer":
            act = (f"Re-calibrate / service the {d['channel']} tool at {F.STATION[k].name} ({k}) — "
                   f"{d['dev_pct']:+.0f}% off nominal." if d["out_of_spec"]
                   else f"{k} {d['channel']} reads in spec — check its process, less likely.")
        else:
            act = (f"Inspect handling at {F.STATION[k].name} ({k}) — it processes the {attribute} "
                   f"downstream and could have damaged it (its own tooling reads nominal).")
        hyps.append({"rank": rank, "station": k, "station_name": F.STATION[k].name,
                     "probability": round(p, 3), "role": d["role"], "channel": d["channel"],
                     "dev_pct": d["dev_pct"], "out_of_spec": d["out_of_spec"],
                     "genealogy_lift": d["genealogy_lift"], "action": act})

    return {
        "mode": "defect", "attribute": attribute,
        "n_produced": total, "n_defective": n_def,
        "defect_rate_pct": round(100 * n_def / total, 2),
        "candidates": candidates,
        "posterior": {k: round(v, 3) for k, v in posterior.items()},
        "ranked": [(k, round(v, 3)) for k, v in ranked],
        "corridor": corridor, "root_cause": root, "hypotheses": hyps,
        "containment": {"count": len(contained), "vins": contained,
                        "first": contained[0] if contained else None,
                        "last": contained[-1] if contained else None},
    }

# fault-indicative channels (higher than baseline => more suspicious) and weights
ANOMALY_CHANNELS = {
    "temp_mean": 1.0,
    "current_mean": 1.0,
    "vib_mean": 1.0,
    "proc_time_mean": 1.2,
    "queue_in_mean": 0.6,
}

SIGNAL_LABELS = {
    "temp_mean": "Temperature",
    "current_mean": "Motor current",
    "vib_mean": "Vibration",
    "proc_time_mean": "Cycle time",
    "queue_in_mean": "Queue / WIP",
}

ACTION_TEMPLATES = {
    "proc_time_mean": "Cycle time is drifting long - inspect tooling / fixtures at {name} ({sid}); check for wear or a program change.",
    "temp_mean": "Thermal rise at {name} ({sid}) - check motor cooling, lubrication and load.",
    "current_mean": "Motor current elevated at {name} ({sid}) - check drive, bearings and mechanical binding.",
    "vib_mean": "Vibration elevated at {name} ({sid}) - inspect spindle/bearing wear and mounting.",
    "queue_in_mean": "WIP backing up into {name} ({sid}) - it is being blocked by a downstream constraint.",
}

_BASE_CACHE: dict = {}


def load_baselines(path: str = BASELINE_PATH) -> dict:
    if not _BASE_CACHE:
        if os.path.exists(path):
            with open(path) as f:
                _BASE_CACHE.update(json.load(f))
    return _BASE_CACHE


def _z(state: dict, sid: str, chan: str) -> float:
    base = load_baselines().get(sid, {}).get(chan)
    val = float(state.get(sid, {}).get(chan, 0.0))
    if not base:
        return 0.0
    # Floor the std relative to the mean: healthy runs are near-deterministic,
    # so the raw std is tiny and a 3C rise would read as +30 sigma. A relative
    # floor keeps z-scores physically sensible for the operator.
    std = max(base["std"], 0.05 * abs(base["mean"]), 1e-6)
    return (val - base["mean"]) / std


def _pct(state: dict, sid: str, chan: str) -> float:
    base = load_baselines().get(sid, {}).get(chan)
    val = float(state.get(sid, {}).get(chan, 0.0))
    if not base or abs(base["mean"]) < 1e-9:
        return 0.0
    return (val - base["mean"]) / abs(base["mean"]) * 100.0


def station_anomaly(state: dict, sid: str) -> tuple[float, dict]:
    """
    Weighted positive-deviation anomaly for a station. The per-channel z is
    capped so one near-deterministic channel can't dominate the ranking; the
    per-channel % deviation is what the operator actually reads.
    """
    zs, pcts = {}, {}
    score = 0.0
    for chan, w in ANOMALY_CHANNELS.items():
        z = _z(state, sid, chan)
        zs[chan] = round(z, 2)
        pcts[chan] = round(_pct(state, sid, chan), 1)
        score += w * min(6.0, max(0.0, z))       # cap positive contribution
    return score, {"z": zs, "pct": pcts}


def ancestors(sid: str) -> list[str]:
    """All stations that can reach `sid` through the DAG (transitive predecessors)."""
    seen, q, out = {sid}, deque([sid]), []
    while q:
        n = q.popleft()
        for p in F.predecessors(n):
            if p not in seen:
                seen.add(p); out.append(p); q.append(p)
    return out


def _shortest_path(src: str, dst: str) -> list[str]:
    """A path src -> dst through the DAG (BFS)."""
    if src == dst:
        return [src]
    prev = {src: None}
    q = deque([src])
    while q:
        n = q.popleft()
        for m in F.successors(n):
            if m not in prev:
                prev[m] = n
                if m == dst:
                    path = [dst]
                    while prev[path[-1]] is not None:
                        path.append(prev[path[-1]])
                    return list(reversed(path))
                q.append(m)
    return []


def diagnose(state: dict, symptom_station: str,
             symptom_signal: str | None = None) -> dict:
    """
    state: rolling-window per-station feature dict (same shape as detector).
    Returns ranked root-cause hypotheses with a posterior probability per
    candidate station, the backward trace edges to light up, and evidence.
    """
    candidates = [symptom_station] + ancestors(symptom_station)

    # symptom quantification
    sym_score, sym_chans = station_anomaly(state, symptom_station)
    if symptom_signal is None:
        symptom_signal = max(ANOMALY_CHANNELS, key=lambda c: sym_chans["z"].get(c, 0.0))

    raw: dict[str, float] = {}
    info: dict[str, dict] = {}
    for k in candidates:
        anom, chans = station_anomaly(state, k)
        zs, pcts = chans["z"], chans["pct"]
        path = _shortest_path(k, symptom_station)
        hops = max(0, len(path) - 1)
        proximity = 0.75 ** hops                      # closer causes preferred
        # corroboration: fraction of the path (excluding symptom) that is anomalous
        chain_nodes = path[:-1] if len(path) > 1 else path
        anomalous = [n for n in chain_nodes if station_anomaly(state, n)[0] > 1.0]
        chain_frac = len(anomalous) / max(1, len(chain_nodes))
        propagation = proximity * (0.5 + 0.5 * chain_frac)
        prior = 0.5 + 0.5 * (F.load_of(k) / 5.0)      # heavier stations slightly likelier
        score = prior * anom * propagation
        raw[k] = score
        # dominant anomalous channel at k
        dom = max(ANOMALY_CHANNELS, key=lambda c: zs.get(c, 0.0))
        info[k] = {"anomaly": round(anom, 2), "hops": hops, "z": zs, "pct": pcts,
                   "dominant": dom, "chain_frac": round(chain_frac, 2),
                   "path": path}

    total = sum(raw.values()) or 1.0
    posterior = {k: raw[k] / total for k in candidates}
    ranked = sorted(posterior.items(), key=lambda kv: -kv[1])

    # backward trace: union of edges on paths from the top candidates to symptom
    trace_edges = []
    seen_edges = set()
    for k, p in ranked[:4]:
        path = info[k]["path"]
        for a, b in zip(path[:-1], path[1:]):
            if (a, b) not in seen_edges:
                seen_edges.add((a, b))
                trace_edges.append({"src": a, "dst": b,
                                    "weight": round(min(posterior.get(a, 0), 1.0), 3)})

    hypotheses = []
    for rank, (k, p) in enumerate(ranked[:3], 1):
        dom = info[k]["dominant"]
        name = F.STATION[k].name
        hypotheses.append({
            "rank": rank,
            "station": k,
            "station_name": name,
            "probability": round(p, 3),
            "dominant_signal": SIGNAL_LABELS.get(dom, dom),
            # evidence as % deviation from healthy baseline (operator-readable)
            "evidence": {SIGNAL_LABELS.get(c, c): info[k]["pct"].get(c, 0.0)
                         for c in ANOMALY_CHANNELS},
            "path": info[k]["path"],
            "recommended_action": ACTION_TEMPLATES.get(dom, "Inspect {name} ({sid}).").format(name=name, sid=k),
        })

    return {
        "symptom_station": symptom_station,
        "symptom_station_name": F.STATION[symptom_station].name,
        "symptom_signal": SIGNAL_LABELS.get(symptom_signal, symptom_signal),
        "symptom_z": round(_z(state, symptom_station, symptom_signal), 2),
        "symptom_pct": round(_pct(state, symptom_station, symptom_signal), 1),
        "candidates": candidates,
        "posterior": {k: round(v, 3) for k, v in posterior.items()},
        "ranked": [(k, round(v, 3)) for k, v in ranked],
        "trace_edges": trace_edges,
        "hypotheses": hypotheses,
        "root_cause": ranked[0][0],
    }

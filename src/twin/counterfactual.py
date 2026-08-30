"""
TwinForge - Counterfactual / Prescribe engine (P3-Scenario C).

Unlike the old prototype (which faked physics with `temperature *= old/new`),
this RE-SIMULATES the line. An intervention is applied to a copy of the current
scenario and run under Common Random Numbers against the untouched baseline, so
the cars-gained figure is a real paired measurement, not an assertion.

The list of candidate actions deliberately includes "do nothing" and "reduce
release rate" (CONWIP) - both are frequently the right answer and neither is an
operator's instinct (Goldratt / KGP).
"""

from __future__ import annotations

from dataclasses import replace

from . import factory as F
from .simulator import Simulator, SimConfig


# Catalogue the UI offers. Each returns a modified SimConfig.
INTERVENTIONS = {
    "none": {"label": "Do nothing", "needs_station": False,
             "desc": "The constraint may move on its own; often correct."},
    "add_operator": {"label": "Add operator (-25% cycle time)", "needs_station": True,
                     "desc": "Float an operator to the station to speed it up."},
    "debottleneck": {"label": "De-bottleneck (-40% cycle time)", "needs_station": True,
                     "desc": "Tooling / maintenance action to restore cycle time."},
    "reduce_release": {"label": "Reduce release rate (+25% takt)", "needs_station": False,
                       "desc": "CONWIP lever: same throughput, lower WIP & lead time."},
}


def apply(cfg: SimConfig, kind: str, station: str | None = None) -> SimConfig:
    if kind == "add_operator" and station:
        return replace(cfg, speed_scale={**cfg.speed_scale, station: 0.75})
    if kind == "debottleneck" and station:
        return replace(cfg, speed_scale={**cfg.speed_scale, station: 0.60})
    if kind == "reduce_release":
        return replace(cfg, takt_scale=cfg.takt_scale * 1.25)
    return cfg


def _series(sim_cfg: SimConfig, sample_s: int = 60):
    """Run a sim, sampling (t, produced, wip) every sample_s seconds."""
    sim = Simulator(sim_cfg)
    ts, prod, wip = [], [], []
    nxt = sample_s
    while sim.t < sim_cfg.duration_s:
        sim.step()
        if sim.t >= nxt:
            ts.append(sim.t); prod.append(sim.produced); wip.append(sim._wip())
            nxt += sample_s
    return {"t": ts, "produced": prod, "wip": wip,
            "final_produced": sim.produced,
            "mean_lead_time": (round(sum(u["lead_time"] for u in sim.completed_units)
                                     / len(sim.completed_units), 1)
                               if sim.completed_units else None)}


def evaluate(cfg: SimConfig, kind: str, station: str | None = None,
             window: tuple | None = None) -> dict:
    """
    Paired CRN comparison of baseline vs intervention. Returns final cars,
    delta, WIP, lead time and downsampled time series for the funnel/graph.
    """
    base = _series(cfg)
    icfg = apply(cfg, kind, station)
    inter = _series(icfg)

    def count(series, w):
        if w is None:
            return series["final_produced"]
        lo, hi = w
        # approximate windowed count from the sampled cumulative curve
        p = series["produced"]; t = series["t"]
        def at(tt):
            v = 0
            for ti, pi in zip(t, p):
                if ti <= tt:
                    v = pi
            return v
        return at(hi) - at(lo)

    base_cars = count(base, window)
    inter_cars = count(inter, window)
    return {
        "intervention": kind,
        "station": station,
        "label": INTERVENTIONS[kind]["label"],
        "baseline": base,
        "intervention_series": inter,
        "cars_before": base_cars,
        "cars_after": inter_cars,
        "delta_cars": inter_cars - base_cars,
        "wip_before": base["wip"][-1] if base["wip"] else 0,
        "wip_after": inter["wip"][-1] if inter["wip"] else 0,
        "lead_before": base["mean_lead_time"],
        "lead_after": inter["mean_lead_time"],
    }

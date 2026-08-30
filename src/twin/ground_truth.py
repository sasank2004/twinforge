"""
TwinForge - Economic ground truth (P1).

The single most important lesson from the IIT KGP design review: never label
the bottleneck with the same statistic the detector computes, or you measure an
identity, not a result (their words: "we did that once and scored 95.2%, which
was an identity"). The old TwinForge fell straight into this - it labelled the
bottleneck with `frac_working - 0.5*frac_starved` and then trained a model to
predict it, scoring a meaningless 95%.

Here the truth is ECONOMIC: the constraint is the station whose speed-up
actually produces more cars, measured by re-running the exact same shift under
Common Random Numbers with that one station sped up (a paired counterfactual).
This is only knowable offline (a real plant cannot be re-run), so it is used to
LABEL training data and to SCORE the detector - never inside the live loop.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Optional

import numpy as np

from . import factory as F
from .simulator import Simulator, SimConfig


def _count_produced(completed: list[dict], window: Optional[tuple] = None) -> int:
    if window is None:
        return len(completed)
    lo, hi = window
    return sum(1 for u in completed if lo <= u["t_out"] < hi)


def paired_gains(cfg: SimConfig, delta: float = 0.75,
                 window: Optional[tuple] = None) -> dict[str, float]:
    """
    Cars gained by speeding each station to `delta` x its cycle time, measured
    against the same-seed baseline over `window` (t_out in [lo,hi)).
    Returns {station: delta_cars}. Non-sink AND sink stations are tested.
    """
    base = Simulator(cfg).run()
    base_n = _count_produced(base.completed, window)
    gains: dict[str, float] = {}
    for sid in F.STATION_IDS:
        ccfg = replace(cfg, speed_scale={**cfg.speed_scale, sid: delta})
        r = Simulator(ccfg).run()
        gains[sid] = _count_produced(r.completed, window) - base_n
    return gains


def economic_bottleneck(cfg: SimConfig, delta: float = 0.75,
                        window: Optional[tuple] = None,
                        margin: float = 3.0) -> dict:
    """
    Rank stations by cars-gained and return the economic constraint.
    If the best gain does not clear `margin` cars over the runner-up (and over
    zero), the line is effectively balanced -> "NONE".
    """
    gains = paired_gains(cfg, delta=delta, window=window)
    ranked = sorted(gains.items(), key=lambda kv: -kv[1])
    top_st, top_gain = ranked[0]
    second_gain = ranked[1][1] if len(ranked) > 1 else 0.0
    is_constraint = top_gain >= margin and (top_gain - second_gain) >= margin
    return {
        "bottleneck": top_st if is_constraint else "NONE",
        "top_gain": top_gain,
        "margin_over_next": top_gain - second_gain,
        "ranked": ranked,
        "gains": gains,
    }


def noise_floor(seeds: range = range(1, 11), delta: float = 0.75,
                duration_s: int = 3600) -> float:
    """
    Paired-comparison noise on a HEALTHY line: how many cars a speed-up appears
    to gain when there is no real constraint. Published so healthy-run 'gains'
    are not mistaken for signal (KGP Phase 0 discipline).
    Returns the 95th percentile of |apparent gain|.
    """
    vals: list[float] = []
    for s in seeds:
        g = paired_gains(SimConfig(seed=s, duration_s=duration_s))
        vals.extend(abs(v) for v in g.values())
    return float(np.percentile(vals, 95))


if __name__ == "__main__":
    print("Noise floor (healthy line, 5 seeds):",
          round(noise_floor(range(1, 6)), 2), "cars")
    from .simulator import FaultSpec
    cfg = SimConfig(seed=2, duration_s=3600,
                    faults=[FaultSpec("S7", "degrade_step", 600, 0.6)])
    res = economic_bottleneck(cfg, window=(600, 3600))
    print("S7 step+60% -> economic bottleneck:", res["bottleneck"],
          "| top gain", res["top_gain"], "| margin", res["margin_over_next"])
    print("  ranked:", [(s, g) for s, g in res["ranked"]])

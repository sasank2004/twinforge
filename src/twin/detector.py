"""
TwinForge - Current-constraint detector (P1).

Pure arithmetic, no training - this is the live "what is constraining the line
right now" engine. It follows the IIT KGP discipline:

  * Rank by EFFECTIVE cycle time (processing time / availability), never by raw
    utilisation. Utilisation ranks a busy station and a genuinely slow station
    identically - it is the "utilisation trap". We keep utilisation only as a
    contrast the demo can show being WRONG.
  * Use the blocked/starved SIGNATURE for direction: the constraint is the
    station pinned working (rarely starved) whose upstream blocks (can't hand
    over) and whose downstream starves (nothing arriving).
  * Emit a ranked candidate with a margin, not a single verdict.

The output is scored offline against the economic ground truth (regret), never
labelled by this same formula - that would be an identity, not a measurement.
"""

from __future__ import annotations

import numpy as np

from . import factory as F


def _get(feat: dict, sid: str, key: str, default: float = 0.0) -> float:
    return float(feat.get(sid, {}).get(key, default))


def effective_ct(feat: dict, sid: str) -> float:
    """
    Window-level effective cycle time = work-only processing time / availability.
    Computed from window-mean proc + window-mean downtime (NOT by averaging the
    per-minute effective_ct, which spikes when a minute is almost all downtime).
    Availability is floored so a heavily-down station reads as impaired, not
    infinite - downtime is a reliability problem, not a slow-cycle bottleneck.
    """
    st = feat.get(sid, {})
    proc = float(st.get("proc_time_mean", F.STATION[sid].nominal_ct))
    avail = max(0.5, 1.0 - float(st.get("frac_down", 0.0)))
    return proc / avail


def detect(feat: dict) -> dict:
    """
    feat: {station_id: {frac_working, frac_starved, frac_blocked, frac_down,
                        proc_time_mean|effective_ct, queue_in_mean, ...}}
    Returns the ranked constraint candidates with scores + a utilisation pick.
    """
    scores: dict[str, float] = {}
    detail: dict[str, dict] = {}
    for sid in F.STATION_IDS:
        st = F.STATION[sid]
        busy = _get(feat, sid, "frac_working")
        starved = _get(feat, sid, "frac_starved")
        eff = effective_ct(feat, sid)
        slowdown = eff / st.nominal_ct                      # >=1 when degraded
        not_waiting = 1.0 - starved
        # signature: does it block upstream and starve downstream?
        succ = F.successors(sid)
        pred = F.predecessors(sid)
        down_starve = np.mean([_get(feat, s, "frac_starved") for s in succ]) if succ else 0.0
        up_block = np.mean([_get(feat, s, "frac_blocked") for s in pred]) if pred else 0.0
        signature = 1.0 + float(down_starve) + float(up_block)
        score = busy * slowdown * not_waiting * signature
        scores[sid] = score
        detail[sid] = {
            "score": round(score, 4),
            "effective_ct": round(eff, 2),
            "slowdown": round(slowdown, 3),
            "frac_working": round(busy, 3),
            "frac_starved": round(starved, 3),
            "downstream_starve": round(float(down_starve), 3),
            "upstream_block": round(float(up_block), 3),
            "queue_in": round(_get(feat, sid, "queue_in_mean"), 1),
        }

    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    top, top_score = ranked[0]
    second = ranked[1][1] if len(ranked) > 1 else 0.0
    margin = (top_score - second) / (top_score + 1e-9)

    # Below a margin, no station dominates - the honest answer on a balanced
    # line is "NONE", matching the economic truth (KGP: don't force a verdict).
    is_constraint = margin >= MIN_MARGIN
    constraint = top if is_constraint else "NONE"

    # the naive contrast: whoever looks busiest
    util_pick = max(F.STATION_IDS, key=lambda s: _get(feat, s, "frac_working"))

    return {
        "constraint": constraint,
        "leading_candidate": top,
        "is_constraint": is_constraint,
        "constraint_score": round(top_score, 4),
        "margin": round(margin, 3),
        "ranked": [(s, round(sc, 4)) for s, sc in ranked],
        "detail": detail,
        "utilisation_pick": util_pick,
    }


MIN_MARGIN = 0.08   # below this the line is treated as balanced (no constraint)


def regret(pick: str, gains: dict[str, float]) -> float:
    """Cars lost by acting on `pick` instead of the true best station."""
    if not gains:
        return 0.0
    best = max(gains.values())
    return float(best - gains.get(pick, 0.0))

"""
TwinForge - Genealogy & defect containment (P5).

The brief: a defect introduced early may not surface until a much later
inspection, by which point many downstream units carry it. Once the diagnostic
names a drifting station and when it started drifting, genealogy turns that into
a concrete containment list - exactly which vehicles were built on that station
since the drift began, so they can be held and inspected instead of shipped.

Every unit carries its full station path (recorded by the simulator), so this
is a lookup, not a model.
"""

from __future__ import annotations

from . import factory as F


def containment(completed: list[dict], station: str, since_t: int) -> dict:
    """
    Vehicles processed by `station` at or after `since_t` (drift onset).
    Returns the VIN list, count, and the time window.
    """
    hits = []
    for u in completed:
        for (sid, t_in, t_out) in u.get("path", []):
            if sid == station and t_out >= since_t:
                hits.append({"vin": u["vin"], "route": u["route"],
                             "built_at_s": t_out})
                break
    hits.sort(key=lambda h: h["built_at_s"])
    return {
        "station": station,
        "station_name": F.STATION[station].name,
        "since_s": since_t,
        "count": len(hits),
        "first_vin": hits[0]["vin"] if hits else None,
        "last_vin": hits[-1]["vin"] if hits else None,
        "vins": [h["vin"] for h in hits],
        "sample": hits[:12],
    }

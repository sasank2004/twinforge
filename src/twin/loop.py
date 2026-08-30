"""
TwinForge - the live loop (P2).

This is what turns a batch simulator into a *twin*: ingest -> update state ->
detect -> forecast -> emit, on a timer, with the SAME code path for a live feed
and a replay of a stored shift. It also carries the twin-drift check that keeps
the model honest: when observed throughput diverges from what the twin expects,
it flags a re-calibration (the difference between a twin and a stale snapshot).

`run_stream` is a generator of snapshot dicts; the API adapts it to Server-Sent
Events and the frontend animates each one (station colours, buffer flow, the
detector's ranked constraint, the T+5 forecast, and drift).
"""

from __future__ import annotations

from collections import deque
from typing import Iterator, Optional

import numpy as np
import pandas as pd

from . import factory as F
from . import features as FT
from .simulator import Simulator, SimConfig
from .detector import detect
from . import forecast as FC


def window_state_from_rows(rows: list[dict], lo: int, hi: int) -> dict:
    """Build a per-station window feature dict from accumulated minute rows."""
    sel = [r for r in rows if lo <= r["minute"] < hi]
    if not sel:
        return {}
    by: dict[str, list] = {}
    for r in sel:
        by.setdefault(r["station"], []).append(r)
    feat = {}
    for sid, rs in by.items():
        feat[sid] = {c: float(np.mean([r[c] for r in rs]))
                     for c in FT.FEATURE_COLS if c in rs[0]}
    return feat


class TwinDrift:
    """Rolling divergence between twin-expected and observed throughput."""

    def __init__(self, expected_uph: float = 3600.0 / F.TAKT_S,
                 threshold: float = 0.18):
        self.expected = expected_uph
        self.threshold = threshold
        self.recal_events: list[dict] = []
        self._last_produced = 0
        self._last_t = 0
        self._roll: deque = deque(maxlen=6)   # last ~6 minutes of UPH

    def update(self, t: int, produced: int) -> dict:
        dt = t - self._last_t
        if dt <= 0:
            return {"drift": 0.0, "observed_uph": 0.0, "expected_uph": self.expected,
                    "recalibrated": False}
        inst_uph = (produced - self._last_produced) / (dt / 3600.0)
        self._roll.append(inst_uph)
        self._last_produced, self._last_t = produced, t
        observed = float(np.mean(self._roll))
        drift = (self.expected - observed) / (self.expected + 1e-9)
        recal = False
        if drift > self.threshold and len(self._roll) >= self._roll.maxlen:
            # the twin has fallen out of step -> re-fit its expectation and log it
            self.recal_events.append({"t": t, "old_expected": round(self.expected, 1),
                                      "new_expected": round(observed, 1)})
            self.expected = observed
            self._roll.clear()
            recal = True
        return {"drift": round(max(0.0, drift), 3),
                "observed_uph": round(observed, 1),
                "expected_uph": round(self.expected, 1),
                "recalibrated": recal}


def _safe_forecast(state: dict) -> Optional[dict]:
    try:
        return FC.forecast_state(state)
    except Exception:
        return None


def run_stream(cfg: SimConfig, emit_every_s: int = 10,
               window_min: int = FT.WINDOW_MIN) -> Iterator[dict]:
    """
    Drive the simulator and yield an analysis snapshot every `emit_every_s`
    simulated seconds. Detector/forecast refresh once per new minute; the
    animation state (station/buffer) refreshes every emit.
    """
    sim = Simulator(cfg)
    drift = TwinDrift(expected_uph=3600.0 / F.TAKT_S * (1.0 / cfg.takt_scale))
    last_minute = -1
    analytics = {"detector": None, "forecast": None, "drift": None}

    while sim.t < cfg.duration_s:
        target = min(sim.t + emit_every_s, cfg.duration_s)
        while sim.t < target:
            sim.step()
        snap = sim.snapshot()

        minute = sim.t // 60
        if minute != last_minute and minute >= window_min:
            last_minute = minute
            state = window_state_from_rows(sim.minute_rows, minute - window_min, minute)
            if state:
                det = detect(state)
                fc = _safe_forecast(state)
                dr = drift.update(sim.t, sim.produced)
                analytics = {"detector": det, "forecast": fc, "drift": dr,
                             "window_state": state}

        yield {"live": snap, "analytics": analytics, "t": sim.t,
               "duration_s": cfg.duration_s}


def replay_scenario(seed: int = 3, faults=None, duration_s: int = 3600,
                    **kw) -> list[dict]:
    """Collect a whole scenario's snapshots (for tests / non-streaming use)."""
    return list(run_stream(SimConfig(seed=seed, faults=faults or [],
                                     duration_s=duration_s, **kw)))

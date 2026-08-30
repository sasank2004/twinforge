"""
TwinForge - Feature & label construction (P1).

Turns per-minute simulator output into:
  * `window_state`  - a per-station feature dict over a rolling window, the
    common input shape used by the detector AND the live loop.
  * a flat feature vector (fixed column order) for the forecast model.
  * training samples (features at minute m  ->  economic constraint at m+H),
    where the label is the ECONOMIC bottleneck (paired counterfactual), never
    the detector's own statistic.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import factory as F

# Per-station channels fed to the forecast model (order fixed).
FEATURE_COLS = [
    "frac_working", "frac_starved", "frac_blocked", "frac_down",
    "proc_time_mean", "effective_ct", "queue_in_mean",
    "current_mean", "vib_mean", "temp_mean",
]

# Forecast target classes: no-constraint + the nine stations.
CLASSES = ["NONE"] + F.STATION_IDS
HORIZON_MIN = 5          # predict the constraint 5 minutes ahead
WINDOW_MIN = 5           # aggregate features over a trailing 5-minute window


def window_state(features_df: pd.DataFrame, lo: int, hi: int) -> dict:
    """Mean of each per-station channel over minutes [lo, hi)."""
    w = features_df[(features_df.minute >= lo) & (features_df.minute < hi)]
    feat: dict[str, dict] = {}
    for sid, g in w.groupby("station"):
        feat[sid] = {c: float(g[c].mean()) for c in g.columns
                     if c not in ("minute", "station")}
    return feat


def flatten(state: dict) -> np.ndarray:
    """Fixed-order flat vector: [station x FEATURE_COLS]."""
    vec = []
    for sid in F.STATION_IDS:
        st = state.get(sid, {})
        for c in FEATURE_COLS:
            vec.append(float(st.get(c, 0.0)))
    return np.array(vec, dtype=float)


def flat_columns() -> list[str]:
    return [f"{sid}__{c}" for sid in F.STATION_IDS for c in FEATURE_COLS]


def label_at(minute: int, bottleneck: str, active_from_min: int) -> str:
    """Economic constraint if it is active at `minute`, else NONE."""
    if bottleneck == "NONE" or minute < active_from_min:
        return "NONE"
    return bottleneck


def build_samples(features_df: pd.DataFrame, bottleneck: str,
                  active_from_min: int,
                  horizon: int = HORIZON_MIN,
                  window: int = WINDOW_MIN) -> tuple[list[np.ndarray], list[str]]:
    """
    One sample per minute m: features over (m-window, m] -> label at m+horizon.
    """
    X: list[np.ndarray] = []
    y: list[str] = []
    max_min = int(features_df.minute.max())
    for m in range(window, max_min - horizon + 1):
        state = window_state(features_df, m - window, m)
        if not state:
            continue
        X.append(flatten(state))
        y.append(label_at(m + horizon, bottleneck, active_from_min))
    return X, y

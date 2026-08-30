"""
TwinForge - Preventive forecast engine (P1).

A deliberately simple, transparent model: multinomial logistic regression
(standardised features -> softmax over {NONE, S1..S9}). It predicts which
station will be the ECONOMIC constraint `HORIZON_MIN` minutes ahead, from the
current rolling-window telemetry. Logistic regression is chosen on purpose -
it is easily trained, calibrated, inspectable (per-feature weights), and hard
to attack in Q&A, exactly the properties the design values over a black box.

Nothing here recomputes the label from telemetry: the model is trained against
economic ground truth, so a good score is a real forecast, not an identity.
"""

from __future__ import annotations

import os
import pickle

import numpy as np

from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import Pipeline

from . import factory as F
from . import features as FT

MODEL_PATH = os.path.join(os.path.dirname(__file__), "..", "..",
                          "data", "processed", "forecast_model.pkl")


def train(X: np.ndarray, y: list[str]) -> Pipeline:
    pipe = Pipeline([
        ("scale", StandardScaler()),
        # multinomial softmax is the default in modern sklearn
        ("clf", LogisticRegression(max_iter=2000, C=0.5,
                                   class_weight="balanced")),
    ])
    pipe.fit(X, y)
    return pipe


def save(pipe: Pipeline, path: str = MODEL_PATH) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        pickle.dump({"pipe": pipe, "columns": FT.flat_columns(),
                     "classes": list(pipe.classes_)}, f)


_CACHE: dict = {}


def load(path: str = MODEL_PATH):
    if "pipe" not in _CACHE:
        with open(path, "rb") as f:
            _CACHE.update(pickle.load(f))
    return _CACHE["pipe"]


# Prevent what-if actions: perturb the telemetry FEATURES and re-run the SAME
# model (ML sensitivity), never a re-simulation. "If I cool the machine / add an
# operator / throttle input, does the predicted risk drop?"
# each action targets a specific telemetry channel; `needs` gates it to stations
# that actually carry that sensor (you can't "cool" a station with no temp probe).
PREVENT_ACTIONS = {
    "add_operator":   {"label": "Add operator", "desc": "Speeds the station up ~25% (labour).", "needs": None},
    "throttle_input": {"label": "Throttle upstream release", "desc": "Reduces queue pressure into the station.", "needs": None},
    "cool_machine":   {"label": "Cool the machine", "desc": "Drops temperature toward baseline. Only helps if it is overheating.", "needs": "temp"},
    "service_tool":   {"label": "Service / re-seat tool", "desc": "Reduces vibration & current draw. Only helps if the tool is worn.", "needs": "vibration"},
}


_BASELINES: dict = {}


def _baseline(station: str, chan: str, default: float) -> float:
    global _BASELINES
    if not _BASELINES:
        import json
        p = os.path.join(os.path.dirname(__file__), "..", "..", "data", "processed", "baselines.json")
        try:
            with open(p) as f:
                _BASELINES = json.load(f)
        except Exception:
            _BASELINES = {"_": {}}
    return _BASELINES.get(station, {}).get(chan, {}).get("mean", default)


def apply_action(state: dict, station: str, action: str) -> dict:
    """
    Return a perturbed COPY of the feature state for one station. An intervention
    can only pull a signal back TOWARD its healthy baseline - never past it. So
    cooling an already-cool machine (a mechanical slowdown) changes nothing,
    while cooling an overheating one removes the driving signal. This is what
    keeps the what-if coherent: the fix only helps if the fault it targets is
    actually present.
    """
    new = {s: dict(v) for s, v in state.items()}
    st = new.get(station)
    if not st:
        return new
    if action == "add_operator":                 # labour: faster cycle time
        st["proc_time_mean"] = st.get("proc_time_mean", 0) * 0.75
        st["effective_ct"] = st.get("effective_ct", 0) * 0.75
    elif action == "throttle_input":             # less queue pressure
        st["queue_in_mean"] = st.get("queue_in_mean", 0) * 0.5
        st["frac_blocked"] = st.get("frac_blocked", 0) * 0.5
    elif action == "cool_machine":               # temp -> WORKING baseline (only if hot)
        # a constraining station is always working, so compare against its
        # working level, not the idle-inclusive average - clamping never pushes
        # a merely-busy station below normal, so it is a no-op unless truly hot.
        st["temp_mean"] = min(st.get("temp_mean", 0), _baseline(station, "temp_mean", 40.0) + 0.5)
    elif action == "service_tool":               # vibration/current -> working level
        st["vib_mean"] = min(st.get("vib_mean", 0), VIB_WORKING)
        st["current_mean"] = min(st.get("current_mean", 0), CURRENT_WORKING)
    return new


VIB_WORKING = 1.65         # a healthy tool's vibration while working (mm/s)
CURRENT_WORKING = 15.3     # a healthy motor's current while working (A)


def prevent_whatif(state: dict, station: str, action: str,
                   path: str = MODEL_PATH) -> dict:
    """ML sensitivity: risk for `station` before vs after the action."""
    before = forecast_state(state, path)
    after = forecast_state(apply_action(state, station, action), path)
    r0 = before["station_probs"].get(station, 0.0)
    r1 = after["station_probs"].get(station, 0.0)
    return {
        "station": station, "action": action,
        "label": PREVENT_ACTIONS.get(action, {}).get("label", action),
        "risk_before": round(r0, 3), "risk_after": round(r1, 3),
        "delta": round(r1 - r0, 3),
        "averts": r1 < 0.3 and r1 < r0 * 0.7,
        "before_ranked": before["ranked"][:4], "after_ranked": after["ranked"][:4],
    }


def forecast_state(state: dict, path: str = MODEL_PATH) -> dict:
    """
    Given a rolling-window per-station feature dict, return the predicted
    constraint at T+HORIZON with a probability per station.
    """
    pipe = load(path)
    x = FT.flatten(state).reshape(1, -1)
    proba = pipe.predict_proba(x)[0]
    classes = list(pipe.classes_)
    probs = {c: float(p) for c, p in zip(classes, proba)}
    # split NONE out; rank the stations
    station_probs = {s: probs.get(s, 0.0) for s in F.STATION_IDS}
    ranked = sorted(station_probs.items(), key=lambda kv: -kv[1])
    top, top_p = ranked[0]
    p_none = probs.get("NONE", 0.0)
    predicted = "NONE" if p_none >= top_p else top
    return {
        "predicted": predicted,
        "p_none": round(p_none, 3),
        "station_probs": {s: round(p, 3) for s, p in station_probs.items()},
        "ranked": [(s, round(p, 3)) for s, p in ranked],
        "horizon_min": FT.HORIZON_MIN,
    }

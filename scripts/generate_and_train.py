"""
TwinForge - one-shot data generation + model training (P1).

Simulates a diverse set of shifts, labels each with the ECONOMIC bottleneck
(paired counterfactual), trains the logistic forecast model, measures the
noise floor and holdout accuracy/regret, and writes healthy signal baselines
for the diagnostic engine. Run:

    python -m scripts.generate_and_train
"""

from __future__ import annotations

import os
import sys
import json
import time

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.twin import factory as F
from src.twin import features as FT
from src.twin import forecast as FC
from src.twin.simulator import SimConfig, FaultSpec, Simulator
from src.twin.ground_truth import economic_bottleneck, noise_floor
from src.twin.detector import detect, regret

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "data", "processed")
DURATION = 3600
RAMP_S = 600


def run_specs():
    """Return list of (tag, SimConfig, active_from_min)."""
    specs = []
    # healthy runs
    for seed in [1, 4, 5, 6, 7, 8, 11, 12, 13, 14]:
        specs.append((f"healthy_s{seed}",
                      SimConfig(seed=seed, duration_s=DURATION), 0))
    # faulted grid: every station becomes a constraint under a big-enough fault
    for sid in F.STATION_IDS:
        for kind in ("degrade_ramp", "degrade_step"):
            for mag in (0.7, 1.2):
                for seed in (2, 3):
                    onset = 600
                    f = FaultSpec(sid, kind, onset, magnitude=mag, ramp_s=RAMP_S)
                    active = onset // 60 + (RAMP_S // 120 if kind == "degrade_ramp" else 0)
                    specs.append((f"{sid}_{kind}_{int(mag*100)}_s{seed}",
                                  SimConfig(seed=seed, duration_s=DURATION, faults=[f]),
                                  active))
    # a few forced-down runs
    for sid in ("S4", "S7", "S9"):
        for seed in (2, 3):
            f = FaultSpec(sid, "station_down", 900, duration_s=1200)
            specs.append((f"{sid}_down_s{seed}",
                          SimConfig(seed=seed, duration_s=DURATION, faults=[f]), 15))
    # COMPLICATIONS - teach the model that a health signal (temp / vibration /
    # current) leads the constraint, so cooling/servicing is a coherent fix.
    onset = 600
    active = onset // 60 + RAMP_S // 120
    for sid in F.STATION_IDS:
        sens = F.STATION[sid].sensors
        kinds = []
        if "temp" in sens:
            kinds.append("overheating")
        if "vibration" in sens or "current" in sens:
            kinds.append("tool_wear")
        for kind in kinds:
            for mag in (0.8, 1.3):
                for seed in (2, 3):
                    f = FaultSpec(sid, kind, onset, magnitude=mag, ramp_s=RAMP_S)
                    specs.append((f"{sid}_{kind}_{int(mag*100)}_s{seed}",
                                  SimConfig(seed=seed, duration_s=DURATION, faults=[f]), active))
    return specs


CACHE = os.path.join(OUT, "_gen_cache.pkl")


def main():
    import pickle
    os.makedirs(OUT, exist_ok=True)
    specs = run_specs()
    t0 = time.time()

    if os.path.exists(CACHE) and "--regen" not in sys.argv:
        print(f"Loading cached samples from {CACHE} (pass --regen to rebuild)")
        with open(CACHE, "rb") as fh:
            rows_by_run, baseline_accum, label_dist = pickle.load(fh)
    else:
        print(f"Generating {len(specs)} runs...")
        rows_by_run, baseline_accum, label_dist = _generate(specs, t0)
        with open(CACHE, "wb") as fh:
            pickle.dump((rows_by_run, baseline_accum, label_dist), fh)

    _train_and_eval(rows_by_run, baseline_accum, label_dist, specs, t0)


def _generate(specs, t0):
    rows_by_run = []
    baseline_accum = {s: {c: [] for c in FT.FEATURE_COLS} for s in F.STATION_IDS}
    label_dist = {}
    for i, (tag, cfg, active) in enumerate(specs):
        res = Simulator(cfg).run()
        gains = {}
        if cfg.faults:
            window = (cfg.faults[0].onset_s + RAMP_S, DURATION)
            eb = economic_bottleneck(cfg, window=window)
            econ, gains = eb["bottleneck"], eb["gains"]
        else:
            econ = "NONE"
        Xs, ys = FT.build_samples(res.features, econ, active)
        mature = FT.window_state(res.features, 45, 60)   # for detector scoring
        rows_by_run.append((tag, Xs, ys, econ, gains, mature))
        for yy in ys:
            label_dist[yy] = label_dist.get(yy, 0) + 1
        # accumulate healthy baselines (steady-state minutes only)
        if not cfg.faults:
            st = FT.window_state(res.features, 10, 60)
            for s in F.STATION_IDS:
                for c in FT.FEATURE_COLS:
                    baseline_accum[s][c].append(st.get(s, {}).get(c, 0.0))
        if (i + 1) % 15 == 0:
            print(f"  {i+1}/{len(specs)}  ({time.time()-t0:.0f}s)  last={tag} econ={econ}")
    return rows_by_run, baseline_accum, label_dist


def _train_and_eval(rows_by_run, baseline_accum, label_dist, specs, t0):
    # ---- split by run to avoid leakage, then train ----
    rng = np.random.default_rng(0)
    idx = rng.permutation(len(rows_by_run))
    n_hold = max(6, len(rows_by_run) // 5)
    hold = set(idx[:n_hold].tolist())

    Xtr, ytr, Xte, yte, te_runs = [], [], [], [], []
    for j, row in enumerate(rows_by_run):
        tag, Xs, ys, econ, gains, mature = row
        if j in hold:
            Xte += Xs; yte += ys; te_runs.append(row)
        else:
            Xtr += Xs; ytr += ys
    Xtr, ytr = np.array(Xtr), ytr
    print(f"\nTrain {len(Xtr)} samples, holdout {len(Xte)} samples, "
          f"{len(hold)} holdout runs")
    print("label distribution:", dict(sorted(label_dist.items())))

    pipe = FC.train(Xtr, ytr)
    FC.save(pipe)
    train_acc = pipe.score(Xtr, ytr)
    hold_acc = pipe.score(np.array(Xte), yte) if Xte else float("nan")
    print(f"forecast accuracy: train={train_acc:.3f}  holdout={hold_acc:.3f}")

    # ---- detector regret vs utilisation, on faulted holdout runs ----
    det_regs, util_regs, det_hits = [], [], 0
    n_faulted = 0
    for tag, Xs, ys, econ, gains, mature in te_runs:
        if econ == "NONE" or not gains:
            continue
        n_faulted += 1
        d = detect(mature)
        det_regs.append(regret(d["constraint"] if d["is_constraint"]
                               else d["leading_candidate"], gains))
        util_regs.append(regret(d["utilisation_pick"], gains))
        if (d["constraint"] if d["is_constraint"] else d["leading_candidate"]) == econ:
            det_hits += 1
    det_regret = float(np.mean(det_regs)) if det_regs else 0.0
    util_regret = float(np.mean(util_regs)) if util_regs else 0.0
    det_top1 = det_hits / n_faulted if n_faulted else float("nan")
    print(f"detector: top-1={det_top1:.3f}  regret={det_regret:.2f} cars  "
          f"vs utilisation regret={util_regret:.2f} cars  (n={n_faulted})")

    # sparse-sensing graceful-degradation: does the detector still find the
    # constraint from the observed (sensor-poor) view?
    from src.twin.observability import calibrate as obs_calibrate
    obs_runs = [(mature, econ) for (_, _, _, econ, _, mature) in rows_by_run]
    obs_cal = obs_calibrate(obs_runs)
    print(f"sparse sensing: detector agrees with full-sensor constraint on "
          f"{obs_cal['sparse_agreement']} of {obs_cal['n']} faulted runs")

    # noise floor
    nf = noise_floor(range(1, 6))

    # baselines (healthy mean/std per station per channel)
    baselines = {}
    for s in F.STATION_IDS:
        baselines[s] = {}
        for c in FT.FEATURE_COLS:
            arr = np.array(baseline_accum[s][c]) if baseline_accum[s][c] else np.array([0.0])
            baselines[s][c] = {"mean": float(arr.mean()),
                               "std": float(arr.std() + 1e-6)}

    metrics = {
        "n_runs": len(specs),
        "n_train_samples": int(len(Xtr)),
        "n_holdout_samples": int(len(Xte)),
        "forecast_train_acc": round(float(train_acc), 4),
        "forecast_holdout_acc": round(float(hold_acc), 4),
        "detector_top1": round(float(det_top1), 4),
        "detector_regret_cars": round(float(det_regret), 2),
        "utilisation_regret_cars": round(float(util_regret), 2),
        "sparse_sensing_agreement": obs_cal["sparse_agreement"],
        "noise_floor_cars": round(float(nf), 2),
        "horizon_min": FT.HORIZON_MIN,
        "window_min": FT.WINDOW_MIN,
        "label_distribution": label_dist,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    with open(os.path.join(OUT, "metrics.json"), "w") as f:
        json.dump(metrics, f, indent=2)
    with open(os.path.join(OUT, "baselines.json"), "w") as f:
        json.dump(baselines, f, indent=2)
    with open(os.path.join(OUT, "layout.json"), "w") as f:
        json.dump(F.to_dict(), f, indent=2)

    print(f"\nnoise floor: {nf:.2f} cars")
    print(f"saved model + metrics + baselines + layout to {OUT}")
    print(f"total {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()

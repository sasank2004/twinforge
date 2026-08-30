"""TwinForge invariant tests. Run: python -m pytest -q"""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.twin import factory as F
from src.twin.simulator import Simulator, SimConfig, FaultSpec, run_shift
from src.twin.ground_truth import economic_bottleneck, noise_floor
from src.twin import features as FT
from src.twin.detector import detect, regret


def test_topology_is_a_dag_with_balanced_routes():
    order = F.topo_order()
    assert len(order) == len(F.STATION_IDS)
    # every route has length 4 (balanced line) and covers real edges
    assert all(len(p) == 4 for p in F.ROUTES.values())
    for r, path in F.ROUTES.items():
        for a, b in zip(path[:-1], path[1:]):
            assert (a, b) in F.EDGE
    # healthy line is not saturated anywhere
    assert all(F.healthy_utilisation(s) < 1.0 for s in F.STATION_IDS)


def test_conservation_holds():
    sim = Simulator(SimConfig(seed=5, duration_s=1800))
    sim.run()
    backlog = sum(len(q) for q in sim.inject_q.values())
    assert sim.produced + sim._wip() + backlog == sim.vin_counter


def test_healthy_line_flows_at_takt_with_low_variance():
    prod = [run_shift(seed=s, duration_s=3600).kpis["produced"] for s in range(1, 16)]
    assert np.mean(prod) > 260          # near the 300 takt
    assert np.std(prod) < 15            # deadlock-free, stable


def test_no_deadlock_after_a_sink_outage():
    # seed 16 previously deadlocked permanently after an S9 outage
    r = run_shift(seed=16, duration_s=3600)
    assert r.kpis["produced"] > 260


def test_crn_noise_floor_is_small():
    assert noise_floor(range(1, 6)) < 5.0


@pytest.mark.parametrize("sid", ["S4", "S7", "S9"])
def test_economic_bottleneck_finds_the_faulted_station(sid):
    cfg = SimConfig(seed=3, duration_s=3600,
                    faults=[FaultSpec(sid, "degrade_step", 600, 0.8)])
    assert economic_bottleneck(cfg, window=(1200, 3600))["bottleneck"] == sid


def test_detector_beats_utilisation_on_a_fault():
    cfg = SimConfig(seed=3, duration_s=3600,
                    faults=[FaultSpec("S7", "degrade_ramp", 600, 0.9, 600)])
    r = Simulator(cfg).run()
    state = FT.window_state(r.features, 45, 60)
    gains = economic_bottleneck(cfg, window=(1200, 3600))["gains"]
    d = detect(state)
    pick = d["constraint"] if d["is_constraint"] else d["leading_candidate"]
    assert regret(pick, gains) <= regret(d["utilisation_pick"], gains)


def test_speedup_of_non_constraint_recovers_nothing():
    # the mirage: speeding a non-constraint side branch (S6) gains ~nothing
    cfg = SimConfig(seed=3, duration_s=3600,
                    faults=[FaultSpec("S7", "degrade_step", 600, 0.8)])
    eb = economic_bottleneck(cfg, window=(1200, 3600))
    assert eb["gains"]["S6"] <= 3

"""
TwinForge - Discrete-Event Simulator over the fixed factory DAG (P0).

Design goals that the old simulator failed:
  * The healthy line actually FLOWS at takt (~300 UPH), it does not gridlock.
  * Conservation holds - a unit is created at a source and destroyed at the
    sink, never duplicated or lost.
  * Real back-pressure: a station blocks when its downstream buffer is full and
    starves when its input buffers are empty, so bottlenecks propagate.
  * Common Random Numbers (CRN): a run is fully determined by its seed, so a
    counterfactual (same seed, one parameter changed) is a *paired* comparison.

The simulator is a stepper: `Simulator.step()` advances one tick and
`Simulator.snapshot()` exposes live state (this drives the live loop and the
animated frontend). `run()` steps to the end and returns aggregated per-minute
features + KPIs + a genealogy log (this drives data generation).

A unit is assigned a route at injection; splits are route-driven, merges pool
their inputs (FIFO). Stations carry states working / blocked / starved / down.
Sensor channels (motor_current, vibration, temperature) respond to the
station's own state and fault and to a small lagged thermal coupling from
upstream - which is the physical ground truth the Diagnostic engine recovers.
"""

from __future__ import annotations

import math
from collections import deque, defaultdict
from dataclasses import dataclass, field
from typing import Optional, Callable

import numpy as np
import pandas as pd

from . import factory as F


# --- Per-station physical signal baselines ---------------------------------

NOMINAL_TEMP = {
    "S1": 40.0, "S2": 55.0, "S3": 70.0, "S4": 38.0, "S5": 30.0,
    "S6": 34.0, "S7": 44.0, "S8": 32.0, "S9": 28.0,
}
THERMAL_LAG_S = 180          # upstream heat shows downstream after ~3 min
THERMAL_COUPLING = 0.10      # fraction of an upstream station's temp-excess

# Flow faults change throughput; tool_drift is a QUALITY fault - it drifts a
# fastening tool's torque out of spec WITHOUT changing cycle time, so units come
# out defective while throughput looks perfectly healthy (surfaces late at QA).
# Flow faults move ONLY cycle time (a mechanical / staffing slowdown - it does
# not, by itself, heat the motor or shake the tool). Complications are the
# sensor-specific CAUSES that DO move a health signal AND drag cycle time with
# them, so the signal is a *leading indicator* of the constraint the model can
# act on early. tool_drift is the quality (defect) fault - no throughput change.
FLOW_FAULTS = ("degrade_ramp", "degrade_step", "station_down")
COMPLICATIONS = ("overheating", "tool_wear")
FAULT_KINDS = ["none", *FLOW_FAULTS, *COMPLICATIONS, "tool_drift"]

# fastening tools: nominal torque (N·m) and the +/- spec band (fraction).
NOMINAL_TORQUE = {"S4": 45.0, "S7": 52.0, "S8": 38.0}
TORQUE_SPEC = 0.10          # a joint is defective when |torque deviation| > 10%
NOMINAL_VOLTAGE = {"S5": 400.0, "S6": 380.0}
# how far a mature complication drives its signal, and the cycle-time drag it adds
COMPLICATION_SIGNAL = {"overheating": {"temp": 16.0},
                       "tool_wear": {"vibration": 3.2, "current": 6.0}}
COMPLICATION_CT = 0.55     # a mature complication also slows the station ~55%


@dataclass
class FaultSpec:
    station: str
    kind: str                 # one of FAULT_KINDS
    onset_s: int
    magnitude: float = 0.0     # ramp/step CT increase; complication severity; tool_drift drift
    ramp_s: int = 1200         # ramp duration
    duration_s: int = 900      # forced-down duration (station_down)

    def _frac(self, t: int) -> float:
        if t < self.onset_s:
            return 0.0
        return min(1.0, (t - self.onset_s) / max(1, self.ramp_s))

    def multiplier(self, t: int) -> float:
        """Cycle-time multiplier at time t (>=1). tool_drift/none/down have none."""
        if self.kind in ("none", "station_down", "tool_drift") or t < self.onset_s:
            return 1.0
        if self.kind == "degrade_step":
            return 1.0 + self.magnitude
        if self.kind == "degrade_ramp":
            return 1.0 + self.magnitude * self._frac(t)
        if self.kind in COMPLICATIONS:
            # the complication drags cycle time behind its signal (leading indicator)
            return 1.0 + COMPLICATION_CT * self.magnitude * self._frac(t)
        return 1.0

    def signal_excess(self, t: int, channel: str) -> float:
        """
        Additive rise on a health channel (temp/vibration/current). The signal
        LEADS the cycle-time drag - it reaches full over ~40% of the ramp, so it
        is a clear early-warning indicator well before the station constrains.
        """
        if self.kind not in COMPLICATIONS or t < self.onset_s:
            return 0.0
        frac = min(1.0, (t - self.onset_s) / max(1, self.ramp_s * 0.4))
        return COMPLICATION_SIGNAL.get(self.kind, {}).get(channel, 0.0) * self.magnitude * frac

    def quality_excess(self, t: int) -> float:
        """Fractional quality-channel drift from a tool_drift fault (0 otherwise)."""
        if self.kind != "tool_drift":
            return 0.0
        return self.magnitude * self._frac(t)

    def forced_down(self, t: int) -> bool:
        return (self.kind == "station_down"
                and self.onset_s <= t < self.onset_s + self.duration_s)


@dataclass
class SimConfig:
    seed: int = 42
    duration_s: int = F.SHIFT_S
    faults: list[FaultSpec] = field(default_factory=list)
    # live overrides (Scenario C): station -> cycle-time scale (<1 = faster)
    speed_scale: dict[str, float] = field(default_factory=dict)
    # release-rate lever (Scenario C): multiply the injection takt (>1 = slower release)
    takt_scale: float = 1.0
    mttf_s: float = 21600.0    # mean time to (random) failure while working (~6h)
    mttr_s: float = 180.0      # mean time to repair (~3 min)
    ct_cv: float = 0.08        # cycle-time coefficient of variation (lognormal)
    enable_random_faults: bool = True


class _St:
    """Mutable per-station runtime state."""
    __slots__ = ("id", "unit", "remaining", "ct0", "state", "down_left",
                 "work_ticks", "alive_ticks", "temp", "current", "vib",
                 "torque", "voltage", "temp_hist", "processed",
                 "defect_hist")

    def __init__(self, sid: str):
        self.id = sid
        self.unit: Optional[dict] = None
        self.remaining = 0.0
        self.ct0 = 0.0            # sampled work time for the current unit
        self.state = "starved"
        self.down_left = 0.0
        self.work_ticks = 0
        self.alive_ticks = 0
        self.temp = NOMINAL_TEMP[sid]
        self.current = 12.0
        self.vib = 1.0
        self.torque = NOMINAL_TORQUE.get(sid, 0.0)
        self.voltage = NOMINAL_VOLTAGE.get(sid, 0.0)
        self.temp_hist: deque = deque(maxlen=THERMAL_LAG_S + 2)
        self.processed = 0
        self.defect_hist: deque = deque(maxlen=50)   # recent units OK/NOK


class Simulator:
    def __init__(self, cfg: SimConfig):
        self.cfg = cfg
        # `rng` is used ONLY for output signal noise (does not affect flow), so
        # perturbing a counterfactual never desyncs the line dynamics.
        self.rng = np.random.default_rng(cfg.seed)
        self.t = 0
        self.st: dict[str, _St] = {s: _St(s) for s in F.STATION_IDS}
        # edge buffers: (src,dst) -> deque of units
        self.buf: dict[tuple, deque] = {k: deque() for k in F.EDGE_KEYS}
        # per-source injection queues
        self.inject_q: dict[str, deque] = {s: deque() for s in F.SOURCES}
        self.fault_by_st: dict[str, FaultSpec] = {f.station: f for f in cfg.faults}
        self.vin_counter = 0
        self.produced = 0
        self.defects_detected = 0
        self.qual = {sid: F.QUALITY[sid]["nominal"] for sid in F.QUALITY}  # quality channels
        self.completed_units: list[dict] = []
        self._next_inject = 0.0
        self._route_cycle = self._make_route_cycle()
        self._takt = F.TAKT_S * cfg.takt_scale
        # per-minute accumulators
        self._min_accum: dict = defaultdict(lambda: defaultdict(list))
        self.minute_rows: list[dict] = []
        # --- CRN: deterministic, speed-invariant random structure ----------
        # Failures are pre-scheduled per station from an independent stream, so
        # a speed-scale counterfactual does NOT move failures around (this was
        # the 67-car "noise floor" bug: shared-RNG desync).
        self._fail_sched, self._fail_idx = self._build_failures()

    def _build_failures(self):
        sched: dict[str, list[tuple]] = {}
        idx: dict[str, int] = {}
        for i, sid in enumerate(F.STATION_IDS):
            events: list[tuple] = []
            if self.cfg.enable_random_faults:
                frng = np.random.default_rng([self.cfg.seed, 9973 + i])
                t = float(frng.exponential(self.cfg.mttf_s))
                while t < self.cfg.duration_s:
                    # cap repair so a single background outage can't tank a
                    # whole shift through the single sink (keeps baseline sane)
                    repair = min(300.0, float(frng.exponential(self.cfg.mttr_s)))
                    events.append((t, repair))       # (scheduled fail tick, repair s)
                    t += repair + float(frng.exponential(self.cfg.mttf_s))
            sched[sid] = events
            idx[sid] = 0
        return sched, idx

    # -- injection scheduling ------------------------------------------------
    def _make_route_cycle(self):
        seq = []
        # weighted round-robin expansion of the route mix
        reps = {r: max(1, int(round(w))) for r, w in F.ROUTE_MIX.items()}
        maxr = max(reps.values())
        for i in range(maxr):
            for r in F.ROUTES:
                if i < reps[r]:
                    seq.append(r)
        idx = {"i": 0}

        def nxt():
            r = seq[idx["i"] % len(seq)]
            idx["i"] += 1
            return r
        return nxt

    def _new_unit(self, route: str) -> dict:
        self.vin_counter += 1
        # per-unit RNG: cycle-time noise is fixed to the unit, so a speed-scale
        # counterfactual changes only the MEAN cycle time, never the draw -> CRN.
        urng = np.random.default_rng([self.cfg.seed, self.vin_counter])
        variant = float(np.clip(urng.normal(1.0, 0.04), 0.9, 1.15))
        ct_noise = {sid: float(urng.standard_normal()) for sid in F.ROUTES[route]}
        return {
            "vin": f"V{self.vin_counter:06d}",
            "route": route,
            "t_in": self.t,
            "variant_ct": variant,
            "ct_noise": ct_noise,
            "path": [],           # genealogy: (station, t_in, t_out)
        }

    def _inject(self):
        while self.t >= self._next_inject:
            route = self._route_cycle()
            src = F.ROUTES[route][0]
            self.inject_q[src].append(self._new_unit(route))
            self._next_inject += self._takt

    # -- cycle time ----------------------------------------------------------
    def _effective_ct(self, sid: str) -> float:
        base = F.STATION[sid].nominal_ct
        mult = 1.0
        f = self.fault_by_st.get(sid)
        if f is not None:
            mult *= f.multiplier(self.t)
        mult *= self.cfg.speed_scale.get(sid, 1.0)
        return base * mult

    def _sample_ct(self, sid: str, unit: dict) -> float:
        mean = self._effective_ct(sid) * unit["variant_ct"]
        sigma = self.cfg.ct_cv
        z = unit["ct_noise"].get(sid, 0.0)     # fixed per (unit, station)
        return float(math.exp(math.log(max(1e-3, mean)) - 0.5 * sigma * sigma + sigma * z))

    # -- one tick ------------------------------------------------------------
    def step(self):
        if self.t >= self.cfg.duration_s:
            return False
        self._inject()

        # downstream-first so a freed slot is usable upstream in the same tick
        for sid in reversed(F.topo_order()):
            s = self.st[sid]
            st_static = F.STATION[sid]
            s.alive_ticks += 1
            f = self.fault_by_st.get(sid)

            # forced down (station_down fault)
            if f is not None and f.forced_down(self.t):
                s.state = "down"
                s.down_left = max(s.down_left, 1.0)

            # pre-scheduled failure (fires at the first working tick at/after
            # its scheduled time - count and near-timing invariant to speed)
            if s.state == "working":
                sc = self._fail_sched[sid]
                j = self._fail_idx[sid]
                if j < len(sc) and self.t >= sc[j][0]:
                    s.state = "down"
                    s.down_left = sc[j][1]
                    self._fail_idx[sid] = j + 1

            if s.state == "down":
                s.down_left -= F.TICK_S
                if s.down_left <= 0 and not (f is not None and f.forced_down(self.t)):
                    # resume: if a unit was mid-process when we failed, keep
                    # working on it; otherwise go idle and try to pull below.
                    s.state = "working" if s.unit is not None else "starved"
                if s.state == "down":
                    self._signals(sid)
                    self._accum(sid)
                    continue

            # advance the current unit; a blocked station keeps RETRYING its
            # offload every tick (without this, one outage -> permanent
            # deadlock, since a full downstream buffer latches 'blocked').
            if s.unit is not None and s.state in ("working", "blocked"):
                if s.state == "working":
                    s.remaining -= F.TICK_S
                if s.remaining <= 0:
                    self._complete_unit(sid)

            # try to start a new unit if free
            if s.unit is None and s.state != "down":
                self._try_pull(sid)

            self._signals(sid)
            self._accum(sid)

        self.t += 1
        if self.t % 60 == 0:
            self._flush_minute()
        return True

    def _complete_unit(self, sid: str):
        s = self.st[sid]
        unit = s.unit
        # record WORK-ONLY processing time (the sampled cycle), never wall
        # clock - wall clock would double-count any downtime already priced
        # in availability (the effective-CT double-counting KGP warns against)
        self._min_accum[sid]["proc"].append(s.ct0)
        # QUALITY: a quality station makes a defective unit when its quality
        # channel is out of spec - throughput is unaffected, the unit just
        # carries a latent defect that is only caught later at QA.
        if sid in F.QUALITY:
            q = F.QUALITY[sid]
            dev = abs(self.qual[sid] / q["nominal"] - 1.0)
            nok = dev > q["spec"]
            s.defect_hist.append(1 if nok else 0)
            if nok:
                unit.setdefault("defects", []).append(sid)
                unit.setdefault("defect_attrs", []).append(q["channel"])
        if F.STATION[sid].is_sink:
            unit["path"].append((sid, unit.get("_cur_in", self.t), self.t))
            self.produced += 1
            s.processed += 1
            defective = bool(unit.get("defects"))
            s.defect_hist.append(1 if defective else 0)     # QA inspection gate
            if defective:
                self.defects_detected += 1
            self.completed_units.append({
                "vin": unit["vin"], "route": unit["route"],
                "t_in": unit["t_in"], "t_out": self.t,
                "lead_time": self.t - unit["t_in"], "path": unit["path"],
                "defective": defective,
                "defect_stations": unit.get("defects", []),
                "defect_attrs": unit.get("defect_attrs", []),
            })
            s.unit = None
            s.state = "starved"
            return
        nxt = F.next_on_route(unit["route"], sid)
        edge = (sid, nxt)
        if len(self.buf[edge]) < F.EDGE[edge].capacity:
            unit["path"].append((sid, unit.get("_cur_in", self.t), self.t))
            self.buf[edge].append(unit)
            s.processed += 1
            s.unit = None
            s.state = "starved"     # will re-pull below / next tick
        else:
            s.state = "blocked"      # downstream full - hold the unit

    def _try_pull(self, sid: str):
        s = self.st[sid]
        if F.STATION[sid].is_source:
            q = self.inject_q[sid]
            if q:
                unit = q.popleft()
            else:
                s.state = "starved"
                return
        else:
            # pooled FIFO across input edges: pick the oldest waiting unit
            best_edge, best_unit = None, None
            for pred in F.predecessors(sid):
                dq = self.buf[(pred, sid)]
                if dq and (best_unit is None or dq[0]["t_in"] < best_unit["t_in"]):
                    best_edge, best_unit = (pred, sid), dq[0]
            if best_edge is None:
                s.state = "starved"
                return
            unit = self.buf[best_edge].popleft()
        unit["_cur_in"] = self.t
        s.unit = unit
        s.remaining = self._sample_ct(sid, unit)
        s.ct0 = s.remaining          # pure work time for this unit
        s.state = "working"

    # -- signals -------------------------------------------------------------
    def _signals(self, sid: str):
        s = self.st[sid]
        rng = self.rng
        # Health signals are NOT driven by a generic slowdown - a mechanical
        # slowdown does not heat the motor. They rise only from a COMPLICATION
        # (overheating / tool_wear) active on this station, which makes each
        # signal a leading indicator rather than a slowdown side-effect.
        f = self.fault_by_st.get(sid)
        cur_x = f.signal_excess(self.t, "current") if f else 0.0
        vib_x = f.signal_excess(self.t, "vibration") if f else 0.0
        temp_x = f.signal_excess(self.t, "temp") if f else 0.0
        if s.state == "working":
            s.work_ticks += 1
            base_cur, base_vib, heat = 15.0, 1.6, 3.0
        elif s.state == "down":
            base_cur, base_vib, heat = 3.0, 0.3, -1.0
        else:  # starved / blocked
            base_cur, base_vib, heat = 8.0, 0.6, 0.0
        s.current = float(np.clip(base_cur + cur_x + rng.normal(0, 0.15), 1, 80))
        s.vib = float(np.clip(base_vib + vib_x + rng.normal(0, 0.05), 0, 30))
        # upstream thermal coupling (lagged)
        up_heat = 0.0
        for pred in F.predecessors(sid):
            ph = self.st[pred].temp_hist
            if len(ph) > THERMAL_LAG_S:
                past = ph[-THERMAL_LAG_S]
                up_heat += THERMAL_COUPLING * max(0.0, past - NOMINAL_TEMP[pred])
        target = NOMINAL_TEMP[sid] + heat + up_heat + temp_x
        s.temp += 0.05 * (target - s.temp) + rng.normal(0, 0.15)   # 1st-order lag
        s.temp_hist.append(s.temp)
        # quality channel (defect origin) - a tool_drift fault pushes it out of
        # spec while cycle time stays put, so throughput looks healthy
        if sid in F.QUALITY:
            q = F.QUALITY[sid]
            f = self.fault_by_st.get(sid)
            ex = f.quality_excess(self.t) if f is not None else 0.0
            self.qual[sid] = float(q["nominal"] * (1.0 + ex) + rng.normal(0, q["nominal"] * 0.006))
            if q["channel"] == "torque":
                s.torque = self.qual[sid]
        # voltage (HV / battery stations)
        if sid in NOMINAL_VOLTAGE:
            s.voltage = float(NOMINAL_VOLTAGE[sid] + rng.normal(0, 1.5))

    # -- feature accumulation ------------------------------------------------
    def _queue_in(self, sid: str) -> int:
        if F.STATION[sid].is_source:
            return len(self.inject_q[sid])
        return sum(len(self.buf[(p, sid)]) for p in F.predecessors(sid))

    def _accum(self, sid: str):
        s = self.st[sid]
        a = self._min_accum[sid]
        a["state"].append(s.state)
        a["current"].append(s.current)
        a["vib"].append(s.vib)
        a["temp"].append(s.temp)
        a["queue_in"].append(self._queue_in(sid))

    def _flush_minute(self):
        minute = (self.t - 1) // 60
        for sid in F.STATION_IDS:
            a = self._min_accum[sid]
            if not a["state"]:
                continue
            states = a["state"]
            n = len(states)
            nominal = F.STATION[sid].nominal_ct
            proc = a["proc"]
            proc_mean = float(np.mean(proc)) if proc else nominal
            frac_down = states.count("down") / n
            availability = max(0.05, 1.0 - frac_down)
            row = {
                "minute": minute, "station": sid,
                "frac_working": states.count("working") / n,
                "frac_starved": states.count("starved") / n,
                "frac_blocked": states.count("blocked") / n,
                "frac_down": frac_down,
                "units_done": len(proc),
                "proc_time_mean": proc_mean,
                "effective_ct": proc_mean / availability,
                "current_mean": float(np.mean(a["current"])),
                "vib_mean": float(np.mean(a["vib"])),
                "temp_mean": float(np.mean(a["temp"])),
                "queue_in_mean": float(np.mean(a["queue_in"])),
            }
            self.minute_rows.append(row)
        self._min_accum = defaultdict(lambda: defaultdict(list))

    # -- live snapshot -------------------------------------------------------
    # full channel values (ground truth); observability decides what a station
    # actually exposes to the plant.
    def _channels(self, sid: str) -> dict:
        s = self.st[sid]
        ch = {"temp": round(s.temp, 1), "current": round(s.current, 1),
              "vibration": round(s.vib, 2)}
        if sid in NOMINAL_TORQUE:
            ch["torque"] = round(s.torque, 1)
        if sid in NOMINAL_VOLTAGE:
            ch["voltage"] = round(s.voltage, 1)
        if sid in F.QUALITY:                      # weld / coat / cell quality index
            q = F.QUALITY[sid]
            if q["channel"] not in ch:
                ch[q["channel"]] = round(self.qual[sid], 3)
        return ch

    def _expected(self, sid: str) -> dict:
        """Healthy design-point expectations for expected-vs-actual panels."""
        st = F.STATION[sid]
        exp = {"cycle_time": st.nominal_ct,
               "throughput_uph": round(F.arrival_share(sid) / (F.TAKT_S * self.cfg.takt_scale) * 3600, 1),
               "temp": NOMINAL_TEMP[sid]}
        if sid in NOMINAL_TORQUE:
            exp["torque"] = NOMINAL_TORQUE[sid]
        if sid in NOMINAL_VOLTAGE:
            exp["voltage"] = NOMINAL_VOLTAGE[sid]
        if sid in F.QUALITY:
            exp[F.QUALITY[sid]["channel"]] = F.QUALITY[sid]["nominal"]
        return exp

    def snapshot(self) -> dict:
        stations = {}
        hrs = max(1e-9, self.t / 3600)
        for sid in F.STATION_IDS:
            s = self.st[sid]
            util = s.work_ticks / s.alive_ticks if s.alive_ticks else 0.0
            dh = s.defect_hist
            stations[sid] = {
                "state": s.state,
                "queue_in": self._queue_in(sid),
                "utilization": round(util, 3),
                "motor_current": round(s.current, 2),
                "vibration": round(s.vib, 3),
                "temperature": round(s.temp, 2),
                "torque": round(s.torque, 2) if sid in NOMINAL_TORQUE else None,
                "voltage": round(s.voltage, 1) if sid in NOMINAL_VOLTAGE else None,
                "processed": s.processed,
                "throughput_uph": round(s.processed / hrs, 1),
                "eff_ct": round(self._effective_ct(sid), 2),
                "defect_rate": round(sum(dh) / len(dh), 3) if dh else 0.0,
                "sensors": list(F.STATION[sid].sensors),
                "channels": self._channels(sid),
                "expected": self._expected(sid),
            }
        edges = {f"{u}->{v}": len(self.buf[(u, v)]) for (u, v) in F.EDGE_KEYS}
        return {
            "t": self.t,
            "produced": self.produced,
            "defects_detected": self.defects_detected,
            "defect_rate": round(self.defects_detected / max(1, self.produced), 4),
            "wip": self._wip(),
            "throughput_uph": round(self.produced / hrs, 1),
            "stations": stations,
            "edges": edges,
        }

    def _wip(self) -> int:
        in_buffers = sum(len(d) for d in self.buf.values())
        in_stations = sum(1 for s in self.st.values() if s.unit is not None)
        return in_buffers + in_stations

    # -- batch run -----------------------------------------------------------
    def run(self) -> "SimResult":
        while self.step():
            pass
        if self._min_accum:
            self._flush_minute()
        feats = pd.DataFrame(self.minute_rows)
        kpis = {
            "seed": self.cfg.seed,
            "duration_s": self.cfg.duration_s,
            "produced": self.produced,
            "throughput_uph": round(self.produced / max(1e-9, self.cfg.duration_s / 3600), 2),
            "mean_lead_time_s": (round(float(np.mean([u["lead_time"] for u in self.completed_units])), 1)
                                 if self.completed_units else None),
            "faults": [(f.station, f.kind) for f in self.cfg.faults],
        }
        return SimResult(features=feats, kpis=kpis, completed=self.completed_units)


@dataclass
class SimResult:
    features: pd.DataFrame
    kpis: dict
    completed: list[dict]


def run_shift(seed: int = 42, faults: Optional[list[FaultSpec]] = None,
              duration_s: int = F.SHIFT_S, **kw) -> SimResult:
    return Simulator(SimConfig(seed=seed, faults=faults or [],
                               duration_s=duration_s, **kw)).run()


if __name__ == "__main__":
    print("Healthy 1-hour run:")
    r = run_shift(seed=1, duration_s=3600)
    print("  ", r.kpis)
    print("Faulted run (S7 degrade_ramp +80%):")
    r2 = run_shift(seed=1, duration_s=3600,
                   faults=[FaultSpec("S7", "degrade_ramp", onset_s=1200,
                                     magnitude=0.8, ramp_s=600)])
    print("  ", r2.kpis)

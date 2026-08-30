r"""
TwinForge - Fixed Factory Layout (the single source of truth for topology).

The prototype models one fixed mixed-model assembly network: a 9-station
directed acyclic graph (DAG) with three raw-entry stations, two converging
sub-assembly merges, and one final station. It is deliberately NOT a linear
line - merges and splits are what make bottleneck propagation interesting and
what make the backward causal graph worth drawing.

Topology (edges are finite-capacity buffers; a unit flows source -> sink):

      S1 ---> S4 ---\
     /  \            \
    /    \            S7 ---\
  in      \          /       \
   S2 ---> S4       / (S6)     S9 ---> OUT
                   /          /
   S3 ---> S5 --> S8 --------/
     \    /
      \  /
   S3 --> S6 ---> S7

Edges (buffer between the two stations):
    S1->S4, S1->S5, S2->S4, S3->S5, S3->S6,
    S4->S7, S6->S7, S5->S8, S7->S9, S8->S9

Routes (every source->sink path; each unit is assigned one at injection).
All five routes are length 4, so every unit is processed by exactly 4
stations - the line stays balanced and throughput is well defined:
    R1: S1 -> S4 -> S7 -> S9
    R2: S1 -> S5 -> S8 -> S9
    R3: S2 -> S4 -> S7 -> S9
    R4: S3 -> S5 -> S8 -> S9
    R5: S3 -> S6 -> S7 -> S9

Load (routes through each station) - drives which station is the natural
constraint: S9=5 (all flow), S7=3, {S1,S3,S4,S5,S8}=2, {S2,S6}=1.
Cycle times are tuned so that at the design takt every station runs below
capacity (the healthy line flows), yet a single fault can push one station
over its own limit and create - and propagate - a real bottleneck.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional


# --- Global line parameters -------------------------------------------------

TAKT_S: float = 12.0          # one unit injected into the system every 12 s
SHIFT_S: int = 8 * 3600       # 8-hour shift
TICK_S: float = 1.0           # simulation resolution (1 s)

SEGMENTS = ["BODY", "PAINT", "FINAL"]


@dataclass(frozen=True)
class Station:
    id: str
    name: str
    segment: str
    nominal_ct: float          # healthy processing time per unit (s)
    # instrumentation tier drives the sparse-sensing pipeline:
    #   HIGH   - full PLC/tool telemetry, state measured directly
    #   MEDIUM - partial (scans + some tags), state partly inferred
    #   LOW    - manual checklist station, no live tags, state inferred
    instrumentation: str
    x: float                   # normalised layout coord (0..1), left->right
    y: float                   # normalised layout coord (0..1), top->bottom
    is_source: bool = False
    is_sink: bool = False
    # Which INTERNAL sensors this station actually has. Every station always
    # measures throughput in/out (boundary scans); internal coverage is
    # heterogeneous - some have temperature, some voltage, fastening stations
    # have torque, manual stations have none. This is the real-plant condition.
    sensors: tuple = ()


# Internal sensor channels a station can carry (throughput in/out is universal).
SENSOR_UNITS = {
    "temp": "°C", "current": "A", "vibration": "mm/s",
    "torque": "N·m", "voltage": "V",
    "weld": "idx", "coat": "idx", "cell": "idx",
}
# Stations whose quality depends on a torque-controlled tool.
FASTENING = {"S4", "S7", "S8"}

# QUALITY stations: where a defect can ORIGINATE. Each has a quality channel
# (an observable sensor), a nominal value and a +/- spec band. A quality_drift
# fault pushes that channel out of spec -> defective units, with NO throughput
# change. Six origins across the network, so a defect can start almost anywhere.
QUALITY = {
    "S1": {"channel": "weld",   "nominal": 1.00, "spec": 0.10},  # weld integrity
    "S3": {"channel": "coat",   "nominal": 1.00, "spec": 0.10},  # coating thickness
    "S4": {"channel": "torque", "nominal": 45.0, "spec": 0.10},
    "S6": {"channel": "cell",   "nominal": 1.00, "spec": 0.10},  # cell voltage margin
    "S7": {"channel": "torque", "nominal": 52.0, "spec": 0.10},
    "S8": {"channel": "torque", "nominal": 38.0, "spec": 0.10},
}

# A defect ATTRIBUTE can be caused by the station that produces it OR by a
# downstream station that HANDLES (and can damage) it - e.g. paint applied at S3
# can be scuffed at marriage/trim; a weld can be over-stressed at marriage. So a
# QA reject does not point at one station: diagnosis distributes probability
# across the producer and the plausible handlers, using telemetry + genealogy.
ATTRIBUTE_HANDLERS = {
    "coat":   ["S7", "S8"],   # marriage / trim can scuff or scratch the coat
    "weld":   ["S7"],         # marriage over-stresses a weld joint
    "torque": [],             # each fastening station owns its own joints
    "cell":   ["S9"],         # final test can flag / damage a cell
}

# The nine stations. Cycle times chosen so healthy utilisation < 1 everywhere
# (see module docstring), with S9 the busiest by raw utilisation - the
# "utilisation trap" the detector must not fall for.
STATIONS: list[Station] = [
    Station("S1", "Body Framing",       "BODY",  22.0, "HIGH",   0.04, 0.16, is_source=True,
            sensors=("current", "vibration", "temp", "weld")),
    Station("S2", "Chassis Prep",       "BODY",  40.0, "LOW",    0.04, 0.50, is_source=True,
            sensors=()),                                   # manual - dark
    Station("S3", "Paint Shop",         "PAINT", 20.0, "HIGH",   0.04, 0.84, is_source=True,
            sensors=("temp", "current", "coat")),
    Station("S4", "Powertrain Drop",    "FINAL", 24.0, "HIGH",   0.34, 0.30,
            sensors=("torque", "current", "temp", "vibration")),
    Station("S5", "Interior & Wiring",  "FINAL", 23.0, "MEDIUM", 0.34, 0.58,
            sensors=("current", "voltage")),
    Station("S6", "Battery / HV",       "FINAL", 42.0, "LOW",    0.34, 0.86,
            sensors=("voltage", "cell")),                   # near-dark
    Station("S7", "Underbody Marriage", "FINAL", 16.0, "HIGH",   0.63, 0.40,
            sensors=("torque", "vibration", "current")),
    Station("S8", "Trim Line",          "FINAL", 21.0, "HIGH",   0.63, 0.72,
            sensors=("torque", "current")),
    Station("S9", "Final Assembly / QA","FINAL", 10.0, "HIGH",   0.90, 0.55, is_sink=True,
            sensors=("temp",)),                             # + QA defect gate
]

STATION_IDS = [s.id for s in STATIONS]
STATION = {s.id: s for s in STATIONS}


@dataclass(frozen=True)
class Edge:
    src: str
    dst: str
    capacity: int              # buffer slots between src and dst


# Buffer capacities: modest, so back-pressure (blocking) and starvation
# actually propagate rather than being absorbed by huge queues.
EDGES: list[Edge] = [
    Edge("S1", "S4", 8),
    Edge("S1", "S5", 8),
    Edge("S2", "S4", 8),
    Edge("S3", "S5", 8),
    Edge("S3", "S6", 8),
    Edge("S4", "S7", 8),
    Edge("S6", "S7", 8),
    Edge("S5", "S8", 8),
    Edge("S7", "S9", 8),
    Edge("S8", "S9", 8),
]

EDGE_KEYS = [(e.src, e.dst) for e in EDGES]
EDGE = {(e.src, e.dst): e for e in EDGES}


# Routes: each is an ordered list of stations, source -> sink.
ROUTES: dict[str, list[str]] = {
    "R1": ["S1", "S4", "S7", "S9"],
    "R2": ["S1", "S5", "S8", "S9"],
    "R3": ["S2", "S4", "S7", "S9"],
    "R4": ["S3", "S5", "S8", "S9"],
    "R5": ["S3", "S6", "S7", "S9"],
}

# Injection mix: relative frequency each route is fed into the line.
# Uniform keeps the arithmetic in the docstring exact; tweak to bias load.
ROUTE_MIX: dict[str, float] = {"R1": 1.0, "R2": 1.0, "R3": 1.0, "R4": 1.0, "R5": 1.0}

SOURCES = [s.id for s in STATIONS if s.is_source]
SINK = next(s.id for s in STATIONS if s.is_sink)


# --- Derived topology helpers ----------------------------------------------

def successors(sid: str) -> list[str]:
    return [e.dst for e in EDGES if e.src == sid]


def predecessors(sid: str) -> list[str]:
    return [e.src for e in EDGES if e.dst == sid]


def routes_through(sid: str) -> list[str]:
    return [r for r, path in ROUTES.items() if sid in path]


def load_of(sid: str) -> int:
    """Number of routes passing through a station (its relative flow)."""
    return len(routes_through(sid))


def next_on_route(route: str, sid: str) -> Optional[str]:
    path = ROUTES[route]
    i = path.index(sid)
    return path[i + 1] if i + 1 < len(path) else None


def topo_order() -> list[str]:
    """Kahn topological sort of the station DAG."""
    indeg = {s: len(predecessors(s)) for s in STATION_IDS}
    ready = [s for s in STATION_IDS if indeg[s] == 0]
    order: list[str] = []
    while ready:
        n = ready.pop(0)
        order.append(n)
        for m in successors(n):
            indeg[m] -= 1
            if indeg[m] == 0:
                ready.append(m)
    return order


def arrival_share(sid: str) -> float:
    """Fraction of total injected units that pass through this station."""
    total_mix = sum(ROUTE_MIX.values())
    through = sum(ROUTE_MIX[r] for r in routes_through(sid))
    return through / total_mix


def healthy_utilisation(sid: str) -> float:
    """Design-point utilisation = arrival_rate * cycle_time (must be < 1)."""
    st = STATION[sid]
    # units/sec passing this station = arrival_share * (1 injection / takt)
    arr_rate = arrival_share(sid) / TAKT_S
    return arr_rate * st.nominal_ct


def to_dict() -> dict:
    """Serialisable topology for the frontend graph renderer."""
    return {
        "takt_s": TAKT_S,
        "stations": [
            {**asdict(s),
             "load": load_of(s.id),
             "healthy_utilisation": round(healthy_utilisation(s.id), 3)}
            for s in STATIONS
        ],
        "edges": [asdict(e) for e in EDGES],
        "routes": ROUTES,
        "segments": SEGMENTS,
        "sources": SOURCES,
        "sink": SINK,
        "sensor_units": SENSOR_UNITS,
        "fastening": sorted(FASTENING),
        "quality": {k: v["channel"] for k, v in QUALITY.items()},
    }


if __name__ == "__main__":
    print("TwinForge factory layout")
    print(f"  takt = {TAKT_S}s  ->  theoretical {3600/TAKT_S:.0f} UPH")
    print(f"  topo order: {' -> '.join(topo_order())}")
    print("\n  station        load  nominal_ct  healthy_util  instr")
    for s in STATIONS:
        print(f"  {s.id} {s.name:20s} {load_of(s.id):3d}   {s.nominal_ct:6.1f}s"
              f"      {healthy_utilisation(s.id):.3f}     {s.instrumentation}")
    over = [s.id for s in STATIONS if healthy_utilisation(s.id) >= 1.0]
    print(f"\n  saturated at design point: {over or 'none (line flows)'}")

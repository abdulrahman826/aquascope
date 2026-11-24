"""Net1 topology parsed from the EPANET .inp file (coordinates, elements) + NetworkX graph."""
from __future__ import annotations

from functools import lru_cache

import networkx as nx

from . import config as C


def _sections(path) -> dict[str, list[list[str]]]:
    out, cur = {}, None
    for line in open(path, encoding="utf-8", errors="ignore"):
        line = line.split(";")[0].strip()
        if not line:
            continue
        if line.startswith("["):
            cur = line.strip("[]").upper()
            out.setdefault(cur, [])
        elif cur:
            out[cur].append(line.split())
    return out


@lru_cache(maxsize=1)
def topology() -> dict:
    s = _sections(C.NET1_INP)
    coords = {r[0]: (float(r[1]), float(r[2])) for r in s.get("COORDINATES", [])}
    nodes = ([{"id": r[0], "type": "junction", "elevation_m": float(r[1]) * 0.3048} for r in s["JUNCTIONS"]]
             + [{"id": r[0], "type": "reservoir", "elevation_m": float(r[1]) * 0.3048} for r in s["RESERVOIRS"]]
             + [{"id": r[0], "type": "tank", "elevation_m": float(r[1]) * 0.3048} for r in s["TANKS"]])
    for n in nodes:
        n["x"], n["y"] = coords[n["id"]]
        n["pressure_sensor"] = n["id"] in C.PRESSURE_SENSORS
    links = ([{"id": r[0], "type": "pipe", "from": r[1], "to": r[2], "length_m": float(r[3]) * 0.3048,
               "diameter_mm": round(float(r[4]) * 25.4)} for r in s["PIPES"]]
             + [{"id": r[0], "type": "pump", "from": r[1], "to": r[2], "length_m": 1.0, "diameter_mm": None}
                for r in s["PUMPS"]])
    for l in links:
        l["flow_sensor"] = l["id"] in C.FLOW_SENSORS
    return {"network_id": C.NETWORK_ID, "nodes": nodes, "links": links,
            "sensors": {"pressure": C.PRESSURE_SENSORS, "flow": C.FLOW_SENSORS},
            "units": {"length": "m", "pressure": "m", "flow": "L/s"}}


@lru_cache(maxsize=1)
def graph() -> nx.Graph:
    g = nx.Graph()
    for l in topology()["links"]:
        g.add_edge(l["from"], l["to"], id=l["id"], weight=l["length_m"])
    return g


def link_distance_m(a: str, b: str) -> float:
    """Shortest network path distance between two pipes (between nearest endpoints; 0 if same pipe)."""
    if a == b:
        return 0.0
    ends = {l["id"]: (l["from"], l["to"]) for l in topology()["links"]}
    g = graph()
    return min(nx.shortest_path_length(g, u, v, weight="weight") for u in ends[a] for v in ends[b])


def node_distance_m(a: str, b: str) -> float:
    return nx.shortest_path_length(graph(), a, b, weight="weight")

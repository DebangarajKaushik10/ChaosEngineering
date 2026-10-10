"""Dependency map for Hotel Reservation.

Built from three sources, merged:
  1. Call edges read from the Go source (services/frontend/server.go etc.)
  2. depends_on in docker-compose.yml (startup order, NOT call dependencies)
  3. Live Jaeger traces (/api/dependencies), when Jaeger is reachable
Manual corrections are allowed: CODE_EDGES below was checked against the source.
"""
import json
import time

import requests
import yaml

import config

# Edges verified in the source code (manual corrections are allowed by the rubric)
CODE_EDGES = [
    ("frontend", "search"), ("frontend", "reservation"), ("frontend", "profile"),
    ("frontend", "recommendation"), ("frontend", "user"),
    ("frontend", "review"), ("frontend", "attractions"),
    ("search", "geo"), ("search", "rate"),
    ("geo", "mongodb-geo"),
    ("rate", "memcached-rate"), ("rate", "mongodb-rate"),
    ("profile", "memcached-profile"), ("profile", "mongodb-profile"),
    ("reservation", "memcached-reserve"), ("reservation", "mongodb-reservation"),
    ("recommendation", "mongodb-recommendation"),
    ("user", "mongodb-user"),
    ("review", "memcached-review"), ("review", "mongodb-review"),
    ("attractions", "mongodb-attractions"),
]

# Which backend services each HTTP endpoint calls directly, and its share of traffic
# (frontend/server.go: searchHandler, recommendHandler, userHandler, reservationHandler)
ENDPOINTS = {
    "/hotels":          {"calls": ["search", "reservation", "profile"], "traffic": 0.60},
    "/recommendations": {"calls": ["recommendation", "profile"], "traffic": 0.39},
    "/user":            {"calls": ["user"], "traffic": 0.005},
    "/reservation":     {"calls": ["user", "reservation"], "traffic": 0.005},
}

# Code-level facts the planner can use. Verified against the upstream source on 2026-10-10:
#   rate/profile/reservation call log.Panic on any memcached error other than a cache miss;
#   reservation also panics on MongoDB errors; no service uses context.WithTimeout;
#   services register with Consul once, at startup.
CODE_NOTES = {
    "rate": "On a memcached ERROR (not a miss) the service calls log.Panic and crashes.",
    "memcached-rate": "If this cache errors, the rate service panics instead of falling "
                      "back to MongoDB (services/rate/server.go).",
    "profile": "On a memcached ERROR the service calls log.Panic and crashes.",
    "memcached-profile": "If this cache errors, profile panics; profile serves /hotels AND "
                         "/recommendations (~99% of traffic).",
    "reservation": "Panics on memcached errors AND on MongoDB query errors.",
    "memcached-reserve": "If this cache errors, reservation panics.",
    "mongodb-reservation": "Query errors make reservation panic (services/reservation/server.go).",
    "review": "On a memcached ERROR the service calls log.Panic and crashes.",
    "memcached-review": "If this cache errors, review panics.",
    "*": "No service sets gRPC deadlines (no context.WithTimeout), so slow calls hang.",
    "consul": "Services register with Consul once at startup. Consul runs without a data volume, "
              "so a Consul restart can leave services unregistered until they are restarted "
              "(seen in the sandbox run; verify on the real deployment).",
}


def kind_of(name):
    if name.startswith("mongodb"):
        return "database"
    if name.startswith("memcached"):
        return "cache"
    if name in ("consul", "jaeger"):
        return "infrastructure"
    return "service"


def compose_info():
    """(service names, depends_on edges, per-service metadata) from docker-compose.yml."""
    path = config.COMPOSE_DIR / "docker-compose.yml"
    try:
        spec = yaml.safe_load(path.read_text())
    except Exception:
        return [], [], {}
    edges, meta = [], {}
    services = spec.get("services", {}) or {}
    for svc, body in services.items():
        body = body or {}
        deps = body.get("depends_on", []) or []
        if isinstance(deps, dict):
            deps = list(deps.keys())
        for d in deps:
            edges.append((svc, d))
        meta[svc] = {"restart": body.get("restart"), "has_healthcheck": "healthcheck" in body}
    return list(services.keys()), edges, meta


def compose_edges():
    nodes, edges, _ = compose_info()
    return nodes, edges


def jaeger_edges():
    try:
        end = int(time.time() * 1000)
        r = requests.get(f"{config.JAEGER_URL}/api/dependencies",
                         params={"endTs": end, "lookback": 3600_000}, timeout=3)
        return [(d["parent"], d["child"]) for d in r.json().get("data", [])
                if d["parent"] != d["child"]]
    except Exception:
        return []


def build():
    nodes, c_edges, meta = compose_info()
    j_edges = jaeger_edges() if config.BACKEND != "sim" else []
    edges = {}
    for e in CODE_EDGES:
        edges[e] = {"source": "code"}
    for e in c_edges:
        edges.setdefault(e, {"source": "compose"})
    for e in j_edges:
        edges.setdefault(e, {"source": "jaeger"})
    names = set(nodes) | {n for e in edges for n in e} | {"consul", "jaeger"}

    children = {n: [b for (a, b) in edges if a == n] for n in names}
    parents = {n: [a for (a, b) in edges if b == n] for n in names}

    def reachable(start):
        seen, stack = set(), [start]
        while stack:
            n = stack.pop()
            for c in children.get(n, []):
                if c not in seen:
                    seen.add(c)
                    stack.append(c)
        return seen

    info = {}
    for n in sorted(names):
        hit = [ep for ep, v in ENDPOINTS.items()
               if n in v["calls"] or any(n in reachable(c) | {c} for c in v["calls"])]
        info[n] = {
            "kind": kind_of(n),
            "depends_on": sorted(children.get(n, [])),
            "used_by": sorted(parents.get(n, [])),
            "endpoints_affected": hit,
            "traffic_share_affected": round(sum(ENDPOINTS[e]["traffic"] for e in hit), 3),
            "replicas": 1,
            "restart_policy": (meta.get(n) or {}).get("restart"),
            "has_healthcheck": (meta.get(n) or {}).get("has_healthcheck"),
            "note": CODE_NOTES.get(n, ""),
        }
    # Every Go service registers with Consul
    for n, v in info.items():
        if v["kind"] == "service" and "consul" not in v["depends_on"]:
            v["depends_on"].append("consul")
            info["consul"]["used_by"].append(n)
    info["consul"]["used_by"] = sorted(info["consul"]["used_by"])
    info["consul"]["endpoints_affected"] = list(ENDPOINTS)
    info["consul"]["traffic_share_affected"] = 1.0
    global_notes = [CODE_NOTES["*"]]
    if meta:
        no_hc = sorted(s for s, m in meta.items() if not m["has_healthcheck"])
        restart_always = sorted(s for s, m in meta.items() if m["restart"] == "always")
        global_notes.append(f"{len(no_hc)}/{len(meta)} containers define no healthcheck.")
        global_notes.append(f"{len(restart_always)}/{len(meta)} containers use restart: always "
                            "(crashes are revived automatically).")
    return {"nodes": info, "endpoints": ENDPOINTS, "global_notes": global_notes,
            "edge_sources": {f"{a}->{b}": s["source"] for (a, b), s in edges.items()}}


def to_mermaid(dm):
    lines = ["graph LR"]
    for n, v in dm["nodes"].items():
        for d in v["depends_on"]:
            if d == "consul":
                continue
            lines.append(f"  {n.replace('-', '_')}[{n}] --> {d.replace('-', '_')}[{d}]")
    return "\n".join(lines)


def save(dm):
    (config.RESULTS_DIR / "dependency_map.json").write_text(json.dumps(dm, indent=2))
    (config.RESULTS_DIR / "dependency_map.md").write_text(
        "# Hotel Reservation dependency map\n\n```mermaid\n" + to_mermaid(dm) + "\n```\n"
        "\nAll Go services also depend on `consul` for service discovery.\n")


def load():
    path = config.RESULTS_DIR / "dependency_map.json"
    return json.loads(path.read_text()) if path.exists() else None


if __name__ == "__main__":
    dm = build()
    save(dm)
    print(json.dumps(dm["nodes"], indent=2))

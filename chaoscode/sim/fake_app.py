"""A small simulator of the Hotel Reservation app (for demos and tests when Docker is unavailable).

It reproduces the *behaviour that matters for chaos experiments*, using the real call graph
(depmap.CODE_EDGES / ENDPOINTS) and the code-level facts verified in the upstream source:
  * a service whose memcached errors (not a miss) panics and fails fast  (rate/profile/reservation/review)
  * a stopped database only hurts services that have no cache in front of it
  * a paused component makes callers hang until their client timeout
  * network delay adds latency per hop, packet loss fails calls, CPU throttle slows services

Faults are injected over HTTP (POST /__fault, /__clear, /__clear_all, /__reset), the same shape as the
other backends. THIS IS A SIMULATION: nothing here proves anything about the real app. Records made
with it are stamped backend="sim" and clearly labelled in every report.

Run standalone:  python -m sim.fake_app --port 5055
"""
import argparse
import json
import random
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import depmap

CHILDREN = {}
for _a, _b in depmap.CODE_EDGES:
    CHILDREN.setdefault(_a, []).append(_b)
PANIC_ON_CACHE = {"rate", "profile", "reservation", "review"}
MAX_HANG_S = 8.0
FAIL, HANG = "fail", "hang"


class World:
    def __init__(self):
        self.faults = {}
        self.lock = threading.Lock()

    def add(self, f):
        with self.lock:
            self.faults[f["id"]] = f

    def remove(self, fid):
        with self.lock:
            self.faults.pop(fid, None)

    def clear(self):
        with self.lock:
            self.faults.clear()

    def on(self, target):
        with self.lock:
            return [f for f in self.faults.values() if f["target"] == target]

    def snapshot(self):
        with self.lock:
            return list(self.faults.values())


WORLD = World()


def _has_cache(svc):
    return any(c.startswith("memcached") for c in CHILDREN.get(svc, []))


def _eval_component(name, kind, owner, rng):
    """Returns (verdict, extra_delay_s). verdict is None (ok), FAIL or HANG."""
    delay = 0.0
    for f in WORLD.on(name):
        tool, p = f["tool"], f.get("params", {})
        behind_cache = kind == "database" and owner is not None and _has_cache(owner)
        if tool == "kill_service":
            return FAIL, 0.0
        if tool == "pause_service":
            return HANG, 0.0
        if tool == "network_delay":
            if rng.random() < (0.05 if behind_cache else 1.0):   # a DB behind a cache is rarely reached
                delay += float(p.get("delay_ms", 0)) / 1000.0
        elif tool == "packet_loss":
            if rng.random() < float(p.get("loss_pct", 0)) / 100.0:
                return FAIL, 0.0
        elif tool == "cpu_throttle":
            c = max(float(p.get("cpus", 1.0)), 0.02)
            if kind == "service":
                delay += 0.004 * (1.0 / c)
            elif not behind_cache:
                delay += 0.002 * (1.0 / c)
        elif tool == "stop_dependency":
            if kind == "cache":
                if owner in PANIC_ON_CACHE:
                    return FAIL, 0.0                          # the owner panics and crashes
            elif kind == "database":
                if behind_cache and rng.random() < 0.9:
                    continue                                  # served from the cache
                return FAIL, 0.0
    return None, delay


def _walk(name, owner, rng, acc):
    kind = depmap.kind_of(name)
    verdict, delay = _eval_component(name, kind, owner, rng)
    acc["delay"] += delay + rng.uniform(0.001, 0.004)
    if verdict == FAIL:
        return FAIL
    if verdict == HANG:
        acc["hang"] = True
    for child in CHILDREN.get(name, []):
        if _walk(child, name, rng, acc) == FAIL:
            return FAIL
    return None


def evaluate(endpoint, rng=random):
    """Walk the call graph of an endpoint. Returns (verdict, total_delay_s)."""
    acc = {"delay": 0.0, "hang": False}
    for svc in depmap.ENDPOINTS[endpoint]["calls"]:
        if _walk(svc, None, rng, acc) == FAIL:
            return FAIL, acc["delay"]
    return (HANG if acc["hang"] else None), acc["delay"]


def _hotels_body():
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "id": str(i), "properties": {"name": f"Hotel {i}", "phone_number": "555-0100"},
         "geometry": {"type": "Point", "coordinates": [-122.095, 38.0235]}} for i in range(1, 6)]}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):  # keep the console quiet
        pass

    def _send(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n))
        except ValueError:
            return {}

    def _app(self):
        ep = urlparse(self.path).path
        if ep not in depmap.ENDPOINTS:
            return self._send(404, {"error": "not found"})
        verdict, delay = evaluate(ep)
        if verdict == HANG:
            end = time.time() + MAX_HANG_S
            while time.time() < end:
                time.sleep(0.1)
                v, _ = evaluate(ep)
                if v != HANG:
                    break
            else:
                return self._send(504, {"error": "upstream timeout"})
        time.sleep(delay)
        if verdict == FAIL:
            return self._send(500, {"error": "upstream unavailable"})
        return self._send(200, _hotels_body() if ep == "/hotels" else {"message": "ok"})

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/__state":
            return self._send(200, {"faults": WORLD.snapshot()})
        if path == "/__health":
            return self._send(200, {"ok": True})
        return self._app()

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/__fault":
            WORLD.add(self._body())
            return self._send(200, {"ok": True})
        if path == "/__clear":
            WORLD.remove(self._body().get("id"))
            return self._send(200, {"ok": True})
        if path in ("/__clear_all", "/__reset"):
            WORLD.clear()
            return self._send(200, {"ok": True})
        return self._app()


def make_server(host="127.0.0.1", port=5055):
    srv = ThreadingHTTPServer((host, port), Handler)
    srv.daemon_threads = True
    srv.handle_error = lambda request, client_address: None   # clients that time out hang up: expected
    return srv


def start_in_thread(host="127.0.0.1", port=5055):
    srv = make_server(host, port)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=5055)
    a = ap.parse_args()
    print(f"Simulated Hotel Reservation on http://{a.host}:{a.port}  (SIMULATION, not the real app)")
    make_server(a.host, a.port).serve_forever()

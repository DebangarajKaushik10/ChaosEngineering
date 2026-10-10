"""Steady background traffic + metrics, matching the repo's wrk2 mixed workload.

OPEN LOOP: a scheduler sends one request every 1/rps seconds no matter how the app behaves.
A closed-loop client (each worker waits for its reply before sending again) slows down when the
app slows down, which hides exactly the tail latency we want to measure (coordinated omission).
Latency is measured from the scheduled send time, and requests still in flight after HANG_S
seconds are counted as failing, so a hung dependency is visible within seconds.
"""
import itertools
import random
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor

import requests

import config


def _user():
    # The app seeds usernames with fmt.Sprintf("Cornell_%x", "<i>") - the hex of the
    # number's TEXT (user 1 -> "Cornell_31"). The repo's wrk2 script sends "Cornell_1",
    # so every login/booking it makes fails. We send the names that actually exist.
    i = random.randint(0, 500)
    return "Cornell_" + str(i).encode().hex(), str(i) * 10


def _dates(max_stay=None):
    a = random.randint(9, 23)
    b = random.randint(a + 1, 24) if max_stay is None else a + random.randint(1, 5)
    return f"2015-04-{a:02d}", f"2015-04-{b:02d}"


def _loc():
    return (38.0235 + (random.randint(0, 481) - 240.5) / 1000.0,
            -122.095 + (random.randint(0, 325) - 157.0) / 1000.0)


def make_request():
    """Returns (endpoint, method, params) using the 60/39/0.5/0.5 traffic mix."""
    coin = random.random()
    lat, lon = _loc()
    if coin < 0.60:
        i, o = _dates()
        return "/hotels", "GET", {"inDate": i, "outDate": o, "lat": lat, "lon": lon}
    if coin < 0.99:
        return "/recommendations", "GET", {
            "require": random.choice(["dis", "rate", "price"]), "lat": lat, "lon": lon}
    if coin < 0.995:
        u, p = _user()
        return "/user", "POST", {"username": u, "password": p}
    i, o = _dates(max_stay=5)
    u, p = _user()
    return "/reservation", "POST", {
        "inDate": i, "outDate": o, "lat": lat, "lon": lon,
        "hotelId": str(random.randint(1, 80)), "customerName": u,
        "username": u, "password": p, "number": "1"}


class LoadGenerator:
    def __init__(self, base_url=None, rps=None, timeout=None, workers=None):
        self.base = (base_url or config.FRONTEND_URL).rstrip("/")
        self.rps = float(rps or config.LOAD_RPS)
        self.timeout = float(timeout or config.REQUEST_TIMEOUT_S)
        # enough threads that a fully hung app (every request waits `timeout`) never starves the scheduler
        self.pool_size = workers or int(self.rps * self.timeout * 1.5) + 8
        self.samples = deque(maxlen=200_000)   # (completed_at, endpoint, ok, latency_ms)
        self.sent = 0
        self._inflight = {}                    # request id -> (scheduled_at, endpoint)
        self._ids = itertools.count()
        self.lock = threading.Lock()
        self._stop = threading.Event()
        self._local = threading.local()
        self._pool = None
        self._thread = None

    # ------------------------------------------------------------ internals ---
    def _session(self):
        s = getattr(self._local, "s", None)
        if s is None:
            s = self._local.s = requests.Session()
        return s

    def _fire(self, rid, sched_t, ep, method, params):
        try:
            r = self._session().request(method, self.base + ep, params=params, timeout=self.timeout)
            ok = (r.status_code == 200) if method == "GET" else r.status_code < 500
        except requests.RequestException:
            ok = False
        end = time.time()
        with self.lock:
            self._inflight.pop(rid, None)
            self.samples.append((end, ep, ok, (end - sched_t) * 1000))

    def _scheduler(self):
        interval = 1.0 / self.rps
        next_t = time.time()
        while not self._stop.is_set():
            wait = next_t - time.time()
            if wait > 0 and self._stop.wait(wait):
                break
            ep, method, params = make_request()
            rid = next(self._ids)
            with self.lock:
                self._inflight[rid] = (next_t, ep)
                self.sent += 1
            try:
                self._pool.submit(self._fire, rid, next_t, ep, method, params)
            except RuntimeError:        # pool shut down
                break
            next_t += interval
            if time.time() - next_t > 2:  # never burst to "catch up" after a stall
                next_t = time.time()

    # --------------------------------------------------------------- public ---
    def start(self):
        self._pool = ThreadPoolExecutor(max_workers=self.pool_size)
        self._thread = threading.Thread(target=self._scheduler, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._pool:
            self._pool.shutdown(wait=False, cancel_futures=True)

    def metrics(self, window_s=5, since=None, until=None):
        """Overall + per-endpoint error rate and latency percentiles."""
        now = time.time()
        lo = since if since is not None else now - window_s
        hi = until if until is not None else now
        rows = []
        with self.lock:
            for s in reversed(self.samples):
                if s[0] < lo:
                    break
                if s[0] <= hi:
                    rows.append(s)
            hung = [(t, ep) for t, ep in self._inflight.values()
                    if lo <= t <= hi and now - t > config.HANG_S]
        rows += [(now, ep, False, (now - t) * 1000) for t, ep in hung]
        return summarize(rows)


def _pct(vals, p):
    if not vals:
        return 0.0
    vals = sorted(vals)
    return round(vals[min(len(vals) - 1, int(p * len(vals)))], 1)


def summarize(rows):
    def block(rs):
        lats = [r[3] for r in rs]
        return {"requests": len(rs),
                "error_rate": round(sum(not r[2] for r in rs) / len(rs), 3) if rs else 0.0,
                "p50_ms": _pct(lats, 0.50), "p95_ms": _pct(lats, 0.95)}
    out = {"overall": block(rows), "endpoints": {}}
    for ep in sorted({r[1] for r in rows}):
        out["endpoints"][ep] = block([r for r in rows if r[1] == ep])
    return out

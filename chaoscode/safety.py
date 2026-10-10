"""Safety guard (validates every AI decision) and watchdog (the kill switch)."""
import threading
import time

import config
import events
from chaos_tools import CATALOG, HIGH_RISK, load_state


def validate(plan):
    """Return (ok, reason). Every plan from the AI goes through this first."""
    if not isinstance(plan, dict):
        return False, "plan must be a JSON object"
    tool, target = plan.get("tool"), plan.get("target")
    if not isinstance(tool, str) or tool not in CATALOG:
        return False, f"tool '{tool}' is not allowed. Allowed: {list(CATALOG)}"
    spec = CATALOG[tool]
    if not isinstance(target, str) or target not in spec["targets"]:
        return False, f"target '{target}' is not allowed for {tool}. Allowed: {spec['targets']}"
    try:
        d = int(plan.get("duration_s", 0))
    except (TypeError, ValueError):
        return False, "duration_s must be an integer"
    if not config.MIN_DURATION_S <= d <= config.MAX_DURATION_S:
        return False, f"duration_s must be {config.MIN_DURATION_S}-{config.MAX_DURATION_S}"
    params = plan.get("params") or {}
    if not isinstance(params, dict):
        return False, "params must be an object"
    for name, (lo, hi) in spec["params"].items():
        if name not in params:
            return False, f"missing parameter '{name}' ({lo}-{hi})"
        try:
            v = float(params[name])
        except (TypeError, ValueError):
            return False, f"parameter '{name}' must be a number"
        if not lo <= v <= hi:
            return False, f"parameter '{name}'={v} outside safe range {lo}-{hi}"
    extra = set(params) - set(spec["params"])
    if extra:
        return False, f"unexpected parameters {sorted(extra)}"
    if load_state():
        return False, "another experiment is still active - one at a time (blast radius)"
    return True, "ok"


def is_high_risk(plan):
    return plan.get("target") in HIGH_RISK


class Watchdog:
    """Polls live metrics; if a threshold stays breached, fires the kill switch."""

    def __init__(self, loadgen, on_abort):
        self.lg, self.on_abort = loadgen, on_abort
        self.reason = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _breach(self, m):
        o = m["overall"]
        if o["requests"] == 0:
            return "no requests completing at all"
        if o["error_rate"] > config.ABORT_ERROR_RATE:
            return f"error rate {o['error_rate']:.0%} > {config.ABORT_ERROR_RATE:.0%}"
        if o["p95_ms"] > config.ABORT_P95_MS:
            return f"p95 latency {o['p95_ms']:.0f} ms > {config.ABORT_P95_MS:.0f} ms"
        return None

    def _run(self):
        breached_since = None
        while not self._stop.wait(1):
            why = self._breach(self.lg.metrics(config.WATCH_WINDOW_S))
            if why is None:
                breached_since = None
                continue
            breached_since = breached_since or time.time()
            if time.time() - breached_since >= config.ABORT_SUSTAIN_S:
                self.reason = f"{why} for {config.ABORT_SUSTAIN_S}s"
                events.log_event("kill_switch_fired", reason=self.reason)
                self.on_abort(self.reason)
                return

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()

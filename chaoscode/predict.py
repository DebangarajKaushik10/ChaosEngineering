"""Structured predictions, a no-LLM baseline predictor, and prediction-vs-reality scoring.

Every plan carries a prediction made BEFORE the fault. After the experiment we compare it with what
was measured. The same scoring is applied to a deterministic graph-based predictor, so we can say
honestly whether the LLM added anything over simply following the dependency graph.
"""
import config

TIMEOUT_MS = config.REQUEST_TIMEOUT_S * 1000.0
PANIC_SERVICES = {"rate", "profile", "reservation", "review"}   # log.Panic on any memcached error
MIN_SAMPLES = 5   # endpoints with fewer requests in the fault window are too noisy to score


def _owners_have_cache(dm, store):
    owners = dm["nodes"].get(store, {}).get("used_by", [])
    if not owners:
        return False
    for o in owners:
        deps = dm["nodes"].get(o, {}).get("depends_on", [])
        if not any(dm["nodes"].get(d, {}).get("kind") == "cache" for d in deps):
            return False
    return True


def graph_baseline(dm, tool, target, params=None, baseline=None):
    """Deterministic prediction from the dependency graph and code notes alone (no LLM)."""
    params = params or {}
    node = dm["nodes"].get(target, {})
    kind = node.get("kind")
    affected = list(node.get("endpoints_affected", []))
    base_p95 = max(((baseline or {}).get("overall") or {}).get("p95_ms") or 20.0, 1.0)
    err, mult, rec = (0.0, 0.05), (0.5, 2.0), 20.0

    if tool == "kill_service":
        err, mult = (0.8, 1.0), (0.05, 3.0)
    elif tool == "pause_service":
        err, mult = (0.8, 1.0), (0.5 * TIMEOUT_MS / base_p95, 1.3 * TIMEOUT_MS / base_p95)
        if kind == "database" and _owners_have_cache(dm, target):
            err, mult, affected = (0.0, 0.2), (0.5, 3.0), []
    elif tool == "stop_dependency":
        if target == "consul":
            err, mult, affected, rec = (0.0, 0.05), (0.5, 2.0), [], 30.0
        elif kind == "cache":
            if set(node.get("used_by", [])) & PANIC_SERVICES:
                err, mult = (0.5, 1.0), (0.05, 5.0)          # users of the cache panic and crash
            else:
                err, mult = (0.0, 0.1), (0.5, 3.0)
        elif kind == "database":
            if _owners_have_cache(dm, target):
                err, mult, affected = (0.0, 0.1), (0.5, 3.0), []   # the cache absorbs it
            else:
                err, mult = (0.8, 1.0), (0.05, 5.0)
    elif tool == "network_delay":
        d = float(params.get("delay_ms", 300))
        est = (base_p95 + d) / base_p95
        err, mult = (0.0, 0.1), (0.6 * est, 2.5 * est)
        if kind == "database" and _owners_have_cache(dm, target):
            affected, mult = [], (0.5, 2.0)
    elif tool == "packet_loss":
        loss = float(params.get("loss_pct", 10)) / 100.0
        err, mult = (loss * 0.3, min(1.0, loss * 3 + 0.05)), (0.5, 5.0 + 20 * loss)
    elif tool == "cpu_throttle":
        c = float(params.get("cpus", 0.1))
        if kind == "database" and _owners_have_cache(dm, target):
            err, mult, affected = (0.0, 0.02), (0.5, 2.0), []
        else:
            err, mult = (0.0, 0.1), (1.0, max(3.0, 1.0 / max(c, 0.02)))
    return {"affected_endpoints": affected,
            "error_rate_range": [round(err[0], 3), round(err[1], 3)],
            "p95_multiplier_range": [round(mult[0], 2), round(mult[1], 2)],
            "recovery_s_max": rec, "confidence": 0.5, "source": "graph_baseline"}


def _range(v):
    lo, hi = float(v[0]), float(v[1])
    if lo > hi:
        lo, hi = hi, lo
    return [lo, hi]


def normalize(pred, fallback, endpoints):
    """Validate an AI-supplied prediction; fall back to the graph baseline if it is unusable."""
    try:
        er = _range(pred["error_rate_range"])
        er = [min(max(er[0], 0.0), 1.0), min(max(er[1], 0.0), 1.0)]
        pr = _range(pred["p95_multiplier_range"])
        if pr[0] < 0:
            raise ValueError("negative multiplier")
        rec = float(pred["recovery_s_max"])
        if rec <= 0:
            raise ValueError("recovery_s_max must be positive")
        conf = min(max(float(pred.get("confidence", 0.5)), 0.0), 1.0)
        aff = [e for e in (pred.get("affected_endpoints") or []) if e in endpoints]
        return {"affected_endpoints": aff, "error_rate_range": [round(x, 3) for x in er],
                "p95_multiplier_range": [round(x, 2) for x in pr], "recovery_s_max": rec,
                "confidence": conf, "source": "ai"}
    except Exception:
        out = dict(fallback)
        out["source"] = "graph_baseline (AI prediction missing or invalid)"
        return out


def impacted_endpoints(baseline, during, min_samples=0):
    """Endpoints whose errors or latency changed materially versus the baseline."""
    out = []
    for ep, m in during["endpoints"].items():
        if m["requests"] < min_samples:
            continue
        b = baseline["endpoints"].get(ep, {"error_rate": 0, "p95_ms": 0})
        de, dl = m["error_rate"] - b["error_rate"], m["p95_ms"] - b["p95_ms"]
        if de > 0.05 or dl > max(100, b["p95_ms"] * 0.5):
            out.append(ep)
    return out


def _in(v, rng):
    return rng[0] <= v <= rng[1]


def score(pred, record):
    """Compare a prediction with what was measured. Returns None if nothing was measured."""
    during, baseline = record.get("during"), record.get("baseline")
    if not pred or not during or not baseline or not during.get("endpoints"):
        return None
    measurable = {ep for ep, m in during["endpoints"].items() if m["requests"] >= MIN_SAMPLES}
    actual = set(impacted_endpoints(baseline, during, MIN_SAMPLES))
    predicted = set(pred["affected_endpoints"]) & measurable
    union = predicted | actual
    jac = 1.0 if not union else len(predicted & actual) / len(union)

    if union:
        n = sum(during["endpoints"][e]["requests"] for e in union)
        err = sum(during["endpoints"][e]["error_rate"] * during["endpoints"][e]["requests"]
                  for e in union) / n
        mult = max(during["endpoints"][e]["p95_ms"] / max(baseline["endpoints"].get(e, {}).get("p95_ms", 0), 1.0)
                   for e in union)
    else:
        err = during["overall"]["error_rate"]
        mult = during["overall"]["p95_ms"] / max(baseline["overall"]["p95_ms"], 1.0)
    rec = record.get("recovery_time_s")
    err_ok, p95_ok = _in(err, pred["error_rate_range"]), _in(mult, pred["p95_multiplier_range"])
    rec_ok = rec is not None and rec <= pred["recovery_s_max"]
    overall = jac >= 0.5 and err_ok and p95_ok
    return {"blast_radius_jaccard": round(jac, 2), "error_in_range": err_ok, "p95_in_range": p95_ok,
            "recovery_ok": rec_ok, "overall_correct": overall,
            "brier": round((pred["confidence"] - (1.0 if overall else 0.0)) ** 2, 3),
            "score": round((jac + err_ok + p95_ok + rec_ok) / 4, 2),
            "actual": {"impacted_endpoints": sorted(actual), "error_rate": round(err, 3),
                       "p95_multiplier": round(mult, 2), "recovery_s": rec},
            "predicted_source": pred.get("source")}


def compare(record, dm):
    """Score the recorded prediction and the no-LLM graph baseline on the same outcome."""
    plan = record["plan"]
    base = graph_baseline(dm, plan["tool"], plan["target"], plan.get("params"), record.get("baseline"))
    return {"recorded": score(record.get("prediction"), record), "graph_baseline": score(base, record)}

"""Blind root-cause attribution (Day 2).

In real incidents nobody knows the true cause. In chaos engineering we injected it, so we can SCORE
the diagnosis. Each investigator sees only ONE view of the evidence and never the injected fault:

  * dependency investigator : the dependency graph + WHICH endpoints were hit  -> ranks TARGETS
  * metrics investigator    : the SHAPE of errors and latency (hang? crash? slow?) -> ranks TOOLS
  * challenger              : everything above -> finds ties the others cannot separate and says
                              what extra evidence would separate them
  * evaluator               : merges the views into ranked (tool, target) hypotheses, then scores
                              them against the ground truth in the experiment record

Everything is deterministic and uses only `during`, `baseline`, `timeline` and the graph. The ground
truth (`record["plan"]`) is only touched inside `evaluate`, after the hypotheses are fixed.
"""
import config
import predict
from chaos_tools import CATALOG

TIMEOUT_MS = config.REQUEST_TIMEOUT_S * 1000.0
BLIND_KEYS = ("baseline", "during", "timeline", "aborted", "abort_reason", "recovery_time_s")


def blind_view(record):
    """The only data the investigators may read. The plan (ground truth) is deliberately absent."""
    return {k: record.get(k) for k in BLIND_KEYS}


def _stats(blind):
    base, during = blind["baseline"], blind["during"]
    measurable = {ep for ep, m in during["endpoints"].items() if m["requests"] >= predict.MIN_SAMPLES}
    impacted = set(predict.impacted_endpoints(base, during, predict.MIN_SAMPLES))
    return measurable, impacted


def failure_mode(blind):
    """hang | fast_fail | lossy | slow | none, from the shape of the numbers only."""
    measurable, impacted = _stats(blind)
    if not impacted:
        return "none", {}
    d = blind["during"]["endpoints"]
    n = sum(d[e]["requests"] for e in impacted)
    err = sum(d[e]["error_rate"] * d[e]["requests"] for e in impacted) / n
    p95 = max(d[e]["p95_ms"] for e in impacted)
    facts = {"error_rate": round(err, 3), "p95_ms": round(p95, 1), "impacted": sorted(impacted)}
    if err >= 0.5:
        return ("hang" if p95 >= 0.5 * TIMEOUT_MS else "fast_fail"), facts
    if err >= 0.05:
        return "lossy", facts
    return "slow", facts


MODE_TOOLS = {"hang": ["pause_service"], "fast_fail": ["kill_service", "stop_dependency"],
              "lossy": ["packet_loss"], "slow": ["network_delay", "cpu_throttle"]}


def _depth(dm):
    """Hops from the frontend (0) down the call graph; used to order ties nearest-the-user first."""
    depth, frontier = {"frontend": 0}, ["frontend"]
    while frontier:
        nxt = []
        for n in frontier:
            for c in dm["nodes"].get(n, {}).get("depends_on", []):
                if c not in depth and c != "consul":
                    depth[c] = depth[n] + 1
                    nxt.append(c)
        frontier = nxt
    return depth


def dependency_investigator(blind, dm):
    """Which components could explain WHICH endpoints were hit? Ranks targets (no tool guess)."""
    measurable, impacted = _stats(blind)
    ranked, evidence = [], []
    if not impacted:
        # nothing visible: the fault was masked, so only masked components remain plausible
        masked = [n for n, v in dm["nodes"].items()
                  if v["kind"] == "database" and any(
                      any(dm["nodes"].get(c, {}).get("kind") == "cache" for c in dm["nodes"][o]["depends_on"])
                      for o in v["used_by"])] + ["consul"]
        ranked = [{"target": t, "score": 0.1} for t in masked]
        evidence.append("No endpoint changed materially: the fault must sit on a path that is masked "
                        "(cache-backed database, Consul with running services).")
        return {"investigator": "dependency", "ranked": ranked, "evidence": evidence,
                "falsifying_test": "If any endpoint degrades under a longer fault, this view is wrong."}
    # endpoints that carried traffic during the fault and stayed healthy rule candidates OUT:
    # a crashed or hung component breaks every endpoint on its call path.
    loose = set(predict.impacted_endpoints(blind["baseline"], blind["during"], 1))
    healthy = {ep for ep, m in blind["during"]["endpoints"].items() if m["requests"] >= 2} - loose
    depth = _depth(dm)
    for name, v in dm["nodes"].items():
        if name == "jaeger":
            continue
        path = set(v["endpoints_affected"])
        serves = path & measurable
        if impacted <= serves and serves and not (path & healthy):
            ranked.append({"target": name, "score": round(len(impacted) / len(serves), 3)})
    ranked.sort(key=lambda r: (-r["score"], depth.get(r["target"], 9), r["target"]))
    evidence.append(f"Impacted endpoints: {sorted(impacted)}; healthy endpoints: {sorted(healthy)}. A "
                    "component can explain the impact only if it sits on the call path of every impacted "
                    "endpoint and of no healthy one. Ties are ordered nearest-to-the-user first.")
    return {"investigator": "dependency", "ranked": ranked, "evidence": evidence,
            "falsifying_test": "Probe the top component directly; if it answers normally it is not the cause."}


def metrics_investigator(blind, dm):
    """What KIND of failure does the shape of the numbers show? Ranks tools (no target guess)."""
    mode, facts = failure_mode(blind)
    tools = MODE_TOOLS.get(mode, [])
    why = {"hang": "errors with latency pinned near the client timeout: callers wait on something that "
                   "never answers (hang, freeze or partition)",
           "fast_fail": "errors with LOW latency: callers get refused quickly (crash or stopped dependency)",
           "lossy": "partial errors: some calls fail and some succeed (packet loss or flapping)",
           "slow": "latency up, errors ~0: calls succeed but slowly (network delay or CPU starvation)",
           "none": "no material change in errors or latency"}[mode]
    return {"investigator": "metrics", "mode": mode, "facts": facts,
            "ranked": [{"tool": t, "score": round(1.0 / len(tools), 3)} for t in tools],
            "evidence": [why], "falsifying_test": "Compare the per-request latency histogram: a hang "
                                                  "has a spike at the timeout, a crash does not."}


def challenger(blind, dm, dep, met):
    """Finds hypotheses the other views cannot tell apart, and what would separate them."""
    ranked = dep["ranked"]
    if not ranked:
        return {"investigator": "challenger", "alternatives": [], "evidence": [], "unresolved": []}
    top = ranked[0]["score"]
    ties = [r["target"] for r in ranked if abs(r["score"] - top) < 1e-9]
    alts, unresolved = [], []
    for t in ties[1:]:
        kind = dm["nodes"][t]["kind"]
        owners = dm["nodes"][t].get("used_by", [])
        if kind in ("cache", "database") and owners:
            test = (f"Check the restart count of {owners[0]}: a crash loop while {t} is down means the "
                    f"service panics on {t} errors (a stopped {t} and a crashed {owners[0]} look the same "
                    "from the outside).")
        else:
            test = f"Probe {t} directly (health check or direct request) to rule it in or out."
        alts.append({"target": t, "distinguishing_test": test})
    if len(ties) > 1:
        unresolved.append(f"{len(ties)} components are indistinguishable from outside: {ties}")
    return {"investigator": "challenger", "alternatives": alts, "unresolved": unresolved,
            "evidence": ["Alternative explanations that fit the same evidence equally well."]}


def combine(dep, met, dm):
    """Merge the target ranking and the tool ranking into ranked (tool, target) hypotheses."""
    pairs = []
    mode = met.get("mode")
    for d in dep["ranked"]:
        t = d["target"]
        kind = dm["nodes"].get(t, {}).get("kind")
        tools = [r["tool"] for r in met["ranked"]] if met["ranked"] else list(CATALOG)
        for tool in tools:
            if t in CATALOG[tool]["targets"]:
                pairs.append({"tool": tool, "target": t,
                              "score": round(d["score"] * (1.0 if mode != "none" else 0.5), 3)})
    # ties: nearest the user first (simplest explanation), then alphabetical for stability
    depth = _depth(dm)
    pairs.sort(key=lambda p: (-p["score"], depth.get(p["target"], 9), p["tool"], p["target"]))
    return pairs


def _rank_of(items, pred):
    for i, it in enumerate(items):
        if pred(it):
            return i + 1
    return None


def evaluate(record, dm):
    """Run all investigators on the blind view, then score against the injected ground truth."""
    if not record.get("during") or not record["during"].get("endpoints"):
        return None
    blind = blind_view(record)
    dep = dependency_investigator(blind, dm)
    met = metrics_investigator(blind, dm)
    chal = challenger(blind, dm, dep, met)
    pairs = combine(dep, met, dm)
    truth = record["plan"]                      # ground truth is first touched HERE
    t_tool, t_target = truth["tool"], truth["target"]
    pair_rank = _rank_of(pairs, lambda p: p["tool"] == t_tool and p["target"] == t_target)
    target_rank = _rank_of(dep["ranked"], lambda r: r["target"] == t_target)
    tool_rank = _rank_of(met["ranked"], lambda r: r["tool"] == t_tool)
    return {"mode": met["mode"], "dependency": dep, "metrics": met, "challenger": chal,
            "combined": pairs[:5],
            "truth": {"tool": t_tool, "target": t_target},
            "score": {"pair_rank": pair_rank, "pair_top1": pair_rank == 1,
                      "pair_top3": pair_rank is not None and pair_rank <= 3,
                      "target_rank": target_rank, "target_top1": target_rank == 1,
                      "target_top3": target_rank is not None and target_rank <= 3,
                      "tool_rank": tool_rank, "tool_top1": tool_rank == 1,
                      "candidates_considered": len(pairs)}}


def aggregate(results):
    """Accuracy of each view across experiments (single views vs the combined multi-view result)."""
    rs = [r for r in results if r]
    n = len(rs)
    if not n:
        return {}
    pct = lambda f: round(sum(1 for r in rs if f(r["score"])) / n, 2)   # noqa: E731
    return {"experiments": n,
            "dependency_only_target_top1": pct(lambda s: s["target_top1"]),
            "dependency_only_target_top3": pct(lambda s: s["target_top3"]),
            "metrics_only_tool_top1": pct(lambda s: s["tool_top1"]),
            "combined_pair_top1": pct(lambda s: s["pair_top1"]),
            "combined_pair_top3": pct(lambda s: s["pair_top3"])}

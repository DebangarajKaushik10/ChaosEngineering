"""Command-line entry point.

  python run.py check                    preflight: Docker, app, images, LLM
  python run.py map                      build + save the dependency map
  python run.py baseline [--seconds 60]  capture normal error rate / latency under steady load
  python run.py killswitch-test          deliberately breach a threshold to prove auto-rollback
  python run.py agent [--n 1] [--yes]    AI picks, a human approves, inject, observe, roll back
  python run.py inject TOOL TARGET       manual injection (still goes through the safety guard)
  python run.py retest EXPERIMENT_ID     re-run the same experiment after a fix, compare before/after
  python run.py safety-demo              submit unsafe plans to the guard, show every rejection
  python run.py report [--llm]           results/REPORT.md + results/report.html
  python run.py status                   active faults + app health
  python run.py undo-all                 emergency restore of everything
  python run.py demo                     whole pipeline against the SIMULATOR (no Docker needed)
"""
import os
import sys

# `demo` runs against the built-in simulator: set the environment BEFORE the modules read it.
if len(sys.argv) > 1 and sys.argv[1] == "demo":
    os.environ["CHAOS_BACKEND"] = "sim"
    os.environ.setdefault("FRONTEND_URL", "http://127.0.0.1:5055")
    os.environ.setdefault("RESULTS_DIR", "results_demo")
    os.environ.setdefault("LLM_PROVIDER", "none")
    os.environ.setdefault("DURATION_SCALE", "0.4")
    os.environ.setdefault("MIN_DURATION_S", "8")
    os.environ.setdefault("WARMUP_S", "3")
    os.environ.setdefault("RECOVERY_TIMEOUT_S", "45")

import argparse  # noqa: E402
import json  # noqa: E402
import shutil  # noqa: E402
import signal  # noqa: E402
import statistics  # noqa: E402
import subprocess  # noqa: E402
import time  # noqa: E402

import agent  # noqa: E402
import chaos_tools  # noqa: E402
import config  # noqa: E402
import depmap  # noqa: E402
import events  # noqa: E402
import predict  # noqa: E402
import safety  # noqa: E402
from loadgen import LoadGenerator  # noqa: E402


# ---------------------------------------------------------------- helpers ---
def say(msg=""):
    print(msg, flush=True)


def start_load(warmup=None):
    warmup = config.WARMUP_S if warmup is None else warmup
    lg = LoadGenerator()
    lg.start()
    say(f"Load generator started against {config.FRONTEND_URL} ({config.LOAD_RPS:.0f} req/s, open loop); "
        f"warming up {warmup} s...")
    time.sleep(warmup)
    return lg


def capture_baseline(lg, seconds, windows=4):
    say(f"Capturing baseline under steady load ({config.LOAD_RPS:.0f} req/s) for {seconds} s "
        f"({windows} windows, to measure natural variation)...")
    t0 = time.time()
    time.sleep(seconds)
    t1 = time.time()
    b = lg.metrics(since=t0, until=t1)
    step = (t1 - t0) / windows
    wins = [lg.metrics(since=t0 + i * step, until=t0 + (i + 1) * step)["overall"] for i in range(windows)]
    wins = [w for w in wins if w["requests"]]
    if len(wins) >= 2:
        b["variance"] = {"windows": len(wins),
                         "p95_mean": round(statistics.fmean(w["p95_ms"] for w in wins), 2),
                         "p95_std": round(statistics.pstdev(w["p95_ms"] for w in wins), 2),
                         "error_mean": round(statistics.fmean(w["error_rate"] for w in wins), 4),
                         "error_std": round(statistics.pstdev(w["error_rate"] for w in wins), 4)}
    b["meta"] = {"backend": config.BACKEND, "rps": config.LOAD_RPS, "seconds": seconds,
                 "captured_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    (config.RESULTS_DIR / "baseline.json").write_text(json.dumps(b, indent=2))
    o = b["overall"]
    say(f"Baseline: {o['requests']} requests, errors {o['error_rate']:.1%}, "
        f"p50 {o['p50_ms']:.0f} ms, p95 {o['p95_ms']:.0f} ms"
        + (f"  (p95 {b['variance']['p95_mean']:.1f} +/- {b['variance']['p95_std']:.1f} ms across windows)"
           if "variance" in b else ""))
    for ep, m in b["endpoints"].items():
        say(f"  {ep:<18} err {m['error_rate']:>5.1%}  p95 {m['p95_ms']:>6.0f} ms  n={m['requests']}")
    return b


def ensure_healthy(lg):
    if not agent.wait_until_healthy(lg, timeout=90, log=say):
        m = lg.metrics(10)["overall"]
        sys.exit(f"App is not healthy (errors {m['error_rate']:.0%}, n={m['requests']}). "
                 "Fix it or run `python run.py undo-all` first.")


def reconcile_on_startup():
    """If a previous run died mid-experiment, restore everything before doing anything new."""
    leftover = chaos_tools.load_state()
    if leftover:
        say(f"Found {len(leftover)} fault(s) left over from a previous run; restoring first...")
        for line in chaos_tools.undo_all():
            say(f"  restored {line}")
        events.log_event("reconcile_on_startup", faults=len(leftover))


def install_ctrl_c_handler():
    def handler(sig, frame):
        say("\nCtrl+C -> manual kill switch: restoring everything...")
        for line in chaos_tools.undo_all():
            say(f"  restored {line}")
        events.log_event("manual_kill_switch", note="Ctrl+C")
        sys.exit(1)
    signal.signal(signal.SIGINT, handler)


def get_dm():
    dm = depmap.build()
    depmap.save(dm)
    return dm


def approve(chosen, yes, allow_high_risk):
    """Human-in-the-loop approval. Returns (approved, reason)."""
    risky = safety.is_high_risk(chosen)
    if yes:
        if risky and not allow_high_risk:
            return False, f"'{chosen['target']}' is high-risk; --yes alone does not approve it (use --allow-high-risk)"
        return True, "approved with --yes" + (" --allow-high-risk" if risky else "")
    if not sys.stdin.isatty():
        return False, "no human available to approve (non-interactive); rerun with --yes"
    say(f"\n  PROPOSED: {chosen['tool']} on {chosen['target']} for {chosen['duration_s']} s "
        f"params={chosen.get('params') or {}}")
    if risky:
        say("  HIGH RISK target: it can leave lasting damage (databases / Consul).")
        ans = input(f"  Type the target name ({chosen['target']}) to approve, anything else to skip: ")
        return ans.strip() == chosen["target"], "typed confirmation" if ans.strip() == chosen["target"] else "declined"
    ans = input("  Approve and run this experiment? [y/N]: ")
    return ans.strip().lower() in ("y", "yes"), "interactive" if ans.strip().lower() in ("y", "yes") else "declined"


# --------------------------------------------------------------- commands ---
def cmd_map(args):
    dm = get_dm()
    say(depmap.to_mermaid(dm))
    for n in dm["global_notes"]:
        say(f"note: {n}")
    say(f"\nSaved to {config.RESULTS_DIR}/dependency_map.json and .md")


def cmd_baseline(args, lg=None):
    own = lg is None
    lg = lg or start_load()
    capture_baseline(lg, args.seconds)
    if own:
        lg.stop()


def cmd_agent(args, lg=None):
    reconcile_on_startup()
    dm = get_dm()
    own = lg is None
    lg = lg or start_load()
    baseline = capture_baseline(lg, args.baseline_seconds)
    history = agent.load_history()
    ran = 0
    attempts = 0
    while ran < args.n and attempts < args.n * 3:
        attempts += 1
        ensure_healthy(lg)
        say(f"\nAsking the planner ({config.LLM_PROVIDER}) for experiment {ran + 1}/{args.n}...")
        plan = agent.plan_next(dm, baseline, history, log=say)
        say("  Ranked candidates:")
        for j, c in enumerate(plan.get("ranked_candidates", []), 1):
            say(f"   {j}. {c['tool']} on {c['target']} - {c['reason']}")
        chosen = plan["chosen"]
        ok, why = approve(chosen, args.yes, args.allow_high_risk)
        events.log_event("approval", tool=chosen["tool"], target=chosen["target"], approved=ok, why=why)
        if not ok:
            say(f"  NOT APPROVED ({why}); asking the planner for another experiment.")
            history.append({"plan": chosen, "skipped": True, "aborted": False,
                            "recovery_time_s": None, "impact_summary": ["skipped: not approved"]})
            continue
        rec = agent.run_experiment(lg, plan, baseline, log=say, dm=dm)
        history.append(rec)
        ran += 1
        time.sleep(3)
    if own:
        lg.stop()
    say(f"\nDone. Results in {config.RESULTS_DIR}/experiments/. Build the report: python run.py report")


def cmd_inject(args):
    reconcile_on_startup()
    params = {k: float(v) for k, v in (p.split("=", 1) for p in args.param)}
    chosen = {"tool": args.tool, "target": args.target, "params": params,
              "duration_s": args.duration, "hypothesis": "manual test",
              "reason": "manual injection", "expected_blast_radius": []}
    ok, why = safety.validate(chosen)
    if not ok:
        events.log_event("plan_rejected", tool=args.tool, target=args.target, reason=why, source="manual")
        sys.exit(f"Rejected by safety guard: {why}")
    dm = get_dm()
    lg = start_load()
    baseline = capture_baseline(lg, 20)
    agent.run_experiment(lg, {"chosen": chosen}, baseline, log=say, dm=dm)
    lg.stop()


def cmd_killswitch_test(args, lg=None):
    """Kill `search` for up to 90 s: /hotels is 60% of traffic, so overall errors
    jump to ~60% and the watchdog must roll back within ABORT_SUSTAIN_S seconds."""
    reconcile_on_startup()
    chosen = {"tool": "kill_service", "target": "search", "params": {}, "duration_s": 90,
              "hypothesis": "Killing search breaches the 50% error threshold and the kill "
                            "switch restores it within ~5 s, well before 90 s",
              "reason": "Day-1 safety-control test", "expected_blast_radius": ["/hotels"]}
    own = lg is None
    lg = lg or start_load()
    baseline = capture_baseline(lg, 20)
    rec = agent.run_experiment(lg, {"chosen": chosen}, baseline, log=say, dm=get_dm())
    if own:
        lg.stop()
    passed = rec["aborted"] and rec.get("recovery_time_s") is not None
    say("\nKILL SWITCH TEST " + ("PASSED" if passed else "DID NOT PASS") +
        f": fault active {rec['fault_active_s']} s of 90 s, recovered in {rec.get('recovery_time_s')} s")
    events.log_event("killswitch_test", passed=passed, fault_active_s=rec["fault_active_s"])
    return passed


def cmd_retest(args):
    reconcile_on_startup()
    path = config.RESULTS_DIR / "experiments" / f"{args.experiment_id}.json"
    if not path.exists():
        sys.exit(f"No such experiment: {path}")
    before = json.loads(path.read_text())
    chosen = dict(before["plan"])
    ok, why = safety.validate(chosen)
    if not ok:
        sys.exit(f"Rejected by safety guard: {why}")
    approved, why = approve(chosen, args.yes, args.allow_high_risk)
    if not approved:
        sys.exit(f"Not approved: {why}")
    dm = get_dm()
    lg = start_load()
    baseline = capture_baseline(lg, 20)
    after = agent.run_experiment(lg, {"chosen": chosen}, baseline, log=say, dm=dm, retest_of=before["id"])
    lg.stop()
    say("\nBEFORE vs AFTER the fix"
        + (f" ({args.note})" if args.note else ""))
    say(f"  impact before : {'; '.join(before['impact_summary'])}")
    say(f"  impact after  : {'; '.join(after['impact_summary'])}")
    say(f"  kill switch   : before={before['aborted']} after={after['aborted']}")
    say(f"  recovery (s)  : before={before.get('recovery_time_s')} after={after.get('recovery_time_s')}")
    improved = (after["impact_summary"] == ["no significant impact"]
                or len(after["impact_summary"]) < len(before["impact_summary"]))
    say("  verdict       : " + ("the fix helped" if improved else "no clear improvement"))


UNSAFE_PLANS = [
    ("kill the frontend", {"tool": "kill_service", "target": "frontend", "duration_s": 30}),
    ("attack Jaeger", {"tool": "pause_service", "target": "jaeger", "duration_s": 30}),
    ("run a shell command", {"tool": "rm_rf", "target": "search", "duration_s": 30}),
    ("600-second outage", {"tool": "kill_service", "target": "search", "duration_s": 600}),
    ("1-second blip below the minimum", {"tool": "pause_service", "target": "geo", "duration_s": 1}),
    ("delay far above the safe range", {"tool": "network_delay", "target": "rate", "duration_s": 30,
                                        "params": {"delay_ms": 99999}}),
    ("missing required parameter", {"tool": "cpu_throttle", "target": "mongodb-geo", "duration_s": 30}),
    ("extra unexpected parameter", {"tool": "kill_service", "target": "rate", "duration_s": 30,
                                    "params": {"signal": "SIGKILL -9 all"}}),
    ("kill a database (wrong tool for it)", {"tool": "kill_service", "target": "mongodb-user", "duration_s": 30}),
    ("injection via target string", {"tool": "pause_service", "target": "geo; docker rm -f $(docker ps -aq)",
                                     "duration_s": 30}),
    ("non-numeric duration", {"tool": "pause_service", "target": "geo", "duration_s": "forever"}),
    ("a valid, safe plan (control)", {"tool": "network_delay", "target": "rate", "duration_s": 30,
                                      "params": {"delay_ms": 300}}),
]


def cmd_safety_demo(args):
    rows = []
    say(f"{'ATTEMPT':<42} {'DECISION':<10} REASON")
    for name, plan in UNSAFE_PLANS:
        ok, why = safety.validate(plan)
        rows.append({"attempt": name, "plan": plan, "ok": ok, "reason": why})
        say(f"{name:<42} {'ALLOWED' if ok else 'REJECTED':<10} {why[:90]}")
        events.log_event("plan_checked", attempt=name, ok=ok, reason=why)
        if not ok:
            events.log_event("plan_rejected", attempt=name, reason=why, source="safety-demo")
    (config.RESULTS_DIR / "safety_demo.json").write_text(json.dumps(rows, indent=2))
    bad = [r for r in rows if r["attempt"] != "a valid, safe plan (control)" and r["ok"]]
    say("\nSAFETY DEMO " + ("PASSED: every unsafe plan was rejected, the safe control was allowed."
                            if not bad and rows[-1]["ok"] else "FAILED"))
    return not bad and rows[-1]["ok"]


def cmd_report(args):
    import report
    md, htm = report.generate(use_llm=args.llm)
    say(f"Report written:\n  {md}\n  {htm}")


def cmd_status(args):
    st = chaos_tools.load_state()
    say(f"Backend: {config.BACKEND}   Frontend: {config.FRONTEND_URL}")
    say(f"Active faults: {len(st)}")
    for f in st.values():
        say(f"  - {f['tool']} on {f['target']} (id {f['id']})")
    lg = LoadGenerator(rps=5)
    lg.start()
    time.sleep(4)
    m = lg.metrics(4)["overall"]
    lg.stop()
    say(f"App health: {m['requests']} requests, errors {m['error_rate']:.0%}, p95 {m['p95_ms']:.0f} ms")


def cmd_undo_all(args):
    for line in chaos_tools.undo_all() or ["nothing recorded; swept containers anyway"]:
        say(f"restored {line}")
    events.log_event("undo_all")


def cmd_check(args):
    problems = []

    def line(ok, msg, hard=True):
        say(f"  [{'OK' if ok else ('FAIL' if hard else 'WARN')}] {msg}")
        if not ok and hard:
            problems.append(msg)

    say(f"Backend: {config.BACKEND}")
    if config.BACKEND == "docker":
        line(bool(shutil.which("docker")), "docker CLI found")
        info = subprocess.run(["docker", "info"], capture_output=True, text=True)
        line(info.returncode == 0, "Docker daemon is running (open Docker Desktop)")
        comp = subprocess.run(["docker", "compose", "version"], capture_output=True, text=True)
        line(comp.returncode == 0, "`docker compose` is available")
        line((config.COMPOSE_DIR / "docker-compose.yml").exists(), f"compose file at {config.COMPOSE_DIR}")
        if info.returncode == 0:
            img = subprocess.run(["docker", "image", "inspect", config.TC_IMAGE], capture_output=True)
            line(img.returncode == 0, f"network-fault image '{config.TC_IMAGE}' pulled "
                 f"(docker pull {config.TC_IMAGE}); only needed for network_delay / packet_loss", hard=False)
            ps = subprocess.run(["docker", "compose", "ps", "--services", "--status", "running"],
                                cwd=config.COMPOSE_DIR if config.COMPOSE_DIR.exists() else None,
                                capture_output=True, text=True)
            running = [s for s in ps.stdout.split() if s]
            line(len(running) >= 20, f"{len(running)} services running (expected 24)")
    lg = LoadGenerator(rps=4)
    lg.start()
    time.sleep(4)
    m = lg.metrics(4)["overall"]
    lg.stop()
    line(m["requests"] > 0 and m["error_rate"] <= config.HEALTHY_ERROR_RATE,
         f"app answers at {config.FRONTEND_URL} (errors {m['error_rate']:.0%}, n={m['requests']})")
    prov = config.LLM_PROVIDER
    keyname = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}.get(prov)
    if prov == "none":
        line(True, "planner: offline rule-based (LLM_PROVIDER=none)")
    else:
        line(bool(os.getenv(keyname or "")), f"planner: {prov} ({keyname} set); otherwise the rule-based "
             "planner is used automatically", hard=False)
    say("\n" + ("All checks passed." if not problems else f"{len(problems)} problem(s) to fix first."))
    return not problems


def cmd_demo(args):
    """The whole pipeline against the simulator: no Docker, ~3 minutes."""
    from sim import fake_app
    port = int(config.FRONTEND_URL.rsplit(":", 1)[1])
    srv = fake_app.start_in_thread("127.0.0.1", port)
    say("=" * 70)
    say("DEMO MODE: running against the built-in SIMULATOR, not the real Hotel Reservation app.")
    say("It checks the pipeline end to end. Use the Docker backend for real evidence.")
    say("=" * 70)
    try:
        say("\n[1/6] Dependency map")
        cmd_map(args)
        lg = start_load()
        say("\n[2/6] Monitoring baseline")
        a = argparse.Namespace(seconds=args.baseline_seconds)
        cmd_baseline(a, lg)
        say("\n[3/6] Safety controls: guard rejects unsafe plans")
        cmd_safety_demo(args)
        say("\n[4/6] Safety controls: kill switch test")
        passed = cmd_killswitch_test(args, lg)
        say("\n[5/6] AI-planned experiments (planner: %s)" % config.LLM_PROVIDER)
        a = argparse.Namespace(n=args.experiments, yes=True, allow_high_risk=False,
                               baseline_seconds=args.baseline_seconds)
        cmd_agent(a, lg)
        lg.stop()
        say("\n[6/6] Report")
        cmd_report(argparse.Namespace(llm=False))
        say(f"\nDEMO COMPLETE. Kill switch test {'passed' if passed else 'DID NOT PASS'}. "
            f"Open {config.RESULTS_DIR / 'report.html'}")
    finally:
        srv.shutdown()


# ------------------------------------------------------------------- main ---
def main():
    p = argparse.ArgumentParser(description="AI-driven chaos engineering for Hotel Reservation")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    sub.add_parser("map")
    b = sub.add_parser("baseline")
    b.add_argument("--seconds", type=int, default=60)
    a = sub.add_parser("agent")
    a.add_argument("--n", type=int, default=1)
    a.add_argument("--baseline-seconds", type=int, default=30)
    a.add_argument("--yes", action="store_true", help="approve experiments without asking (not high-risk ones)")
    a.add_argument("--allow-high-risk", action="store_true", help="with --yes, also approve databases/Consul")
    i = sub.add_parser("inject")
    i.add_argument("tool", choices=list(chaos_tools.CATALOG))
    i.add_argument("target")
    i.add_argument("--duration", type=int, default=30)
    i.add_argument("--param", action="append", default=[], help="e.g. delay_ms=300")
    sub.add_parser("killswitch-test")
    rt = sub.add_parser("retest")
    rt.add_argument("experiment_id")
    rt.add_argument("--note", default="")
    rt.add_argument("--yes", action="store_true")
    rt.add_argument("--allow-high-risk", action="store_true")
    sub.add_parser("safety-demo")
    r = sub.add_parser("report")
    r.add_argument("--llm", action="store_true", help="LLM writes the executive summary (facts only)")
    sub.add_parser("status")
    sub.add_parser("undo-all")
    d = sub.add_parser("demo")
    d.add_argument("--experiments", type=int, default=3)
    d.add_argument("--baseline-seconds", type=int, default=12)
    args = p.parse_args()

    if args.cmd == "undo-all":
        return cmd_undo_all(args)
    if args.cmd in ("report", "safety-demo", "check", "status", "map"):
        pass
    else:
        install_ctrl_c_handler()
    {"check": lambda a: sys.exit(0 if cmd_check(a) else 1), "map": cmd_map, "baseline": cmd_baseline,
     "agent": cmd_agent, "inject": cmd_inject, "killswitch-test": lambda a: sys.exit(0 if cmd_killswitch_test(a) else 1),
     "retest": cmd_retest, "safety-demo": lambda a: sys.exit(0 if cmd_safety_demo(a) else 1),
     "report": cmd_report, "status": cmd_status, "demo": cmd_demo}[args.cmd](args)


if __name__ == "__main__":
    main()

"""The ONLY actions the AI agent can take. Each tool has an inject and an undo.

The LLM never runs shell commands. It names a tool + parameters, the safety
guard validates them, and this module runs fixed, pre-written docker commands.

Safety properties of this module:
  * write-ahead: a fault is saved to results/active_faults.json BEFORE it is applied, so even a
    half-applied fault can be found and undone;
  * `undo` and `undo_all` are idempotent and never need the agent process;
  * every fault also gets an independent reaper process (reaper.py) that undoes it after
    duration + grace seconds, even if the agent has crashed.
"""
import json
import os
import subprocess
import sys
import time
import uuid

import config

STATE_FILE = config.RESULTS_DIR / "active_faults.json"
BACKEND = config.BACKEND  # docker | native | sim (read at call time, so tests can patch it)

GO_SERVICES = ["search", "geo", "rate", "profile", "reservation",
               "recommendation", "user", "review", "attractions"]
CACHES = ["memcached-rate", "memcached-profile", "memcached-reserve", "memcached-review"]
DATABASES = ["mongodb-geo", "mongodb-rate", "mongodb-profile", "mongodb-reservation",
             "mongodb-recommendation", "mongodb-user", "mongodb-review",
             "mongodb-attractions"]
# Targets that need explicit human approval even in --yes mode (they can leave lasting state damage)
HIGH_RISK = set(DATABASES) | {"consul"}

# tool name -> description, allowed targets, numeric params with (min, max)
CATALOG = {
    "kill_service": {
        "description": "Crash a service container (SIGKILL). Docker's automatic restart is switched "
                       "off for the experiment, so it stays down for duration_s, then it is "
                       "restarted and its restart policy restored.",
        "failure_type": "Service or container crash",
        "targets": GO_SERVICES, "params": {}},
    "pause_service": {
        "description": "Freeze a container's processes. It looks 'running' but never "
                       "answers - a hang, not a crash.",
        "failure_type": "Service hang",
        "targets": GO_SERVICES + CACHES + DATABASES, "params": {}},
    "network_delay": {
        "description": "Add latency to all traffic leaving a container (tc netem).",
        "failure_type": "Slow network",
        "targets": GO_SERVICES + CACHES + DATABASES, "params": {"delay_ms": (50, 2000)}},
    "packet_loss": {
        "description": "Drop a percentage of packets leaving a container (tc netem).",
        "failure_type": "Lossy network",
        "targets": GO_SERVICES + CACHES + DATABASES, "params": {"loss_pct": (1, 50)}},
    "stop_dependency": {
        "description": "Cleanly stop a cache, database or Consul for duration_s.",
        "failure_type": "Failed dependency",
        "targets": CACHES + DATABASES + ["consul"], "params": {}},
    "cpu_throttle": {
        "description": "Limit a container to a fraction of one CPU to simulate overload.",
        "failure_type": "Overloaded database / service",
        "targets": DATABASES + GO_SERVICES, "params": {"cpus": (0.02, 1.0)}},
}


# ---------------------------------------------------------------- helpers ---
def sh(*args, check=True):
    """Run a command with a fixed argument list (never through a shell)."""
    try:
        r = subprocess.run(list(args), capture_output=True, text=True,
                           cwd=config.COMPOSE_DIR if config.COMPOSE_DIR.exists() else None)
    except FileNotFoundError:
        raise RuntimeError(f"'{args[0]}' not found. Is Docker installed and on PATH?")
    if check and r.returncode != 0:
        raise RuntimeError(f"{' '.join(args)} failed: {r.stderr.strip() or r.stdout.strip()}")
    return r.stdout.strip()


def compose(*args, check=True):
    return sh("docker", "compose", *args, check=check)


def container_of(service):
    """Docker container NAME for a compose service."""
    cid = compose("ps", "-a", "-q", service)
    if not cid:
        raise RuntimeError(f"No container found for service '{service}'. Is the app up?")
    name = sh("docker", "inspect", "-f", "{{.Name}}", cid.splitlines()[0])
    return name.lstrip("/")


def load_state():
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {}


_load_state = load_state  # backwards-compatible name used by safety.py


def _save_state(state):
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    os.replace(tmp, STATE_FILE)


# ---------------------------------------------------------- network faults ---
_DEV = "dev=$(ip route | awk '/default/ {print $5; exit}'); "


def _tc(name, tc_cmd, check=True):
    """Run `tc` inside the target's network namespace using a throw-away helper container."""
    return sh("docker", "run", "--rm", "--net", f"container:{name}", "--cap-add", "NET_ADMIN",
              config.TC_IMAGE, "sh", "-c", _DEV + tc_cmd, check=check)


def _netem_on(fault, target, netem_args, duration_s):
    name = container_of(target)
    fault["container"] = name
    fault["engine"] = config.NETEM_ENGINE
    if config.NETEM_ENGINE == "pumba":
        pumba = f"chaos-pumba-{fault['id']}"
        fault["pumba"] = pumba
        sh("docker", "run", "-d", "--rm", "--name", pumba, "--label", "chaos=1",
           "-v", "/var/run/docker.sock:/var/run/docker.sock", config.PUMBA_IMAGE,
           "netem", "--duration", f"{duration_s}s", "--tc-image", config.PUMBA_TC_IMAGE,
           *netem_args["pumba"], name)
    else:
        _tc(name, f"tc qdisc replace dev $dev root netem {netem_args['tc']}")


def _netem_off(fault):
    if fault.get("engine") == "pumba" and fault.get("pumba"):
        sh("docker", "stop", "-t", "5", fault["pumba"], check=False)
        if sh("docker", "ps", "-q", "--filter", f"name={fault['pumba']}", check=False):
            sh("docker", "rm", "-f", fault["pumba"], check=False)
            compose("restart", fault["target"], check=False)
    elif fault.get("container"):
        _tc(fault["container"], "tc qdisc del dev $dev root", check=False)


# ------------------------------------------------------------ inject/undo ---
def _do_inject(fault):
    tool, target, p, dur = fault["tool"], fault["target"], fault["params"], fault["duration_s"]
    if BACKEND == "native":
        from native import backend
        backend.inject(fault)
    elif BACKEND == "sim":
        from sim import backend
        backend.inject(fault)
    elif tool == "kill_service":
        cid = container_of(target)
        policy = sh("docker", "inspect", "-f", "{{.HostConfig.RestartPolicy.Name}}", cid,
                    check=False) or "no"
        fault["original_restart"] = policy
        if policy not in ("", "no"):      # all 24 services are `restart: always`: switch it off,
            sh("docker", "update", "--restart=no", cid)  # otherwise Docker revives the crash in seconds
        compose("kill", target)
    elif tool == "pause_service":
        compose("pause", target)
    elif tool == "network_delay":
        d = int(p["delay_ms"])
        _netem_on(fault, target, {"tc": f"delay {d}ms", "pumba": ["delay", "--time", str(d)]}, dur)
    elif tool == "packet_loss":
        pct = int(p["loss_pct"])
        _netem_on(fault, target, {"tc": f"loss {pct}%", "pumba": ["loss", "--percent", str(pct)]}, dur)
    elif tool == "stop_dependency":
        compose("stop", target)
    elif tool == "cpu_throttle":
        cid = container_of(target)
        fault["original_nano_cpus"] = int(sh("docker", "inspect", "-f",
                                             "{{.HostConfig.NanoCpus}}", cid) or 0)
        sh("docker", "update", "--cpus", str(p["cpus"]), cid)
    else:
        raise ValueError(f"Unknown tool {tool}")


def _spawn_reaper(fault_id, ttl_s):
    """Detached process that undoes the fault after ttl_s even if this process dies."""
    if not config.REAPER_ENABLED:
        return
    kw = {}
    if os.name == "nt":
        kw["creationflags"] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    subprocess.Popen([sys.executable, str(config.HERE / "reaper.py"), fault_id, str(int(ttl_s))],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     cwd=str(config.HERE), env=os.environ.copy(), **kw)


def inject(tool, target, duration_s, **params):
    fault_id = uuid.uuid4().hex[:8]
    fault = {"id": fault_id, "tool": tool, "target": target, "duration_s": duration_s,
             "params": params, "backend": BACKEND, "started_at": time.time()}
    state = load_state()
    state[fault_id] = fault          # write-ahead: recorded before anything is changed
    _save_state(state)
    try:
        _do_inject(fault)
        state = load_state()
        state[fault_id] = fault      # persist details learned while injecting (container, policy...)
        _save_state(state)
    except Exception:
        try:
            undo(fault)
        except Exception:
            pass
        raise
    _spawn_reaper(fault_id, duration_s + config.REAPER_GRACE_S)
    return fault


def undo(fault):
    """Idempotent rollback of one fault."""
    tool, target = fault["tool"], fault["target"]
    be = fault.get("backend", BACKEND)
    try:
        if be == "native":
            from native import backend
            backend.undo(fault)
        elif be == "sim":
            from sim import backend
            backend.undo(fault)
        elif tool in ("kill_service", "stop_dependency"):
            policy = fault.get("original_restart")
            if tool == "kill_service" and policy not in (None, "", "no"):
                sh("docker", "update", f"--restart={policy}", container_of(target), check=False)
            compose("start", target)
        elif tool == "pause_service":
            compose("unpause", target, check=False)
        elif tool in ("network_delay", "packet_loss"):
            _netem_off(fault)
        elif tool == "cpu_throttle":
            orig = fault.get("original_nano_cpus", 0)
            # 0 = was unlimited. Restore to all CPUs Docker has (on Docker Desktop that is the
            # VM's CPUs, which can be fewer than the host's - a larger value is rejected).
            ncpu = sh("docker", "info", "--format", "{{.NCPU}}", check=False)
            cpus = orig / 1e9 if orig else float(ncpu or 1)
            sh("docker", "update", "--cpus", str(cpus), container_of(target))
    except Exception:
        raise  # the fault stays recorded, so `undo-all` or the reaper can retry it
    state = load_state()
    state.pop(fault["id"], None)
    _save_state(state)


def undo_all():
    """Emergency restore: undo every recorded fault, then sweep for leftovers."""
    restored = []
    for fault in list(load_state().values()):
        try:
            undo(fault)
            restored.append(f"{fault['tool']} on {fault['target']}")
        except Exception as e:  # keep going - restoring everything matters most
            restored.append(f"FAILED {fault['tool']} on {fault['target']}: {e}")
    if BACKEND == "native":
        from native import backend
        backend.sweep()
        _save_state({})
        return restored
    if BACKEND == "sim":
        from sim import backend
        try:
            backend.sweep()
        except Exception:
            pass
        _save_state({})
        return restored
    for cid in sh("docker", "ps", "-q", "--filter", "label=chaos=1", check=False).split():
        sh("docker", "stop", cid, check=False)
    compose("unpause", check=False)
    compose("start", check=False)
    _save_state({})
    return restored


def reset_app():
    """Return the app to a known-good state after a risky experiment (e.g. Consul).

    Consul in dev mode keeps registrations in memory and the Go services register only once at
    startup, so after a Consul restart the services must be restarted to register again.
    Returns True if a reset was performed.
    """
    if BACKEND == "docker":
        compose("restart", *GO_SERVICES, check=False)
        return True
    if BACKEND == "sim":
        from sim import backend
        backend.reset()
        return True
    return False

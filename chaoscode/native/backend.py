"""Chaos tools for the no-Docker sandbox (CHAOS_BACKEND=native).

  kill_service     SIGKILL the process and mark it stopped (like `docker kill`)
  pause_service    SIGSTOP / SIGCONT
  stop_dependency  mark stopped; the supervisor sends SIGTERM and keeps it down
  network_delay    Toxiproxy latency toxic on the component's proxy
  packet_loss      Toxiproxy reset_peer toxic on a share of connections
                   (closest Toxiproxy analogue; Pumba gives true packet loss on Docker)
  cpu_throttle     duty-cycle SIGSTOP/SIGCONT so the process gets `cpus` of one CPU
"""
import json
import os
import signal
import threading
import time
import urllib.request

from native import sandbox

_throttles = {}


def _pid(target):
    st = sandbox.read_state()
    if target not in st or not st[target]["pid"]:
        raise RuntimeError(f"'{target}' is not running in the sandbox")
    return st[target]["pid"]


def _toxi(method, path, body=None):
    req = urllib.request.Request(sandbox.TOXI_API + path, method=method,
                                 data=json.dumps(body).encode() if body else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as r:
        return r.read()


def _flag(target, on):
    f = sandbox.STOPPED / target
    if on:
        f.touch()
    else:
        f.unlink(missing_ok=True)


def _duty_cycle(pid, cpus, stop_evt, period=0.1):
    run, rest = period * cpus, period * (1 - cpus)
    while not stop_evt.is_set():
        try:
            os.kill(pid, signal.SIGCONT)
            time.sleep(run)
            os.kill(pid, signal.SIGSTOP)
            time.sleep(rest)
        except OSError:
            break
    try:
        os.kill(pid, signal.SIGCONT)
    except OSError:
        pass


def inject(fault):
    tool, target, p = fault["tool"], fault["target"], fault["params"]
    if tool == "kill_service":
        _flag(target, True)
        os.kill(_pid(target), signal.SIGKILL)
    elif tool == "pause_service":
        fault["pid"] = _pid(target)
        os.kill(fault["pid"], signal.SIGSTOP)
    elif tool == "stop_dependency":
        _flag(target, True)
    elif tool == "network_delay":
        _toxi("POST", f"/proxies/{target}/toxics", {
            "name": "chaos", "type": "latency", "stream": "downstream",
            "attributes": {"latency": int(p["delay_ms"]), "jitter": 0}})
    elif tool == "packet_loss":
        _toxi("POST", f"/proxies/{target}/toxics", {
            "name": "chaos", "type": "reset_peer", "stream": "downstream",
            "toxicity": float(p["loss_pct"]) / 100, "attributes": {"timeout": 0}})
    elif tool == "cpu_throttle":
        fault["pid"] = _pid(target)
        evt = threading.Event()
        threading.Thread(target=_duty_cycle, args=(fault["pid"], float(p["cpus"]), evt),
                         daemon=True).start()
        _throttles[fault["id"]] = evt
    else:
        raise ValueError(tool)


def undo(fault):
    tool, target = fault["tool"], fault["target"]
    if tool in ("kill_service", "stop_dependency"):
        _flag(target, False)  # supervisor restarts it
    elif tool in ("pause_service", "cpu_throttle"):
        evt = _throttles.pop(fault["id"], None)
        if evt:
            evt.set()
            time.sleep(0.25)
        try:
            os.kill(fault.get("pid") or _pid(target), signal.SIGCONT)
        except (OSError, RuntimeError):
            pass
    elif tool in ("network_delay", "packet_loss"):
        try:
            _toxi("DELETE", f"/proxies/{target}/toxics/chaos")
        except Exception:
            pass


def sweep():
    """Emergency restore: clear every flag, toxic and stopped process."""
    for f in sandbox.STOPPED.glob("*"):
        f.unlink()
    for n, v in sandbox.read_state().items():
        if v.get("pid"):
            try:
                os.kill(v["pid"], signal.SIGCONT)
            except OSError:
                pass
        try:
            _toxi("DELETE", f"/proxies/{n}/toxics/chaos")
        except Exception:
            pass

"""Run Hotel Reservation WITHOUT Docker: native processes + a supervisor.

  python native/sandbox.py up        start everything (supervisor runs in background)
  python native/sandbox.py status    show each component, PID, restarts
  python native/sandbox.py down      stop everything

Layout:
  frontend :5000 ──► Toxiproxy ──► Go services ──► Toxiproxy ──► FerretDB (MongoDB API) / memcached
  The fake Consul advertises each service's Toxiproxy port, so every link is proxied.

The supervisor reconciles desired state every 0.5 s, like Docker's restart: always:
  - a component that DIES on its own (e.g. a panic) is restarted with exponential backoff
  - a component that was stopped ON PURPOSE (flag file in run/stopped/) stays down
"""
import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
BIN = Path(os.getenv("HOTEL_BIN", HERE / "bin"))
RUN = Path(os.getenv("SANDBOX_RUN", HERE / "run"))
STATE = RUN / "state.json"
STOPPED = RUN / "stopped"
TOXI_API = "http://127.0.0.1:8474"

GO_PORTS = {"profile": 8081, "search": 8082, "geo": 8083, "rate": 8084,
            "recommendation": 8085, "user": 8086, "reservation": 8087,
            "review": 8088, "attractions": 8089}
MEMC_PORTS = {"memcached-rate": 11211, "memcached-profile": 11212,
              "memcached-reserve": 11213, "memcached-review": 11214}
MONGO_PORTS = {"mongodb-geo": 27101, "mongodb-profile": 27102, "mongodb-rate": 27103,
               "mongodb-review": 27104, "mongodb-attractions": 27105,
               "mongodb-recommendation": 27106, "mongodb-reservation": 27107,
               "mongodb-user": 27108}
PROXY_OFFSET = 10000  # Toxiproxy listens on real_port + 10000


def proxied(port):
    return port + PROXY_OFFSET


def config_json():
    p = lambda name, ports: f"127.0.0.1:{proxied(ports[name])}"
    return {
        "consulAddress": "127.0.0.1:8500", "jaegerAddress": "127.0.0.1:6831",
        "FrontendPort": "5000",
        "GeoPort": "8083", "GeoMongoAddress": p("mongodb-geo", MONGO_PORTS),
        "ProfilePort": "8081", "ProfileMongoAddress": p("mongodb-profile", MONGO_PORTS),
        "ProfileMemcAddress": p("memcached-profile", MEMC_PORTS),
        "ReviewPort": "8088", "ReviewMongoAddress": p("mongodb-review", MONGO_PORTS),
        "ReviewMemcAddress": p("memcached-review", MEMC_PORTS),
        "AttractionsPort": "8089", "AttractionsMongoAddress": p("mongodb-attractions", MONGO_PORTS),
        "RatePort": "8084", "RateMongoAddress": p("mongodb-rate", MONGO_PORTS),
        "RateMemcAddress": p("memcached-rate", MEMC_PORTS),
        "RecommendPort": "8085",
        "RecommendMongoAddress": p("mongodb-recommendation", MONGO_PORTS),
        "ReservePort": "8087", "ReserveMongoAddress": p("mongodb-reservation", MONGO_PORTS),
        "ReserveMemcAddress": p("memcached-reserve", MEMC_PORTS),
        "SearchPort": "8082",
        "UserPort": "8086", "UserMongoAddress": p("mongodb-user", MONGO_PORTS),
        "KnativeDomainName": "",
    }


def components():
    """name -> (argv, port_to_wait_for, kind). Order = startup order."""
    c = {"toxiproxy": ([str(BIN / "toxiproxy-server-linux-amd64"), "-host", "127.0.0.1",
                        "-port", "8474", "-config", str(RUN / "toxiproxy.json")], 8474, "infra")}
    port_map = {str(p): proxied(p) for p in GO_PORTS.values()}
    c["consul"] = ([sys.executable, str(HERE / "fake_consul.py"), "8500", json.dumps(port_map)],
                   8500, "infra")
    for name, port in MEMC_PORTS.items():
        c[name] = (["memcached", "-p", str(port), "-U", "0", "-l", "127.0.0.1",
                    "-m", "128", "-t", "2", "-u", "root"], port, "cache")
    for i, (name, port) in enumerate(MONGO_PORTS.items()):
        data = RUN / "data" / name
        data.mkdir(parents=True, exist_ok=True)
        c[name] = ([str(BIN / "ferretdb-linux-amd64"), "--handler=sqlite",
                    f"--sqlite-url=file:{data}/", f"--listen-addr=127.0.0.1:{port}",
                    f"--debug-addr=127.0.0.1:{28100 + i}", "--telemetry=disable",
                    f"--state-dir={data}", "--log-level=WARN"], port, "database")
    for name, port in GO_PORTS.items():
        c[name] = ([str(BIN / name)], port, "service")
    c["frontend"] = ([str(BIN / "frontend")], 5000, "service")
    return c


def toxiproxy_config():
    proxies = []
    for name, port in {**GO_PORTS, **MEMC_PORTS, **MONGO_PORTS}.items():
        proxies.append({"name": name, "listen": f"127.0.0.1:{proxied(port)}",
                        "upstream": f"127.0.0.1:{port}", "enabled": True})
    return proxies


def port_open(port):
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def alive(pid):
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:  # reap zombies and treat them as dead
        return os.waitpid(pid, os.WNOHANG) == (0, 0)
    except ChildProcessError:
        return True


def read_state():
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {}


def write_state(st):
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=1))
    tmp.replace(STATE)


# ------------------------------------------------------------ supervisor ---
def supervise():
    comps = components()
    st = {n: {"pid": None, "restarts": 0, "started": 0, "backoff": 0.1, "next": 0,
              "kind": comps[n][2], "port": comps[n][1]} for n in comps}
    logs = RUN / "logs"
    logs.mkdir(exist_ok=True)
    first_boot = True

    def start(n):
        argv = comps[n][0]
        log = open(logs / f"{n}.log", "ab")
        p = subprocess.Popen(argv, cwd=RUN, stdout=log, stderr=subprocess.STDOUT,
                             start_new_session=True)
        st[n].update(pid=p.pid, started=time.time())

    def shutdown(*_):
        for n in reversed(list(comps)):
            pid = st[n]["pid"]
            if pid and alive(pid):
                try:
                    os.kill(pid, signal.SIGCONT)
                    os.kill(pid, signal.SIGTERM)
                except OSError:
                    pass
        time.sleep(1)
        for n in comps:
            pid = st[n]["pid"]
            if pid and alive(pid):
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass
        STATE.unlink(missing_ok=True)
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    while True:
        for n in comps:
            s = st[n]
            want_down = (STOPPED / n).exists()
            running = s["pid"] is not None and alive(s["pid"])
            if want_down:
                if running:
                    try:
                        os.kill(s["pid"], signal.SIGCONT)
                        os.kill(s["pid"], signal.SIGTERM)
                    except OSError:
                        pass
                s["status"] = "stopped"
                s["backoff"] = 0.1
                continue
            if running:
                s["status"] = "running"
                if time.time() - s["started"] > 10:
                    s["backoff"] = 0.1  # Docker resets backoff after 10 s of uptime
                continue
            # not running and should be
            if s["pid"] is not None and not first_boot:
                if s.get("status") == "running":           # it just died on its own
                    s["restarts"] += 1
                    s["next"] = time.time() + s["backoff"]
                    s["backoff"] = min(s["backoff"] * 2, 60)
                    s["status"] = "restarting"
                if time.time() < s["next"]:
                    continue
            start(n)
            s["status"] = "running"
            if first_boot:  # bring components up in dependency order
                for _ in range(100):
                    if port_open(comps[n][1]):
                        break
                    time.sleep(0.2)
        first_boot = False
        write_state({n: {k: v for k, v in s.items() if k in
                         ("pid", "restarts", "status", "kind", "port")} for n, s in st.items()})
        time.sleep(0.5)


# -------------------------------------------------------------- commands ---
def up():
    if read_state():
        print("Sandbox already running. Use `down` first.")
        return
    for d in (RUN, STOPPED):
        d.mkdir(parents=True, exist_ok=True)
    for f in STOPPED.iterdir():
        f.unlink()
    (RUN / "config.json").write_text(json.dumps(config_json(), indent=2))
    (RUN / "toxiproxy.json").write_text(json.dumps(toxiproxy_config(), indent=2))
    sup_log = open(RUN / "supervisor.log", "ab")
    subprocess.Popen([sys.executable, __file__, "supervise"], stdout=sup_log,
                     stderr=subprocess.STDOUT, start_new_session=True)
    print("Starting 26 components (toxiproxy, consul, 4 memcached, 8 FerretDB, 10 services)...")
    for _ in range(240):
        time.sleep(0.5)
        st = read_state()
        if st and all(v["status"] == "running" for v in st.values()) and port_open(5000):
            print("All components running. Frontend: http://localhost:5000")
            return
    print("Timed out waiting; check native/run/logs/*.log and `status`.")


def down():
    pid = None
    try:
        out = subprocess.run(["pgrep", "-f", f"{__file__} supervise"], capture_output=True,
                             text=True).stdout.split()
        pid = int(out[0]) if out else None
    except Exception:
        pass
    if pid:
        os.kill(pid, signal.SIGTERM)
        time.sleep(2)
    STATE.unlink(missing_ok=True)
    print("Sandbox stopped.")


def status():
    st = read_state()
    if not st:
        print("Sandbox is not running.")
        return
    print(f"{'component':<24}{'kind':<10}{'status':<12}{'pid':>8}{'restarts':>10}")
    for n, v in st.items():
        print(f"{n:<24}{v['kind']:<10}{v['status']:<12}{v['pid'] or '-':>8}{v['restarts']:>10}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    {"up": up, "down": down, "status": status, "supervise": supervise}[cmd]()

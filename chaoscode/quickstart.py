#!/usr/bin/env python3
"""One command to get from nothing to a complete evidence set (Docker backend).

  python quickstart.py            full run: app up -> map -> baseline -> safety -> kill switch -> chaos -> report
  python quickstart.py --setup    only get the app running (milestone 1) and stop
  python quickstart.py --demo     simulator only, no Docker (pipeline check, NOT evidence)

Safe to re-run: every step is idempotent. Stop anytime with Ctrl+C, then `python run.py undo-all`.
"""
import argparse
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
DSB = Path(os.getenv("DSB_DIR", HERE.parent / "DeathStarBench"))
COMPOSE_DIR = Path(os.getenv("COMPOSE_DIR", DSB / "hotelReservation"))
os.environ["COMPOSE_DIR"] = str(COMPOSE_DIR)
URL = os.getenv("FRONTEND_URL", "http://localhost:5000")
OVERRIDE = """# Written by ResiliAI quickstart: pins images that upstream leaves on :latest (reproducibility).
services:
  consul:
    image: hashicorp/consul:1.15.4
  jaeger:
    image: jaegertracing/all-in-one:1.57
"""


def step(n, msg):
    print(f"\n=== [{n}] {msg}", flush=True)


def run(cmd, cwd=None, check=True):
    print("   $ " + " ".join(map(str, cmd)), flush=True)
    r = subprocess.run(list(map(str, cmd)), cwd=cwd)
    if check and r.returncode != 0:
        sys.exit(f"\nFAILED: {' '.join(map(str, cmd))}\nSee README.md > Troubleshooting.")
    return r.returncode


def app_up():
    try:
        with urllib.request.urlopen(URL + "/hotels?inDate=2015-04-09&outDate=2015-04-10&lat=37.7749&lon=-122.4194",
                                    timeout=4) as r:
            return r.status == 200
    except Exception:
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--setup", action="store_true", help="stop after the app is running")
    ap.add_argument("--demo", action="store_true", help="simulator only (no Docker)")
    ap.add_argument("--no-pin", action="store_true", help="do not pin consul/jaeger versions")
    ap.add_argument("--experiments", type=int, default=3)
    ap.add_argument("--baseline-seconds", type=int, default=60)
    a = ap.parse_args()
    py = sys.executable

    step(0, "Python dependencies")
    run([py, "-m", "pip", "install", "-q", "-r", HERE / "requirements.txt"], check=False)
    if a.demo:
        return run([py, HERE / "run.py", "demo"])

    step(1, "Docker available?")
    if not shutil.which("docker") or run(["docker", "info"], check=False) != 0:
        sys.exit("Docker is not running. Start Docker Desktop, wait for 'Engine running', then re-run.")
    run(["docker", "compose", "version"])

    step(2, "Get the Hotel Reservation app (DeathStarBench)")
    if not COMPOSE_DIR.exists():
        run(["git", "clone", "--depth", "1", "https://github.com/delimitrou/DeathStarBench", DSB])
    print(f"   using {COMPOSE_DIR}")
    if not a.no_pin:
        ov = COMPOSE_DIR / "docker-compose.override.yml"
        if not ov.exists():
            ov.write_text(OVERRIDE)
            print("   wrote docker-compose.override.yml (pins consul 1.15.4, jaeger 1.57)")

    step(3, "Build and start all 24 services (first run: 5-15 min)")
    if app_up():
        print("   app already answering, skipping build")
    else:
        run(["docker", "compose", "up", "-d", "--build"], cwd=COMPOSE_DIR)
        print("   waiting for the frontend (up to 3 min)...", flush=True)
        for _ in range(60):
            if app_up():
                break
            time.sleep(3)
        else:
            sys.exit("App did not come up. Run `docker compose logs --tail 50` in the hotelReservation folder.")
    print("   APP IS UP:", URL)

    step(4, "Pull the network-fault helper image")
    run(["docker", "pull", os.getenv("TC_IMAGE", "nicolaka/netshoot")], check=False)
    run([py, HERE / "run.py", "check"], check=False)
    if a.setup:
        print("\nMilestone 1 done. Take a screenshot of `docker compose ps` and of the app answering.")
        return 0

    R = [py, HERE / "run.py"]
    step(5, "Milestone 2: dependency map");        run(R + ["map"])
    step(6, "Milestone 3: monitoring baseline");  run(R + ["baseline", "--seconds", a.baseline_seconds])
    step(7, "Milestone 5a: safety guard");         run(R + ["safety-demo"])
    step(8, "Milestone 5b: kill switch test");     run(R + ["killswitch-test"])
    step(9, "Milestone 4: chaos experiments")
    run(R + ["agent", "--n", a.experiments, "--yes"])
    step(10, "Report");                             run(R + ["report"])
    print(f"\nDONE. Open {HERE / 'results' / 'report.html'}  (and results/REPORT.md)")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)

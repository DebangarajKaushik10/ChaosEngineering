# ResiliAI: AI-driven chaos engineering for DeathStarBench Hotel Reservation

The AI **proposes and predicts**. Deterministic code **validates, approves, injects, measures and cleans up**.
The AI never runs a shell command: it can only name one of 6 whitelisted tools with bounded parameters.

```
baseline -> AI plan + numeric prediction -> safety guard -> human approval -> inject
         -> watch (kill switch) -> rollback -> recovery time -> prediction scored -> report
```

## 0. What you need
- Docker Desktop (Windows/macOS) or Docker Engine + compose plugin (Linux), **6 GB+ RAM given to Docker**
  (Docker Desktop > Settings > Resources). 24 containers run.
- Python 3.9+ and git.
- An LLM key is **optional**. Without one the built-in rule-based planner is used automatically.

## 1. Run it (the fast path, ~15 min first time)
```bash
cd Tangerine
python quickstart.py            # app up, map, baseline, safety, kill switch, 3 experiments, report
```
Then open `results/report.html` (and `results/REPORT.md`). Windows: use `python`; macOS/Linux: `python3`.

No Docker yet / want to check the pipeline first? `python run.py demo` (3 min, simulator, clearly labelled
SIMULATED; **not evidence**, only proof the code works).

Optional LLM planner: `export ANTHROPIC_API_KEY=...` (Windows PowerShell: `$env:ANTHROPIC_API_KEY="..."`) and
`pip install anthropic`. For OpenAI-compatible servers: `LLM_PROVIDER=openai OPENAI_BASE_URL=... OPENAI_API_KEY=...`.

## 2. Run it step by step (matches the 6 milestones; do this if you want to explain each step)
| # | Milestone | Command | Evidence to save |
|---|---|---|---|
| 1 | Setup: app running | `python quickstart.py --setup` | screenshot of `docker compose ps` (24 services) and `python run.py check` all OK |
| 2 | Application mapping | `python run.py map` | `results/dependency_map.json`, mermaid graph in the report |
| 3 | Monitoring baseline | `python run.py baseline --seconds 60` | `results/baseline.json` (error rate, p50/p95, variance) |
| 4 | First failure injection | `python run.py agent --n 1` (asks you to approve) | impact line + timeline chart in the report |
| 5 | Safety controls | `python run.py safety-demo` then `python run.py killswitch-test` | rejected-plan table, "kill switch fired, rolled back" in `results/safety_log.jsonl` |
| 6 | Day-1 demo | script below | `python run.py report` |

Manual injection (still passes the safety guard): `python run.py inject kill_service search --duration 30`,
`python run.py inject network_delay profile --duration 30 --param delay_ms=300`.
After a fix: `python run.py retest <experiment_id>` compares before/after.

## 3. Five-minute demo script
1. `docker compose ps` shows 24 services; open `http://localhost:5000/hotels?inDate=2015-04-09&outDate=2015-04-10&lat=37.7749&lon=-122.4194`.
2. `python run.py map`: "the system found its own dependency graph from compose + Jaeger traces".
3. `python run.py baseline --seconds 30`: "normal is x% errors, p95 y ms, +/- variance".
4. `python run.py safety-demo`: "11 unsafe plans, including shell injection, all rejected before reaching Docker".
5. `python run.py agent --n 1`: the AI proposes a fault *with a numeric prediction*; you type `y`. Show the live
   error rate; the kill switch fires if it crosses 50% errors or p95 > 3 s for 5 s. Say what happened.
6. `python run.py report`; open `report.html`: impact, recovery time, prediction vs reality, proposed fix.
7. Safety net: `python run.py status` (no faults left) and mention `python run.py undo-all`.

## 4. Safety design (say this to the mentor)
- Whitelist of 6 tools and allowed targets; frontend/Jaeger are never targets; bounded durations and parameters;
  one fault at a time.
- Databases and Consul are **high risk**: always need an explicit human yes (`--allow-high-risk` to pre-approve).
- Kill switch: 50% errors or p95 > 3 s for 5 s -> automatic rollback. Tested by `killswitch-test`.
- Write-ahead fault file (`results/active_faults.json`) + startup reconciliation + an **independent reaper
  process** that undoes the fault after duration+15 s even if the agent crashes + Ctrl+C handler + `undo-all`.
- Open-loop load generator (fixed request rate, latency measured from scheduled send time) so a hung service
  cannot hide its own damage ("coordinated omission").
- Upstream facts that shaped the design (checked in the DeathStarBench source): every service is `restart: always`,
  so a plain `docker kill` is revived in seconds; we turn the policy off for the experiment and restore it after.
  Services `log.Panic` on cache errors, have no gRPC deadlines, and register with Consul only once (so Consul
  experiments need a service restart, done by `reset_app`).

## 5. Standout features
Structured numeric predictions scored against reality and compared with a no-LLM graph predictor (does the AI
add value?); blind root-cause attribution (investigators see only metrics + graph, scored against the injected
truth); variance-aware recovery detection; before/after retests; proposed fixes tied to code notes.

## 6. Troubleshooting
| Symptom | Fix |
|---|---|
| `Docker is not running` | Start Docker Desktop, wait for "Engine running" |
| Build slow / containers exit | Give Docker 6 GB+ RAM; `docker compose logs --tail 50 <service>` |
| `mongodb-*` keeps restarting | MongoDB 5 needs a CPU with AVX; on old CPUs/VMs edit the compose image to `mongo:4.4` |
| Frontend never answers | Services started before Consul/Mongo: `docker compose restart` in `hotelReservation/` |
| `network_delay` fails | `docker pull nicolaka/netshoot` (needs internet); or `NETEM_ENGINE=pumba` |
| Experiment refuses: "app unhealthy" | Wait/restart the app; the agent only injects when the baseline is healthy |
| Anything looks stuck or broken | `python run.py undo-all` then `python run.py status` |
| Port 5000 busy (macOS AirPlay) | Turn off AirPlay Receiver or change the published port and set `FRONTEND_URL` |
| No API key | Fine: rule-based planner is used; the report says which planner ran |

## 7. Tests
`python -m unittest discover -s tests -t .` (22 tests, ~40 s, no Docker needed). The Docker command layer is tested
with a fake `docker` binary that records the exact commands.

## 8. Honest limitations
- `python run.py demo` and the tests use a simulator. Its numbers are **not** evidence about Hotel Reservation; every
  such record is stamped `backend: sim` and the report carries a SIMULATED banner.
- The real-Docker code path was built from the upstream source and verified with a fake docker binary, but could
  not be run against 24 live containers where this was written. Run `python quickstart.py` once before demo day and
  fix anything environment-specific using the troubleshooting table.
- The Streamlit dashboard (`streamlit run dashboard/app.py`, needs `pip install -r requirements-extras.txt`) is optional;
  `report.html` is the guaranteed visualisation.
- Blind attribution is heuristic: ties (for example a cache vs the service that panics on it) are reported as
  ambiguities, not hidden.

## 9. Layout
`quickstart.py` one-shot setup+run | `run.py` all commands | `agent.py` planner + experiment loop | `predict.py`
predictions + scoring | `safety.py` guard + kill switch | `chaos_tools.py` the 6 tools | `reaper.py` dead-man switch |
`loadgen.py` open-loop load | `depmap.py` dependency map | `investigate.py` blind attribution | `remediation.py` fixes |
`report.py` report | `sim/` simulator | `native/` no-Docker sandbox backend | `dashboard/` Streamlit | `tests/` |
`docs/` plan, review, build guide, mentor explanation, checklist | `results_demo/` sample (SIMULATED) output.

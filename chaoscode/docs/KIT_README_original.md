# AI Chaos Agent: Hotel Reservation (Day 1)

An AI planner chooses which failure to inject into DeathStarBench's Hotel Reservation app and
explains why. Code then validates the choice, injects it, watches live metrics, and rolls it
back automatically if a safety threshold is breached. Recovery time is measured afterward.

```
dependency map + baseline ──► AI planner (LLM) ──► safety guard ──► chaos tools ──► docker
                                  ▲   rejected? retry with reason      │
                                  └────────────────────────────────────┘
load generator ──► metrics ──► watchdog (kill switch) ──► undo + recovery timer ──► results/
```

## Quick start with Docker Desktop (one command)

Open Docker Desktop (give it 6 GB+ RAM in Settings > Resources), then:

```bash
cd chaos-agent
python quickstart.py                 # clone app, build, start, map, baseline, kill-switch test, 3 AI experiments
python quickstart.py --setup-only    # only start the app
python quickstart.py --down          # remove the app's containers
```

Without an API key, the offline rule-based planner is used. To use an LLM, set one first:
`set ANTHROPIC_API_KEY=...` (Windows cmd), `$env:ANTHROPIC_API_KEY="..."` (PowerShell),
or `export ANTHROPIC_API_KEY=...` (macOS/Linux). OpenAI or Ollama: see "Other LLMs" below.

`sample_results/` holds a real run from the no-Docker sandbox for comparison.

## Manual setup (what quickstart does)

```bash
# 1. The app
git clone https://github.com/delimitrou/DeathStarBench.git
cd DeathStarBench/hotelReservation && docker compose up -d --build && cd ../..

# 2. Pre-pull the chaos images (do this at home, not on venue Wi-Fi)
docker pull gaiaadm/pumba && docker pull gaiadocker/iproute2

# 3. This kit (place it next to DeathStarBench/)
cd chaos-agent
pip install -r requirements.txt
export ANTHROPIC_API_KEY=...        # or see "Other LLMs" below
```

If DeathStarBench is somewhere else: `export COMPOSE_DIR=/path/to/hotelReservation`

## Day-1 checklist, in order

| Rubric item | Command | Output |
|---|---|---|
| App running end to end | `curl "localhost:5000/hotels?inDate=2015-04-09&outDate=2015-04-10&lat=38.0235&lon=-122.095"` | JSON hotels |
| Application mapping | `python run.py map` | `results/dependency_map.md` (Mermaid) and `.json` |
| Monitoring baseline | `python run.py baseline --seconds 60` | `results/baseline.json` |
| Safety controls (tested once) | `python run.py killswitch-test` | Prints `KILL SWITCH TEST PASSED` |
| First failure injection, chosen by AI | `python run.py agent --n 1` | `results/experiments/<id>.json` |
| More AI experiments | `python run.py agent --n 3` | One JSON file per experiment |

Manual injection, for testing your detector: `python run.py inject network_delay rate --duration 30 --param delay_ms=300`

**Emergency restore:** press `Ctrl+C` during any run, or run `python run.py undo-all`.

## What the AI can and cannot do

The LLM only returns JSON naming a tool, target, parameters and duration. It never runs commands.

| Tool | Failure type | Allowed targets | Parameter range |
|---|---|---|---|
| `kill_service` | Container crash | Go services | none |
| `pause_service` | Hang | Services, caches, DBs | none |
| `network_delay` | Slow network | Services, caches, DBs | `delay_ms` 50–2000 |
| `packet_loss` | Lossy network | Services, caches, DBs | `loss_pct` 1–50 |
| `stop_dependency` | Failed dependency | Caches, DBs, Consul | none |
| `cpu_throttle` | Overloaded DB | DBs, services | `cpus` 0.02–1.0 |

Safety guard rules: allow-listed tools and targets only, duration 10–120 s, one experiment at a
time, and `frontend` and `jaeger` are never targets. A rejected plan goes back to the LLM with the
reason so it can choose again. After 3 rejections, the rule-based planner takes over.

Kill switch: rolls back if errors exceed 50% or p95 exceeds 3000 ms for 5 seconds. Adjust with
`ABORT_ERROR_RATE`, `ABORT_P95_MS`, `ABORT_SUSTAIN_S`.

## Other LLMs

```bash
export LLM_PROVIDER=openai OPENAI_API_KEY=...                       # OpenAI
export LLM_PROVIDER=openai OPENAI_BASE_URL=http://localhost:11434/v1 \
       OPENAI_API_KEY=ollama LLM_MODEL=llama3.1                     # local Ollama
export LLM_PROVIDER=none                                            # offline rule-based planner
```

## Files

| File | Role |
|---|---|
| `run.py` | Command-line entry point |
| `agent.py` | AI planner prompt, offline fallback planner, experiment runner, recovery timer |
| `chaos_tools.py` | The 6 allowed tools, each with inject and undo; tracks active faults on disk |
| `safety.py` | Safety guard (validates plans) and watchdog (kill switch) |
| `loadgen.py` | Steady traffic in the repo's wrk2 mix (60% search, 39% recommend) and metrics |
| `depmap.py` | Dependency map from source-code edges, compose `depends_on`, and Jaeger traces |
| `llm.py` | One function, `ask_json`, for Anthropic, OpenAI-compatible, or none |

## Each experiment record contains

The plan (tool, target, hypothesis, reason), the top-5 ranked candidates, baseline metrics,
metrics during the fault, a per-second timeline, whether the kill switch fired and why, recovery
time, and a per-endpoint impact summary. Day 2's analyst agent reads these files.

## No-Docker mode (lighter, Linux or WSL2)

Runs the same Go services as plain processes: FerretDB in place of MongoDB, real memcached,
Toxiproxy on every link, and a minimal Consul stand-in. It uses less RAM than 24 containers.

```bash
sudo apt install memcached
bash native/setup.sh                 # builds services, downloads FerretDB + Toxiproxy
python3 native/sandbox.py up         # start all 26 components
python3 native/sandbox.py status     # PIDs and restart counts (crash loops show here)
export CHAOS_BACKEND=native          # the chaos tools now act on processes and Toxiproxy
python3 run.py killswitch-test
python3 run.py agent --n 3
python3 native/sandbox.py down
```

A supervisor restarts crashed services with exponential backoff, like Docker's `restart: always`.
Services stopped on purpose stay down. In this mode `packet_loss` resets a share of connections
(Toxiproxy has no packet loss), and `cpu_throttle` duty-cycles the process with SIGSTOP and SIGCONT.

See `sample_results/FINDINGS.md` for a real run: 8 experiments and 3 critical weaknesses, verified with logs.

## Notes

- MongoDB 5.0 needs a CPU with AVX. Give Docker Desktop at least 6 GB of RAM.
- On Windows, run from WSL2 or PowerShell with Docker Desktop. Pumba needs the Docker socket,
  which Docker Desktop provides.

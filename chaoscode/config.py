"""Central settings. Override any of these with environment variables."""
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent

# Folder that contains DeathStarBench's hotelReservation/docker-compose.yml
COMPOSE_DIR = Path(os.getenv("COMPOSE_DIR", HERE.parent / "DeathStarBench" / "hotelReservation")).resolve()
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5000")
JAEGER_URL = os.getenv("JAEGER_URL", "http://localhost:16686")

_results = Path(os.getenv("RESULTS_DIR", "results"))
RESULTS_DIR = _results if _results.is_absolute() else HERE / _results
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

# docker = real containers (default) | native = no-Docker sandbox | sim = built-in simulator
BACKEND = os.getenv("CHAOS_BACKEND", "docker")

# ---- Load generator (open loop: requests are sent at a fixed rate, whatever the app does) ----
LOAD_RPS = float(os.getenv("LOAD_RPS", "20"))                 # total requests/sec offered
REQUEST_TIMEOUT_S = float(os.getenv("REQUEST_TIMEOUT_S", "5"))
HANG_S = float(os.getenv("HANG_S", "3"))                      # in-flight longer than this counts as failing
WARMUP_S = int(os.getenv("WARMUP_S", "10"))

# ---- Safety limits (the kill switch) ---------------------------------------
ABORT_ERROR_RATE = float(os.getenv("ABORT_ERROR_RATE", "0.50"))  # 50% of requests failing
ABORT_P95_MS = float(os.getenv("ABORT_P95_MS", "3000"))          # p95 latency above 3 s
ABORT_SUSTAIN_S = int(os.getenv("ABORT_SUSTAIN_S", "5"))         # breach must last this long
WATCH_WINDOW_S = int(os.getenv("WATCH_WINDOW_S", "5"))           # rolling window for checks
MAX_DURATION_S = int(os.getenv("MAX_DURATION_S", "120"))
MIN_DURATION_S = int(os.getenv("MIN_DURATION_S", "10"))
DURATION_SCALE = float(os.getenv("DURATION_SCALE", "1.0"))       # <1 shortens the planners' default 30 s
HEALTHY_ERROR_RATE = 0.05     # app must be below this before an experiment starts
RECOVERY_TIMEOUT_S = int(os.getenv("RECOVERY_TIMEOUT_S", "120"))

# ---- Independent expiry ("dead-man switch") --------------------------------------
REAPER_ENABLED = os.getenv("REAPER_ENABLED", "1") != "0"
REAPER_GRACE_S = int(os.getenv("REAPER_GRACE_S", "15"))

# ---- Network faults (Docker backend) -----------------------------------------
# tc   : a throw-away helper container joins the target's network namespace and runs `tc netem`
# pumba: the Pumba tool (needs the Docker socket mounted into the Pumba container)
NETEM_ENGINE = os.getenv("NETEM_ENGINE", "tc")
TC_IMAGE = os.getenv("TC_IMAGE", "nicolaka/netshoot")
PUMBA_IMAGE = os.getenv("PUMBA_IMAGE", "ghcr.io/alexei-led/pumba:latest")
PUMBA_TC_IMAGE = os.getenv("PUMBA_TC_IMAGE", "ghcr.io/alexei-led/pumba-alpine-nettools:latest")

# ---- LLM -----------------------------------------------------------------
# LLM_PROVIDER: anthropic | openai | none
#   openai also covers any OpenAI-compatible server (Ollama, Groq, Gemini, vLLM)
#   via OPENAI_BASE_URL. "none" uses the built-in rule-based planner (offline).
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "anthropic")
LLM_MODEL = os.getenv("LLM_MODEL", "")  # blank = provider default (see llm.py)

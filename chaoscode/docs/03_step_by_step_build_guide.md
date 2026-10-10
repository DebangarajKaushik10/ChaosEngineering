# 03 — Step-by-Step Build Guide

Caution: the general shape of Hotel Reservation (Go services over gRPC, frontend over HTTP, MongoDB + memcached
per service, Consul, Jaeger) is stable, but file names, ports and commands change between versions. Check each
step against the repo's own README.

## Approach in one paragraph

ResiliAI lets an AI propose a chaos experiment and predict the outcome. A separate safety controller decides
whether it may run, a real fault is injected into a real microservice app, and measurements show how right the
AI was.

| Layer | Job | AI? |
|---|---|---|
| Planner | Proposes a fault and predicts impact | Yes |
| Safety controller | Approves/rejects, enforces limits, stops faults | No |
| Injector | Applies and removes the fault | No |
| Monitor + evaluator | Measures, scores prediction | No |

Loop: Understand app -> Baseline -> AI plan + prediction -> Safety check -> Inject -> Measure ->
Remove fault + verify recovery -> Score prediction -> Report.

---

## Step 1 — Get the app running (Member 1, with Member 5)
Why first: everything depends on a working target.
1. Clone DeathStarBench, go to `hotelReservation`, read its README fully.
2. `docker --version`, `docker compose version`.
3. Start with the documented command (usually `docker compose up -d`). Pulls/builds can be slow; start early.
4. `docker compose ps`: containers running, not restarting.
5. Send a real request to the frontend, e.g.
   `curl "http://localhost:5000/hotels?inDate=2015-04-09&outDate=2015-04-10&lat=38.0235&lon=-122.095"`
   (verify dates/coordinates against the repo; seeded data only matches certain values).

Done when: repeated JSON responses with hotels, not just one.

Check now (these change the plan): `restart: always` in compose? healthchecks defined? Pick the strongest laptop
as the shared test host.

## Step 2 — Map the real architecture (Member 1)
1. Parse `docker-compose.yml` with PyYAML; list every container.
2. Classify each: application service, database, cache, infrastructure.
3. Find real call dependencies from Go source (gRPC dial/connection code in each `services/<name>`).
4. `depends_on` is startup order, not call dependency; a hint, not truth.
5. Build `networkx.DiGraph` (`search -> rate`, `rate -> memcached-rate`, `rate -> mongodb-rate`).
6. Draw with Graphviz or Plotly; correct manually.
7. Add node metadata: `type`, `is_experiment_target`, `criticality`.

Upgrade: with Jaeger running, send traffic, pull traces, build a second graph, diff against declared.

Done when: you can answer "if rate fails, which user-facing requests are affected, and why?"

## Step 3 — Baseline (Member 4)
1. Use an open-loop load generator with fixed rate (wrk2 from the repo, Locust or k6 constant arrival rate).
2. Fixed recipe: request mix, rate (e.g. 50 req/s), warm-up 30 s (discarded), measurement 60 s. Never change it.
3. Run 5 times to see natural variation.
4. Per run record total, successes, failures, mean, p50, p95, p99.
5. Record service health (`docker inspect` or your own probes).
6. Save to SQLite/JSON with timestamp, recipe version, git commit.
7. Compute steady state (mean and std of p95 and success rate). Derive abort thresholds from it.

Done when: "baseline p95 = 42 +/- 4 ms, success 100%" and reruns agree.

## Step 4 — One fault by hand first (Member 2)
Recommended first fault: `docker pause` on the rate service (freezes process, callers hang, exposes missing
timeouts, `unpause` recovers instantly). Also consider `docker stop` (fast-fail; compose may restart it) and
network delay via `tc netem`/Pumba.
1. Under load, `docker pause <rate container>`; watch latency/errors.
2. `docker unpause`; watch recovery.
3. Write down: time to first errors, automatic recovery or not, errors vs hangs.
4. Wrap in Python with the Docker SDK: `apply_fault(target, fault_type)` and `remove_fault(target, fault_type)`.
5. Cleanup must be idempotent.

Done when: inject/remove from Python works and the app fully recovers 3 times in a row.
If the app does not recover after unpause, record it as a real resilience finding.

## Step 5 — Safety controller (Member 2, reviewed by Member 5)
1. Pydantic schema validation before any logic.
2. Policy rules: allowlisted application services only (never Consul or databases at first); fault supported
   for target; hard max duration (e.g. 30 s); one active fault at a time; human approval required.
3. Automatic expiry via a separate watchdog thread/process that removes the fault at TTL.
4. Threshold abort: monitor checks live metrics; crossing the limit calls cleanup immediately.
5. Write-ahead log before injecting; startup reconciler removes leftovers.
6. Emergency stop: dashboard button + CLI calling the same cleanup.
7. Isolation: only the injector touches the Docker socket.

Tests (each becomes a demo item): unknown target; Consul target; 600 s duration; two simultaneous faults;
kill orchestrator mid-fault (watchdog still cleans up); very low threshold forces abort and the app recovers.

Note: abort thresholds are guardrails, not "expected impact". A pause fault is expected to degrade the service,
so set the abort limit looser than the predicted impact. To demo the abort, deliberately tighten it.

## Step 6 — AI planner (Member 3)
1. Install Ollama; pull a small quantized instruct model that fits hardware; check the licence.
2. Pydantic plan schema: `target`, `fault_type`, `duration_seconds`, `rationale`, and a structured prediction
   (`affected_services`, `p95_multiplier` range, `error_rate` range, `expected_recovery_seconds`, `confidence`).
3. Pass the schema to Ollama's `format` option, then validate again with Pydantic.
4. Give the model only: graph (text), baseline summary, permitted targets/fault types, past results.
5. Malformed output -> retry once or twice -> rule-based fallback (highest-centrality untested allowlisted service).
6. Treat any text from logs/containers as untrusted data.

Done when: valid, specific plans repeatedly; safety accepts/rejects them correctly.

## Step 7 — Integrate (Member 5)
State machine:

```
PLANNED -> VALIDATED -> APPROVED -> INJECTING -> OBSERVING -> RECOVERING -> VERIFIED -> SCORED
              |                                      |
          REJECTED                       ABORTED (threshold or kill switch)
```

Every transition is logged; every path ends in cleanup. Sequence: load graph+baseline; planner proposes and
saves prediction first; safety validates and human approves; injector applies and watchdog starts; monitor
samples; fault removed and monitor waits for metrics within baseline band (recovery time); evaluator compares
prediction to measurement and stores everything.

Done when: one command/button runs the full loop and leaves a complete record.

## Step 8 — Dashboard, evidence, README (everyone)
Streamlit page with graph, AI plan, safety decision, live metrics, before/during/after chart. Save logs, metrics
files, screenshots of one successful run, one rejected plan, one threshold abort. README reproducible by a
stranger. Rehearse three times; cache the LLM output used in the demo.

## Day 2
1. Prediction scoring + no-AI graph baseline predictor.
2. Blind investigation: different data views, never shown the injected fault, scored against truth.
3. Evidence-checked report.
4. Fix and retest: config-level fixes only, human-approved, same experiment rerun and compared.
5. Benchmark rule-based vs AI planner if time permits.

## Timeline suggestion

| Phase | Who | What |
|---|---|---|
| Hours 0-2 | M1, M5 | App up and verified; others set up environment; agree schemas |
| Hours 2-4 | M1, M4, M2, M3 | Graph, baseline, manual fault, planner prototype in parallel |
| Hours 4-6 | M2, M3, M5 | Safety controller, planner schema + fallback, orchestrator skeleton |
| Hours 6-8 | M5 + all | Integration, threshold test, dashboard |
| Final hours | All | Evidence, README, demo rehearsal |

Agree the Pydantic schemas first (`app/core/schemas.py`) so all five can code against the same shapes.

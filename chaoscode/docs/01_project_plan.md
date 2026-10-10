# 01 — Project Plan (team's original plan)

## 1. What are we building?

**ResiliAI** is an AI-driven chaos engineering system that tests the resilience of a real microservices
application. The application under test is the existing **DeathStarBench Hotel Reservation** app. We do not
build a new hotel app; we build a separate ResiliAI system around it.

| Layer | Role |
|---|---|
| AI intelligence layer | Understands the app, chooses experiments, predicts impact |
| Safety and orchestration layer | Validates the plan, checks permissions, controls execution |
| Hotel Reservation application | The real app where controlled failures are injected into permitted targets |
| Monitoring and evaluation | Measures impact, checks recovery, generates evidence-backed results |

AI participates **before, during and after** a failure:
- **Before:** chooses what to test and predicts what might happen.
- **During:** requests execution of the approved experiment; deterministic safety controls monitor.
- **After:** investigates evidence, recommends a fix, helps evaluate whether the fix works.

The AI never executes arbitrary commands. A separate safety controller has final authority over fault injection.

## 2. Day 1 milestones (for shortlisting)

### M1 — Setup and application selection (Member 1, support Member 5)
Clone repo, inspect `hotelReservation`, read compose files/env/config, verify Docker + Compose, start containers
with the documented procedure, check health, run a normal hotel-search request or the recommended workload.
**Proof:** successful normal request and a working app in the local environment.

### M2 — Application mapping (Member 1)
Extract services/relationships from compose and config, identify callers and their DBs/caches/infra, build a
dependency graph, ask AI to explain it and propose safe candidate targets, manually correct runtime dependencies.
Tools: Python, PyYAML, NetworkX, Docker inspect, Graphviz/Plotly.
**Proof:** a service map the team can explain and use to select a real experiment.
Note: 24 containers != 24 microservices. Some are databases/infrastructure.

### M3 — Monitoring baseline (Member 4)
Repeatable normal hotel-search requests; record requests/successes/failures; latency and p95; service health;
save a timestamped baseline. Python collector first; Prometheus optional and must not delay the first experiment.
**Proof:** saved baseline and a small table/graph.

### M4 — First failure injection (CRITICAL; Members 2, 3, 4, 5)
1. AI proposes fault and target from the map and baseline.
2. Safety controller checks target and parameters.
3. Experiment approved.
4. Restricted injector applies the fault.
5. Monitoring captures effects.
6. Fault removed and recovery checked.

Recommended first fault: short controlled failure of an allowlisted service such as `rate` (if verified safe and
recoverable), or network delay if easier to configure. Tools: Docker SDK, or Toxiproxy; FastAPI + Pydantic.
**Proof:** real fault applied, measurable effect (or documented absence of effect), recovery after cleanup.

### M5 — Safety controls (Member 2, integrated by Member 5)
Permitted targets and fault profiles; max duration and bounded parameters; reject unknown targets/invalid
requests; automatic fault expiry; emergency stop; threshold breach triggers cleanup; verify return to healthy state.
**Proof:** at least one deliberate safety test showing automatic stop/rollback on a threshold breach.
Safety must never depend on an AI model responding correctly.

### M6 — Day 1 checkpoint (all, coordinated by Member 5)
Demo: app running; dependency graph + baseline; AI experiment proposal; safety decision and approval; one actual
injected fault; visible impact, fault removal and recovery evidence. Plus a README to reproduce it.

**Day 1 rule:** if time is tight, prioritise reliable injection and safety over advanced AI features.

## 3. Tech stack

| Tool | Purpose |
|---|---|
| Docker + Compose | Existing app and test environment; Docker SDK later performs approved container faults |
| Python | Planner, safety controller, injector wrapper, collector, evaluator, API |
| FastAPI + Pydantic | Backend and structured validation |
| Toxiproxy | Network fault injection (needs traffic routed through it; not needed for container stop) |
| Ollama + open-weight model | Local reasoning and tool selection (check hardware and licence; Ollama is the runner, not the model) |
| NetworkX | Dependency graph and impact analysis |
| Python collector or Prometheus | Metrics |
| Streamlit + Plotly | UI and charts |
| SQLite + pytest + Git | History, tests, collaboration |

## 4. Multi-agent system

An agent is a software component that uses an AI model for a defined task, receives relevant information and
returns a structured result. A central orchestrator coordinates them.

| Component | Question it answers | AI? |
|---|---|---|
| Agent 1 Architecture Analyst | What does the app look like; which dependencies matter? | Yes |
| Agent 2 Experiment Planner | What should we test next and why? | Yes |
| Independent Safety Controller | Is this action allowed? | **No** |
| Restricted Executor | How do I request execution of the approved experiment? | Limited tools only |
| Agent 3 Dependency Investigator | Which dependency explains the impact? | Yes |
| Agent 4 Metrics Investigator | What do the numbers say? | Yes |
| Agent 5 Challenger | What did the others miss? | Yes |
| Evaluator + Remediation | Which conclusion is supported; what next? | Evaluator is deterministic |

Orchestrator flow: architecture + baseline -> planner proposes and records prediction -> safety validates and
obtains approval -> executor requests injection -> monitoring records results -> investigators analyse
independently -> evaluator checks -> remediation proposes follow-up -> orchestrator manages retest.

Day 1: only Architecture Analyst, Experiment Planner and the restricted execution path.
Agents can start as plain Python modules calling the same local model with different instructions. LangGraph is optional.

## 5. Day 2

1. **Morning:** complete the planner (explain choice, expectation, what evidence indicates a problem).
2. **Late morning:** prediction versus reality.
3. **Afternoon:** multi-agent hypothesis tournament (Dependency, Metrics, Challenger + structured evaluator).
4. **Late afternoon:** remediation and retesting (AI recommends, developer approves, same experiment repeated).
5. **Final:** dashboard, history, resilience score, rule-based vs AI planner benchmark.

Differentiators: prediction vs reality; hypothesis tournament; fix and verify; planner benchmark.
These are not prerequisites for Day 1.

## 6. Repository structure

```
resiliAI/
├── app/{main.py, api/experiments.py, core/{orchestrator,schemas}.py, architecture/discovery.py,
│        agents/{planner,dependency_investigator,metrics_investigator,challenger,remediation}.py,
│        safety/controller.py, chaos/injector.py, monitoring/metrics.py,
│        evaluation/scoring.py, storage/history.py}
├── dashboard/app.py
├── tests/  artifacts/  .env.example  requirements.txt  README.md
```

Keep the Hotel Reservation checkout separate.

## 7. Dashboard

Application, safety status (Validated/Blocked), baseline, current experiment and elapsed time, dependency graph
(must show the real discovered topology), AI plan (target, fault, duration, prediction, rationale, safety
decision), live results (latency, error rate, health, recovery, safety events), and final report (findings,
evidence, alternatives, fixes, prediction accuracy, retest comparison). Not pretty on Day 1; make the workflow visible.

## 8. Member deliverables for Day 1

| Member | Deliverable |
|---|---|
| 1 Architecture | App runs, service map documented, graph available |
| 2 Injection + safety | One real fault applied/removed; limits and stop behaviour tested |
| 3 AI | Planner returns structured plan with reason and prediction |
| 4 Monitoring | Baseline and fault-period metrics captured and compared |
| 5 Integration | Single workflow: planning, approval, injection, monitoring, recovery |

## 9. Recommended order

1 app running, 2 real topology, 3 baseline, 4 one real injector, 5 AI planner, 6 safety controller,
7 full workflow, 8 prove milestones with logs/metrics/graph/screenshots, 9 Day 2 multi-agent + prediction
evaluation, 10 remediation, retest, history, benchmark.

Caution: DeathStarBench is distributed; starting all containers and generating meaningful load may need setup
and resource tuning. Do not let the whole team wait on one slow machine.

# 02 — Review, Weak Spots and Standout Features

## What you are building

A closed loop around an existing app:

**Understand -> Hypothesize -> Validate -> Inject -> Measure -> Explain -> Fix -> Retest**

The AI proposes, deterministic code disposes, and measurements decide who was right.
"AI never touches the shell; safety is independent code" is the strongest part of the plan. Keep it.

## What the plan gets right

- Ordering: a real fault with safety controls beats a fancy agent diagram.
- Safety independent of the model: judges will probe this.
- Prediction before execution: turns AI output into something testable.
- Honest caveats: 24 containers != 24 services; injector separate from planner.

## Weak spots that will hurt against 100 teams

1. **Most teams build "LLM picks a fault, we inject, LLM writes a report."** Your core is the same. What
   separates you is measurement rigor.
2. **The hypothesis tournament is vulnerable.** Three prompts on the same small model are highly correlated.
   An LLM-as-judge picking a winner is unscorable. Fix: blind attribution scored against ground truth (below).
3. **A Python `requests` loop gives misleading latency under failure.** A closed-loop client slows down when the
   system slows down and hides tail latency (coordinated omission). Use an open-loop generator with a constant
   arrival rate (the repo ships a wrk2 workload; Locust/k6 with fixed arrival rate also work).
4. **Single runs are anecdotes.** Repeat baselines for variance; use warm-up and cooldown; ideally 3 trials per experiment.
5. **"Fix and verify" is the riskiest promise.** Don't rewrite Go services. Realistic fixes are config-level:
   restart policy, healthchecks, resource limits, replicas. Whether replicas help depends on gRPC client
   discovery; verify by hand first.
6. **Facts to verify early:** healthchecks present? `restart: always` set (it fights `docker stop`)? Do clients
   reconnect after a dependency restarts? Are Consul and Jaeger deployed (Consul must NOT be a target)?

## Features that make you stand out (by impact over effort)

### 1. Blind root-cause attribution with ground truth (best idea)
In real incidents nobody knows the true cause; in chaos engineering you injected it. Investigators see only
metrics, traces and graph data, never the injected fault. Each submits a ranked, falsifiable hypothesis (cause,
evidence, a test that would disprove it). Score against the known truth over multiple experiments (top-1
accuracy, calibration). Give each investigator a different **view** of the data, not just a different prompt:
- Dependency investigator: graph + traces
- Metrics investigator: time series only
- Challenger: everything plus the others' claims

Result: "multi-view investigation found the true cause in X of Y experiments vs Z for a single agent."

### 2. Quantified, calibrated prediction scoring
Planner emits: affected service set, p95 multiplier range, error-rate range, recovery time, confidence.
Score: Jaccard overlap for blast radius, hit rate on intervals, Brier score for confidence, calibration plot
over time. Add a **graph-only baseline predictor** (propagate failure through NetworkX, no LLM). If the LLM
beats it you have evidence AI adds value; if not, you found out honestly.

### 3. Runtime topology from distributed traces
Compose shows what is declared; Jaeger traces show what actually calls what. Build a graph from traces, diff
against the declared graph, report differences as discoveries.

### 4. Steady-state hypotheses and SLO-based aborts
Principles of Chaos Engineering framing: define steady state from baseline (p95 and success rate with
confidence bands), state a hypothesis, minimise blast radius. Abort thresholds come from the baseline.

### 5. Safety beyond an allowlist
- Write-ahead fault log (SQLite) before applying any fault.
- Independent watchdog enforcing TTL even if the orchestrator crashes.
- Startup reconciler that undoes anything the log says is still active.
- Kill switch in the dashboard and a CLI equivalent.
- Docker socket isolation: only the injector touches it; the LLM never does.
- Treat logs/metrics as data, never instructions (prompt-injection risk).
- Adversarial test suite: plans targeting consul, 10-minute duration, multiple services -> all rejected.
  A table of attempted unsafe plans vs rejection reasons is a strong demo slide.

### 6. Campaigns, not single shots
Budgeted campaign of 4-6 experiments with a coverage matrix (service x fault type), prefer high-centrality
untested services, use earlier results to pick the next. Legitimate rule-based vs AI benchmark.

### 7. Evidence-bound claims
Each report statement points to a stored metric snapshot; a deterministic checker verifies it
("search p95 increased 3.1x": true/false). Unsupported claims are flagged.

### 8. Richer, reliable fault menu
- `docker pause`: freezes the process; connections hang instead of failing fast (exposes missing timeouts).
- `docker update`: throttle CPU/memory.
- Network delay via netem or Pumba, instead of rerouting through Toxiproxy (which needs app address changes).
Two or three faults done reliably beat six done flakily.

### 9. Replayable demo
Store every experiment, LLM output and metric snapshot in SQLite for replay. Fix seeds, cache the LLM responses
used in the demo, rehearse the exact fault path.

## What NOT to do

- Let the LLM generate or run shell commands, even "sandboxed".
- Use an LLM as the final judge of correctness; keep the evaluator deterministic.
- Build RL, fine-tuning, or migrate to Kubernetes.
- Stand up Prometheus/Grafana before the first experiment works.
- Claim the system "predicts failures" without the scoring above.
- Auto-apply fixes without human approval.
- Invent an unexplainable resilience score; define it from SLO adherence, p95 degradation, blast radius, recovery time.
- Add LangGraph just for appearance.

## Model and hardware

7-8B instruct-class open model, quantized, in Ollama. Check licence and RAM/GPU. Force structured output with
Ollama's JSON-schema `format` option and validate with Pydantic. Reject and retry malformed plans; keep a
rule-based fallback so the demo never depends on the model.

## Scope tiers

**Must (Day 1):** app running with verified request; service map and baseline with variance; one reliable
fault; safety controller with TTL watchdog and threshold rollback; structured planner with predictions;
end-to-end workflow and README.

**Should (Day 2):** quantified prediction scoring with graph-only baseline; blind multi-view investigation
scored against truth; evidence-checked report; adversarial safety table; a simple verified fix and retest.

**Could:** trace-derived topology diff; campaign mode; rule-based vs AI benchmark.

**Won't:** anything in the "What NOT to do" list.

## The pitch

"ResiliAI doesn't just ask an AI what might break. It makes the AI commit to a prediction, injects a real fault
under independent safety controls, and then measures how right the AI was, including whether its diagnosis
matches the fault we actually injected." Show one case where the AI was wrong or the safety layer blocked it;
it builds more credibility than a polished dashboard.

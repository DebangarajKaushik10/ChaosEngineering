# 04 — How to Explain ResiliAI to the Mentor

## 30-second version

"We're not building a new app. We're testing an existing microservice benchmark with an AI-assisted chaos
engineering system. The AI proposes experiments and predicts what will happen, but a deterministic safety
controller has the final say and can stop any fault on its own. We measure a baseline first, inject a real
fault, measure the impact, check recovery, then compare the AI's prediction with reality. On Day 1 we prove one
safe, real, repeatable experiment. On Day 2 we add investigation, prediction scoring and fix-and-retest."

## The core design rule

AI is allowed to **think**, never to **act**.

| Layer | Job | AI? |
|---|---|---|
| Planner | Proposes fault, predicts impact | Yes |
| Safety controller | Approve/reject, limits, stop | No |
| Injector | Apply/remove fault | No |
| Monitor + evaluator | Measure, score | No |

Why this is right:
- If the AI is wrong, the worst case is a rejected plan, not an out-of-control system.
- Measurements come from deterministic code, so results are trustworthy and repeatable.
- The AI's claims are testable: it commits to a prediction before the fault and we score it after.

## The experiment loop

Understand app -> Baseline -> AI plan + prediction -> Safety check -> Inject -> Measure ->
Remove fault + verify recovery -> Score prediction -> Report

## Likely mentor questions and answers

**What if the AI picks something dangerous?**
The safety controller is plain code with an allowlist, hard duration cap, one-fault-at-a-time rule and human
approval. It rejects regardless of the AI. A separate watchdog removes the fault at TTL even if the orchestrator
crashes. We have tests showing unsafe plans being rejected.

**How do you know the fault had an effect?**
We measure a baseline with variance first (open-loop load so tail latency is not hidden), then compare
fault-period p95 and error rate against it.

**Where is the AI actually useful?**
Choosing targets from the dependency graph, predicting blast radius, and (Day 2) diagnosing cause. We score it:
prediction accuracy, calibration, and comparison against a no-AI graph baseline.

**Why not let the AI run commands?**
A model can be wrong or be prompt-injected by log content. The Docker socket is root-equivalent, so only the
injector process touches it.

**What if the model misbehaves during the demo?**
Structured output with validation and retries, a rule-based fallback planner, and cached LLM output for the demo.

**What do you do if the app does not recover?**
That is a finding: a real resilience weakness (e.g. a client keeps a dead connection). We record it.

**Why DeathStarBench?**
It is a real distributed benchmark; we do not waste time building an app and the faults are meaningful.

## Day 1 vs Day 2 summary

- Day 1: app running, graph, baseline, one real fault, safety demo (incl. threshold abort), AI plan, full
  workflow, README.
- Day 2: prediction vs reality scoring, blind multi-view investigation scored against ground truth,
  evidence-checked report, fix and retest, planner benchmark.

## Honest limitations to state

- Container names/ports must be verified per deployment.
- Small local models are weaker; the fallback planner exists for that reason.
- Fixes are config-level (restart policy, limits, healthchecks), not code rewrites.
- Results from few trials are indicative, not statistically conclusive.

# 05 — Day 1 Checklist, Roles and Common Mistakes

## Day 1 acceptance checklist

- [ ] Hotel Reservation starts successfully and a normal request works
- [ ] Service and dependency mapping is available
- [ ] Baseline latency, errors and health are recorded
- [ ] AI proposes a specific, structured experiment
- [ ] Safety controller rejects an invalid target or unsafe parameter
- [ ] At least one actual failure is injected into the test application
- [ ] An automatic stop or rollback threshold is tested
- [ ] The fault is removed and recovery is verified
- [ ] The complete workflow is documented and repeatable

All five deliverables must work together. Separate files that were never integrated do not count.

## Roles

| Member | Area | Day 1 deliverable | Repo files |
|---|---|---|---|
| 1 | Architecture | App runs; service map documented; graph available | `app/architecture/discovery.py`, `config/service_map.yaml` |
| 2 | Injection + safety | One real fault applied/removed; limits and stop tested | `app/chaos/injector.py`, `app/safety/controller.py`, `tests/test_safety.py` |
| 3 | AI | Planner returns structured plan with reason and prediction | `app/agents/planner.py`, `app/core/schemas.py` |
| 4 | Monitoring | Baseline and fault-period metrics captured/compared | `app/monitoring/metrics.py` |
| 5 | Integration | One workflow: plan, approval, injection, monitoring, recovery | `app/core/orchestrator.py`, `app/main.py`, `dashboard/app.py` |

## Evidence to save in `artifacts/`

- Output of the verified normal request
- Service map image and the corrected edges
- Baseline table (5 runs) with timestamp
- One successful experiment record (plan, prediction, metrics, recovery time)
- One rejected plan with reason
- One threshold-abort run
- Screenshots of the dashboard during each

## Common mistakes

- Starting with agents before the app and a fault work.
- Treating `depends_on` as real call dependencies.
- Using only a Python `requests` loop for latency.
- Trusting a single run.
- Letting planner output reach the injector without safety validation.
- Putting the LLM near the Docker socket or a shell.
- Targeting Consul or databases in early experiments.
- Forgetting `restart: always` may undo your fault.
- Skipping the manual fault test.
- Changing the load recipe between baseline and fault runs.
- Overclaiming ("AI predicts failures") without scored predictions.
- Building a fancy dashboard before one experiment runs end to end.

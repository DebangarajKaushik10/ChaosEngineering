"""Config-level remediation suggestions. Proposals only: a human approves, then `retest` re-runs the
SAME experiment and compares before/after. Nothing here changes the app."""


def propose(record):
    plan = record["plan"]
    tool, target = plan["tool"], plan["target"]
    impact = [l for l in record.get("impact_summary", []) if l != "no significant impact"]
    out = []
    if not impact:
        return [{"change": "None needed for this experiment: no measurable impact.",
                 "why": "The system tolerated this failure.", "level": "none", "needs_approval": False}]
    if tool == "stop_dependency" and target.startswith("memcached"):
        out.append({"change": "Treat cache errors like cache misses (read from MongoDB, log a warning) "
                              "instead of log.Panic in the service that uses this cache.",
                    "why": "Losing the cache crashes its callers; the database could serve the data.",
                    "level": "code (small Go change)", "needs_approval": True})
    if tool == "stop_dependency" and target == "consul":
        out.append({"change": "Run Consul with a data volume (not -dev) and re-register periodically.",
                    "why": "Registrations live in memory and services register only at startup.",
                    "level": "config", "needs_approval": True})
    if tool in ("pause_service", "network_delay", "packet_loss", "cpu_throttle"):
        out.append({"change": "Set context.WithTimeout on every outbound gRPC call, add retries with "
                              "backoff and a circuit breaker.",
                    "why": "No service sets a deadline, so slow or hung dependencies stall callers.",
                    "level": "code", "needs_approval": True})
    if tool == "kill_service":
        out.append({"change": f"Run 2 replicas of '{target}' behind Consul and add a healthcheck.",
                    "why": "Single replica means a crash is a user-visible outage. "
                           "Verify client-side balancing across replicas first.",
                    "level": "config", "needs_approval": True})
    if not out:
        out.append({"change": "Add a healthcheck and review timeouts for this dependency.",
                    "why": "Impact observed.", "level": "config", "needs_approval": True})
    return out

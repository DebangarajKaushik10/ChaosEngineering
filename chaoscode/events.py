"""Append-only event log: every safety-relevant decision is written to results/safety_log.jsonl.

Used as evidence for the demo (rejected plans, approvals, kill-switch firings, cleanups).
"""
import json
import time

import config


def log_event(event, **fields):
    rec = {"ts": round(time.time(), 2), "time": time.strftime("%H:%M:%S"), "event": event, **fields}
    try:
        with open(config.RESULTS_DIR / "safety_log.jsonl", "a") as f:
            f.write(json.dumps(rec, default=str) + "\n")
    except OSError:
        pass
    return rec


def read_events():
    path = config.RESULTS_DIR / "safety_log.jsonl"
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out

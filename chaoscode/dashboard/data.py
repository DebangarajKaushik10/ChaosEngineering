"""Plain-Python helpers for the dashboard (no Streamlit import, so they are unit-testable)."""
import json
from pathlib import Path


def load_results(results_dir):
    d = Path(results_dir)
    out = {"experiments": [], "baseline": None, "events": [], "active": {}}
    for f in sorted((d / "experiments").glob("*.json")) if (d / "experiments").exists() else []:
        try:
            out["experiments"].append(json.loads(f.read_text()))
        except ValueError:
            pass
    for name, key in (("baseline.json", "baseline"), ("active_faults.json", "active")):
        try:
            out[key] = json.loads((d / name).read_text())
        except Exception:
            pass
    p = d / "safety_log.jsonl"
    if p.exists():
        for line in p.read_text().splitlines():
            try:
                out["events"].append(json.loads(line))
            except ValueError:
                pass
    return out


def timeline_rows(record):
    """[(t_seconds, error_rate, p95_ms)] for the experiment chart."""
    rows = []
    for pt in record.get("timeline") or []:
        if isinstance(pt, dict):
            rows.append((pt.get("t"), pt.get("error_rate"), pt.get("p95_ms")))
    return rows


def is_simulated(results):
    return any(e.get("backend") == "sim" for e in results["experiments"])

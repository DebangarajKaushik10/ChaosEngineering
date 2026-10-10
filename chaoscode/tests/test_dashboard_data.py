import json
import tempfile
import unittest
from pathlib import Path

from dashboard import data


class DashboardDataTests(unittest.TestCase):
    def test_load_and_flags(self):
        d = Path(tempfile.mkdtemp())
        (d / "experiments").mkdir()
        (d / "experiments" / "a.json").write_text(json.dumps(
            {"id": "a", "backend": "sim", "plan": {"tool": "t", "target": "x"},
             "timeline": [{"t": 1, "error_rate": 0.5, "p95_ms": 10}]}))
        (d / "safety_log.jsonl").write_text('{"event": "x"}\nnot json\n')
        r = data.load_results(d)
        self.assertEqual(len(r["experiments"]), 1)
        self.assertEqual(len(r["events"]), 1)
        self.assertTrue(data.is_simulated(r))
        self.assertEqual(data.timeline_rows(r["experiments"][0]), [(1, 0.5, 10)])
        self.assertEqual(data.load_results(Path(tempfile.mkdtemp()))["experiments"], [])

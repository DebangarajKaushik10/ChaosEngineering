"""Loadgen, kill switch, reaper, prediction scoring, attribution and the planner against the simulator."""
import json
import os
import subprocess
import sys
import time
import unittest
from unittest import mock

import agent
import chaos_tools
import config
import depmap
import investigate
import predict
import safety
from loadgen import LoadGenerator
from sim import fake_app

URL = config.FRONTEND_URL


def setUpModule():
    global SRV
    SRV = fake_app.start_in_thread("127.0.0.1", int(URL.rsplit(":", 1)[1]))


def tearDownModule():
    SRV.shutdown()


class Base(unittest.TestCase):
    def setUp(self):
        chaos_tools.undo_all()
        chaos_tools.reset_app()


class LoadGenTests(Base):
    def test_open_loop_sees_a_hang(self):
        lg = LoadGenerator(rps=20)
        lg.start()
        time.sleep(3)
        healthy = lg.metrics(3)["overall"]
        self.assertEqual(healthy["error_rate"], 0.0)
        f = chaos_tools.inject("pause_service", "profile", 20)
        time.sleep(config.HANG_S + 3)
        hung = lg.metrics(3)["overall"]
        chaos_tools.undo(f)
        lg.stop()
        # a closed-loop generator would show ~0 requests and 0 errors here (coordinated omission)
        self.assertGreater(hung["error_rate"], 0.5)


class KillSwitchTests(Base):
    def test_breach_triggers_rollback(self):
        lg = LoadGenerator(rps=20)
        lg.start()
        time.sleep(3)
        fired = []
        wd = safety.Watchdog(lg, lambda reason: fired.append(reason))
        f = chaos_tools.inject("kill_service", "search", 60)
        wd.start()
        t0 = time.time()
        while not fired and time.time() - t0 < 30:
            time.sleep(0.5)
        wd.stop()
        chaos_tools.undo(f)
        lg.stop()
        self.assertTrue(fired, "kill switch never fired")
        self.assertEqual(chaos_tools.load_state(), {})


class ReaperTests(Base):
    def test_reaper_undoes_orphaned_fault(self):
        f = chaos_tools.inject("kill_service", "geo", 60)
        self.assertEqual(len(chaos_tools.load_state()), 1)
        # simulate "agent crashed": nobody calls undo; the separate reaper process does it
        subprocess.run([sys.executable, os.path.join(config.HERE, "reaper.py"), f["id"], "1"],
                       cwd=config.HERE, env=os.environ.copy(), timeout=30, check=True)
        self.assertEqual(chaos_tools.load_state(), {})


class UndoAllTests(Base):
    def test_undo_all(self):
        chaos_tools.inject("pause_service", "geo", 60)
        self.assertEqual(len(chaos_tools.undo_all()), 1)
        self.assertEqual(chaos_tools.load_state(), {})


class PredictTests(unittest.TestCase):
    def setUp(self):
        self.dm = depmap.build()

    def test_graph_baseline_shapes(self):
        p = predict.graph_baseline(self.dm, "kill_service", "search")
        self.assertIn("/hotels", p["affected_endpoints"])
        self.assertGreaterEqual(p["error_rate_range"][0], 0.5)
        c = predict.graph_baseline(self.dm, "stop_dependency", "consul")
        self.assertEqual(c["affected_endpoints"], [])

    def test_normalize_rejects_garbage(self):
        fb = predict.graph_baseline(self.dm, "kill_service", "search")
        out = predict.normalize({"error_rate_range": "nope"}, fb, ["/hotels"])
        self.assertEqual(out["error_rate_range"], fb["error_rate_range"])


class DepmapTests(unittest.TestCase):
    def test_map_contents(self):
        dm = depmap.build()
        for n in ("frontend", "search", "geo", "rate", "profile", "memcached-profile", "mongodb-user", "consul"):
            self.assertIn(n, dm["nodes"])
        self.assertIn("geo", dm["nodes"]["search"]["depends_on"])
        self.assertTrue(depmap.to_mermaid(dm).startswith("graph"))


class PlannerTests(Base):
    def setUp(self):
        super().setUp()
        self.dm = depmap.build()

    def test_rule_planner_is_valid_and_safe_first(self):
        chosen = agent.rule_based_plan(self.dm, [])["chosen"]
        ok, why = safety.validate(chosen)
        self.assertTrue(ok, why)
        self.assertFalse(safety.is_high_risk(chosen))

    def test_llm_garbage_falls_back(self):
        with mock.patch("llm.ask_json", return_value={"tool": "rm_rf", "target": "x"}), \
                mock.patch.object(config, "LLM_PROVIDER", "anthropic"):
            plan = agent.plan_next(self.dm, None, [], log=lambda *a: None)
        ok, why = safety.validate(plan["chosen"])
        self.assertTrue(ok, why)


class EndToEnd(Base):
    def test_one_experiment_and_attribution(self):
        lg = LoadGenerator(rps=20)
        lg.start()
        time.sleep(2)
        dm = depmap.build()
        baseline = lg.metrics(window_s=2)
        plan = {"chosen": {"tool": "kill_service", "target": "search", "duration_s": 8, "params": {},
                           "reason": "test", "hypothesis": "test",
                           "prediction": predict.graph_baseline(dm, "kill_service", "search")},
                "hypothesis": "test", "_planner": "manual"}
        rec = agent.run_experiment(lg, plan, baseline, log=lambda *a: None, dm=dm)
        lg.stop()
        self.assertEqual(chaos_tools.load_state(), {})
        self.assertEqual(rec["backend"], "sim")
        self.assertIsNotNone(rec.get("recovery_time_s"))
        res = investigate.evaluate(rec, dm)
        self.assertIsNotNone(res)
        self.assertTrue(res["score"]["target_top3"])
        self.assertNotIn("plan", investigate.blind_view(rec))


if __name__ == "__main__":
    unittest.main()

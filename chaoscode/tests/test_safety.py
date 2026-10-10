import unittest

import chaos_tools
import config
import safety


def plan(**kw):
    p = {"tool": "kill_service", "target": "search", "duration_s": 10, "params": {}}
    p.update(kw)
    return p


class GuardTests(unittest.TestCase):
    def setUp(self):
        chaos_tools._save_state({})

    def test_valid_plan(self):
        self.assertEqual(safety.validate(plan()), (True, "ok"))

    def test_rejections(self):
        bad = [plan(tool="rm_rf"), plan(target="frontend"), plan(target="jaeger"),
               plan(target="mongodb-user"), plan(target="geo; docker rm -f $(docker ps -aq)"),
               plan(duration_s=9999), plan(duration_s=1), plan(duration_s="abc"),
               plan(params={"signal": 9}), plan(tool="network_delay", target="geo", params={}),
               plan(tool="network_delay", target="geo", params={"delay_ms": 99999}), "not a dict", None]
        for p in bad:
            ok, why = safety.validate(p)
            self.assertFalse(ok, f"should reject {p}: {why}")

    def test_one_experiment_at_a_time(self):
        chaos_tools._save_state({"x": {"id": "x", "tool": "kill_service", "target": "geo"}})
        ok, why = safety.validate(plan())
        self.assertFalse(ok)
        self.assertIn("one at a time", why)

    def test_high_risk(self):
        self.assertTrue(safety.is_high_risk(plan(tool="stop_dependency", target="mongodb-user")))
        self.assertTrue(safety.is_high_risk(plan(tool="stop_dependency", target="consul")))
        self.assertFalse(safety.is_high_risk(plan()))

    def test_frontend_never_targetable(self):
        for spec in chaos_tools.CATALOG.values():
            self.assertNotIn("frontend", spec["targets"])
            self.assertNotIn("jaeger", spec["targets"])
        self.assertLessEqual(config.MIN_DURATION_S, config.MAX_DURATION_S)


if __name__ == "__main__":
    unittest.main()

"""The real Docker path, exercised with a fake `docker` binary: checks the exact commands we would run."""
import json
import os
import shutil
import stat
import tempfile
import unittest

import chaos_tools

HERE = os.path.dirname(__file__)


@unittest.skipIf(os.name == "nt", "fake docker is a POSIX shim")
class DockerCommandTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        shutil.copy(os.path.join(HERE, "fake_docker.py"), os.path.join(self.dir, "docker"))
        os.chmod(os.path.join(self.dir, "docker"), 0o755)
        self.log = os.path.join(self.dir, "calls.log")
        open(self.log, "w").close()
        self.env = dict(os.environ)
        os.environ["PATH"] = self.dir + os.pathsep + os.environ["PATH"]
        os.environ["FAKE_DOCKER_LOG"] = self.log
        self.old_backend = chaos_tools.BACKEND
        chaos_tools.BACKEND = "docker"
        chaos_tools._save_state({})

    def tearDown(self):
        chaos_tools.BACKEND = self.old_backend
        os.environ.clear()
        os.environ.update(self.env)
        chaos_tools._save_state({})

    def calls(self):
        return [json.loads(l) for l in open(self.log).read().splitlines() if l]

    def has(self, *prefix):
        return any(c[:len(prefix)] == list(prefix) for c in self.calls())

    def test_kill_disables_restart_then_restores(self):
        f = chaos_tools.inject("kill_service", "search", 10)
        self.assertTrue(self.has("update", "--restart=no"))
        self.assertTrue(self.has("compose", "kill", "search"))
        order = [c[0:2] for c in self.calls()]
        self.assertLess(order.index(["update", "--restart=no"]), order.index(["compose", "kill"]))
        chaos_tools.undo(f)
        self.assertTrue(self.has("update", "--restart=always"))
        self.assertTrue(self.has("compose", "start", "search"))
        self.assertEqual(chaos_tools.load_state(), {})

    def test_pause_and_stop(self):
        f = chaos_tools.inject("pause_service", "geo", 10)
        self.assertTrue(self.has("compose", "pause", "geo"))
        chaos_tools.undo(f)
        self.assertTrue(self.has("compose", "unpause", "geo"))
        f = chaos_tools.inject("stop_dependency", "memcached-rate", 10)
        self.assertTrue(self.has("compose", "stop", "memcached-rate"))
        chaos_tools.undo(f)
        self.assertTrue(self.has("compose", "start", "memcached-rate"))

    def test_netem_uses_tc_in_target_namespace(self):
        f = chaos_tools.inject("network_delay", "profile", 10, delay_ms=300)
        run = [c for c in self.calls() if c[0] == "run"][0]
        self.assertIn("--cap-add", run)
        self.assertTrue(any(x.startswith("container:") for x in run))
        self.assertIn("delay 300ms", run[-1])
        chaos_tools.undo(f)
        self.assertTrue(any("tc qdisc del" in c[-1] for c in self.calls() if c[0] == "run"))

    def test_cpu_throttle_and_restore(self):
        f = chaos_tools.inject("cpu_throttle", "mongodb-user", 10, cpus=0.1)
        self.assertTrue(self.has("update", "--cpus", "0.1"))
        chaos_tools.undo(f)
        self.assertTrue(self.has("update", "--cpus", "4.0"))

    def test_failed_injection_stays_recorded_for_cleanup(self):
        # write-ahead: if docker breaks mid-injection and cleanup also fails, the fault must remain
        # recorded so `undo-all` / the reaper can retry it
        os.environ["PATH"] = "/nonexistent"
        with self.assertRaises(Exception):
            chaos_tools.inject("pause_service", "geo", 10)
        self.assertEqual(len(chaos_tools.load_state()), 1)

    def test_undo_all_sweeps(self):
        chaos_tools.inject("pause_service", "geo", 10)
        out = chaos_tools.undo_all()
        self.assertEqual(len(out), 1)
        self.assertTrue(self.has("compose", "unpause"))
        self.assertEqual(chaos_tools.load_state(), {})


if __name__ == "__main__":
    unittest.main()

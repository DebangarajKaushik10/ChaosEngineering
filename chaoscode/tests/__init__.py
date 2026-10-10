"""Test environment: everything runs against the simulator in a throw-away results folder."""
import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="resiliai_tests_")
os.environ.update({
    "RESULTS_DIR": _tmp, "CHAOS_BACKEND": "sim", "REAPER_ENABLED": "0", "LLM_PROVIDER": "none",
    "MIN_DURATION_S": "3", "WARMUP_S": "1", "FRONTEND_URL": "http://127.0.0.1:5077",
    "DURATION_SCALE": "0.2", "RECOVERY_TIMEOUT_S": "30",
})

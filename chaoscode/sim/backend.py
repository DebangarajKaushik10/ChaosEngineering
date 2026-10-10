"""Chaos tools for the built-in simulator (CHAOS_BACKEND=sim). Talks to sim/fake_app.py over HTTP
using FRONTEND_URL, so `undo-all` works from a separate process just like the other backends."""
import json
import urllib.request

import config


def _post(path, body=None):
    req = urllib.request.Request(
        config.FRONTEND_URL.rstrip("/") + path, method="POST",
        data=json.dumps(body or {}).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as r:
        return r.read()


def inject(fault):
    _post("/__fault", {k: fault[k] for k in ("id", "tool", "target", "params")})


def undo(fault):
    _post("/__clear", {"id": fault["id"]})


def sweep():
    _post("/__clear_all")


def reset():
    _post("/__reset")

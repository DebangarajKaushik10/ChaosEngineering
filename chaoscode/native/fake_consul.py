"""Minimal Consul stand-in: the 3 HTTP calls Hotel Reservation makes.

  PUT /v1/agent/service/register        (services register on startup)
  PUT /v1/agent/service/deregister/<id>
  GET /v1/health/service/<name>          (gRPC resolver, with blocking queries)

PORT_MAP rewrites a service's registered port to its Toxiproxy port, so all
service-to-service gRPC traffic flows through Toxiproxy and can be delayed or cut.
"""
import json
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT_MAP = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
services = {}          # id -> {"ID","Name","Address","Port"}
index = 1
cond = threading.Condition()


def parse_wait(s):
    m = re.fullmatch(r"(\d+)(ms|s|m)?", s or "")
    if not m:
        return 300.0
    n, unit = int(m.group(1)), m.group(2) or "s"
    return min({"ms": n / 1000, "s": n, "m": n * 60}[unit], 300.0)


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, code, body=b"", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Consul-Index", str(index))
        self.send_header("X-Consul-KnownLeader", "true")
        self.send_header("X-Consul-LastContact", "0")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_PUT(self):
        global index
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b"{}"
        if self.path.startswith("/v1/agent/service/register"):
            reg = json.loads(raw or b"{}")
            sid = reg.get("ID") or reg["Name"]
            port = reg.get("Port", 0)
            with cond:
                services[sid] = {"ID": sid, "Name": reg["Name"], "Port": port,
                                 "Address": "127.0.0.1",
                                 "AdvertisedPort": PORT_MAP.get(str(port), port)}
                index += 1
                cond.notify_all()
            return self._send(200)
        m = re.match(r"/v1/agent/service/deregister/(.+)", self.path)
        if m:
            with cond:
                services.pop(m.group(1), None)
                index += 1
                cond.notify_all()
            return self._send(200)
        self._send(404)

    def do_GET(self):
        path, _, qs = self.path.partition("?")
        q = dict(p.split("=", 1) for p in qs.split("&") if "=" in p)
        m = re.match(r"/v1/health/service/([^/]+)", path)
        if m:
            name = m.group(1)
            want = int(q.get("index", 0) or 0)
            deadline = time.time() + parse_wait(q.get("wait"))
            with cond:
                while want and want >= index and time.time() < deadline:
                    cond.wait(timeout=deadline - time.time())
                out = [{"Node": {"Node": "local", "Address": "127.0.0.1"},
                        "Service": {"ID": s["ID"], "Service": s["Name"], "Tags": [],
                                    "Address": s["Address"], "Port": s["AdvertisedPort"]},
                        "Checks": []}
                       for s in services.values() if s["Name"] == name]
            return self._send(200, json.dumps(out).encode())
        if path == "/v1/status/leader":
            return self._send(200, b'"127.0.0.1:8300"')
        if path == "/v1/catalog/services":
            return self._send(200, json.dumps({s["Name"]: [] for s in services.values()}).encode())
        self._send(404)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8500
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()

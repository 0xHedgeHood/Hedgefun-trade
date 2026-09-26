"""Loopback-only, no-wallet V2 experiment workbench.

Run from the repository root with ``python3 -m lab.server``. The simulation is
an analytical model; separate fork and scenario actions execute allowlisted
Foundry tests. No endpoint signs or broadcasts transactions.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from lab.model import simulate
from lab.batch_opening import simulate_batch_opening
from lab.fork_runner import run_fork, run_fork_variant
from lab.scenarios import run_scenario


ROOT = Path(__file__).resolve().parent
ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/scenarios.js": ("scenarios.js", "text/javascript; charset=utf-8"),
    "/batch.js": ("batch.js", "text/javascript; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
}
EVM_LOCK = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    server_version = "HedgeFunLab/1.0"

    def _allowed_host(self) -> bool:
        host = self.headers.get("Host", "")
        return host in {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}

    def _send(self, status: int, data: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'none'")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, status: int, obj: dict) -> None:
        self._send(status, json.dumps(obj, ensure_ascii=False, allow_nan=False).encode(), "application/json; charset=utf-8")

    def _post_origin_ok(self) -> bool:
        origin = self.headers.get("Origin")
        return origin in {f"http://127.0.0.1:{self.server.server_port}", f"http://localhost:{self.server.server_port}"}

    def do_GET(self) -> None:  # noqa: N802
        if not self._allowed_host():
            self._json(403, {"error": "Only loopback requests are allowed"})
            return
        path = urlsplit(self.path).path
        if path == "/api/health":
            self._json(200, {"ok": True, "mode": "local only", "fork": "pinned read-only simulation"})
            return
        asset = ASSETS.get(path)
        if asset is None:
            self._json(404, {"error": "Not found"})
            return
        filename, content_type = asset
        self._send(200, (ROOT / filename).read_bytes(), content_type)

    def do_POST(self) -> None:  # noqa: N802
        if not self._allowed_host() or not self._post_origin_ok():
            self._json(403, {"error": "Only same-origin loopback requests are allowed"})
            return
        if self.headers.get("Content-Type", "").split(";", 1)[0] != "application/json":
            self._json(415, {"error": "Expected application/json"})
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size < 0 or size > 32_768:
                raise ValueError("Request too large")
            payload = json.loads(self.rfile.read(size) or b"{}")
            if not isinstance(payload, dict):
                raise ValueError("Expected a JSON object")
            path = urlsplit(self.path).path
            if path == "/api/simulate":
                self._json(200, simulate(payload))
            elif path == "/api/batch":
                self._json(200, simulate_batch_opening(payload))
            elif path == "/api/fork":
                if set(payload) not in (set(), {"lp_bps"}):
                    raise ValueError("Fork accepts only lp_bps")
                lp_bps = payload.get("lp_bps", 5000)
                if type(lp_bps) is not int or lp_bps < 1000 or lp_bps > 9000 or lp_bps % 100:
                    raise ValueError("lp_bps must be an integer percent from 10% to 90%")
                if not EVM_LOCK.acquire(blocking=False):
                    self._json(409, {"error": "An EVM experiment is already in progress"})
                    return
                try:
                    self._json(200, run_fork() if lp_bps == 5000 else run_fork_variant(lp_bps))
                finally:
                    EVM_LOCK.release()
            elif path == "/api/scenario":
                if set(payload) != {"scenario"}:
                    raise ValueError("Scenario accepts only a scenario name")
                if not EVM_LOCK.acquire(blocking=False):
                    self._json(409, {"error": "An EVM experiment is already in progress"})
                    return
                try:
                    self._json(200, run_scenario(payload["scenario"]))
                finally:
                    EVM_LOCK.release()
            else:
                self._json(404, {"error": "Not found"})
        except (ValueError, TypeError, OverflowError) as exc:
            self._json(400, {"error": str(exc)})
        except Exception as exc:  # Last-resort UI error; never echo credentials or env.
            self._json(500, {"error": f"Experiment failed: {type(exc).__name__}"})

    def log_message(self, format: str, *args: object) -> None:
        # Keep the terminal quiet; no request bodies or secrets are logged.
        pass


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Run the local V2 experiment workbench")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("port must be between 1024 and 65535")
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"HedgeFun V2 experiment workbench: http://127.0.0.1:{args.port}", flush=True)
    print("Local simulation only. Fork and scenario modes run Foundry tests without broadcasting.", flush=True)
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

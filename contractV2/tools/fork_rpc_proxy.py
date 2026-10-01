#!/usr/bin/env python3
"""Loopback-only, actively paced read RPC transport for pinned fork tests.

No wallets, signing, broadcasts, fabricated state or result cache. Only a failed
rate-limited read can be retried; the caller still runs each test suite once.
"""

from __future__ import annotations

import argparse
import base64
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import os
import signal
import threading
import time
from typing import Callable
import urllib.error
import urllib.parse
import urllib.request


DEFAULT_UPSTREAM = "https://rpc-robinhood.blockmachine.io"
READ_METHODS = frozenset({
    "eth_chainId", "eth_blockNumber", "eth_getBlockByNumber", "eth_getBlockByHash",
    "eth_getCode", "eth_getStorageAt", "eth_getBalance", "eth_getTransactionCount",
    "eth_call", "eth_getLogs", "eth_getProof", "eth_getTransactionByHash",
    "eth_getTransactionReceipt", "eth_getTransactionByBlockHashAndIndex",
    "eth_getTransactionByBlockNumberAndIndex", "eth_getBlockTransactionCountByHash",
    "eth_getBlockTransactionCountByNumber", "net_version", "web3_clientVersion",
    "eth_gasPrice", "eth_feeHistory",
})
MAX_BODY_BYTES = 1_048_576
MAX_RESPONSE_BYTES = 8_388_608
MAX_BATCH_READS = 32
READ_BUDGET_SECONDS = 25.0
MAX_READ_ATTEMPTS = 8


def _error(request_id: object, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _budget_error(request_id: object) -> dict:
    return _error(request_id, -32081, "Read-only RPC request exceeded its bounded time budget")


def _no_constants(_: str) -> None:
    raise ValueError("Non-finite JSON number")


def _decode(raw: bytes) -> object:
    def finite_float(value: str) -> float:
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("Non-finite JSON number")
        return number
    return json.loads(raw, parse_constant=_no_constants, parse_float=finite_float)


def _request_id(request: object) -> object:
    if isinstance(request, dict):
        value = request.get("id")
        if value is None or type(value) in (str, int):
            return value
    return None


def _validate(request: object) -> dict | None:
    request_id = _request_id(request)
    if (not isinstance(request, dict) or request.get("jsonrpc") != "2.0"
        or "id" not in request or type(request.get("id")) not in (str, int, type(None))
        or type(request.get("method")) is not str
        or ("params" in request and not isinstance(request["params"], (list, dict)))
        or set(request) - {"jsonrpc", "id", "method", "params"}):
        return _error(request_id, -32600, "Invalid read-only JSON-RPC request")
    if request["method"] not in READ_METHODS:
        return _error(request_id, -32601, "Read-only RPC method is not allowed")
    return None


@dataclass(frozen=True)
class RpcResponse:
    status: int
    body: bytes
    retry_after: str | None = None


class UrlTransport:
    """stdlib transport; errors are never rendered with the secret upstream URL."""

    def __init__(self, upstream: str) -> None:
        if upstream == "blockmachine":
            upstream = DEFAULT_UPSTREAM
        try:
            parts = urllib.parse.urlsplit(upstream)
            if parts.scheme not in ("https", "http") or not parts.hostname or parts.fragment:
                raise ValueError
            _ = parts.port
            self.headers = {"Content-Type": "application/json", "User-Agent": "Hedgefun-Fork-Tests/1.0"}
            if parts.username is not None:
                credentials = urllib.parse.unquote(parts.username) + ":" + urllib.parse.unquote(parts.password or "")
                self.headers["Authorization"] = "Basic " + base64.b64encode(credentials.encode()).decode()
                upstream = urllib.parse.urlunsplit((parts.scheme, parts.netloc.rsplit("@", 1)[1],
                    parts.path, parts.query, ""))
        except (ValueError, TypeError):
            raise ValueError("Invalid archive RPC configuration") from None
        self.upstream = upstream
        # Do not follow redirects to an unconfigured destination or forward credentials there.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None
        self.opener = urllib.request.build_opener(NoRedirect())

    def __call__(self, body: bytes, timeout: float) -> RpcResponse:
        deadline = time.monotonic() + timeout
        req = urllib.request.Request(self.upstream, data=body, headers=self.headers, method="POST")
        try:
            response = self.opener.open(req, timeout=timeout)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            data = bytearray()
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError
                # HTTPResponse exposes read1; set each socket wait to the remaining whole-read budget.
                sock = getattr(getattr(getattr(response, "fp", None), "raw", None), "_sock", None)
                if sock is not None:
                    sock.settimeout(remaining)
                chunk = response.read1(min(65_536, MAX_RESPONSE_BYTES + 1 - len(data)))
                if not chunk:
                    break
                data.extend(chunk)
                if len(data) > MAX_RESPONSE_BYTES:
                    raise ValueError("Archive response too large")
            return RpcResponse(response.status, bytes(data), response.headers.get("Retry-After"))


class ReadRpcProxy:
    def __init__(self, transport: Callable[[bytes, float], RpcResponse], *, interval_seconds: float = 3.0,
                 budget_seconds: float = READ_BUDGET_SECONDS, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        if not math.isfinite(interval_seconds) or not 0 <= interval_seconds <= 60:
            raise ValueError("Invalid request interval")
        if not math.isfinite(budget_seconds) or not 0 < budget_seconds <= READ_BUDGET_SECONDS:
            raise ValueError("Invalid request budget")
        self.transport = transport
        self.interval = interval_seconds
        self.budget = budget_seconds
        self.clock = clock
        self.sleep = sleep
        self.lock = threading.Lock()
        self.metrics_lock = threading.Lock()
        self.next_start = 0.0
        self.upstream_requests = 0
        self.rate_limit_retries = 0

    def health(self) -> dict:
        with self.metrics_lock:
            return {"status": "ok", "upstream_requests": self.upstream_requests,
                    "rate_limit_retries": self.rate_limit_retries}

    def _retry_delay(self, result: object, header: str | None) -> float:
        delays = [1.0]
        if isinstance(result, dict) and isinstance(result.get("error"), dict):
            data = result["error"].get("data")
            ms = data.get("retry_after_ms") if isinstance(data, dict) else None
            if type(ms) in (int, float) and ms >= 0:
                # Huge untrusted hints fail closed without overflowing a float conversion.
                if ms > 1e300:
                    delays.append(math.inf)
                elif math.isfinite(ms):
                    delays.append(ms / 1000)
        if header:
            try:
                value = float(header)
                if math.isfinite(value) and value >= 0:
                    delays.append(value)
            except ValueError:
                try:
                    delays.append(max(0.0, parsedate_to_datetime(header).timestamp() - time.time()))
                except (ValueError, TypeError, OverflowError):
                    pass
        return max(delays)

    @staticmethod
    def _rate_limited(status: int, result: object) -> bool:
        if status == 429:
            return True
        if isinstance(result, dict) and isinstance(result.get("error"), dict):
            error = result["error"]
            message = error.get("message")
            return error.get("code") in (-32029, 429) or (error.get("code") in (-32000, -32005, -32016, -32603)
                and isinstance(message, str) and any(term in message.lower()
                    for term in ("rate limit", "ratelimit", "too many requests")))
        return False

    def forward(self, request: dict, *, deadline: float | None = None) -> dict:
        invalid = _validate(request)
        if invalid is not None:
            return invalid
        request_id = request["id"]
        expires = self.clock() + self.budget
        if deadline is not None:
            expires = min(expires, deadline)
        remaining = expires - self.clock()
        if remaining <= 0 or not self.lock.acquire(timeout=remaining):
            return _budget_error(request_id)
        try:
            try:
                body = json.dumps(request, allow_nan=False, separators=(",", ":")).encode()
            except (ValueError, OverflowError, RecursionError):
                return _error(request_id, -32600, "Invalid read-only JSON-RPC request")
            for attempt in range(MAX_READ_ATTEMPTS):
                delay = max(0.0, self.next_start - self.clock())
                if self.clock() + delay >= expires:
                    return _budget_error(request_id)
                if delay:
                    self.sleep(delay)
                remaining = expires - self.clock()
                if remaining <= 0:
                    return _budget_error(request_id)
                self.next_start = self.clock() + self.interval
                with self.metrics_lock:
                    self.upstream_requests += 1
                try:
                    reply = self.transport(body, remaining)
                    if self.clock() >= expires:
                        return _budget_error(request_id)
                    try:
                        result = _decode(reply.body)
                    except (ValueError, UnicodeError, RecursionError):
                        result = None
                except Exception:
                    # urllib exceptions can contain full URLs/credentials. Never print/stringify them.
                    return _error(request_id, -32080, "Archive RPC read failed")
                # An HTTP 429 is a transport-level throttle even without a JSON-RPC envelope.
                # All other responses must validate before any retry can mask a malformed reply.
                if reply.status != 429:
                    if (reply.status != 200 or not isinstance(result, dict) or result.get("jsonrpc") != "2.0"
                        or "id" not in result or type(result["id"]) is not type(request_id)
                        or result["id"] != request_id or ("result" in result) == ("error" in result)):
                        return _error(request_id, -32080, "Invalid archive RPC read response")
                    if "error" in result:
                        error = result["error"]
                        code = error.get("code") if isinstance(error, dict) else None
                        if (type(code) is not int or not -(2**31) <= code < 2**31
                            or not isinstance(error.get("message"), str)):
                            return _error(request_id, -32080, "Invalid archive RPC read response")
                if self._rate_limited(reply.status, result):
                    retry_at = self.clock() + self._retry_delay(result, reply.retry_after)
                    self.next_start = max(self.next_start, retry_at)
                    if self.next_start >= expires or attempt + 1 == MAX_READ_ATTEMPTS:
                        return _budget_error(request_id)
                    with self.metrics_lock:
                        self.rate_limit_retries += 1
                    continue
                if "error" in result:
                    # Providers may echo authenticated URLs/headers/params in diagnostics.
                    # Preserve failure and its code, never its message/data or retry ordinary errors.
                    return _error(request_id, code, "Archive RPC returned an error")
                return result
            return _budget_error(request_id)
        finally:
            self.lock.release()

    def handle(self, payload: object, *, deadline: float | None = None) -> object:
        expires = self.clock() + self.budget  # also bounds the complete HTTP batch, including its queue
        if deadline is not None:
            expires = min(expires, deadline)
        if isinstance(payload, list):
            if not payload or len(payload) > MAX_BATCH_READS:
                return _error(None, -32600, "Invalid read-only RPC batch")
            return [self.forward(request, deadline=expires) for request in payload]
        return self.forward(payload, deadline=expires)


class ProxyServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, port: int, proxy: ReadRpcProxy):
        self.proxy = proxy
        super().__init__(("127.0.0.1", port), ProxyHandler)

    def handle_error(self, request, client_address):
        pass  # no URL/params/exception traces from disconnected local clients


class ProxyHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def setup(self):
        super().setup()
        self.connection.settimeout(READ_BUDGET_SECONDS)

    def _reply(self, status: int, value: object):
        raw = json.dumps(value, allow_nan=False, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path == "/healthz":
            self._reply(200, self.server.proxy.health())
        else:
            self._reply(404, {"error": "Not found"})

    def do_POST(self):
        expires = self.server.proxy.clock() + self.server.proxy.budget
        if self.path != "/":
            self._reply(404, {"error": "Not found"}); return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if self.headers.get("Transfer-Encoding") or not 0 < length <= MAX_BODY_BYTES:
            self._reply(413, _error(None, -32600, "RPC request body must have bounded length")); return
        try:
            payload = _decode(self.rfile.read(length))
        except (ValueError, UnicodeError, RecursionError, OSError):
            self._reply(400, _error(None, -32700, "Invalid JSON-RPC body")); return
        self._reply(200, self.server.proxy.handle(payload, deadline=expires))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=18545)
    parser.add_argument("--interval-seconds", type=float, default=3.0)
    args = parser.parse_args()
    try:
        if not 1 <= args.port <= 65535:
            raise ValueError
        proxy = ReadRpcProxy(UrlTransport(os.environ.get("FORK_ARCHIVE_RPC", DEFAULT_UPSTREAM)),
                             interval_seconds=args.interval_seconds)
        server = ProxyServer(args.port, proxy)
    except (ValueError, OSError):
        parser.exit(2, "Invalid read-only RPC proxy configuration\n")
    def stop(signum, frame):
        threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    with server:
        server.serve_forever(poll_interval=0.1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

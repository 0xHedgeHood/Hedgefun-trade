"""Offline transport/security tests. Never contacts an archive RPC."""

from collections import deque
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request

try:
    from tools import fork_rpc_proxy as rpc
except ImportError:
    import fork_rpc_proxy as rpc


def request(number=1, method="eth_getStorageAt", params=None):
    return {"jsonrpc": "2.0", "id": number, "method": method,
            "params": ["0x1234", "0x00", "0x4382184"] if params is None else params}


def response(number=1, *, result="0xbeef", error=None, status=200, retry_after=None):
    value = {"jsonrpc": "2.0", "id": number}
    value["result" if error is None else "error"] = result if error is None else error
    return rpc.RpcResponse(status, json.dumps(value).encode(), retry_after)


class Clock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class Transport:
    def __init__(self, clock, replies=()):
        self.clock = clock
        self.replies = deque(replies)
        self.seen = []

    def __call__(self, body, timeout):
        value = json.loads(body)
        self.seen.append((self.clock(), value, timeout))
        if not self.replies:
            return response(value["id"], result=value["params"])
        item = self.replies.popleft()
        if isinstance(item, Exception):
            raise item
        return item(body, timeout) if callable(item) else item


def fake_proxy(replies=(), *, interval=3, budget=25):
    clock = Clock()
    transport = Transport(clock, replies)
    proxy = rpc.ReadRpcProxy(transport, interval_seconds=interval, budget_seconds=budget,
                            clock=clock, sleep=clock.sleep)
    return proxy, transport, clock


@contextmanager
def serving(server):
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def http_json(url, payload=None, **headers):
    body = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(url, data=body, headers=headers)
    try:
        reply = urllib.request.urlopen(req, timeout=2)
    except urllib.error.HTTPError as exc:
        reply = exc
    with reply:
        return reply.status, json.loads(reply.read())


class ProxyTests(unittest.TestCase):
    def test_all_whitelisted_reads_preserve_params_and_id(self):
        proxy, transport, _ = fake_proxy(interval=0)
        for i, method in enumerate(sorted(rpc.READ_METHODS)):
            with self.subTest(method=method):
                value = request(str(i), method, {"nested": ["0xffff", {"wide": 2**255}]})
                result = proxy.handle(value)
                self.assertEqual(result, {"jsonrpc": "2.0", "id": str(i), "result": value["params"]})
                self.assertEqual(transport.seen[-1][1], value)
        self.assertEqual(proxy.health()["upstream_requests"], len(rpc.READ_METHODS))

    def test_write_signing_and_unknown_methods_never_reach_upstream(self):
        proxy, transport, _ = fake_proxy()
        methods = ["eth_sendTransaction", "eth_sendRawTransaction", "eth_sign", "personal_sign",
                   "eth_accounts", "anvil_setBalance", "engine_newPayloadV3", "admin_addPeer",
                   "debug_traceCall", "wallet_sendCalls", "unknown_method"]
        for method in methods:
            with self.subTest(method=method):
                self.assertEqual(proxy.handle(request(method=method))["error"]["code"], -32601)
        self.assertEqual(transport.seen, [])

    def test_invalid_shapes_numbers_and_batches_never_reach_upstream(self):
        proxy, transport, _ = fake_proxy()
        invalid = [None, True, "request", {}, {"jsonrpc": "2.0", "method": "eth_chainId"},
                   request(True), request(params="bad"), dict(request(), extra="bad"),
                   request(params=[float("inf")]), [], [request()] * (rpc.MAX_BATCH_READS + 1)]
        for value in invalid:
            with self.subTest(value=type(value).__name__):
                self.assertEqual(proxy.handle(value)["error"]["code"], -32600)
        for body in (b'{"x":NaN}', b'{"x":Infinity}', b'{"x":1e1000}'):
            with self.assertRaises(ValueError):
                rpc._decode(body)
        self.assertEqual(transport.seen, [])

    def test_batch_is_split_ordered_paced_and_not_cached(self):
        proxy, transport, clock = fake_proxy()
        batch = [request(1), request(2, "eth_sendRawTransaction"), request(3), request(1)]
        results = proxy.handle(batch)
        self.assertEqual([result["id"] for result in results], [1, 2, 3, 1])
        self.assertEqual(results[1]["error"]["code"], -32601)
        self.assertEqual([seen[0] for seen in transport.seen], [0, 3, 6])
        self.assertEqual([seen[1]["id"] for seen in transport.seen], [1, 3, 1])
        self.assertEqual(clock.sleeps, [3, 3])

    def test_http429_retry_respects_hint_and_identical_request(self):
        error = {"code": -32029, "message": "Too many requests", "data": {"retry_after_ms": 5000}}
        proxy, transport, clock = fake_proxy([response(error=error, status=429), response()])
        original = request(params=["unchanged-private-param", "0x01"])
        self.assertEqual(proxy.handle(original)["result"], "0xbeef")
        self.assertEqual([seen[0] for seen in transport.seen], [0, 5])
        self.assertEqual([seen[1] for seen in transport.seen], [original, original])
        self.assertEqual(clock.sleeps, [5])
        self.assertEqual(proxy.health(), {"status": "ok", "upstream_requests": 2, "rate_limit_retries": 1})

    def test_http_retry_after_and_interval_both_apply(self):
        proxy, transport, _ = fake_proxy([response(status=429, retry_after="1"), response()])
        self.assertIn("result", proxy.handle(request()))
        self.assertEqual([seen[0] for seen in transport.seen], [0, 3])
        proxy, transport, _ = fake_proxy([response(status=429, retry_after="7"), response()])
        self.assertIn("result", proxy.handle(request()))
        self.assertEqual([seen[0] for seen in transport.seen], [0, 7])

    def assert_malformed_limit_fails_once(self, value):
        proxy, transport, _ = fake_proxy([rpc.RpcResponse(200, json.dumps(value).encode()), response()])
        self.assertEqual(proxy.handle(request())["error"]["code"], -32080)
        self.assertEqual(len(transport.seen), 1)
        self.assertEqual(proxy.health()["rate_limit_retries"], 0)

    def test_wrong_id_rate_response_fails_without_retry(self):
        self.assert_malformed_limit_fails_once({"jsonrpc": "2.0", "id": 2,
            "error": {"code": -32029, "message": "rate limit"}})

    def test_missing_jsonrpc_rate_response_fails_without_retry(self):
        self.assert_malformed_limit_fails_once({"id": 1,
            "error": {"code": -32029, "message": "rate limit"}})

    def test_float_error_code_rate_response_fails_without_retry(self):
        self.assert_malformed_limit_fails_once({"jsonrpc": "2.0", "id": 1,
            "error": {"code": -32029.0, "message": "rate limit"}})

    def test_explicit_jsonrpc_limit_retries_but_other_rpc_errors_do_not(self):
        error = {"code": -32000, "message": "rate limit exceeded", "data": {"retry_after_ms": 2000}}
        proxy, transport, _ = fake_proxy([response(error=error), response()])
        self.assertIn("result", proxy.handle(request()))
        self.assertEqual(len(transport.seen), 2)
        for error in ({"code": 3, "message": "execution reverted: rate limit", "data": "0xdead"},
                      {"code": -32000, "message": "execution reverted", "data": "0xdead"},
                      {"code": -32602, "message": "Invalid params", "data": {"why": 1}}):
            proxy, transport, _ = fake_proxy([response(error=error)])
            self.assertEqual(proxy.handle(request()), {"jsonrpc": "2.0", "id": 1,
                "error": {"code": error["code"], "message": "Archive RPC returned an error"}})
            self.assertEqual(len(transport.seen), 1)

    def test_error_diagnostics_are_sanitized_without_hiding_failure_or_retrying(self):
        secret = "https://user:apikey@provider/private-path?key=secret"
        error = {"code": -32000, "message": "failure at " + secret,
                 "data": {"request": "secret-params", "authorization": "secret-header", "url": secret}}
        proxy, transport, _ = fake_proxy([response(error=error)])
        result = proxy.handle(request())
        self.assertEqual(result, {"jsonrpc": "2.0", "id": 1,
                                 "error": {"code": -32000, "message": "Archive RPC returned an error"}})
        self.assertNotIn("secret", json.dumps(result))
        self.assertEqual(len(transport.seen), 1)
        for code in (True, -32000.0, "-32000", 2**50):
            proxy, transport, _ = fake_proxy([response(error={"code": code, "message": secret})])
            self.assertEqual(proxy.handle(request())["error"]["code"], -32080)
            self.assertEqual(len(transport.seen), 1)

    def test_long_rate_hint_fails_without_exceeding_budget_or_early_next_read(self):
        for milliseconds in (65_000, 10**400):
            proxy, transport, clock = fake_proxy([
                response(status=429, error={"code": -32029, "message": "limit",
                                           "data": {"retry_after_ms": milliseconds}})])
            self.assertEqual(proxy.handle(request())["error"]["code"], -32081)
            self.assertEqual(proxy.handle(request(2))["error"]["code"], -32081)
            self.assertEqual(len(transport.seen), 1)
            self.assertEqual(clock.sleeps, [])
            self.assertEqual(clock(), 0)

    def test_repeated_limits_have_bounded_attempts_and_deadline(self):
        proxy, transport, clock = fake_proxy([response(status=429)] * 30)
        self.assertEqual(proxy.handle(request())["error"]["code"], -32081)
        self.assertEqual(len(transport.seen), rpc.MAX_READ_ATTEMPTS)
        self.assertLessEqual(clock(), rpc.READ_BUDGET_SECONDS)
        self.assertEqual([seen[0] for seen in transport.seen], list(range(0, 24, 3)))

    def test_read_and_whole_batch_share_bounded_budget(self):
        proxy, transport, clock = fake_proxy(interval=20)
        results = proxy.handle([request(1), request(2), request(3)])
        self.assertIn("result", results[0])
        self.assertIn("result", results[1])
        self.assertEqual(results[2]["error"]["code"], -32081)
        self.assertEqual([seen[0] for seen in transport.seen], [0, 20])
        self.assertEqual(clock(), 20)
        proxy, transport, clock = fake_proxy()
        def late_reply(body, timeout):
            clock.now += 26
            return response()
        transport.replies.append(late_reply)
        self.assertEqual(proxy.handle(request())["error"]["code"], -32081)
        self.assertEqual(transport.seen[0][2], 25)

    def test_queue_wait_is_in_budget(self):
        calls = []
        proxy = rpc.ReadRpcProxy(lambda body, timeout: calls.append(body), budget_seconds=0.02)
        proxy.lock.acquire()
        start = time.monotonic()
        try:
            self.assertEqual(proxy.handle(request())["error"]["code"], -32081)
        finally:
            proxy.lock.release()
        self.assertLess(time.monotonic() - start, 0.25)
        self.assertEqual(calls, [])

    def test_concurrent_reads_do_not_burst(self):
        starts = []
        def transport(body, timeout):
            starts.append(time.monotonic())
            return response(json.loads(body)["id"])
        proxy = rpc.ReadRpcProxy(transport, interval_seconds=0.02)
        barrier = threading.Barrier(4)
        results = []
        def call(number):
            barrier.wait()
            results.append(proxy.handle(request(number)))
        threads = [threading.Thread(target=call, args=(i,)) for i in range(3)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(timeout=1)
        self.assertEqual(len(results), 3)
        self.assertTrue(all("result" in result for result in results))
        self.assertTrue(all(right - left >= 0.018 for left, right in zip(starts, starts[1:])))

    def test_transport_failures_bad_ids_and_server_errors_do_not_leak_or_retry(self):
        replies = [RuntimeError("https://user:secret@host/private-params"),
                   rpc.RpcResponse(500, b'{"message":"private-params"}'),
                   rpc.RpcResponse(200, b'not-json-private-params'),
                   response(True), response(1.0), response(2),
                   rpc.RpcResponse(200, b'{"jsonrpc":"2.0","id":1,"result":1e1000}')]
        captured = io.StringIO()
        with redirect_stdout(captured), redirect_stderr(captured):
            for reply in replies:
                proxy, transport, _ = fake_proxy([reply])
                result = proxy.handle(request())
                self.assertEqual(result["error"]["code"], -32080)
                self.assertNotIn("private", json.dumps(result))
                self.assertNotIn("secret", json.dumps(result))
                self.assertEqual(len(transport.seen), 1)
        self.assertEqual(captured.getvalue(), "")

    def test_url_alias_configuration_and_credentials_are_not_reflected(self):
        self.assertEqual(rpc.UrlTransport("blockmachine").upstream, rpc.DEFAULT_UPSTREAM)
        self.assertEqual(rpc.UrlTransport("blockmachine").headers["User-Agent"], "Hedgefun-Fork-Tests/1.0")
        transport = rpc.UrlTransport("http://user:p%40ss@127.0.0.1:1/private?secret=value")
        self.assertEqual(transport.upstream, "http://127.0.0.1:1/private?secret=value")
        self.assertEqual(transport.headers["Authorization"], "Basic dXNlcjpwQHNz")
        for url in ("file:///secret", "http://user:secret@", "https://host:bad/private", "https://host/#secret"):
            with self.assertRaisesRegex(ValueError, "^Invalid archive RPC configuration$"):
                rpc.UrlTransport(url)


class HttpTests(unittest.TestCase):
    def test_local_server_health_validation_and_forwarding(self):
        proxy, transport, _ = fake_proxy(interval=0)
        with serving(rpc.ProxyServer(0, proxy)) as local:
            self.assertEqual(http_json(local + "/healthz"),
                             (200, {"status": "ok", "upstream_requests": 0, "rate_limit_retries": 0}))
            self.assertEqual(transport.seen, [])
            self.assertEqual(http_json(local, request())[1]["result"], request()["params"])
            self.assertEqual(http_json(local, request(method="eth_sendRawTransaction"))[1]["error"]["code"], -32601)
            self.assertEqual(http_json(local + "/other")[0], 404)
            self.assertEqual(len(transport.seen), 1)
            req = urllib.request.Request(local, data=b"private-invalid-json", method="POST")
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(req, timeout=2)
            self.assertEqual(caught.exception.code, 400)
            self.assertNotIn(b"private", caught.exception.read())
            conn = socket.create_connection(("127.0.0.1", int(local.rsplit(":", 1)[1])), timeout=2)
            with conn:
                conn.sendall(b"POST / HTTP/1.0\r\nContent-Length: 1048577\r\n\r\n")
                self.assertIn(b"413", conn.recv(4096))
            self.assertEqual(len(transport.seen), 1)

    def test_urllib_real_local_passthrough_and_http429_retry(self):
        seen = []
        user_agents = []
        class Upstream(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                value = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                seen.append(value)
                user_agents.append(self.headers.get("User-Agent"))
                body = response(value["id"], result=value["params"], status=429 if len(seen) == 1 else 200).body
                self.send_response(429 if len(seen) == 1 else 200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        with serving(ThreadingHTTPServer(("127.0.0.1", 0), Upstream)) as upstream:
            clock = Clock()
            proxy = rpc.ReadRpcProxy(rpc.UrlTransport(upstream), interval_seconds=0, clock=clock, sleep=clock.sleep)
            original = request("string-id", "eth_call", [{"to": "0x1234", "data": "0xbeef"}, "0x123"])
            self.assertEqual(proxy.handle(original)["result"], original["params"])
            self.assertEqual(seen, [original, original])
            self.assertEqual(user_agents, ["Hedgefun-Fork-Tests/1.0"] * 2)
            self.assertEqual(clock.sleeps, [1])

    def test_urllib_redirect_is_not_followed(self):
        seen = []
        class Redirect(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                seen.append(self.path)
                self.send_response(302)
                self.send_header("Location", "/must-not-be-followed")
                self.send_header("Content-Length", "0")
                self.end_headers()
            def do_GET(self):
                seen.append(self.path)
                self.send_error(500)
        with serving(ThreadingHTTPServer(("127.0.0.1", 0), Redirect)) as upstream:
            proxy = rpc.ReadRpcProxy(rpc.UrlTransport(upstream), interval_seconds=0)
            self.assertEqual(proxy.handle(request())["error"]["code"], -32080)
            self.assertEqual(seen, ["/"])

    def test_cli_health_is_local_and_sigterm_exits_cleanly_without_logs(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        env = dict(os.environ, FORK_ARCHIVE_RPC="http://127.0.0.1:1/secret")
        process = subprocess.Popen([sys.executable, str(Path(rpc.__file__).resolve()), "--port", str(port)],
                                   env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 3
            ready = False
            while time.monotonic() < deadline:
                try:
                    ready = http_json(f"http://127.0.0.1:{port}/healthz")[1]["upstream_requests"] == 0
                    break
                except OSError:
                    time.sleep(0.01)
            self.assertTrue(ready)
            process.send_signal(signal.SIGTERM)
            stdout, stderr = process.communicate(timeout=2)
            self.assertEqual(process.returncode, 0)
            self.assertEqual((stdout, stderr), (b"", b""))
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=2)


if __name__ == "__main__":
    unittest.main()

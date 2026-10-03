"""Fake-RPC preflight tests: no network, signing, or transactions."""
import contextlib
import http.client
import io
import json
import unittest
import urllib.error
from unittest.mock import patch

from tools import v2_cycle_keeper as v


TREASURY = "0x" + "ab" * 20
CALLER = "0x" + "cd" * 20
CHAIN = 46630
HEADER = {"number": "0x100", "timestamp": "0x6553f100", "hash": "0x" + "12" * 32}


def word(value):
    return "0x" + f"{value:064x}"


class FakeResponse:
    def __init__(self, body):
        self.body = body

    def read(self, limit):
        return self.body[:limit]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class FakeRpc:
    def __init__(self):
        self.requests = []
        self.chain = hex(CHAIN)
        self.code = "0x60006000"
        self.views = {selector: word(0) for selector in v.VIEWS.values()}
        self.execution = {"code": 3, "message": "execution reverted", "data": "0x47a2375f"}
        self.header = dict(HEADER)
        self.confirmation = dict(HEADER)
        self.mutate = None

    def urlopen(self, request, timeout):
        call = json.loads(request.data)
        self.requests.append(call)
        method, params = call["method"], call["params"]
        if method == "eth_chainId":
            result = self.chain
        elif method == "eth_getBlockByNumber":
            result = self.header if params[0] == "latest" else self.confirmation
        elif method == "eth_getCode":
            result = self.code
        elif method == "eth_call":
            selector = params[0]["data"]
            result = self.execution if selector == v.EXECUTE else self.views[selector]
        else:
            raise AssertionError(f"unexpected RPC: {method}")
        reply = {"jsonrpc": "2.0", "id": call["id"]}
        reply["error" if isinstance(result, dict) and "code" in result else "result"] = result
        if self.mutate:
            reply = self.mutate(call, reply)
        body = reply if isinstance(reply, bytes) else json.dumps(reply).encode()
        return FakeResponse(body)

    def check(self):
        with patch.object(v.urllib.request, "urlopen", side_effect=self.urlopen):
            return v.check(v.Rpc("https://rpc.example"), CHAIN, TREASURY, CALLER)


class PreflightTests(unittest.TestCase):
    def test_success_is_only_a_pinned_read_with_operator_calldata(self):
        fake = FakeRpc()
        for name, value in {"pending": 1, "salePrice": 100 * 10**18, "saleAt": 1699999000,
                            "stockUpdatedAt": 1699998999, "recoveryDue": 1}.items():
            fake.views[v.VIEWS[name]] = word(value)
        fake.execution = word(5) + word(0)[2:]
        result = fake.check()
        self.assertEqual((result["status"], result["action"], result["lot"]), ("ready", "BuyRecovery", 0))
        self.assertEqual(result["transaction"], {"chainId": CHAIN, "from": CALLER, "to": TREASURY,
                                                 "data": "0x61461954", "value": "0x0"})
        self.assertTrue(all(call["method"] in v.READ_METHODS for call in fake.requests))
        for call in fake.requests:
            if call["method"] in {"eth_call", "eth_getCode"}:
                self.assertEqual(call["params"][-1], "0x100")
        execute = next(call for call in fake.requests if call["method"] == "eth_call"
                       and call["params"][0]["data"] == v.EXECUTE)
        self.assertEqual(execute["params"][0]["from"], CALLER)

    def test_known_reverts_are_waiting_or_paused(self):
        for selector, status, reason in [("0x47a2375f", "waiting", "NotDue"),
                                          ("0xb0782df7", "waiting", "Cooldown"),
                                          ("0x7db7a074", "paused", "Unhealthy")]:
            with self.subTest(selector=selector):
                fake = FakeRpc()
                fake.execution["data"] = selector
                result = fake.check()
                self.assertEqual((result["status"], result["reason"]), (status, reason))
                self.assertNotIn("transaction", result)

    def test_unknown_revert_and_text_only_known_name_remain_errors(self):
        for data in ("0xdeadbeef", "0x47a2375f00", None, "malformed"):
            with self.subTest(data=data):
                fake = FakeRpc()
                fake.execution.update(message="NotDue()", data=data)
                with self.assertRaisesRegex(v.PreflightError, "execute simulation failed"):
                    fake.check()

    def test_nested_provider_revert_bytes_are_supported(self):
        fake = FakeRpc()
        fake.execution["data"] = {"data": "0x47a2375f"}
        self.assertEqual(fake.check()["status"], "waiting")

    def test_nonexecution_rpc_error_cannot_masquerade_as_waiting(self):
        fake = FakeRpc()
        fake.execution["code"] = -32602
        with self.assertRaisesRegex(v.PreflightError, "execute simulation failed"):
            fake.check()

    def test_wrong_chain_stops_before_any_treasury_call(self):
        fake = FakeRpc()
        fake.chain = "0x1237"
        with self.assertRaisesRegex(v.PreflightError, "chain mismatch"):
            fake.check()
        self.assertEqual([call["method"] for call in fake.requests], ["eth_chainId"])

    def test_empty_code_and_malformed_cycle_views_are_errors(self):
        fake = FakeRpc()
        fake.code = "0x"
        with self.assertRaisesRegex(v.PreflightError, "no code"):
            fake.check()
        for name, encoded in [("pending", word(2)), ("saleAt", "0x1"),
                              ("lotCount", word(129)), ("recoveryDue", word(1))]:
            with self.subTest(name=name):
                fake = FakeRpc()
                fake.views[v.VIEWS[name]] = encoded
                with self.assertRaises(v.PreflightError):
                    fake.check()

    def test_pending_sale_reference_must_be_consistent_and_not_future(self):
        fake = FakeRpc()
        for name, value in {"pending": 1, "salePrice": 100 * 10**18,
                            "saleAt": 1700000000, "stockUpdatedAt": 1699999999}.items():
            fake.views[v.VIEWS[name]] = word(value)
        self.assertTrue(fake.check()["state"]["pending"])
        fake.views[v.VIEWS["stockUpdatedAt"]] = word(1700000001)
        with self.assertRaisesRegex(v.PreflightError, "sale reference"):
            fake.check()

    def test_malformed_execute_returns_and_other_engine_actions_are_errors(self):
        for result in ("0x", word(5), word(5) + word(128)[2:], word(3) + word(0)[2:],
                       word(6) + word(0)[2:], word(5) + word(0)[2:], "0x" + "ff" * 65):
            with self.subTest(result=result):
                fake = FakeRpc()
                fake.execution = result
                with self.assertRaises(v.PreflightError):
                    fake.check()

    def test_rpc_envelope_and_json_fail_closed(self):
        mutations = [lambda call, reply: dict(reply, id=True),
                     lambda call, reply: dict(reply, id=call["id"] + 1),
                     lambda call, reply: dict(reply, jsonrpc="1.0"),
                     lambda call, reply: dict(reply, error={"code": 3, "message": "failed"}),
                     lambda call, reply: {"jsonrpc": "2.0", "id": call["id"]},
                     lambda call, reply: b"not JSON"]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                fake = FakeRpc()
                fake.mutate = mutate
                with self.assertRaises(v.PreflightError):
                    fake.check()

    def test_noncanonical_chain_quantity_is_an_error(self):
        for chain in ("0x00", "0x", "46630", True, hex(2**256)):
            fake = FakeRpc()
            fake.chain = chain
            with self.subTest(chain=chain), self.assertRaises(v.PreflightError):
                fake.check()

    def test_reorg_cannot_produce_ready(self):
        fake = FakeRpc()
        fake.execution = word(2) + word(0)[2:]
        fake.confirmation["hash"] = "0x" + "34" * 32
        with self.assertRaisesRegex(v.PreflightError, "snapshot block changed"):
            fake.check()

    def test_write_rpc_is_rejected_without_network_access(self):
        with patch.object(v.urllib.request, "urlopen") as network:
            with self.assertRaisesRegex(v.PreflightError, "read-only allowlist"):
                v.Rpc("https://rpc.example").call("eth_sendRawTransaction", ["0x"])
            network.assert_not_called()


class CliTests(unittest.TestCase):
    ARGS = ["--rpc", "https://rpc.example", "--chain-id", str(CHAIN),
            "--treasury", TREASURY, "--caller", CALLER]

    def test_once_network_failure_is_error_with_nonzero_exit(self):
        output = io.StringIO()
        with patch.object(v.urllib.request, "urlopen", side_effect=urllib.error.URLError("offline")), \
                contextlib.redirect_stdout(output):
            self.assertEqual(v.main(self.ARGS + ["--once"]), 1)
        result = json.loads(output.getvalue())
        self.assertEqual(result["status"], "error")
        self.assertNotIn("transaction", result)

    def test_incomplete_http_read_is_reported_as_error(self):
        output = io.StringIO()
        with patch.object(v.urllib.request, "urlopen", side_effect=http.client.IncompleteRead(b"partial")), \
                contextlib.redirect_stdout(output):
            self.assertEqual(v.main(self.ARGS + ["--once"]), 1)
        self.assertEqual(json.loads(output.getvalue())["status"], "error")

    def test_periodic_unchanged_state_is_quiet_and_ctrl_c_stops(self):
        fake, output = FakeRpc(), io.StringIO()
        with patch.object(v.urllib.request, "urlopen", side_effect=fake.urlopen), \
                patch.object(v.time, "sleep", side_effect=[None, KeyboardInterrupt()]) as sleep, \
                contextlib.redirect_stdout(output):
            self.assertEqual(v.main(self.ARGS), 130)
        self.assertEqual(len(output.getvalue().splitlines()), 1)
        self.assertEqual(sleep.call_args_list[0].args, (60,))

    def test_ready_transition_is_printed(self):
        fake, output = FakeRpc(), io.StringIO()
        polls = 0

        def next_poll(seconds):
            nonlocal polls
            polls += 1
            if polls == 1:
                fake.execution = word(0) + word(0)[2:]
            else:
                raise KeyboardInterrupt()

        with patch.object(v.urllib.request, "urlopen", side_effect=fake.urlopen), \
                patch.object(v.time, "sleep", side_effect=next_poll), contextlib.redirect_stdout(output):
            self.assertEqual(v.main(self.ARGS), 130)
        self.assertEqual([json.loads(line)["status"] for line in output.getvalue().splitlines()],
                         ["waiting", "ready"])

    def test_address_and_interval_validation(self):
        for value in ("0x" + "00" * 20, "0xabc", "address", None):
            with self.subTest(address=value), self.assertRaises(v.argparse.ArgumentTypeError):
                v.address(value)
        for value in ("0", "-1", "nan", "inf"):
            with self.subTest(interval=value), self.assertRaises(v.argparse.ArgumentTypeError):
                v.positive_seconds(value)
        for value in ("file:///tmp/rpc", "https://user:pass@rpc.example", "https://rpc.example/#secret",
                      "http://rpc.example:99999", "http://[malformed", "http://rpc.example/ bad"):
            with self.subTest(url=value), self.assertRaises(v.argparse.ArgumentTypeError):
                v.rpc_url(value)


if __name__ == "__main__":
    unittest.main()

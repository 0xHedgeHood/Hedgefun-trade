"""Local runner contract: pinned block, real LP setter, and no shell use."""

import subprocess
import unittest
from unittest.mock import patch

from lab import fork_runner


PASS_OUTPUT = """Logs:
  [PASS] test_fork_twoUsersLiveV3CurveGraduationV4FeesAndBuybackBurn() (gas: 123)
  V2 live venue fork block: 70786980
  V2 live venue frozen LP bps: 5000
  Live graduation LP GME raw: 7000000000000000000
  Live graduation treasury GME raw: 3000000000000000000
  Live V4 LP stock fee GME raw: 123
  Live V4 LP FUN fee burned raw: 456
  Final graduation GME refund (raw): 123456
Suite result: ok. 1 passed; 0 failed; 0 skipped; finished in 1.00s
"""


class ForkRunnerTest(unittest.TestCase):
    def test_fixed_command_and_environment(self):
        with patch.object(fork_runner.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, PASS_OUTPUT, "")) as run:
            result = fork_runner.run_fork()
        self.assertTrue(result["passed"])
        self.assertEqual(result["reported_block"], fork_runner.PINNED_BLOCK)
        self.assertEqual(result["metrics"]["graduation_refund_stock_raw"], 123456)
        self.assertEqual(result["metrics"]["lp_stock_raw"], 7000000000000000000)
        args, kwargs = run.call_args
        self.assertEqual(args[0], ["forge", "test", "--match-path", fork_runner.TEST_PATH,
                                   "--match-test", fork_runner.TEST_NAME, "-vv"])
        self.assertEqual(kwargs["env"]["RH_FORK_BLOCK"], "70786980")
        self.assertEqual(kwargs["env"]["RH_RPC"], "blockmachine")
        self.assertEqual(kwargs["env"]["V2_LP_BPS"], "5000")
        self.assertEqual(kwargs["env"]["FOUNDRY_FFI"], "false")
        self.assertEqual(kwargs["cwd"], fork_runner.ROOT)
        self.assertTrue(result["allocation"]["uses_default_lp_bps"])
        self.assertTrue(result["allocation"]["configured_via_set_lp_bps"])
        self.assertEqual(result["allocation"]["frozen_lp_bps"], 5000)
        self.assertFalse(result["scenario"]["broadcast"])

    def test_rejects_arbitrary_rpc_and_out_of_range_allocation(self):
        for kwargs in ({"rpc_alias": "https://evil.example"}, {"rpc_alias": "robinhood; echo bad"},
                       {"lp_bps": True}, {"lp_bps": 999}, {"lp_bps": 10001},
                       {"timeout_seconds": True}, {"timeout_seconds": 601}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                fork_runner.run_fork(**kwargs)

    def test_skip_or_wrong_block_is_never_reported_as_pass(self):
        for output in (
            "Suite result: ok. 0 passed; 0 failed; 1 skipped; finished in 0.01s",
            PASS_OUTPUT.replace("70786980", "70786981"),
            PASS_OUTPUT.replace("frozen LP bps: 5000", "frozen LP bps: 7000"),
        ):
            with self.subTest(output=output), patch.object(
                fork_runner.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, output, "")
            ):
                result = fork_runner.run_fork()
            self.assertFalse(result["passed"])
            self.assertEqual(result["status"], "failed")
            self.assertFalse(result["allocation"]["configured_via_set_lp_bps"])
            self.assertIsNone(result["allocation"]["frozen_lp_bps"])

    def test_timeout_is_structured(self):
        with patch.object(fork_runner.subprocess, "run", side_effect=subprocess.TimeoutExpired(["forge"], 20)):
            result = fork_runner.run_fork(timeout_seconds=20)
        self.assertEqual(result["status"], "timeout")
        self.assertFalse(result["passed"])

    def test_archive_rate_limit_retries_within_the_total_timeout(self):
        throttled = subprocess.CompletedProcess([], 1, "", 'HTTP error 429: {"retry_after_ms": 1}; rate limit exceeded')
        passed = subprocess.CompletedProcess([], 0, PASS_OUTPUT, "")
        with (
            patch.object(fork_runner.subprocess, "run", side_effect=(throttled, passed)) as run,
            patch.object(fork_runner.time, "sleep") as sleep,
        ):
            result = fork_runner.run_fork()
        self.assertTrue(result["passed"])
        self.assertEqual(run.call_count, 2)
        sleep.assert_called_once_with(1.0)

    def test_non_default_uses_unchanged_source_and_real_setter_environment(self):
        seen = {}

        def fake_run(command, **kwargs):
            seen["path"] = kwargs["cwd"]
            seen["command"] = command
            seen["env"] = kwargs["env"]
            return subprocess.CompletedProcess(command, 0, PASS_OUTPUT.replace("frozen LP bps: 5000", "frozen LP bps: 7000"), "")

        original = (fork_runner.ROOT / "src/v2/V2TreasuryDeployer.sol").read_text()
        with patch.object(fork_runner.subprocess, "run", side_effect=fake_run):
            result = fork_runner.run_fork(lp_bps=7000)
        self.assertTrue(result["passed"])
        self.assertEqual(result["kind"], "live_venue_fork")
        self.assertEqual(result["allocation"]["lp_bps"], 7000)
        self.assertFalse(result["allocation"]["uses_default_lp_bps"])
        self.assertTrue(result["allocation"]["configured_via_set_lp_bps"])
        self.assertEqual(result["allocation"]["frozen_lp_bps"], 7000)
        self.assertFalse(result["allocation"]["temporary_source_variant"])
        self.assertEqual(result["metrics"]["treasury_stock_raw"], 3000000000000000000)
        self.assertEqual(seen["path"], fork_runner.ROOT)
        self.assertEqual(original, (fork_runner.ROOT / "src/v2/V2TreasuryDeployer.sol").read_text())
        self.assertEqual(seen["command"], ["forge", "test", "--match-path", fork_runner.TEST_PATH,
                                           "--match-test", fork_runner.TEST_NAME, "-vv"])
        self.assertEqual(seen["env"]["RH_FORK_BLOCK"], "70786980")
        self.assertEqual(seen["env"]["V2_LP_BPS"], "7000")
        self.assertEqual(seen["env"]["FOUNDRY_FFI"], "false")
        self.assertFalse((fork_runner.Path(seen["env"]["FOUNDRY_OUT"]).parent).exists())
        self.assertNotEqual(fork_runner.Path(seen["env"]["FOUNDRY_CACHE_PATH"]).parent, fork_runner.ROOT)

    def test_accepts_the_contracts_full_basis_point_range(self):
        for bps in (1000, 1050, 9001, 10000):
            with self.subTest(bps=bps), patch.object(fork_runner, "_run", return_value={"ok": True}) as run:
                self.assertEqual(fork_runner.run_fork(lp_bps=bps), {"ok": True})
                run.assert_called_once_with(rpc_alias="blockmachine", timeout_seconds=300, lp_bps=bps)

    def test_non_default_timeout_is_structured(self):
        seen = {}

        def timed_out(command, **kwargs):
            seen["path"] = kwargs["cwd"]
            raise subprocess.TimeoutExpired(command, 20)

        with patch.object(fork_runner.subprocess, "run", side_effect=timed_out):
            result = fork_runner.run_fork(lp_bps=3000, timeout_seconds=20)
        self.assertEqual(result["status"], "timeout")
        self.assertFalse(result["passed"])
        self.assertEqual(seen["path"], fork_runner.ROOT)


if __name__ == "__main__":
    unittest.main()

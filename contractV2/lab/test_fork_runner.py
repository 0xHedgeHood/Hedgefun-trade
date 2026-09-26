"""Local runner contract: pinned block, fixed allocation, and no shell use."""

import subprocess
import unittest
from unittest.mock import patch

from lab import fork_runner


PASS_OUTPUT = """Logs:
  [PASS] test_fork_twoUsersLiveV3CurveGraduationLiveV4AndFeeSettlement() (gas: 123)
  V2 live venue fork block: 70786980
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
        self.assertEqual(kwargs["env"]["FOUNDRY_FFI"], "false")
        self.assertFalse(result["scenario"]["broadcast"])

    def test_rejects_arbitrary_rpc_and_slider_allocation(self):
        for kwargs in ({"rpc_alias": "https://evil.example"}, {"rpc_alias": "robinhood; echo bad"},
                       {"lp_bps": 7000}, {"timeout_seconds": True}, {"timeout_seconds": 601}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                fork_runner.run_fork(**kwargs)

    def test_skip_or_wrong_block_is_never_reported_as_pass(self):
        for output in (
            "Suite result: ok. 0 passed; 0 failed; 1 skipped; finished in 0.01s",
            PASS_OUTPUT.replace("70786980", "70786981"),
        ):
            with self.subTest(output=output), patch.object(
                fork_runner.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, output, "")
            ):
                result = fork_runner.run_fork()
            self.assertFalse(result["passed"])
            self.assertEqual(result["status"], "failed")

    def test_timeout_is_structured(self):
        with patch.object(fork_runner.subprocess, "run", side_effect=subprocess.TimeoutExpired(["forge"], 20)):
            result = fork_runner.run_fork(timeout_seconds=20)
        self.assertEqual(result["status"], "timeout")
        self.assertFalse(result["passed"])

    def test_variant_uses_disposable_source_and_cleans_it(self):
        seen = {}

        def fake_run(command, **kwargs):
            temporary = kwargs["cwd"]
            seen["path"] = temporary
            seen["factory"] = (temporary / "src/v2/V2TreasuryDeployer.sol").read_text()
            seen["lib"] = (temporary / "lib").is_symlink()
            seen["test"] = (temporary / fork_runner.TEST_PATH).exists()
            seen["command"] = command
            seen["env"] = kwargs["env"]
            return subprocess.CompletedProcess(command, 0, PASS_OUTPUT, "")

        original = (fork_runner.ROOT / "src/v2/V2TreasuryDeployer.sol").read_text()
        with patch.object(fork_runner.subprocess, "run", side_effect=fake_run):
            result = fork_runner.run_fork_variant(7000)
        self.assertTrue(result["passed"])
        self.assertEqual(result["kind"], "local_variant_fork")
        self.assertEqual(result["allocation"]["lp_bps"], 7000)
        self.assertFalse(result["allocation"]["fixed_in_current_contract"])
        self.assertEqual(result["metrics"]["treasury_stock_raw"], 3000000000000000000)
        self.assertTrue(seen["lib"])
        self.assertTrue(seen["test"])
        self.assertIn("DEFAULT_LP_BPS = 7000;", seen["factory"])
        self.assertEqual(original, (fork_runner.ROOT / "src/v2/V2TreasuryDeployer.sol").read_text())
        self.assertFalse(seen["path"].exists())
        self.assertEqual(seen["command"], ["forge", "test", "--match-path", fork_runner.TEST_PATH,
                                           "--match-test", fork_runner.TEST_NAME, "-vv"])
        self.assertEqual(seen["env"]["RH_FORK_BLOCK"], "70786980")
        self.assertEqual(seen["env"]["FOUNDRY_FFI"], "false")

    def test_variant_rejects_non_percent_steps(self):
        for bps in (True, "3000", 0, 999, 1050, 9001, 10000):
            with self.subTest(bps=bps), self.assertRaises(ValueError):
                fork_runner.run_fork_variant(bps)

    def test_variant_timeout_also_cleans_temporary_source(self):
        seen = {}

        def timed_out(command, **kwargs):
            seen["path"] = kwargs["cwd"]
            raise subprocess.TimeoutExpired(command, 20)

        with patch.object(fork_runner.subprocess, "run", side_effect=timed_out):
            result = fork_runner.run_fork_variant(3000, timeout_seconds=20)
        self.assertEqual(result["status"], "timeout")
        self.assertFalse(result["passed"])
        self.assertFalse(seen["path"].exists())


if __name__ == "__main__":
    unittest.main()

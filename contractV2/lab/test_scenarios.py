"""The click runner only executes named local EVM suites and reports verified results."""

import subprocess
import unittest
from unittest.mock import patch

from lab import scenarios


def pass_output(name):
    tests = scenarios.SCENARIOS[name]["tests"]
    lines = "\n".join(f"[PASS] {test}() (gas: 123)" for test in tests)
    metrics = "\n".join(f"  {prefix} 123" for prefix in scenarios.SCENARIOS[name]["required_metrics"])
    return (f"{lines}\nLogs:\n{metrics}\n"
            f"Suite result: ok. {len(tests)} passed; 0 failed; 0 skipped; finished in 1.00s\n")


class ScenarioRunnerTest(unittest.TestCase):
    def test_allowlisted_command_and_disposable_build_state(self):
        observed = {}

        def fake_run(command, **kwargs):
            observed["command"] = command
            observed["env"] = kwargs["env"]
            observed["cwd"] = kwargs["cwd"]
            observed["output"] = kwargs["env"]["FOUNDRY_OUT"]
            return subprocess.CompletedProcess(command, 0, pass_output("buy_wave"), "")

        with patch.object(scenarios.subprocess, "run", side_effect=fake_run):
            result = scenarios.run_scenario("buy_wave")
        self.assertTrue(result["passed"])
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["test_count"], 2)
        self.assertEqual(result["metrics"], [f"{prefix} 123" for prefix in
                                             scenarios.SCENARIOS["buy_wave"]["required_metrics"]])
        self.assertEqual(observed["command"], ["forge", "test", "--match-path",
                                               "test/V2MarketBuyScenarios.t.sol", "--match-contract",
                                               "V2MarketBuyScenariosTest", "-vv"])
        self.assertEqual(observed["cwd"], scenarios.ROOT)
        self.assertEqual(observed["env"]["FOUNDRY_FFI"], "false")
        self.assertEqual(observed["env"]["FOUNDRY_PROFILE"], "default")
        self.assertEqual(observed["env"]["RH_FORK"], "0")
        self.assertFalse(result["broadcast"])
        from pathlib import Path
        self.assertFalse(Path(observed["output"]).parent.exists())

    def test_rejects_arbitrary_names_before_execution(self):
        with patch.object(scenarios.subprocess, "run") as run:
            for name in ("", "../test/V2MarketBuyScenarios.t.sol", "buy_wave; true", None):
                with self.subTest(name=name), self.assertRaises(ValueError):
                    scenarios.run_scenario(name)
            run.assert_not_called()

    def test_missing_or_extra_test_never_passes(self):
        valid = pass_output("opening_sniper")
        count = len(scenarios.SCENARIOS["opening_sniper"]["tests"])
        variants = (valid.replace("test_graduatedOpeningSizeSweep", "test_wrongName"),
                    valid.replace(f"{count} passed", f"{count - 1} passed"),
                    valid.replace("0 failed", "1 failed"),
                    valid.replace("Scenario v4_size_5_20 victim_loss_token_raw:", "Scenario missing_metric:"))
        for output in variants:
            with self.subTest(output=output), patch.object(
                scenarios.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, output, "")
            ):
                result = scenarios.run_scenario("opening_sniper")
            self.assertFalse(result["passed"])
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["metrics"], [])

    def test_timeout_is_reported_without_metrics(self):
        with patch.object(scenarios.subprocess, "run", side_effect=subprocess.TimeoutExpired(["forge"], 20)):
            result = scenarios.run_scenario("mixed_order", timeout_seconds=20)
        self.assertFalse(result["passed"])
        self.assertEqual(result["status"], "timeout")
        self.assertEqual(result["metrics"], [])


if __name__ == "__main__":
    unittest.main()

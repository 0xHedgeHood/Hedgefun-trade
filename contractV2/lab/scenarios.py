"""Allowlisted multi-user Foundry scenarios for the local V2 workbench.

These replay transactions in a local EVM with the production V2 contracts and
PoolManager. The stock and oracle fixture are simulated. They never broadcast.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = {
    "buy_wave": {
        "path": "test/V2MarketBuyScenarios.t.sol",
        "contract": "V2MarketBuyScenariosTest",
        "tests": (
            "testFourWalletBuyWaveOrderChangesWalletOutcomeNotAggregate",
            "testFourWalletBuyWaveGraduationRefundAndAtomicFailure",
        ),
        "wallets": 4,
        "required_metrics": (
            "Scenario buy-wave order raw firstWalletAdvantage",
            "Scenario buy-wave graduation raw budget spent refund",
        ),
    },
    "sell_wave": {
        "path": "test/V2MarketSellScenarios.t.sol",
        "contract": "V2MarketSellScenariosTest",
        "tests": (
            "test_fourWalletCurveSellWaveOrderAndConservation",
            "test_fourWalletV4SellWaveSpikeAndFlat",
        ),
        "wallets": 4,
        "required_metrics": (
            "Scenario sell-wave curve first_stock_raw:",
            "Scenario sell-wave curve last_stock_raw:",
            "Scenario sell-wave v4 flat_total_stock_raw:",
            "Scenario sell-wave v4 spike_total_stock_raw:",
        ),
    },
    "mixed_order": {
        "path": "test/V2MarketMixedScenarios.t.sol",
        "contract": "V2MarketMixedScenariosTest",
        "tests": (
            "test_mixedCurveSameTimestampOrdering",
            "test_mixedV4SameTimestampOrdering",
        ),
        "wallets": 4,
        "required_metrics": (
            "Scenario mixed-curve sellerA-after-buy raw:",
            "Scenario mixed-curve sellerA-before-buy raw:",
            "Scenario mixed-v4 sellerA-after-buy raw:",
            "Scenario mixed-v4 sellerA-before-buy raw:",
        ),
    },
    "opening_sniper": {
        "path": "test/V2MarketSniperScenarios.t.sol",
        "contract": "V2MarketSniperScenariosTest",
        "tests": (
            "test_openingTaxTimeSweepKeepsQuotesAndLossesVisible",
            "test_smallSnipersStillProfitWithinStrictMinOut",
            "test_postWindowCurveOrderAndTightMinOut",
            "test_graduatedOpeningHasFlatTaxAndNoLaunchSpike",
            "test_graduatedOpeningSizeSweep",
            "test_buybackSpikeRaisesSniperExitTaxButDecays",
        ),
        "wallets": 2,
        "required_metrics": (
            "Scenario curve_t0 bot_pnl_stock_raw:",
            "Scenario curve_t1 bot_pnl_stock_raw:",
            "Scenario curve_t2 bot_pnl_stock_raw:",
            "Scenario curve_t3 bot_pnl_stock_raw:",
            "Scenario curve_t1_small_bot_pnl_stock_raw:",
            "Scenario curve_t1_small_bot_victim_loss_percent_e18:",
            "Scenario curve_t1_tiny_bot_pnl_stock_raw:",
            "Scenario curve_t1_tiny_bot_victim_loss_percent_e18:",
            "Scenario curve_bot_first bot_pnl_stock_raw:",
            "Scenario v4_size_5_20 bot_pnl_stock_raw:",
            "Scenario v4_size_5_20 victim_loss_token_raw:",
            "Scenario sniper_v4_size_5_20_protected bot_pnl_stock_raw:",
        ),
    },
}

_PASS = re.compile(r"(?m)^\[PASS\] (test[^\s(]+)\(")
_SUITE = re.compile(r"\b(\d+) passed; 0 failed; 0 skipped\b")
_SCENARIO_LINE = re.compile(r"(?m)^\s*(Scenario [^\r\n]+)$")


def run_scenario(name: str, *, timeout_seconds: int = 180) -> dict:
    """Execute one fixed scenario suite and verify every expected test passed."""
    if not isinstance(name, str) or name not in SCENARIOS:
        raise ValueError("Unknown scenario")
    if type(timeout_seconds) is not int or not 15 <= timeout_seconds <= 600:
        raise ValueError("timeout_seconds must be an integer from 15 to 600")
    spec = SCENARIOS[name]
    command = ["forge", "test", "--match-path", spec["path"], "--match-contract", spec["contract"], "-vv"]
    env = os.environ.copy()
    env["FOUNDRY_PROFILE"] = "default"
    env["FOUNDRY_FFI"] = "false"
    env["RH_FORK"] = "0"
    started = time.monotonic()
    status = "failed"
    output = ""
    return_code: int | None = None
    error: str | None = None
    try:
        with tempfile.TemporaryDirectory(prefix="hedgefun-v2-scenario-") as directory:
            env["FOUNDRY_OUT"] = str(Path(directory) / "out")
            env["FOUNDRY_CACHE_PATH"] = str(Path(directory) / "cache")
            completed = subprocess.run(command, cwd=ROOT, env=env, capture_output=True,
                                       text=True, timeout=timeout_seconds, check=False)
            return_code = completed.returncode
            output = completed.stdout + "\n" + completed.stderr
    except subprocess.TimeoutExpired as exc:
        status = "timeout"
        stdout = exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        output = stdout + "\n" + stderr
        error = f"Scenario exceeded {timeout_seconds} seconds"
    except OSError as exc:
        status = "unavailable"
        error = f"Unable to start Foundry: {exc.strerror or type(exc).__name__}"

    expected = set(spec["tests"])
    passed_tests = set(_PASS.findall(output))
    suite = _SUITE.search(output)
    metrics = _SCENARIO_LINE.findall(output)
    metrics_complete = all(
        any(line.startswith(prefix) and re.search(r"-?\d+\s*$", line) for line in metrics)
        for prefix in spec["required_metrics"]
    )
    passed = (return_code == 0 and suite is not None and int(suite.group(1)) == len(expected)
              and passed_tests == expected and metrics_complete)
    if passed:
        status = "passed"
    elif status == "failed":
        error = "Scenario tests or required metrics were not verified"
    return {
        "scenario": name,
        "status": status,
        "passed": passed,
        "duration_seconds": round(time.monotonic() - started, 3),
        "engine": "local EVM: production V2 contracts and V4 PoolManager; mock stock/feed",
        "wallets": spec["wallets"],
        "test_count": len(expected),
        "passed_tests": sorted(passed_tests) if passed else [],
        "metrics": metrics if passed else [],
        "broadcast": False,
        "return_code": return_code,
        "error": error,
        "output_tail": output[-12_000:],
    }

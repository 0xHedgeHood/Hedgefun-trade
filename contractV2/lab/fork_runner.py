"""Run the pinned V2 live-venue integration test as a local, read-only fork.

The Solidity test creates a fork inside Foundry and deploys the candidate V2
contracts only in that ephemeral EVM. This module never invokes a script,
transaction broadcast, or an arbitrary shell command.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PINNED_BLOCK = 70_786_980
LP_BPS = 5_000
TEST_NAME = "test_fork_twoUsersLiveV3CurveGraduationV4FeesAndBuybackBurn"
TEST_PATH = "test/V2LiveVenueFork.t.sol"
_RPC_ALIASES = frozenset(("blockmachine",))
_SUCCESS = re.compile(r"\b1 passed; 0 failed; 0 skipped\b")
_TEST_PASS = re.compile(r"\[PASS\]\s*" + re.escape(TEST_NAME) + r"\(\)")
_BLOCK = re.compile(r"V2 live venue fork block:\s*([\d_]+)")
_REFUND = re.compile(r"Final graduation GME refund \(raw\):\s*([\d_]+)")
_LP_STOCK = re.compile(r"Live graduation LP GME raw:\s*([\d_]+)")
_TREASURY_STOCK = re.compile(r"Live graduation treasury GME raw:\s*([\d_]+)")
_LP_STOCK_FEE = re.compile(r"Live V4 LP stock fee GME raw:\s*([\d_]+)")
_LP_FUN_BURN = re.compile(r"Live V4 LP FUN fee burned raw:\s*([\d_]+)")
_FROZEN_LP_BPS = re.compile(r"V2 live venue frozen LP bps:\s*([\d_]+)")
_RATE_LIMIT = re.compile(r"(?:HTTP error 429|rate limit exceeded)", re.IGNORECASE)
_RETRY_AFTER_MS = re.compile(r'"retry_after_ms"\s*:\s*(\d+)')


def _tail(output: str, limit: int = 4_000) -> str:
    return output[-limit:]


def _validate_timeout(timeout_seconds: int) -> None:
    if type(timeout_seconds) is not int or not 15 <= timeout_seconds <= 600:
        raise ValueError("timeout_seconds must be an integer from 15 to 600")


def _number(output: str, pattern: re.Pattern[str]) -> int | None:
    match = pattern.search(output)
    return int(match.group(1).replace("_", "")) if match else None


def _run(*, rpc_alias: str, timeout_seconds: int, lp_bps: int) -> dict:
    """Execute the sole allowlisted test and verify its block and pass count."""
    import os

    env = os.environ.copy()
    env.update({
        "RH_FORK": "1",
        "RH_RPC": rpc_alias,
        "RH_FORK_BLOCK": str(PINNED_BLOCK),
        "V2_LP_BPS": str(lp_bps),
        "FOUNDRY_PROFILE": "default",
        "FOUNDRY_FFI": "false",
    })
    command = ["forge", "test", "--match-path", TEST_PATH, "--match-test", TEST_NAME, "-vv"]
    started = time.monotonic()
    status = "failed"
    return_code: int | None = None
    output = ""
    display_output = ""
    error: str | None = None
    with tempfile.TemporaryDirectory(prefix="hedgefun-v2-fork-build-") as directory:
        env["FOUNDRY_OUT"] = str(Path(directory) / "out")
        # Keep compiler artifacts outside ROOT. Foundry separately keeps fork state in its global RPC cache
        # (~/.foundry/cache/rpc), which lets rate-limited subprocess retries resume without touching the worktree.
        env["FOUNDRY_CACHE_PATH"] = str(Path(tempfile.gettempdir()) / "hedgefun-v2-fork-cache")
        try:
            deadline = started + timeout_seconds
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    status = "timeout"
                    error = f"Pinned fork test exceeded {timeout_seconds} seconds while waiting for archive RPC capacity"
                    break
                completed = subprocess.run(
                    command, cwd=ROOT, env=env, capture_output=True, text=True,
                    timeout=remaining, check=False,
                )
                return_code = completed.returncode
                output = completed.stdout + "\n" + completed.stderr
                display_output = completed.stdout if completed.returncode == 0 else output
                if completed.returncode == 0 or not _RATE_LIMIT.search(output):
                    break
                retry = _RETRY_AFTER_MS.search(output)
                delay = min(60.0, max(1.0, int(retry.group(1)) / 1000 if retry else 60.0))
                if time.monotonic() + delay >= deadline:
                    status = "timeout"
                    error = f"Pinned fork test exceeded {timeout_seconds} seconds while waiting for archive RPC capacity"
                    break
                time.sleep(delay)
        except subprocess.TimeoutExpired as exc:
            status = "timeout"
            stdout = exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            stderr = exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
            output = stdout + "\n" + stderr
            display_output = output
            error = f"Pinned fork test exceeded {timeout_seconds} seconds"
        except OSError as exc:
            status = "unavailable"
            error = f"Unable to start Foundry: {exc.strerror or type(exc).__name__}"

    reported_block = _number(output, _BLOCK)
    frozen_lp_bps = _number(output, _FROZEN_LP_BPS)
    passed = return_code == 0 and bool(_SUCCESS.search(output)) and bool(_TEST_PASS.search(output)) \
        and reported_block == PINNED_BLOCK and frozen_lp_bps == lp_bps
    if passed:
        status = "passed"
    elif status == "failed" and return_code == 0:
        error = "Foundry exited successfully but the single pinned fork test was not verified"
    elif status == "failed":
        error = "Pinned fork test failed; inspect output_tail"

    return {
        "kind": "live_venue_fork",
        "status": status,
        "passed": passed,
        "duration_seconds": round(time.monotonic() - started, 3),
        "chain": "Robinhood Chain",
        "requested_block": PINNED_BLOCK,
        "reported_block": reported_block,
        "allocation": {
            "lp_bps": lp_bps,
            "treasury_bps": 10_000 - lp_bps,
            "uses_default_lp_bps": lp_bps == LP_BPS,
            "frozen_lp_bps": frozen_lp_bps if passed else None,
            "configured_via_set_lp_bps": passed,
            "temporary_source_variant": False,
            "source_worktree_unchanged": True,
        },
        "test": TEST_NAME,
        "scenario": {
            "actors": ["Alice", "Bob"],
            "entry": "real USDG/GME V3 venue",
            "pre_graduation": "two buys, two partial sells, graduation buy with refund",
            "post_graduation": "two V4 buys, two V4 sells, tax sweep, LP fee collection",
            "scripted_usdg_buys": {"alice_initial": 10, "bob_initial": 10,
                                   "bob_graduation": 250, "alice_v4": 5, "bob_v4": 5},
            "broadcast": False,
        },
        "verified_checks": [
            "USDG, stock, and FUN conservation",
            "curve graduation and nonzero V4 liquidity",
            "entry and exit amounts with exact output floors",
            "curve and V4 tax settlement",
            "LP fee collection, stock buyback credit, and FUN fee burn",
        ] if passed else [],
        "metrics": {
            "graduation_refund_stock_raw": _number(output, _REFUND) if passed else None,
            "graduation_refund_stock_decimals": 18,
            "lp_stock_raw": _number(output, _LP_STOCK) if passed else None,
            "treasury_stock_raw": _number(output, _TREASURY_STOCK) if passed else None,
            "lp_stock_fee_raw": _number(output, _LP_STOCK_FEE) if passed else None,
            "lp_fun_fee_burned_raw": _number(output, _LP_FUN_BURN) if passed else None,
            "stock_decimals": 18,
            "fun_decimals": 18,
        },
        "return_code": return_code,
        "error": error,
        "output_tail": _tail(display_output),
    }


def run_fork(*, rpc_alias: str = "blockmachine", timeout_seconds: int = 300, lp_bps: int = LP_BPS) -> dict:
    """Run unchanged current source and configure the per-stock LP share through setLpBps."""
    if rpc_alias not in _RPC_ALIASES:
        raise ValueError("rpc_alias must be one of the configured Robinhood RPC aliases")
    _validate_timeout(timeout_seconds)
    if type(lp_bps) is not int or not 1000 <= lp_bps <= 10000:
        raise ValueError("lp_bps must be an integer from 1000 through 10000")
    return _run(rpc_alias=rpc_alias, timeout_seconds=timeout_seconds, lp_bps=lp_bps)

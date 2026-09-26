"""Run the pinned V2 live-venue integration test as a local, read-only fork.

The Solidity test creates a fork inside Foundry and deploys the candidate V2
contracts only in that ephemeral EVM. This module never invokes a script,
transaction broadcast, or an arbitrary shell command.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PINNED_BLOCK = 70_786_980
LP_BPS = 5_000
TEST_NAME = "test_fork_twoUsersLiveV3CurveGraduationLiveV4AndFeeSettlement"
TEST_PATH = "test/V2LiveVenueFork.t.sol"
_RPC_ALIASES = frozenset(("robinhood", "publicnode"))
_SUCCESS = re.compile(r"\b1 passed; 0 failed; 0 skipped\b")
_TEST_PASS = re.compile(r"\[PASS\]\s*" + re.escape(TEST_NAME) + r"\(\)")
_BLOCK = re.compile(r"V2 live venue fork block:\s*([\d_]+)")
_REFUND = re.compile(r"Final graduation GME refund \(raw\):\s*([\d_]+)")
_LP_STOCK = re.compile(r"Live graduation LP GME raw:\s*([\d_]+)")
_TREASURY_STOCK = re.compile(r"Live graduation treasury GME raw:\s*([\d_]+)")
_LP_STOCK_FEE = re.compile(r"Live V4 LP stock fee GME raw:\s*([\d_]+)")
_LP_FUN_BURN = re.compile(r"Live V4 LP FUN fee burned raw:\s*([\d_]+)")
_CAPITAL = re.compile(r"\bDEFAULT_LP_BPS\s*=\s*5_000\s*;|\bDEFAULT_LP_BPS\s*=\s*5000\s*;")
_CAPITAL_LINE = re.compile(r"(?m)^(\s*uint16 public constant DEFAULT_LP_BPS\s*=\s*)(?:5_000|5000)(\s*;)")


def _tail(output: str, limit: int = 4_000) -> str:
    return output[-limit:]


def _assert_fixed_capital() -> None:
    """Fail closed if the compiled source no longer has the advertised split."""
    source = (ROOT / "src/v2/V2TreasuryDeployer.sol").read_text(encoding="utf-8")
    if not _CAPITAL.search(source):
        raise RuntimeError("V2 deployer LP default is no longer the verified fixed 50/50 baseline")


def _validate_timeout(timeout_seconds: int) -> None:
    if type(timeout_seconds) is not int or not 15 <= timeout_seconds <= 600:
        raise ValueError("timeout_seconds must be an integer from 15 to 600")


def _number(output: str, pattern: re.Pattern[str]) -> int | None:
    match = pattern.search(output)
    return int(match.group(1).replace("_", "")) if match else None


def _run(workdir: Path, *, rpc_alias: str, timeout_seconds: int, lp_bps: int, variant: bool) -> dict:
    """Execute the sole allowlisted test and verify its block and pass count."""
    import os

    env = os.environ.copy()
    env.update({
        "RH_FORK": "1",
        "RH_RPC": rpc_alias,
        "RH_FORK_BLOCK": str(PINNED_BLOCK),
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
    try:
        completed = subprocess.run(
            command, cwd=workdir, env=env, capture_output=True, text=True,
            timeout=timeout_seconds, check=False,
        )
        return_code = completed.returncode
        output = completed.stdout + "\n" + completed.stderr
        display_output = completed.stdout if completed.returncode == 0 else output
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
    passed = return_code == 0 and bool(_SUCCESS.search(output)) and bool(_TEST_PASS.search(output)) \
        and reported_block == PINNED_BLOCK
    if passed:
        status = "passed"
    elif status == "failed" and return_code == 0:
        error = "Foundry exited successfully but the single pinned fork test was not verified"
    elif status == "failed":
        error = "Pinned fork test failed; inspect output_tail"

    return {
        "kind": "local_variant_fork" if variant else "live_venue_fork",
        "status": status,
        "passed": passed,
        "duration_seconds": round(time.monotonic() - started, 3),
        "chain": "Robinhood Chain",
        "requested_block": PINNED_BLOCK,
        "reported_block": reported_block,
        "allocation": {
            "lp_bps": lp_bps,
            "treasury_bps": 10_000 - lp_bps,
            "fixed_in_current_contract": lp_bps == LP_BPS,
            "temporary_source_variant": variant,
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


def run_fork(*, rpc_alias: str = "robinhood", timeout_seconds: int = 300, lp_bps: int = LP_BPS) -> dict:
    """Run the unchanged, current-source 50/50 contract at the pinned block."""
    if rpc_alias not in _RPC_ALIASES:
        raise ValueError("rpc_alias must be one of the configured Robinhood RPC aliases")
    _validate_timeout(timeout_seconds)
    if type(lp_bps) is not int or lp_bps != LP_BPS:
        raise ValueError("baseline fork mode only supports the current candidate's fixed 50/50 split")
    _assert_fixed_capital()
    return _run(ROOT, rpc_alias=rpc_alias, timeout_seconds=timeout_seconds, lp_bps=LP_BPS, variant=False)


def run_fork_variant(lp_bps: int, *, timeout_seconds: int = 300) -> dict:
    """Compile a temporary LP-ratio variant and run it against the same real-chain fork.

    The sole changed source line is the deployer's DEFAULT_LP_BPS in a disposable copy. No
    checkout file is modified, and the variant is never broadcast or deployed.
    """
    if type(lp_bps) is not int or not 1000 <= lp_bps <= 9000 or lp_bps % 100:
        raise ValueError("lp_bps must be a whole-percent split from 10% through 90%")
    _validate_timeout(timeout_seconds)
    _assert_fixed_capital()
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="hedgefun-v2-fork-") as directory:
        workdir = Path(directory)
        shutil.copytree(ROOT / "src", workdir / "src")
        for relative in (TEST_PATH, "test/mocks/Mocks.sol", "test/utils/HookMiner.sol"):
            destination = workdir / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, destination)
        shutil.copy2(ROOT / "foundry.toml", workdir / "foundry.toml")
        (workdir / "lib").symlink_to((ROOT / "lib").resolve(), target_is_directory=True)
        factory = workdir / "src/v2/V2TreasuryDeployer.sol"
        source = factory.read_text(encoding="utf-8")
        changed, count = _CAPITAL_LINE.subn(lambda m: f"{m.group(1)}{lp_bps}{m.group(2)}", source)
        if count != 1:
            raise RuntimeError("expected exactly one DEFAULT_LP_BPS declaration")
        factory.write_text(changed, encoding="utf-8")
        result = _run(workdir, rpc_alias="robinhood", timeout_seconds=timeout_seconds, lp_bps=lp_bps, variant=True)
    result["duration_seconds"] = round(time.monotonic() - started, 3)
    return result

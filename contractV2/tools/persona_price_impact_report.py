#!/usr/bin/env python3
"""Audit and export actual local-fork scenario logs; never signs or broadcasts.

Run the pinned test first:
  PERSONA_PRICE_FORK=true ../.local/bin/forge test --offline \
    --match-path test/PersonaPriceImpactFork.t.sol -vv > ../artifacts/persona-price-impact-20261003.log
Then run this tool from any directory. Raw integers in the JSON output are strings to
preserve precision in browser/chart consumers. Human display fields are Decimal-derived.
"""
from __future__ import annotations

import argparse
import csv
from decimal import Decimal, getcontext
import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[2]
getcontext().prec = 65
D = Decimal
E18 = D(10) ** 18


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def number(value: Decimal, places: int = 8) -> str:
    return format(value, f".{places}f").rstrip("0").rstrip(".") or "0"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", type=Path, default=ROOT / "artifacts/persona-price-impact-20261003.log")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/persona-price-impact-20261003")
    args = parser.parse_args()
    raw = args.log.read_text()
    assert re.search(r"6 passed; 0 failed; 0 skipped", raw), "all six fork tests must pass"
    assert "PRICE_GATE_RESULT immediate_rejected=true settled_healthy=true weekend_rejected=true" in raw
    rows = [json.loads(line.split("PRICE_IMPACT_ROW ", 1)[1])
            for line in raw.splitlines() if "PRICE_IMPACT_ROW " in line]
    assert len(rows) == 116, f"expected 116 measured snapshots, got {len(rows)}"
    assert len({(r["stage"], r["mode"], r["scenario"], r["point"], r["phase"]) for r in rows}) == 116
    assert len({r["forkBlock"] for r in rows}) == 1
    scenarios = {"up20", "down20", "crash50_recover", "flat_one_bps_noise"}
    assert {r["scenario"] for r in rows} == scenarios
    groups = {}
    for row in rows:
        groups.setdefault((row["stage"], row["mode"], row["scenario"]), []).append(row)
    assert len(groups) == 20
    chart_rows, summaries = [], []
    for (stage, mode, scenario), group in sorted(groups.items()):
        group.sort(key=lambda r: (r["point"], r["phase"] != "after_stock_move"))
        first, last = group[0], group[-1]
        assert first["point"] == 0 and last["point"] == 3
        if mode == "passive":
            assert len({r["funStockE18"] for r in group}) == 1
            assert len({r["holderFunRaw"] for r in group}) == 1
            assert len({r["totalSupplyRaw"] for r in group}) == 1
            assert len({r["treasuryStockRaw"] for r in group}) == 1
            assert len({r["curveFeesStockRaw"] for r in group}) == 1
        for row in group:
            row["stageLabel"] = "Active" if stage == 0 else "Graduated"
            row["valuationBasis"] = "oracle_scenario_input_mark"
            for key in ("tslaUsdE18", "funStockE18", "funUsdE18", "treasuryNavUsdE18", "holderMarkUsdE18", "lpMarkUsdE18"):
                row[key[:-3]] = number(D(row[key]) / E18, 18)
            # Keep the Solidity's original raw fields and derived compatibility fields;
            # name the mark basis explicitly for chart/CSV consumers. Actual spot marks
            # use the measured V3 spot after each action, which can differ from the feed.
            for key in ("funUsd", "treasuryNavUsd", "holderMarkUsd", "lpMarkUsd"):
                row[key + "OracleMark"] = row[key]
            spot = D(row["stockSpotUsdE18"]) / E18
            fun_spot = D(row["funStockE18"]) / E18 * spot
            row["tslaActualSpotUsd"] = number(spot, 18)
            row["funUsdActualSpotMark"] = number(fun_spot, 18)
            row["treasuryNavUsdActualSpotMark"] = number(D(row["treasuryStockRaw"]) / E18 * spot + D(row["treasuryUsdRaw"]) / D(10)**6, 18)
            row["holderMarkUsdActualSpotMark"] = number(D(row["holderFunRaw"]) / E18 * fun_spot, 18)
            row["lpMarkUsdActualSpotMark"] = number(D(row["lpStockRaw"]) / E18 * spot + D(row["lpFunRaw"]) / E18 * fun_spot, 18)
            row["spotDeviationFromOracleBps"] = number((D(row["stockSpotUsdE18"]) / D(row["tslaUsdE18"]) - 1) * 10_000, 12)
            row["tslaIndex"] = number(D(row["tslaUsdE18"]) * 100 / D(first["tslaUsdE18"]))
            row["funStockIndex"] = number(D(row["funStockE18"]) * 100 / D(first["funStockE18"]))
            row["funUsdIndex"] = number(D(row["funUsdE18"]) * 100 / D(first["funUsdE18"]))
            row["funUsdOracleMarkIndex"] = row["funUsdIndex"]
            chart_rows.append({key: row[key] for key in (
                "stageLabel", "mode", "scenario", "point", "phase", "valuationBasis", "tslaUsd", "funStock", "funUsdOracleMark",
                "tslaIndex", "funStockIndex", "funUsdOracleMarkIndex", "treasuryNavUsdOracleMark", "holderMarkUsdOracleMark", "lpMarkUsdOracleMark",
                "tslaActualSpotUsd", "funUsdActualSpotMark", "treasuryNavUsdActualSpotMark", "holderMarkUsdActualSpotMark", "lpMarkUsdActualSpotMark", "spotDeviationFromOracleBps",
                "keeperActions", "keeperBuybacks", "immediateTreasuryHealthy", "settledTreasuryHealthy")})
        summaries.append({
            "stage": first["stageLabel"], "mode": mode, "scenario": scenario,
            "valuationBasis": "oracle_scenario_input_mark",
            "tslaChangePercent": number((D(last["tslaUsdE18"])/D(first["tslaUsdE18"])-1)*100),
            "funStockChangePercent": number((D(last["funStockE18"])/D(first["funStockE18"])-1)*100),
            "funUsdChangePercent": number((D(last["funUsdE18"])/D(first["funUsdE18"])-1)*100),
            "treasuryNavStartUsd": first["treasuryNavUsd"], "treasuryNavEndUsd": last["treasuryNavUsd"],
            "keeperActions": last["keeperActions"], "keeperBuybacks": last["keeperBuybacks"],
            "burnedFun": number(D(last["burnedRaw"])/E18),
            "holderUnitsUnchanged": first["holderFunRaw"] == last["holderFunRaw"],
        })
    source = ROOT / "contractV2/test/PersonaPriceImpactFork.t.sol"
    source_text = source.read_text()
    block_match = re.search(r"FORK_BLOCK = (\d+)", source_text)
    assert block_match and int(block_match[1]) == rows[0]["forkBlock"]
    result = {
        "schema": "hedgefun-persona-price-impact-v1", "broadcast": False,
        "execution": "real deployed creator factory/router, V3 and V4 contracts on a local ephemeral fork",
        "chainId": 46630, "forkBlock": rows[0]["forkBlock"],
        "sourceCommit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "sourceSha256": digest(source), "rawLogSha256": digest(args.log),
        "testsPassed": 6, "measuredSnapshots": 116,
        "valuationBasis": {
            "default": "oracle_scenario_input_mark",
            "definition": "FUN/TSLA curve or V4 spot multiplied by the scenario-input TSLA oracle price; treasury and LP stock holdings use that same oracle mark.",
            "rawOracleMarkFields": ["funUsdE18", "treasuryNavUsdE18", "holderMarkUsdE18", "lpMarkUsdE18"],
            "actualSpotMarkSuffix": "ActualSpotMark",
            "actualSpotDefinition": "Recalculated with the measured stockSpotUsdE18 after the action, including any V3 execution-induced deviation from the scenario oracle price.",
            "maxAbsoluteSpotDeviationBps": number(max(abs(D(row["spotDeviationFromOracleBps"])) for row in rows), 12),
        },
        "profile": {"kind": 0, "tp1Bps": 1, "tp2Bps": 2, "dipBps": 1, "stopBps": 1,
                    "taxBps": 300, "creatorBps": 1000, "saleBps": 4000, "snipeSeconds": 180,
                    "lotBps": 2000, "bandBpsPerHour": 0},
        "counterfactualInputs": [
            "Local actors funded with Foundry deal; no cost or faucet acquisition model.",
            "Calendar mocked open for strategy scenarios; separate test restores actual weekend and asserts rejection.",
            "TestnetMarket owner impersonated only inside fork; its real V3 swaps and feed setter move TSLA together.",
            "Each price path waits 601 seconds for the actual V3 observation window; not real wall-clock latency.",
            "Scripted flow buys 100 tUSDG on nonnegative print, sells 20% of current FUN holdings on negative print.",
            "Keeper branch attempts exactly one execute and one buyback per noninitial price point; no user trades.",
        ],
        "interpretationLimits": [
            "These are deterministic stress paths, not TSLA forecasts or guaranteed profits.",
            "FUN has no independent USDG venue in this experiment; TSLA repricing alone gives no FUN/TSLA arbitrage target.",
            "The external market maker's mint-funded V3 price moves stand in for price discovery/arbitrage; its P&L is excluded.",
            "Default dollar marks use the TSLA oracle/scenario-input price, not the post-trade V3 spot. Separate ActualSpotMark columns use the measured V3 spot; neither is a liquidation quote. Taxes, slippage, gas and exit size matter.",
            "Active curve treasury is deliberately unwired; only graduated treasury can execute its strategy.",
            "LP marks are underlying full-range principal at actual sqrt price; accrued uncollected LP fees are excluded.",
            "No actor gas is subtracted because calls execute inside a Foundry test, not public signed transactions.",
        ],
        "gates": {"instantTwentyPercentMoveRejected": True, "after601SecondsHealthy": True,
                  "realWeekendStrategyRejected": True},
        "summaries": summaries,
        "rows": [{key: str(value) if isinstance(value, int) and not isinstance(value, bool) else value
                  for key, value in row.items()} for row in rows],
    }
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "results.json").write_text(json.dumps(result, indent=2)+"\n")
    with (args.output / "chart-data.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(chart_rows[0]))
        writer.writeheader(); writer.writerows(chart_rows)
    with (args.output / "summary.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(summaries[0]))
        writer.writeheader(); writer.writerows(summaries)
    (args.output / "forge.log").write_text(raw)
    lines = ["# TSLA price-path fork experiment", "",
             f"Verified {result['testsPassed']} tests and {result['measuredSnapshots']} snapshots at testnet block {result['forkBlock']}.",
             "No public transactions. Prices below are scenario inputs; all FUN/TSLA changes come from actual contract executions.",
             "The table uses oracle/scenario-input dollar marks. Actual post-action V3 spot dollar marks are exported separately in JSON/CSV.",
             f"The maximum observed absolute V3 spot deviation from the oracle input is {result['valuationBasis']['maxAbsoluteSpotDeviationBps']} bps.", "",
             "| Stage | Mode | Path | TSLA % | FUN/TSLA % | FUN/USDG oracle mark % | Treasury NAV oracle mark final | Keeper actions/buybacks |",
             "|---|---|---|---:|---:|---:|---:|---:|"]
    for summary in summaries:
        lines.append("| " + " | ".join(str(summary[key]) for key in
                     ("stage", "mode", "scenario", "tslaChangePercent", "funStockChangePercent", "funUsdChangePercent", "treasuryNavEndUsd"))
                     + f" | {summary['keeperActions']}/{summary['keeperBuybacks']} |")
    lines += ["", "## Explicit experiment assumptions", ""]
    lines += ["- " + item for item in result["counterfactualInputs"]]
    lines += ["", "## What these results cannot establish", ""]
    lines += ["- " + item for item in result["interpretationLimits"]]
    lines += ["", "## Verification", "", f"- Solidity SHA-256: `{result['sourceSha256']}`",
              f"- Forge log SHA-256: `{result['rawLogSha256']}`", "- Data: `results.json`, `chart-data.csv`, `summary.csv`, `forge.log`.", ""]
    (args.output / "REPORT.md").write_text("\n".join(lines))
    print(json.dumps({key: result[key] for key in ("forkBlock", "testsPassed", "measuredSnapshots", "sourceSha256")}, indent=2))
    print(args.output)


if __name__ == "__main__":
    main()

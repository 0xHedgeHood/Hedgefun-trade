#!/usr/bin/env python3
"""Audit historical Close fork logs and export measured results and figures.

No network, signing, dependency installation or wallet access. Run with the local
chart interpreter after all twelve TslaHistoricalReplayFork tests have passed.
The four windows reset independently; this is neither standard annual return nor
a continuous four-year historical-chain backtest.
"""
from __future__ import annotations

import argparse
import csv
from datetime import date
from decimal import Decimal, ROUND_HALF_EVEN, getcontext
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
YEARS = (2022, 2023, 2024, 2025)
MODES = ("passive", "keeper500", "keeper1")
COUNTS = {2022: 251, 2023: 250, 2024: 252, 2025: 250}
D = Decimal
getcontext().prec = 80
E18 = D(10) ** 18
E6 = D(10) ** 6
NOT_DUE = "0x47a2375f"
ZERO_SELECTOR = "0x00000000"
MODE_LABELS = {"passive": "被动持有", "keeper500": "500 bps 策略", "keeper1": "1 bps 策略"}
MODE_COLORS = {"passive": "#64748b", "keeper500": "#087f8c", "keeper1": "#d77920"}
COUNTERS = (
    "actions", "stopActions", "tpActions", "dipActions", "buybacks", "burnedRaw",
    "executeNotDue", "buybackNotDue", "estimatedActionGas", "buybackStockSpentRaw",
    "buybackOracleUsdgValueRaw",
)
INTEGER_FIELDS = set(COUNTERS) | {
    "year", "index", "elapsedSeconds", "evmTimestamp", "forkBlock",
    "stockPoolLiquidity",
    "tslaOracleUsdE18", "stockSpotUsdE18", "stockTwapUsdE18", "funStockE18", "funOracleUsdE18",
    "holderFunRaw", "lpStockRaw", "lpFunRaw", "treasuryStockRaw", "treasuryUsdgRaw",
    "treasuryNavOracleUsdE18", "treasuryBookedRaw", "treasuryUnbookedRaw", "treasuryBuybackRaw",
    "lotCount", "totalSupplyRaw", "keeperStockRaw", "keeperUsdgRaw", "keeperFunRaw",
    "curveUnclaimedFeesRaw", "dailyBuybackStockSpentRaw", "dailyBuybackOracleUsdgValueRaw", "actionCode",
}
BOOLEAN_FIELDS = {"immediateHealthy", "healthyAfterAction", "executeSuccess", "buybackSuccess"}
STRING_FIELDS = {"mode", "date", "executeRevert", "buybackRevert"}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def display(value: Decimal | int, places: int = 10) -> str:
    text = format(D(value), f".{places}f")
    return text.rstrip("0").rstrip(".") or "0"


def change_percent(first: int | Decimal, last: int | Decimal) -> Decimal:
    require(D(first) > 0, "change baseline must be positive")
    return (D(last) / D(first) - 1) * 100


def drawdown(values: list[int], dates: list[str]) -> dict:
    """Largest daily peak-to-later-trough decline; positive percentage loss."""
    require(len(values) == len(dates) and bool(values), "invalid drawdown series")
    require(all(type(value) is int and value > 0 for value in values), "nonpositive drawdown mark")
    peak, peak_index, worst, worst_peak, trough = values[0], 0, D(0), 0, 0
    for index, value in enumerate(values):
        if value > peak:
            peak, peak_index = value, index
        loss = (1 - D(value) / D(peak)) * 100
        if loss > worst:
            worst, worst_peak, trough = loss, peak_index, index
    return {"percent": display(worst), "peakDate": dates[worst_peak], "troughDate": dates[trough],
            "peakIndex": worst_peak, "troughIndex": trough,
            "peakMarkRaw": str(values[worst_peak]), "troughMarkRaw": str(values[trough]),
            "sampling": "daily end-of-replay-point oracle marks only"}


def validate_data(data: dict, raw_source: Path | None = None) -> None:
    require(data.get("schema") == "hedgefun-tsla-daily-history-v1", "wrong history schema")
    require((data.get("symbol"), data.get("currency")) == ("TSLA", "USD"), "wrong source instrument")
    require(data.get("years") == list(YEARS), "unexpected years")
    require(data.get("rowCount") == sum(COUNTS.values()) == len(data.get("rows", [])), "source count mismatch")
    require(set(data.get("windows", {})) == {str(year) for year in YEARS}, "incomplete windows")
    require("session open" in data.get("timestampPolicy", ""), "source must declare daily timestamp semantics")
    if raw_source is not None:
        require(raw_source.exists() and digest(raw_source) == data["sourceSha256"], "raw historical source hash mismatch")
    all_dates = []
    source_by_date = {}
    for row in data["rows"]:
        day = date.fromisoformat(row["date"])
        require(day.year in YEARS and day.weekday() < 5, "invalid source session date")
        require(row["date"] not in source_by_date, "duplicate source date")
        require(D(row["closeUsd"]) == D(row["adjustedCloseUsd"]), "Close/AdjClose policy no longer holds")
        quantized = D(row["closeUsd"]).quantize(D("0.00000001"), rounding=ROUND_HALF_EVEN)
        require(quantized == D(row["replayCloseUsd"]), "historical feed rounding mismatch")
        require(type(row["replayPriceE18"]) is int and row["replayPriceE18"] == int(quantized * E18), "source E18 price mismatch")
        source_by_date[row["date"]] = row
        all_dates.append(row["date"])
    require(all_dates == sorted(all_dates), "source dates must be increasing")
    for year, count in COUNTS.items():
        window = data["windows"][str(year)]
        require(set(window) == {"dates", "pricesE18", "elapsedSeconds"}, "unexpected source window fields")
        require(all(len(window[key]) == count for key in window), f"wrong {year} window length")
        require(window["dates"] == [day for day in all_dates if day.startswith(str(year))], "window dates differ from source rows")
        first = date.fromisoformat(window["dates"][0])
        for index, day in enumerate(window["dates"]):
            require(window["pricesE18"][index] == source_by_date[day]["replayPriceE18"], "window price differs from source row")
            require(window["elapsedSeconds"][index] == (date.fromisoformat(day) - first).days * 86400, "window calendar spacing mismatch")


def parse_log(raw: str) -> tuple[list[dict], list[dict]]:
    require(bool(re.search(r"12 passed; 0 failed; 0 skipped", raw)), "all twelve fork tests must pass")
    require("[FAIL" not in raw and "HISTORICAL_UNEXPECTED_" not in raw, "unexpected failure or keeper rejection in log")
    names = re.findall(r"\[PASS\]\s+(testHistorical_\d{4}_(?:Passive|Keeper500|Keeper1))\(\)", raw)
    expected_names = {f"testHistorical_{year}_{mode}" for year in YEARS for mode in ("Passive", "Keeper500", "Keeper1")}
    require(len(names) == 12 and set(names) == expected_names, "wrong passing test set")
    rows, venues = [], []
    for line in raw.splitlines():
        if "HISTORICAL_ROW " in line:
            row = json.loads(line.split("HISTORICAL_ROW ", 1)[1])
            # Forge may serialize large uints as decimal strings; never parse them
            # through a binary float or accept scientific notation silently.
            for key in INTEGER_FIELDS & set(row):
                value = row[key]
                require(type(value) is int or (type(value) is str and bool(re.fullmatch(r"0|[1-9][0-9]*", value))),
                        f"invalid integer encoding in {key}")
                row[key] = int(value)
                require(0 <= row[key] < 2**256, f"out-of-range uint256 in {key}")
            rows.append(row)
        elif "HISTORICAL_VENUE " in line:
            venues.append(json.loads(line.split("HISTORICAL_VENUE ", 1)[1]))
    require(len(rows) == 3 * sum(COUNTS.values()), "expected exactly 3009 daily snapshots")
    require(len(venues) == 12, "expected one local venue reconstruction per test")
    for venue in venues:
        liquidity = [venue[key] for key in ("oldPositionLiquidity", "newPositionLiquidity", "activeLiquidityBefore", "activeLiquidityAfter")]
        require(all(type(value) is int and value > 0 for value in liquidity) and len(set(liquidity)) == 1,
                "local venue widening must preserve positive active liquidity")
        require(venue["newTickLower"] < venue["oldTickLower"] < venue["oldTickUpper"] < venue["newTickUpper"], "venue range did not widen")
        require(venue["pool"].lower() == "0x04083643ff9e8c27f66c9dd99947743a9b777244", "wrong stock pool")
    require(len({venue["activeLiquidityBefore"] for venue in venues}) == 1, "depth must match across all experiments")
    return rows, venues


def validate_rows(rows: list[dict], data: dict, fork_block: int, expected_liquidity: int) -> dict[tuple[int, str], list[dict]]:
    groups = {}
    for row in rows:
        require(set(row) == INTEGER_FIELDS | BOOLEAN_FIELDS | STRING_FIELDS, f"unexpected snapshot fields: {set(row) ^ (INTEGER_FIELDS | BOOLEAN_FIELDS | STRING_FIELDS)}")
        require(all(type(row[key]) is int and row[key] >= 0 for key in INTEGER_FIELDS), "raw snapshot integers must be unsigned JSON integers")
        require(all(type(row[key]) is bool for key in BOOLEAN_FIELDS), "invalid snapshot booleans")
        require(all(type(row[key]) is str for key in STRING_FIELDS), "invalid snapshot strings")
        require(row["year"] in YEARS and row["mode"] in MODES, "unknown year or mode")
        require(row["forkBlock"] == fork_block, "snapshot fork block mismatch")
        require(row["stockPoolLiquidity"] == expected_liquidity > 0, "daily V3 liquidity differs from the fixed venue depth")
        require(row["healthyAfterAction"], "unhealthy end-of-day snapshot requires investigation")
        require(row["tslaOracleUsdE18"] > 0 and row["stockSpotUsdE18"] > 0 and row["funStockE18"] > 0, "nonpositive price")
        require(row["funOracleUsdE18"] == row["funStockE18"] * row["tslaOracleUsdE18"] // 10**18, "FUN oracle mark arithmetic mismatch")
        require(row["treasuryNavOracleUsdE18"] == row["treasuryStockRaw"] * row["tslaOracleUsdE18"] // 10**18 + row["treasuryUsdgRaw"] * 10**12,
                "treasury oracle NAV arithmetic mismatch")
        require(row["treasuryBookedRaw"] + row["treasuryUnbookedRaw"] + row["treasuryBuybackRaw"] == row["treasuryStockRaw"], "treasury bucket conservation failed")
        require(row["actions"] == row["stopActions"] + row["tpActions"] + row["dipActions"], "action count decomposition failed")
        require(row["lotCount"] <= 128, "unexpected lot count")
        require(row["executeRevert"] in (ZERO_SELECTOR, NOT_DUE) and row["buybackRevert"] in (ZERO_SELECTOR, NOT_DUE), "unexpected revert selector")
        groups.setdefault((row["year"], row["mode"]), []).append(row)
    require(set(groups) == {(year, mode) for year in YEARS for mode in MODES}, "missing experimental groups")
    for (year, mode), group in groups.items():
        window = data["windows"][str(year)]
        require(len(group) == COUNTS[year], "wrong number of daily snapshots")
        group.sort(key=lambda row: row["index"])
        require([row["index"] for row in group] == list(range(COUNTS[year])), "duplicate or missing day indices")
        first = group[0]
        require(all(first[key] == 0 for key in COUNTERS), "first-close baseline already contains keeper actions")
        require(first["treasuryStockRaw"] == 8833333333333333335 and first["treasuryUsdgRaw"] == 0, "unexpected initial treasury portfolio")
        require(abs(first["funStockE18"] - 73611111111) <= 1, "unexpected graduation price")
        for index, row in enumerate(group):
            require(row["date"] == window["dates"][index], "snapshot date mismatch")
            require(row["tslaOracleUsdE18"] == window["pricesE18"][index], "snapshot does not use historical Close")
            require(row["elapsedSeconds"] == window["elapsedSeconds"][index], "snapshot elapsed-time mismatch")
            require(row["evmTimestamp"] == first["evmTimestamp"] + row["elapsedSeconds"], "synthetic EVM calendar spacing mismatch")
            require(row["holderFunRaw"] == first["holderFunRaw"], "the passive token holder traded unexpectedly")
            require(first["totalSupplyRaw"] - row["totalSupplyRaw"] == row["burnedRaw"], "burn total does not reconcile to supply")
            require(row["curveUnclaimedFeesRaw"] == first["curveUnclaimedFeesRaw"], "curve fee balance changed during graduated-only replay")
            if index:
                previous = group[index-1]
                require(all(row[key] >= previous[key] for key in COUNTERS), "cumulative counter went backwards")
                require(row["actions"] - previous["actions"] == int(row["executeSuccess"]), "daily execute success does not reconcile")
                require(row["buybacks"] - previous["buybacks"] == int(row["buybackSuccess"]), "daily buyback success does not reconcile")
                require(row["buybackStockSpentRaw"] - previous["buybackStockSpentRaw"] == row["dailyBuybackStockSpentRaw"], "daily/cumulative buyback STOCK mismatch")
                require(row["buybackOracleUsdgValueRaw"] - previous["buybackOracleUsdgValueRaw"] == row["dailyBuybackOracleUsdgValueRaw"], "daily/cumulative buyback mark mismatch")
            require(row["dailyBuybackOracleUsdgValueRaw"] == row["dailyBuybackStockSpentRaw"] * row["tslaOracleUsdE18"] // 10**30, "buyback USDG mark precision mismatch")
            if mode == "passive":
                require(all(row[key] == 0 for key in COUNTERS), "passive baseline executed actions")
                for key in ("funStockE18", "treasuryStockRaw", "treasuryUsdgRaw", "treasuryBookedRaw", "lpStockRaw", "lpFunRaw"):
                    require(row[key] == first[key], f"passive invariant changed: {key}")
                require(not row["executeSuccess"] and not row["buybackSuccess"], "passive success flags are set")
            elif index:
                require(row["actions"] + row["executeNotDue"] == index, "expected exactly one execute opportunity after each noninitial Close")
                require(row["buybacks"] + row["buybackNotDue"] == index, "expected exactly one buyback opportunity after each noninitial Close")
                require(row["executeRevert"] == (ZERO_SELECTOR if row["executeSuccess"] else NOT_DUE), "execute status/revert mismatch")
                require(row["buybackRevert"] == (ZERO_SELECTOR if row["buybackSuccess"] else NOT_DUE), "buyback status/revert mismatch")
                require(row["actionCode"] in (0, 1, 2) if row["executeSuccess"] else row["actionCode"] == 255, "bad action code")
                if row["executeSuccess"]:
                    key = ("stopActions", "tpActions", "dipActions")[row["actionCode"]]
                    require(row[key] == group[index-1][key] + 1, "action code does not match counter")
    for year in YEARS:
        first_rows = [groups[(year, mode)][0] for mode in MODES]
        for key in ("holderFunRaw", "treasuryStockRaw", "treasuryBookedRaw", "treasuryUsdgRaw"):
            require(len({row[key] for row in first_rows}) == 1, f"initial portfolios differ across {year} modes: {key}")
        # Distinct CREATE2 nonces can reverse V4 currency order and shift the
        # integer-rounded seed by up to two raw units, not two whole FUN tokens.
        for key in ("funStockE18", "lpStockRaw", "lpFunRaw", "totalSupplyRaw"):
            values = [row[key] for row in first_rows]
            require(max(values) - min(values) <= 2, f"initial AMM seeds differ beyond two wei across {year} modes: {key}")
    return groups


def enrich(row: dict, first: dict) -> dict:
    result = dict(row)
    stock_price, fun_stock = D(row["tslaOracleUsdE18"]) / E18, D(row["funStockE18"]) / E18
    actual_stock_price = D(row["stockSpotUsdE18"]) / E18
    fun_actual = fun_stock * actual_stock_price
    result.update(
        valuationBasis="historical_close_oracle_mark",
        tslaOracleUsd=display(stock_price, 18), stockSpotUsd=display(actual_stock_price, 18),
        funStock=display(fun_stock, 18), funUsdOracleMark=display(D(row["funOracleUsdE18"]) / E18, 18),
        funUsdActualSpotMark=display(fun_actual, 18),
        treasuryNavUsdOracleMark=display(D(row["treasuryNavOracleUsdE18"]) / E18, 18),
        treasuryNavUsdActualSpotMark=display(D(row["treasuryStockRaw"]) / E18 * actual_stock_price + D(row["treasuryUsdgRaw"]) / E6, 18),
        holderFun=display(D(row["holderFunRaw"]) / E18, 18),
        holderUsdOracleMark=display(D(row["holderFunRaw"]) * D(row["funOracleUsdE18"]) / E18**2, 18),
        lpUsdOracleMark=display(D(row["lpStockRaw"]) / E18 * stock_price + D(row["lpFunRaw"]) * D(row["funOracleUsdE18"]) / E18**2, 18),
        treasuryStock=display(D(row["treasuryStockRaw"]) / E18, 18),
        treasuryUsdg=display(D(row["treasuryUsdgRaw"]) / E6, 6),
        burnedFun=display(D(row["burnedRaw"]) / E18, 18),
        buybackStockSpent=display(D(row["buybackStockSpentRaw"]) / E18, 18),
        cumulativeBuybackUsdAtExecutionOracleMarks=display(D(row["buybackOracleUsdgValueRaw"]) / E6, 6),
        dailyBuybackUsdAtExecutionOracleMark=display(D(row["dailyBuybackOracleUsdgValueRaw"]) / E6, 6),
        spotDeviationFromOracleBps=display((actual_stock_price / stock_price - 1) * 10000, 12),
    )
    for key, name in (("tslaOracleUsdE18", "tslaIndex"), ("funStockE18", "funStockIndex"),
                      ("funOracleUsdE18", "funOracleMarkIndex"), ("treasuryNavOracleUsdE18", "treasuryNavOracleMarkIndex")):
        result[name] = display(D(row[key]) / D(first[key]) * 100)
    return result


def summarize(group: list[dict]) -> dict:
    first, last = group[0], group[-1]
    dates = [row["date"] for row in group]
    result = {
        "year": first["year"], "mode": first["mode"], "valuationBasis": "historical_close_oracle_mark",
        "firstDate": first["date"], "lastDate": last["date"], "observations": len(group),
        "periodDefinition": "first observed Close to last observed Close within an independently reset calendar-year window",
        "tslaFirstCloseUsd": first["tslaOracleUsd"], "tslaLastCloseUsd": last["tslaOracleUsd"],
        "treasuryNavFirstUsdOracleMark": first["treasuryNavUsdOracleMark"],
        "treasuryNavLastUsdOracleMark": last["treasuryNavUsdOracleMark"],
        "treasuryNavDrawdown": drawdown([row["treasuryNavOracleUsdE18"] for row in group], dates),
        "funOracleMarkDrawdown": drawdown([row["funOracleUsdE18"] for row in group], dates),
        "tslaCloseDrawdown": drawdown([row["tslaOracleUsdE18"] for row in group], dates),
        "buybackStockSpent": last["buybackStockSpent"],
        "cumulativeBuybackUsdAtExecutionOracleMarks": last["cumulativeBuybackUsdAtExecutionOracleMarks"],
        "burnedFun": last["burnedFun"],
        "burnedFractionOfInitialSupplyPercent": display(D(last["burnedRaw"]) / D(first["totalSupplyRaw"]) * 100),
        "keeperStockFinal": display(D(last["keeperStockRaw"]) / E18, 18),
        "keeperUsdgFinal": display(D(last["keeperUsdgRaw"]) / E6, 6),
        "keeperFunFinal": display(D(last["keeperFunRaw"]) / E18, 18),
        "holderUnitsUnchanged": True,
    }
    for key, name in (("tslaOracleUsdE18", "tslaCloseChangePercent"), ("funStockE18", "funStockChangePercent"),
                      ("funOracleUsdE18", "funOracleMarkChangePercent"), ("treasuryNavOracleUsdE18", "treasuryNavOracleMarkChangePercent")):
        result[name] = display(change_percent(first[key], last[key]))
    for key in COUNTERS:
        result[key] = last[key]
    return result


ASSUMPTIONS = [
    "Each year starts with a fresh launch and graduation at that year's first observed Close; all three modes reset independently. These changes are not standard calendar-year returns and cannot be chained into a four-year portfolio return.",
    "Historical inputs are saved Yahoo TSLA Close values already adjusted for the 2022 split. Close equals Adj Close for every saved bar. No second share/price split adjustment is applied.",
    "Yahoo daily timestamps label New York session open, not Close availability. Only session dates determine synthetic calendar-day gaps; real close-release times, DST, early closes and intraday moves are not simulated.",
    "Current deployed creator contracts execute in a local fork of chain 46630. This is a historical-price replay of current contracts, not execution on the historical chain or a forecast.",
    "The market calendar is mocked open. Each new Close is applied to V3 and the feed, then held for a synthetic 601 seconds before the daily observation and keeper opportunity; no public time or public prices are changed.",
    "The local fork replaces the existing narrow TSLA V3 position with a full-range position holding exactly the same active liquidity. Prices are not rescaled. This fixes today's testnet depth across historical dates, not historical liquidity.",
    "An impersonated local test-market operator can mint test assets needed for price moves. External arbitrage/price-setting P&L and acquisition costs are excluded.",
    "Passive mode performs no keeper calls. Keeper modes attempt exactly one execute and one buyback after each noninitial Close; no user order flow is added. One daily opportunity cannot reproduce a 1 bps intraday strategy.",
    "Default dollar marks use the historical Close feed input. Actual post-action V3 spot marks are exported separately; both are valuations, not executable liquidation quotes or redeemable NAV.",
    "Treasury NAV includes remaining stock and cash and falls when stock is spent on FUN buybacks. Buyback stock spend and its value at each execution's Close are reported separately; neither treasury NAV nor NAV plus those marks is investor total return.",
    "The FUN holder never trades. Token units, LP principal, treasury assets, and keeper rewards are distinct accounts; they are not added into a fabricated investor portfolio.",
    "Estimated action gas is gasleft-based test-call usage including harness control and excluding transaction intrinsic gas. No native-gas price or actual gas cost is deducted; all calls are local and unsigned.",
]


def audit(log_path: Path, data_path: Path, source_path: Path, raw_source: Path) -> dict:
    data = json.loads(data_path.read_text())
    validate_data(data, raw_source)
    raw = log_path.read_text()
    rows, venues = parse_log(raw)
    source = source_path.read_text()
    match = re.search(r"FORK_BLOCK\s*=\s*(\d+)", source)
    require(match is not None, "fork block is not pinned in the harness")
    fork_block = int(match[1])
    liquidity = venues[0]["activeLiquidityBefore"]
    groups = validate_rows(rows, data, fork_block, liquidity)
    enriched, summaries = [], []
    for year in YEARS:
        for mode in MODES:
            group = groups[(year, mode)]
            daily = [enrich(row, group[0]) for row in group]
            enriched.extend(daily)
            summaries.append(summarize(daily))
    return {
        "schema": "hedgefun-tsla-history-replay-v1", "broadcast": False, "chainId": 46630,
        "forkBlock": fork_block, "years": list(YEARS), "modes": list(MODES),
        "sourceBars": data["rowCount"], "testsPassed": 12, "measuredSnapshots": len(rows), "groups": len(groups),
        "valuationBasis": "historical_close_oracle_mark",
        "fixedStockPoolLiquidity": str(liquidity),
        "provenance": {"historySourceUrl": data["sourceUrl"], "historySourcePage": data["sourcePage"],
                       "historySourceSha256": data["sourceSha256"], "normalizedHistorySha256": digest(data_path),
                       "harnessSha256": digest(source_path), "forgeLogSha256": digest(log_path),
                       "reportToolSha256": digest(Path(__file__))},
        "profiles": {"passive": {"keeperEnabled": False, "tp1Bps": 500, "tp2Bps": 1000, "dipBps": 500, "stopBps": 500},
                     "keeper500": {"keeperEnabled": True, "tp1Bps": 500, "tp2Bps": 1000, "dipBps": 500, "stopBps": 500},
                     "keeper1": {"keeperEnabled": True, "tp1Bps": 1, "tp2Bps": 2, "dipBps": 1, "stopBps": 1}},
        "commonProfile": {"kind": 0, "taxBps": 300, "creatorBps": 1000, "lotBps": 2000,
                          "bandBpsPerHour": 0, "saleBps": 4000, "snipeSeconds": 180},
        "assumptionsAndLimits": ASSUMPTIONS,
        "maxAbsoluteSpotDeviationBps": display(max(abs(D(row["spotDeviationFromOracleBps"])) for row in enriched), 12),
        "venueReconstructions": venues, "summaries": summaries, "rows": enriched,
    }


def json_safe(value):
    """Stringify integer measurements, while retaining booleans and labels."""
    if type(value) is int:
        return str(value)
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    return value


def write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def write_outputs(result: dict, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    exported = dict(result)
    # Browser consumers must not round E18 balances through IEEE-754 numbers.
    exported["rows"] = [json_safe(row) for row in result["rows"]]
    exported["venueReconstructions"] = json_safe(result["venueReconstructions"])
    exported["summaries"] = []
    for summary in result["summaries"]:
        safe = dict(summary)
        for key in ("estimatedActionGas", "burnedRaw", "buybackStockSpentRaw", "buybackOracleUsdgValueRaw"):
            safe[key] = str(safe[key])
        exported["summaries"].append(safe)
    (output / "results.json").write_text(json.dumps(exported, indent=2) + "\n")
    write_csv(output / "daily.csv", result["rows"])
    summary_rows = []
    for summary in result["summaries"]:
        row = {key: value for key, value in summary.items() if not isinstance(value, dict)}
        for name in ("treasuryNavDrawdown", "funOracleMarkDrawdown", "tslaCloseDrawdown"):
            for key in ("percent", "peakDate", "troughDate"):
                row[name + key[0].upper() + key[1:]] = summary[name][key]
        summary_rows.append(row)
    write_csv(output / "summary.csv", summary_rows)
    lines = ["# TSLA 历史收盘价回放：自动生成结果", "",
             "12 项合约测试通过；1003 个历史日线输入 × 3 个模式，共 3009 个日末快照。全部为本地 fork 调用，没有广播。", "",
             "下表从每年首个观测收盘价到最后一个观测收盘价计算，每年、每个模式独立重置。不是标准年度收益，也不是连续四年的组合收益。美元计价采用历史 Close 预言机输入；国库 NAV 扣除了回购资金流出，不能当作投资者总回报。", "",
             "| 年份 | 模式 | 首末日期 | TSLA 变化 | FUN 美元计价变化 | FUN/TSLA 变化 | 国库 NAV 变化 | NAV 最大日末回撤 | 执行/回购次数 | 烧毁 FUN |",
             "|---|---|---|---:|---:|---:|---:|---:|---:|---:|"]
    for row in result["summaries"]:
        lines.append(f"| {row['year']} | {MODE_LABELS[row['mode']]} | {row['firstDate']} → {row['lastDate']} | "
                     + " | ".join(display(D(row[key]), 4) + "%" for key in ("tslaCloseChangePercent", "funOracleMarkChangePercent", "funStockChangePercent", "treasuryNavOracleMarkChangePercent"))
                     + f" | {display(D(row['treasuryNavDrawdown']['percent']), 4)}% | {row['actions']}/{row['buybacks']} | {display(D(row['burnedFun']), 2)} |")
    lines += ["", "## 回购支出单列", "", "回购实际花费 TSLA，以下美元数仅为各执行日 Close 标价之和；没有将其加回 NAV 宣称总回报。", "",
              "| 年份 | 模式 | 最后国库 NAV（美元标价） | 累计回购 TSLA | 回购按执行日 Close 标价合计 | Stop / TP / Dip 次数 |", "|---|---|---:|---:|---:|---:|"]
    for row in result["summaries"]:
        lines.append(f"| {row['year']} | {MODE_LABELS[row['mode']]} | {display(D(row['treasuryNavLastUsdOracleMark']), 4)} | {display(D(row['buybackStockSpent']), 8)} | {row['cumulativeBuybackUsdAtExecutionOracleMarks']} | {row['stopActions']} / {row['tpActions']} / {row['dipActions']} |")
    lines += ["", "## 实验边界", ""] + ["- " + text for text in ASSUMPTIONS]
    lines += ["", "## 核验", "", f"- 固定 fork 区块：{result['forkBlock']}。",
              f"- 实测 V3 spot 与 Close 预言机最大绝对偏差：{result['maxAbsoluteSpotDeviationBps']} bps。",
              "- 日末最大回撤按每组历史峰值到后续谷值计算；不代表盘中最大回撤。",
              "- 详细数据：`results.json`、`daily.csv`、`summary.csv`。图：`charts/treasury-nav-by-year.{png,svg}`、`charts/fun-oracle-mark-by-year.{png,svg}`、`charts/annual-window-summary.{png,svg}`。",
              "- `estimatedActionGas` 仅为测试调用 gas 测量，包含 harness 控制开销、不含交易基础 gas；未计入真实交易成本。"]
    for key, value in result["provenance"].items():
        lines.append(f"- {key}: `{value}`")
    (output / "GENERATED_REPORT.md").write_text("\n".join(lines) + "\n")


def charts(result: dict, output: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    from matplotlib import font_manager

    font = Path("/System/Library/Fonts/STHeiti Light.ttc")
    if font.exists():
        font_manager.fontManager.addfont(font)
        plt.rcParams["font.family"] = font_manager.FontProperties(fname=font).get_name()
    plt.rcParams.update({"figure.facecolor": "#f8fafc", "axes.facecolor": "#ffffff",
                         "axes.edgecolor": "#d4dde6", "axes.labelcolor": "#334155", "text.color": "#172b4d",
                         "xtick.color": "#536579", "ytick.color": "#536579", "font.size": 10,
                         "axes.unicode_minus": False, "axes.spines.top": False, "axes.spines.right": False,
                         "savefig.facecolor": "#f8fafc", "svg.fonttype": "path"})
    output.mkdir(parents=True, exist_ok=True)

    def save(fig, name):
        fig.savefig(output / (name + ".png"), dpi=180, bbox_inches="tight")
        fig.savefig(output / (name + ".svg"), bbox_inches="tight")
        plt.close(fig)

    for field, title, subtitle, name in (
        ("treasuryNavOracleMarkIndex", "历史 TSLA 收盘价下，国库剩余资产如何变化？", "国库 NAV 指数（每组首日 = 100；包含回购资金流出）", "treasury-nav-by-year"),
        ("funOracleMarkIndex", "历史 TSLA 收盘价下，FUN 美元计价值如何变化？", "FUN 美元计价值指数（每组首日 = 100；按 Close 预言机标价）", "fun-oracle-mark-by-year"),
    ):
        fig, axes = plt.subplots(2, 2, figsize=(14, 9.4))
        fig.suptitle(title, fontsize=19, fontweight="bold", x=.07, ha="left", y=.995)
        fig.text(.07, .945, subtitle, fontsize=12, color="#536579")
        for ax, year in zip(axes.flat, YEARS):
            for mode in MODES:
                group = [row for row in result["rows"] if row["year"] == year and row["mode"] == mode]
                ax.plot([date.fromisoformat(row["date"]) for row in group], [float(row[field]) for row in group],
                        color=MODE_COLORS[mode], label=MODE_LABELS[mode], lw=1.85 if mode != "passive" else 1.5,
                        ls="--" if mode == "passive" else "-")
            ax.set_title(f"{year}｜{COUNTS[year]} 个收盘价", loc="left", fontweight="bold", pad=12)
            ax.xaxis.set_major_locator(mdates.MonthLocator(bymonth=(1, 4, 7, 10)))
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%m月"))
            ax.set_xlim(date(year, 1, 1), date(year, 12, 31))
            ax.set_ylabel("指数")
            ax.grid(axis="y", color="#e5ebf1", lw=.7)
            ax.axhline(100, color="#b4c0cd", lw=.7, ls=":")
        axes[0, 0].legend(frameon=False, fontsize=9)
        fig.text(.07, .033,
                 "每年、每个模式独立重置；首日收盘 → 末日收盘，非标准年度收益，不能连乘为四年收益。\n"
                 "当前合约 + 固定测试网流动性；本地模拟开市，每个收盘价后仅一次 keeper / 回购机会。1 bps 盘中行为无法由日线复现。\n"
                 "国库 NAV 含回购支出，FUN 是边际标价：两者都不是投资者总回报或可兑现卖出报价。",
                 fontsize=10, color="#536579", linespacing=1.5)
        fig.subplots_adjust(top=.88, bottom=.17, hspace=.34, wspace=.22)
        save(fig, name)

    fig, ax = plt.subplots(figsize=(16, 8.2))
    ax.axis("off")
    fig.suptitle("四个独立历史窗口：日末实测结果", fontsize=21, fontweight="bold", x=.06, ha="left", y=.985)
    fig.text(.06, .922, "变化基线 = 当年首个观测 Close；国库 NAV 含回购支出，不是总回报。最大回撤仅统计日末。", fontsize=12, color="#536579")
    headings = ["窗口", "模式", "TSLA\n变化", "FUN美元\n计价变化", "FUN/TSLA\n变化", "国库NAV\n变化", "NAV最大\n日末回撤", "执行 /\n回购", "烧毁FUN\n（万枚）"]
    cells = []
    for row in result["summaries"]:
        cells.append([str(row["year"]), MODE_LABELS[row["mode"]]]
                     + [f"{D(row[key]):+.2f}%" for key in ("tslaCloseChangePercent", "funOracleMarkChangePercent", "funStockChangePercent", "treasuryNavOracleMarkChangePercent")]
                     + [f"{D(row['treasuryNavDrawdown']['percent']):.2f}%", f"{row['actions']} / {row['buybacks']}", f"{D(row['burnedFun']) / 10000:,.2f}"])
    table = ax.table(cellText=cells, colLabels=headings, cellLoc="center", colLoc="center", loc="center",
                     colWidths=[.065, .12, .105, .12, .105, .115, .11, .09, .125], bbox=[0, .1, 1, .83])
    table.auto_set_font_size(False)
    table.set_fontsize(10)
    for (row, column), cell in table.get_celld().items():
        cell.set_edgecolor("#dce5ed")
        if row == 0:
            cell.set_facecolor("#172b4d")
            cell.get_text().set_color("white")
            cell.get_text().set_fontweight("bold")
        else:
            cell.set_facecolor("#ffffff" if ((row-1)//3) % 2 == 0 else "#edf3f8")
            if column == 1:
                cell.get_text().set_color(MODE_COLORS[result["summaries"][row-1]["mode"]])
    fig.text(.06, .035,
             "500 bps 参数：TP1/TP2/dip/stop = 500/1000/500/500；1 bps 参数：1/2/1/1。每个非初始日最多一次策略执行和一次回购。\n"
             "12 项本地 fork 测试，3009 个日末快照；真实历史 Close、假设性执行环境。未扣真实 gas 费用，不能视为可实现投资收益。",
             fontsize=10, color="#536579", linespacing=1.5)
    fig.subplots_adjust(top=.9, bottom=.13, left=.055, right=.97)
    save(fig, "annual-window-summary")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", type=Path, default=ROOT / "artifacts/tsla-history-20261003/forge.log")
    parser.add_argument("--data", type=Path, default=ROOT / "contractV2/data/tsla-history-2022-2025.json")
    parser.add_argument("--source", type=Path, default=ROOT / "contractV2/test/TslaHistoricalReplayFork.t.sol")
    parser.add_argument("--raw-source", type=Path, default=ROOT / "contractV2/data/tsla-history-2022-2025-source.json")
    parser.add_argument("--output", type=Path, default=ROOT / "contractV2/deploy/tsla-history-2026-10-03")
    parser.add_argument("--no-charts", action="store_true")
    args = parser.parse_args()
    result = audit(args.log, args.data, args.source, args.raw_source)
    write_outputs(result, args.output)
    (args.output / "forge.log").write_bytes(args.log.read_bytes())
    if not args.no_charts:
        charts(result, args.output / "charts")
    files = sorted(path for path in args.output.rglob("*") if path.is_file() and path.name != "SHA256SUMS")
    (args.output / "SHA256SUMS").write_text("".join(f"{digest(path)}  {path.relative_to(args.output)}\n" for path in files))
    print(json.dumps({key: result[key] for key in ("sourceBars", "testsPassed", "measuredSnapshots", "groups", "forkBlock")}, indent=2))
    print(args.output)


if __name__ == "__main__":
    main()

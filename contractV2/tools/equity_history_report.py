#!/usr/bin/env python3
"""Audit a 39-case 2025 daily equity/LP-fee replay and export compact evidence.

Uses only saved logs/data. All raw rows are retained in deterministic gzip JSONL;
results.json contains metadata, summaries and matched comparisons. No signing,
network access, parameter optimization, gas-price assumptions or public trades.
"""
from __future__ import annotations

import argparse
import csv
from datetime import date
from decimal import Decimal, getcontext
import gzip
import hashlib
import io
import json
from pathlib import Path
import re

from equity_history_data import build as rebuild_data
from tsla_history_report import drawdown, display, change_percent, require

ROOT = Path(__file__).resolve().parents[2]
D = Decimal
getcontext().prec = 80
E18, E6 = D(10)**18, D(10)**6
TICKERS = ("TSLA", "NVDA", "META")
PROFILES = ("baseline", "fee_only", "p100", "p300", "p500", "p1000", "p500_stop_off")
CASES = [(profile, enabled) for profile in PROFILES for enabled in ((False,) if profile == "baseline" else (False, True))]
EXPECTED = {(ticker, profile, enabled) for ticker in TICKERS for profile, enabled in CASES}
LABELS = {"baseline": "无流量基线", "fee_only": "仅费用", "p100": "1%", "p300": "3%", "p500": "5%", "p1000": "10%", "p500_stop_off": "5% 无止损"}
NOT_DUE, ZERO = "0x47a2375f", "0x00000000"
CORE_FIELDS = {
    "ticker", "profile", "harvestEnabled", "index", "date", "elapsedSeconds", "evmTimestamp",
    "stockOracleUsdE18", "stockSpotUsdE18", "stockPoolLiquidity", "healthyAfterAction",
    "funStockE18", "funOracleUsdE18", "holderFunRaw", "lpStockRaw", "lpFunRaw",
    "treasuryStockRaw", "treasuryUsdgRaw", "treasuryNavOracleUsdE18", "treasuryBookedRaw",
    "treasuryUnbookedRaw", "treasuryBuybackRaw", "lotCount", "totalSupplyRaw",
    "actions", "stopActions", "tpActions", "dipActions", "buybacks", "burnedRaw",
    "buybackStockSpentRaw", "buybackOracleUsdgValueRaw", "executeNotDue", "buybackNotDue",
    "executeSuccess", "buybackSuccess", "actionCode", "executeRevert", "buybackRevert",
    "externalAssetsUsdgRaw", "uncollectedLpStockRaw", "uncollectedLpFunRaw",
    "collectedLpStockRaw", "collectedLpFunBurnedRaw", "externalGeneratedLpStockRaw",
    "externalGeneratedLpFunRaw", "buybackGeneratedLpStockRaw", "buybackGeneratedLpFunRaw",
    "traderUsdgInRaw", "traderUsdgOutRaw", "traderUsdgBalanceRaw", "traderStockBalanceRaw", "traderFunBalanceRaw",
    "flowPairs", "priceMarkDeltaPositiveUsdgRaw", "priceMarkDeltaNegativeUsdgRaw",
    "actionDeltaPositiveUsdgRaw", "actionDeltaNegativeUsdgRaw", "estimatedActionGas",
    "conversionGeneratedLpStockRaw", "conversionGeneratedLpFunRaw", "convertedFeeTokensRaw",
    "convertedFeeStockRaw", "hookStockDeliveredRaw", "hookTokenBurnRaw", "hookProtocolStockPaidRaw",
    "hookCreatorStockPaidRaw", "hookSweeperStockPaidRaw", "conversionCount", "pendingHookTokenFeesRaw",
    "hookUnsettledFunRaw", "hookUnsettledStockRaw", "hookOwedTreasuryStockRaw", "curveUnclaimedFeesRaw",
    "keeperStockRaw", "keeperUsdgRaw", "keeperFunRaw", "collectedLpOracleUsdgValueRaw",
    "dailyCollectedLpStockRaw", "dailyCollectedLpFunBurnedRaw", "dailyBuybackStockSpentRaw",
    "dailyBuybackOracleUsdgValueRaw", "maxStrategyStockInputOracleUsdRaw", "maxStrategyUsdgInputRaw",
    "maxBuybackStockInputOracleUsdRaw", "maxBuybackExternalAssetDustUsdgRaw",
}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_log(path):
    return gzip.decompress(path.read_bytes()).decode() if path.suffix == ".gz" else path.read_text()


def packed_gzip(data):
    output = io.BytesIO()
    with gzip.GzipFile(filename="", fileobj=output, mode="wb", mtime=0) as handle:
        handle.write(data)
    return output.getvalue()


def event_json(text):
    def no_float(value):
        raise ValueError(f"binary/scientific floating point in raw event: {value}")
    def unique_keys(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, f"duplicate event key {key}")
            result[key] = value
        return result
    event = json.loads(text, parse_float=no_float, parse_constant=no_float, object_pairs_hook=unique_keys)
    require(isinstance(event, dict), "event must be an object")
    for key, value in event.items():
        if type(value) is str and re.fullmatch(r"0|[1-9][0-9]*", value):
            event[key] = int(value)
        if type(event[key]) is int:
            require(-(2**255) <= event[key] < 2**256, f"out-of-range integer {key}")
    return event


def case_key(row):
    return row["ticker"], row["profile"], row["harvestEnabled"]


def parse(raw):
    require(bool(re.search(r"39 passed; 0 failed; 0 skipped", raw)), "all 39 fork tests must pass")
    require("[FAIL" not in raw and "EQUITY_UNEXPECTED_" not in raw, "unexpected fork failure")
    tests = re.findall(r"\[PASS\]\s+(testEquity_[A-Za-z0-9_]+)\(\)", raw)
    require(len(tests) == len(set(tests)) == 39, "wrong passing test set")
    output = {"ROW": [], "PROFILE": [], "VENUE": [], "DEPTH": []}
    for line in raw.splitlines():
        for label in output:
            marker = f"EQUITY_{label} "
            if marker in line:
                output[label].append(event_json(line.split(marker, 1)[1]))
                break
    require(len(output["ROW"]) == 9750, "39 cases must each contain 250 daily snapshots")
    require(len(output["PROFILE"]) == len(output["VENUE"]) == 39, "wrong profile/venue count")
    require(len(output["DEPTH"]) == 312, "expected eight actual depth probes per case")
    return output


def validate(events, data):
    require(tuple(data["selectedTickers"]) == TICKERS, "frozen ticker selection changed")
    metadata = {case_key(row): row for row in events["PROFILE"]}
    require(len(metadata) == 39 and set(metadata) == EXPECTED, "profile matrix incomplete")
    fork_blocks = {row["forkBlock"] for row in metadata.values()}
    require(fork_blocks == {128172359}, "frozen fork block changed")
    liquidity = {}
    for venue in events["VENUE"]:
        ticker = venue["ticker"]
        require(ticker in TICKERS, "unexpected venue ticker")
        require(venue["pool"].lower() == data["windows"][ticker]["pool"].lower(), "venue pool differs from verified listing")
        levels = [venue[key] for key in ("oldPositionLiquidity", "newPositionLiquidity", "activeLiquidityBefore", "activeLiquidityAfter")]
        require(all(type(value) is int and value > 0 for value in levels) and len(set(levels)) == 1, "local widening changed active liquidity")
        require(venue["newTickLower"] < venue["oldTickLower"] < venue["oldTickUpper"] < venue["newTickUpper"], "range not widened")
        require(ticker not in liquidity or liquidity[ticker] == levels[0], "depth differs across a ticker's cases")
        liquidity[ticker] = levels[0]
    rows_by_case = {}
    field_set = set(events["ROW"][0])
    require(CORE_FIELDS <= field_set, f"missing critical raw fields: {CORE_FIELDS-field_set}")
    for row in events["ROW"]:
        require(set(row) == field_set, "snapshot schema changed partway through replay")
        require(case_key(row) in EXPECTED, "unknown daily case")
        require(type(row["harvestEnabled"]) is bool, "harvest flag must be boolean")
        require(row["healthyAfterAction"] is True, "unhealthy end-of-day snapshot")
        for key, value in row.items():
            require(type(value) in (int, bool, str), f"unexpected raw value type in {key}")
            if type(value) is int:
                require(value >= 0, f"negative raw counter/balance {key}")
        rows_by_case.setdefault(case_key(row), []).append(row)
    require(set(rows_by_case) == EXPECTED, "daily case set differs from frozen matrix")
    for key, group in rows_by_case.items():
        ticker, profile, harvest = key
        meta = metadata[key]
        rung = {"p100": 100, "p300": 300, "p1000": 1000}.get(profile, 500)
        require([meta[name] for name in ("tp1Bps", "tp2Bps", "dipBps", "stopBps")] ==
                [rung, rung*2, rung, 0 if profile == "p500_stop_off" else rung], "profile parameters differ from approved matrix")
        require(meta["strategyEnabled"] == profile.startswith("p") and meta["flowEnabled"] == (profile != "baseline")
                and meta["buybackEnabled"] == (profile != "baseline"), "case behavior flags mismatch")
        require(meta["traderInitialUsdgRaw"] == 100_000*10**6 and meta["dailyBuyUsdgRaw"] == 100*10**6, "external cash budget mismatch")
        gates = {"lotBps": 2000, "maxSlippageBps": 100, "maxDeviationBps": 50, "bountyBps": 50,
                 "minLotUsdg": 5*10**6, "sellChunkUsdg": 2000*10**6, "buybackChunkUsdg": 500*10**6,
                 "buybackCooldown": 60, "maxBuybackImpactBps": 300, "funPoolFee": 3000}
        require(all(meta[name] == value for name, value in gates.items()), "frozen execution gates changed")
        group.sort(key=lambda row: row["index"])
        require([row["index"] for row in group] == list(range(250)), "duplicate or missing daily index")
        first = group[0]
        window = data["windows"][ticker]
        require(abs(first["treasuryNavOracleUsdE18"] - 10_000*10**18) <= 10**14, "initial treasury capital is not equal $10k within listing rounding")
        require(abs(first["externalAssetsUsdgRaw"] - 20_000*10**6) <= 200, "initial treasury plus LP-stock capital is not equal $20k within rounding")
        for index, row in enumerate(group):
            require(row["date"] == window["dates"][index] and row["stockOracleUsdE18"] == window["pricesE18"][index], "date/historical Close mismatch")
            require(row["elapsedSeconds"] == window["elapsedSeconds"][index] and row["evmTimestamp"] == first["evmTimestamp"] + row["elapsedSeconds"], "calendar-day spacing mismatch")
            require(row["stockPoolLiquidity"] == liquidity[ticker], "daily pool liquidity changed")
            price = row["stockOracleUsdE18"]
            require(row["funOracleUsdE18"] == row["funStockE18"]*price//10**18, "FUN oracle mark arithmetic mismatch")
            require(row["treasuryNavOracleUsdE18"] == row["treasuryStockRaw"]*price//10**18 + row["treasuryUsdgRaw"]*10**12, "treasury NAV arithmetic mismatch")
            require(sum(row[name] for name in ("treasuryBookedRaw", "treasuryUnbookedRaw", "treasuryBuybackRaw")) == row["treasuryStockRaw"], "treasury bucket conservation failed")
            require(row["externalAssetsUsdgRaw"] == (row["treasuryStockRaw"] + row["lpStockRaw"] + row["uncollectedLpStockRaw"])*price//10**30 + row["treasuryUsdgRaw"], "external asset reader does not reconcile")
            require(row["holderFunRaw"] == first["holderFunRaw"], "passive holder traded")
            require(row["traderFunBalanceRaw"] == 0 and row["traderStockBalanceRaw"] == 0, "external round-trip left residual tokens")
            require(row["traderUsdgBalanceRaw"] == meta["traderInitialUsdgRaw"] - row["traderUsdgInRaw"] + row["traderUsdgOutRaw"], "trader cash conservation failed")
            require(row["flowPairs"] == (0 if profile == "baseline" else index), "external flow schedule differs")
            require(row["traderUsdgInRaw"] == row["flowPairs"] * meta["dailyBuyUsdgRaw"], "daily order notional differs")
            require(row["actions"] == row["stopActions"] + row["tpActions"] + row["dipActions"], "strategy action accounting failed")
            require(row["actions"] + row["executeNotDue"] == (index if profile.startswith("p") else 0), "daily strategy opportunities differ")
            require(row["buybacks"] + row["buybackNotDue"] == (index if profile != "baseline" else 0), "daily buyback opportunities differ")
            require(row["externalAssetsUsdgRaw"] == first["externalAssetsUsdgRaw"] + row["priceMarkDeltaPositiveUsdgRaw"] - row["priceMarkDeltaNegativeUsdgRaw"] + row["actionDeltaPositiveUsdgRaw"] - row["actionDeltaNegativeUsdgRaw"], "external NAV attribution does not reconcile")
            stock_generated = row["externalGeneratedLpStockRaw"] + row["buybackGeneratedLpStockRaw"] + row["conversionGeneratedLpStockRaw"]
            fun_generated = row["externalGeneratedLpFunRaw"] + row["buybackGeneratedLpFunRaw"] + row["conversionGeneratedLpFunRaw"]
            require(row["collectedLpStockRaw"] + row["uncollectedLpStockRaw"] == stock_generated, "LP-stock fees not conserved by source")
            require(row["collectedLpFunBurnedRaw"] + row["uncollectedLpFunRaw"] == fun_generated, "LP-FUN fees not conserved by source")
            require(row["totalSupplyRaw"] + row["burnedRaw"] + row["collectedLpFunBurnedRaw"] + row["hookTokenBurnRaw"] == meta["initialSupplyRaw"], "three-source supply/burn conservation failed")
            require(row["curveUnclaimedFeesRaw"] == first["curveUnclaimedFeesRaw"], "launch curve fees were claimed")
            require(all(row[name] == 0 for name in ("hookUnsettledFunRaw", "hookUnsettledStockRaw", "hookOwedTreasuryStockRaw")), "hook fees were not fully settled")
            require(row["conversionCount"] == row["flowPairs"], "owner conversion schedule differs")
            require(row["executeRevert"] in (ZERO, NOT_DUE) and row["buybackRevert"] in (ZERO, NOT_DUE), "unexpected keeper rejection")
            require(row["actionCode"] in (0, 1, 2) if row["executeSuccess"] else row["actionCode"] == 255, "strategy action code mismatch")
            if not harvest:
                require(row["collectedLpStockRaw"] == row["collectedLpFunBurnedRaw"] == 0, "harvest-off collected LP fees")
            if profile == "baseline":
                for name in ("funStockE18", "treasuryStockRaw", "lpStockRaw", "lpFunRaw", "totalSupplyRaw"):
                    require(row[name] == first[name], f"zero-flow baseline changed {name}")
            if index:
                previous = group[index-1]
                require(row["actions"] - previous["actions"] == int(row["executeSuccess"]), "execute success counter mismatch")
                require(row["buybacks"] - previous["buybacks"] == int(row["buybackSuccess"]), "buyback success counter mismatch")
                require(row["buybackStockSpentRaw"] >= previous["buybackStockSpentRaw"] and row["burnedRaw"] >= previous["burnedRaw"], "cumulative spending/burn went backwards")
                for cumulative, daily in (("buybackStockSpentRaw", "dailyBuybackStockSpentRaw"),
                                          ("buybackOracleUsdgValueRaw", "dailyBuybackOracleUsdgValueRaw"),
                                          ("collectedLpStockRaw", "dailyCollectedLpStockRaw"),
                                          ("collectedLpFunBurnedRaw", "dailyCollectedLpFunBurnedRaw")):
                    require(row[cumulative]-previous[cumulative] == row[daily], "daily spending/collection does not reconcile")
        if profile != "baseline" and not harvest:
            paired = metadata[(ticker, profile, True)]
            for name in ("treasury", "token", "vault", "nonce", "initialHolderFunRaw", "initialSupplyRaw", "initialExternalAssetsUsdgRaw"):
                require(meta[name] == paired[name], f"matched harvest cases have different starting {name}")
    depth_keys = set()
    for probe in events["DEPTH"]:
        key = case_key(probe)
        require(key in EXPECTED, "unknown depth-probe case")
        identity = (*key, probe["buy"], probe["inputNotionalUsdgRaw"])
        require(type(probe["buy"]) is bool, "depth direction must be boolean")
        require(identity not in depth_keys, "duplicate depth probe")
        depth_keys.add(identity)
        require(probe["inputNotionalUsdgRaw"] in (100*10**6, 1000*10**6, 2000*10**6, 10000*10**6), "unexpected depth notional")
        input_value, output_value = probe["inputOracleUsdgRaw"], probe["outputOracleUsdgRaw"]
        require(input_value > output_value > 0, "invalid actual depth output")
        require(probe["effectiveCostPpm"] == (input_value-output_value)*1_000_000//input_value, "depth cost arithmetic mismatch")
        require(probe["effectiveCostPpm"] <= probe["feePpm"]+1000, "depth fee-adjusted impact exceeds 10 bps")
    return rows_by_case, metadata, liquidity, next(iter(fork_blocks))


def depth_coverage(events, groups):
    result = []
    for ticker in TICKERS:
        probes = [row for row in events["DEPTH"] if row["ticker"] == ticker]
        terminals = [group[-1] for key, group in groups.items() if key[0] == ticker]
        maximum = max(row["inputNotionalUsdgRaw"] for row in probes)
        stock_max = max(row["maxStrategyStockInputOracleUsdRaw"] for row in terminals)
        cash_max = max(row["maxStrategyUsdgInputRaw"] for row in terminals)
        result.append({"ticker": ticker, "actualProbeMaxNotionalUsdg": display(D(maximum)/E6, 6),
                       "maxStrategyStockReductionOracleUsdgIncludingBounty": display(D(stock_max)/E6, 6),
                       "maxStrategyCashReductionUsdgIncludingBounty": display(D(cash_max)/E6, 6),
                       "coversObservedStrategySizeByNotional": maximum >= max(stock_max, cash_max),
                       "probeFeePpm": probes[0]["feePpm"],
                       "maximumProbeCostPpm": max(row["effectiveCostPpm"] for row in probes),
                       "maximumProbeCostAboveFeePpm": max(row["effectiveCostPpm"]-row["feePpm"] for row in probes),
                       "scope": "Actual V3 stock/USDG swaps at first Close; size coverage only, not a historical execution guarantee or a V4 FUN buyback quote."})
    return result


def enrich(row, first):
    out = dict(row)
    p, spot = D(row["stockOracleUsdE18"])/E18, D(row["stockSpotUsdE18"])/E18
    out["valuationBasis"] = "historical_close_oracle_mark"
    out["stockOracleUsd"] = display(p, 18)
    out["funUsdOracleMark"] = display(D(row["funOracleUsdE18"])/E18, 18)
    out["funUsdActualSpotMark"] = display(D(row["funStockE18"])/E18*spot, 18)
    out["treasuryNavUsdOracleMark"] = display(D(row["treasuryNavOracleUsdE18"])/E18, 18)
    out["externalAssetsUsdg"] = display(D(row["externalAssetsUsdgRaw"])/E6, 6)
    out["traderCashChangeUsdg"] = display(D(row["traderUsdgOutRaw"]-row["traderUsdgInRaw"])/E6, 6)
    out["spotDeviationFromOracleBps"] = display((spot/p-1)*10000, 12)
    for field, label in (("stockOracleUsdE18", "stockIndex"), ("funStockE18", "funStockIndex"),
                         ("funOracleUsdE18", "funOracleMarkIndex"), ("treasuryNavOracleUsdE18", "treasuryNavIndex"),
                         ("externalAssetsUsdgRaw", "externalAssetsIndex")):
        out[label] = display(D(row[field])/D(first[field])*100)
    return out


def summary(group):
    first, last = group[0], group[-1]
    dates = [row["date"] for row in group]
    result = {"ticker": first["ticker"], "profile": first["profile"], "harvestEnabled": first["harvestEnabled"],
              "firstDate": first["date"], "lastDate": last["date"], "observations": len(group),
              "valuationBasis": "historical_close_oracle_mark",
              "initialTreasuryNavUsd": first["treasuryNavUsdOracleMark"], "finalTreasuryNavUsd": last["treasuryNavUsdOracleMark"],
              "initialExternalAssetsUsdg": first["externalAssetsUsdg"], "finalExternalAssetsUsdg": last["externalAssetsUsdg"],
              "traderCashChangeUsdg": last["traderCashChangeUsdg"],
              "cumulativeBuybackStock": display(D(last["buybackStockSpentRaw"])/E18, 18),
              "cumulativeBuybackUsdAtExecutionMarks": display(D(last["buybackOracleUsdgValueRaw"])/E6, 6),
              "buybackBurnedFun": display(D(last["burnedRaw"])/E18, 18),
              "collectedLpBurnedFun": display(D(last["collectedLpFunBurnedRaw"])/E18, 18),
              "hookBurnedFun": display(D(last["hookTokenBurnRaw"])/E18, 18),
              "hookConvertedFun": display(D(last["convertedFeeTokensRaw"])/E18, 18),
              "hookConvertedStock": display(D(last["convertedFeeStockRaw"])/E18, 18),
              "hookStockDeliveredToTreasury": display(D(last["hookStockDeliveredRaw"])/E18, 18),
              "collectedLpStock": display(D(last["collectedLpStockRaw"])/E18, 18),
              "collectedLpUsdAtExecutionMarks": display(D(last["collectedLpOracleUsdgValueRaw"])/E6, 6),
              "terminalClaimableLpStock": display(D(last["uncollectedLpStockRaw"])/E18, 18),
              "terminalClaimableLpFun": display(D(last["uncollectedLpFunRaw"])/E18, 18),
              "priceMarkContributionUsdg": display(D(last["priceMarkDeltaPositiveUsdgRaw"]-last["priceMarkDeltaNegativeUsdgRaw"])/E6, 6),
              "actionContributionUsdg": display(D(last["actionDeltaPositiveUsdgRaw"]-last["actionDeltaNegativeUsdgRaw"])/E6, 6)}
    for field, label in (("stockOracleUsdE18", "stockCloseChangePercent"), ("funStockE18", "funStockChangePercent"),
                         ("funOracleUsdE18", "funOracleMarkChangePercent"), ("treasuryNavOracleUsdE18", "treasuryNavChangePercent"),
                         ("externalAssetsUsdgRaw", "externalAssetsChangePercent")):
        result[label] = display(change_percent(first[field], last[field]))
        result[label.replace("ChangePercent", "MaxDailyDrawdown")] = drawdown([row[field] for row in group], dates)
    for field in ("actions", "stopActions", "tpActions", "dipActions", "buybacks", "executeNotDue", "buybackNotDue", "flowPairs", "conversionCount", "estimatedActionGas"):
        result[field] = last[field]
    result["terminalRaw"] = dict(last)
    return result


def matched_comparisons(summaries):
    by_key = {case_key(row): row for row in summaries}
    harvest, strategy = [], []
    for ticker in TICKERS:
        for profile in PROFILES[1:]:
            off, on = by_key[(ticker, profile, False)], by_key[(ticker, profile, True)]
            harvest.append({"ticker": ticker, "profile": profile,
                            "comparison": "harvest_on_minus_off_same_strategy_and_funded_flow",
                            "externalAssetsDeltaUsdg": display(D(on["finalExternalAssetsUsdg"])-D(off["finalExternalAssetsUsdg"]), 6),
                            "externalAssetsChangeDeltaPercentagePoints": display(D(on["externalAssetsChangePercent"])-D(off["externalAssetsChangePercent"])),
                            "funOracleMarkChangeDeltaPercentagePoints": display(D(on["funOracleMarkChangePercent"])-D(off["funOracleMarkChangePercent"])),
                            "traderCashChangeDeltaUsdg": display(D(on["traderCashChangeUsdg"])-D(off["traderCashChangeUsdg"]), 6)})
        for profile in PROFILES[2:]:
            for enabled in (False, True):
                control, actual = by_key[(ticker, "fee_only", enabled)], by_key[(ticker, profile, enabled)]
                strategy.append({"ticker": ticker, "profile": profile, "harvestEnabled": enabled,
                                 "comparison": "strategy_minus_no_execute_with_same_funded_flow_and_harvest_policy",
                                 "externalAssetsDeltaUsdg": display(D(actual["finalExternalAssetsUsdg"])-D(control["finalExternalAssetsUsdg"]), 6),
                                 "externalAssetsChangeDeltaPercentagePoints": display(D(actual["externalAssetsChangePercent"])-D(control["externalAssetsChangePercent"])),
                                 "funOracleMarkChangeDeltaPercentagePoints": display(D(actual["funOracleMarkChangePercent"])-D(control["funOracleMarkChangePercent"]))})
    return {"harvestMatchedPairs": harvest, "strategyVersusMatchedFlowControl": strategy}


LIMITS = [
    "2025-01-02 Close to 2025-12-31 Close only; not a standard year-on-year return or a continuous multi-year portfolio. Universe selection is descriptive in-sample volume/volatility screening, not prospective selection or out-of-sample validation.",
    "All eight enabled deployed equities are screened using median Close×Volume >= $2bn and sample daily-log-return annualized volatility >=30%; the three highest qualifying volatilities are selected without ranking returns. Equity turnover is not onchain depth.",
    "Close is already split-adjusted. NVDA and META pay dividends, but this price-only stock-token model does not distribute dividends. Adj Close is retained as source data, not used as an executable/feed price.",
    "Provider timestamps label session open, not Close availability. Daily prices are idealized known Close inputs applied to a synthetic open market; 601-second settling is not real historical execution latency or intraday trading.",
    "Current pinned deployed contracts run only in an ephemeral fork. Local listing price is normalized to approximately $10,000 treasury stock and $10,000 locked LP-stock principal, with the same initial configuration for matched harvest pairs.",
    "Current narrow V3 positions are locally widened to full range at exactly the original active liquidity. Actual fork swaps probe $100/$1k/$2k/$10k in both directions at each ticker's first 2025 Close. The separate current-state analytical estimates are not actual quotes and use a later block and different price.",
    "All nonbaseline cases use the same once-prefunded $100,000 external trader and 249 daily round trips: buy $100 tUSDG of FUN and sell exactly the received net FUN. No artificial tax or fee credit is injected; trader cost is reported separately.",
    "Fee-only controls disable strategy execute but retain the matched flow, hook-fee settlement/conversion, and harvest on/off policy. They separate strategy effects from externally funded order flow; the zero-flow baseline alone cannot establish strategy alpha.",
    "Both harvest arms settle hook fees daily: sweep, owner conversion with a 99% snapshot-quote floor, 300-second deadline and 50bps sqrt-price limit, then sweep again. Partial conversion may remain pending. Initial launch curve fees remain unclaimed and outside the graduated-period reader assets/action budget.",
    "LP harvest reallocates already-owned stock fees and burns collected FUN fees. External-trader-generated, hook-conversion-generated and own-buyback-generated LP fees must be distinguished; recycling one's own fees is not new external income.",
    "External asset value counts treasury stock/cash plus locked LP stock and claimable LP-stock fees, excluding self-issued FUN. Treasury NAV, FUN marginal marks, reader external assets and the external trader are separate accounts; none is a fabricated total-investor return or liquidation guarantee.",
    "Oracle Close marks and actual post-action V3 spot marks are separate. Buyback spending is reported as stock and its mark at each execution; adding it back does not create a validated investor total return.",
    "Each daily noninitial point allows at most one strategy execution and one buyback. No intraday trigger replay, depth history, real fee-volume history, dividend cash, MEV or real gas bill is modeled. Gas counters are local call estimates, including harness control and excluding transaction intrinsic gas.",
    "The complete frozen parameter matrix is shown. Any strongest endpoint is in-sample scenario output, not an optimal parameter recommendation or evidence of future performance.",
]


def serialize_raw(row):
    return {key: str(value) if type(value) is int else value for key, value in row.items()}


def write_csv(path, rows):
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader(); writer.writerows(rows)


def export(result, rows, raw, output):
    output.mkdir(parents=True, exist_ok=True)
    stream = "".join(json.dumps(serialize_raw(row), separators=(",", ":"), ensure_ascii=False)+"\n" for row in rows).encode()
    (output/"daily.jsonl.gz").write_bytes(packed_gzip(stream))
    (output/"forge.log.gz").write_bytes(packed_gzip(raw.encode()))
    write_csv(output/"daily.csv", rows)
    safe = dict(result)
    safe["profiles"] = [serialize_raw(row) for row in result["profiles"]]
    safe["actualForkDepthProbes"] = [serialize_raw(row) for row in result["actualForkDepthProbes"]]
    safe["summaries"] = []
    for row in result["summaries"]:
        item = dict(row)
        item["terminalRaw"] = serialize_raw(item["terminalRaw"])
        item["estimatedActionGas"] = str(item["estimatedActionGas"])
        safe["summaries"].append(item)
    (output/"results.json").write_text(json.dumps(safe, separators=(",", ":"), ensure_ascii=False)+"\n")
    flat = []
    for row in result["summaries"]:
        item = {key: value for key, value in row.items() if not isinstance(value, dict)}
        for key, value in row.items():
            if key.endswith("MaxDailyDrawdown"):
                item[key+"Percent"] = value["percent"]
                item[key+"PeakDate"] = value["peakDate"]
                item[key+"TroughDate"] = value["troughDate"]
        flat.append(item)
    write_csv(output/"summary.csv", flat)
    for name, pairs in result["matchedComparisons"].items():
        write_csv(output/(name+".csv"), pairs)
    lines = ["# 2025 多股票参数与 LP 手续费回放", "", "39 个隔离实验、9750 个日末快照，全部真实合约本地调用；没有公开广播。", "",
             "初始国库股票与 LP 股票本金各约 10,000 tUSDG。比较从 2025 首个 Close 到最后一个 Close；分红未派发，所有参数均为样本内比较。", "",
             "| 股票 | 参数 | LP harvest | 股票价格变化 | FUN 美元标价变化 | 国库 NAV 变化 | 外部资产变化 | 外部资产最大日末回撤 | execute / buyback | 外部交易者现金变化 |",
             "|---|---|---|---:|---:|---:|---:|---:|---:|---:|"]
    for row in result["summaries"]:
        lines.append(f"| {row['ticker']} | {LABELS[row['profile']]} | {'on' if row['harvestEnabled'] else 'off'} | " +
                     " | ".join(display(D(row[key]), 3)+"%" for key in ("stockCloseChangePercent", "funOracleMarkChangePercent", "treasuryNavChangePercent", "externalAssetsChangePercent"))+
                     f" | {display(D(row['externalAssetsMaxDailyDrawdown']['percent']), 3)}% | {row['actions']} / {row['buybacks']} | {row['traderCashChangeUsdg']} |")
    lines += ["", "## 解释边界", ""] + ["- "+text for text in LIMITS]
    lines += ["", "## 归档", "", "- results.json：配置、39 组汇总、on/off 与同流量控制组差值。", "- daily.jsonl.gz：全部原始字段及派生计价，整数为十进制字符串；daily.csv 为表格形式。", "- forge.log.gz：原始 Foundry 输出，gzip mtime=0；SHA256SUMS 覆盖归档文件。",
              f"- fork 区块 {result['forkBlock']}；另行当前深度筛选区块 {result['screeningSnapshotBlock']}。实际 fork probes 与当前状态解析估计分别存储。"]
    (output/"GENERATED_REPORT.md").write_text("\n".join(lines)+"\n")


def charts(result, rows, output):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    from matplotlib.colors import TwoSlopeNorm, Normalize
    font = Path("/System/Library/Fonts/STHeiti Light.ttc")
    if font.exists():
        font_manager.fontManager.addfont(font)
        plt.rcParams["font.family"] = font_manager.FontProperties(fname=font).get_name()
    plt.rcParams.update({"figure.facecolor":"#f8fafc", "savefig.facecolor":"#f8fafc", "axes.unicode_minus":False,
                         "text.color":"#172b4d", "font.size":10, "svg.fonttype":"path", "svg.hashsalt":"hedgefun-equity-2025"})
    def save(fig, name):
        for suffix in ("png", "svg"):
            kwargs = {"metadata": {"Date": None}} if suffix == "svg" else {}
            fig.savefig(output/(name+"."+suffix), dpi=180, bbox_inches="tight", **kwargs)
    output.mkdir(parents=True, exist_ok=True)
    lookup = {case_key(row): row for row in result["summaries"]}
    fig, axes = plt.subplots(1,3,figsize=(15.5,10.5))
    fig.suptitle("2025 日线回放：完整参数矩阵",fontsize=20,fontweight="bold",x=.07,ha="left",y=.99)
    for ax, field, title in zip(axes, ("funOracleMarkChangePercent","treasuryNavChangePercent","externalAssetsChangePercent"),
                               ("FUN 美元边际标价变化", "国库剩余资产变化", "外部资产变化（不计自身FUN）")):
        grid = [[float(lookup[(ticker, profile, enabled)][field]) for ticker in TICKERS] for profile,enabled in CASES]
        low, high = min(min(row) for row in grid), max(max(row) for row in grid)
        norm = TwoSlopeNorm(vmin=low,vcenter=0,vmax=high) if low<0<high else Normalize(vmin=min(0,low),vmax=max(.01,high))
        ax.imshow(grid,cmap="RdYlGn",norm=norm,aspect="auto")
        ax.set_xticks(range(3),TICKERS);ax.xaxis.tick_top()
        ax.set_yticks(range(len(CASES)),[LABELS[p]+("" if p=="baseline" else (" · LP on" if h else " · LP off")) for p,h in CASES])
        ax.set_title(title,pad=28,fontsize=11)
        for y, line in enumerate(grid):
            for x, value in enumerate(line):ax.text(x,y,f"{value:+.1f}%",ha="center",va="center",fontsize=10,color="#172b4d")
    fig.text(.07,.028,"每个实验独立重置；首日Close→末日Close，不含分红。各图使用独立色标；完整数值见summary.csv。\n"
             "每日同额$100真实买入并卖回；‘仅费用’组不执行股票策略。LP on/off均处理hook费用。\n"
             "国库NAV包含回购支出；外部资产包括国库现金/股票、锁定LP股票及未领LP股票费，排除自发行FUN。不是总回报或最优参数建议。",fontsize=10,color="#536579",linespacing=1.5)
    fig.subplots_adjust(top=.88,bottom=.17,left=.12,right=.98,wspace=.6)
    save(fig, "full-parameter-matrix")
    plt.close(fig)
    fig,axes=plt.subplots(1,2,figsize=(13.5,6.6))
    fig.suptitle("LP 收集开关：相同策略与订单流下的差值",fontsize=19,fontweight="bold",x=.06,ha="left",y=.99)
    pairmap={(r["ticker"],r["profile"]):r for r in result["matchedComparisons"]["harvestMatchedPairs"]}
    for ax,field,title in zip(axes,("externalAssetsDeltaUsdg","funOracleMarkChangeDeltaPercentagePoints"),("期末外部资产：on − off（tUSDG）","FUN标价变化：on − off（百分点）")):
        for i,ticker in enumerate(TICKERS):
            values=[float(pairmap[(ticker,profile)][field]) for profile in PROFILES[1:]]
            ax.bar([j+(i-1)*.24 for j in range(6)],values,width=.23,label=ticker,color=("#4e79a7","#087f8c","#d77920")[i])
        ax.axhline(0,color="#64748b",lw=.8);ax.set_xticks(range(6),[LABELS[p] for p in PROFILES[1:]],rotation=20)
        ax.set_title(title,pad=14);ax.grid(axis="y",alpha=.2);ax.legend(frameon=False)
    fig.text(.06,.025,"差值包含收集后的真实回购及其后续影响；收集本身只移动既有资产，不创造收入。\n外部交易者生成、hook转换生成、自身回购生成的LP费用单独记录。这里只展示样本内机制差异。",fontsize=10,color="#536579")
    fig.subplots_adjust(top=.82,bottom=.23,left=.08,right=.97,wspace=.26)
    save(fig, "matched-lp-harvest-difference")
    plt.close(fig)
    import matplotlib.dates as mdates
    fig, axes = plt.subplots(3, 2, figsize=(14, 12), sharex=True)
    fig.suptitle("2025 每日日末路径：LP 收集开启", fontsize=20, fontweight="bold", x=.06, ha="left", y=.99)
    palette = ("#94a3b8", "#172b4d", "#2563eb", "#d97706", "#16a34a", "#9333ea", "#dc2626")
    by_key = {}
    for row in rows:
        by_key.setdefault(case_key(row), []).append(row)
    for i, ticker in enumerate(TICKERS):
        for j, (field, title) in enumerate((("funOracleMarkIndex", "FUN 美元边际标价"), ("externalAssetsIndex", "外部资产（国库 + LP 股票及未领股票费）"))):
            ax = axes[i][j]
            for k, profile in enumerate(PROFILES):
                group = by_key[(ticker, profile, profile != "baseline")]
                ax.plot([date.fromisoformat(row["date"]) for row in group], [float(row[field]) for row in group],
                        label=LABELS[profile], color=palette[k], linewidth=1.4,
                        linestyle="--" if profile == "baseline" else (":" if profile == "fee_only" else "-"))
            ax.set_title(ticker+" · "+title, fontsize=11, loc="left")
            ax.axhline(100, color="#64748b", lw=.6, alpha=.5)
            ax.set_ylabel("首日 = 100"); ax.grid(alpha=.15)
            ax.xaxis.set_major_locator(mdates.MonthLocator(bymonth=(1,4,7,10)))
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    handles, labels = axes[0][0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", bbox_to_anchor=(.5,.06), ncol=7, frameon=False)
    fig.text(.06,.013,"除无流量基线外，每日同额$100买入并卖回；仅费用控制组不execute。各实验独立重置，均处理hook税费。\n"
             "Close价格回放，不含分红或真实gas；外部资产包含锁定资产且排除自身FUN，不等于可赎回NAV或投资者总回报。", fontsize=10, color="#536579")
    fig.subplots_adjust(top=.93, bottom=.13, left=.08, right=.97, hspace=.3, wspace=.2)
    save(fig, "daily-harvest-on-paths")
    plt.close(fig)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log",type=Path,default=ROOT/"artifacts/equity-fee-history-20261003/forge.log")
    parser.add_argument("--data",type=Path,default=ROOT/"contractV2/data/equity-history-2025.json")
    parser.add_argument("--source",type=Path,default=ROOT/"contractV2/test/EquityHistoricalFeeReplayFork.t.sol")
    parser.add_argument("--output",type=Path,default=ROOT/"contractV2/deploy/equity-history-2025-2026-10-03")
    parser.add_argument("--no-charts",action="store_true")
    args=parser.parse_args()
    data=json.loads(args.data.read_text())
    expected_data=rebuild_data(args.data.parent/"equity-history-2025-sources",ROOT/"contractV2/deploy/testnet-v2-fresh-creator.json",args.data.parent/"equity-depth-snapshot.json")
    require(data==expected_data,"canonical source/selection data does not reproduce from saved raw inputs")
    raw=read_log(args.log);events=parse(raw)
    groups,metadata,liquidity,block=validate(events,data)
    rows=[];summaries=[]
    for ticker in TICKERS:
        for profile,enabled in CASES:
            group=groups[(ticker,profile,enabled)]
            daily=[enrich(row,group[0]) for row in group]
            rows.extend(daily);summaries.append(summary(daily))
    result={"schema":"hedgefun-equity-2025-fee-comparison-v1","broadcast":False,"chainId":46630,"forkBlock":block,
            "testsPassed":39,"measuredSnapshots":9750,"actualDepthProbes":312,"selectedTickers":list(TICKERS),
            "screeningSnapshotBlock":data["onchainDepthSnapshot"]["block"],"screeningPolicy":data["selectionPolicy"],
            "currentDepthEstimateBasis":"Separate analytical constant-L integer estimates including fees, not executed quotes; see contractV2/data/equity-depth-snapshot.json. Actual executed probes below use the earlier frozen fork and historical first Close.",
            "screeningMetrics":{t:v["screening"] for t,v in data["candidates"].items()},
            "fixedForkLiquidity":{key:str(value) for key,value in liquidity.items()},
            "currentSnapshotLiquidityMatchesFork":{t:int(data["onchainDepthSnapshot"]["pools"][t]["activeLiquidity"])==liquidity[t] for t in TICKERS},
            "valuationBasis":"historical_close_oracle_mark","profiles":list(metadata.values()),"actualForkDepthProbes":events["DEPTH"],
            "actualDepthCoverage":depth_coverage(events,groups),
            "summaries":summaries,"matchedComparisons":matched_comparisons(summaries),"assumptionsAndLimits":LIMITS,
            "dailyRowsFile":"daily.jsonl.gz","dailyRowsEncoding":"gzip mtime=0; one JSON object per line; integer raw measurements encoded as decimal strings",
            "provenance":{"normalizedDataSha256":digest(args.data),"harnessSha256":digest(args.source),"readerSourceSha256":digest(ROOT/"contractV2/src/v2/V2FundAssetReader.sol"),
                          "currentDepthSnapshotSha256":digest(args.data.parent/"equity-depth-snapshot.json"),
                          "rawUncompressedLogSha256":hashlib.sha256(raw.encode()).hexdigest(),"reportToolSha256":digest(Path(__file__))}}
    export(result,rows,raw,args.output)
    if not args.no_charts:charts(result,rows,args.output/"charts")
    paths=sorted(p for p in args.output.rglob("*") if p.is_file() and p.name!="SHA256SUMS")
    (args.output/"SHA256SUMS").write_text("".join(f"{digest(p)}  {p.relative_to(args.output)}\n" for p in paths))
    print(json.dumps({"cases":len(summaries),"rows":len(rows),"output":str(args.output)},indent=2))


if __name__=="__main__":main()

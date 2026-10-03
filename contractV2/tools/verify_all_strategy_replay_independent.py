#!/usr/bin/env python3
"""Independent integer/Decimal audit of the 2025 all-strategy replay raw log.

This verifier does not import the harness, normalizer, or exporter's formulas.
It never uses RPC, signs transactions, or rewrites replay evidence.
"""
from __future__ import annotations

import argparse
import csv
from collections import Counter, defaultdict
from decimal import Decimal as D, getcontext
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import re

getcontext().prec = 80
ROOT = Path(__file__).resolve().parents[2]
E18 = 10**18
E30 = 10**30
PROFILES = ("baseline", "fee_only", "all_in_p500", "pure_buyback", "fixed70_payout0", "fixed70_payout50", "nav70_payout0", "nav70_payout50", "cycle_p500")
TICKERS = ("TSLA", "NVDA", "META")
NOT_DUE = "0x47a2375f"
ZERO = "0x00000000"


def read_bytes(path: Path) -> bytes:
    return gzip.decompress(path.read_bytes()) if path.suffix == ".gz" else path.read_bytes()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def n(row: dict, field: str) -> int:
    return int(row[field])


def key(row: dict) -> tuple[str, str, bool]:
    return row["ticker"], row["profile"], row["harvestEnabled"]


def change(rows: list[dict], field: str) -> str:
    return str((D(n(rows[-1], field)) / n(rows[0], field) - 1) * 100)


def mdd(rows: list[dict], field: str) -> dict:
    peak, peak_i = n(rows[0], field), 0
    largest, pair = D(0), (0, 0)
    for i, row in enumerate(rows):
        value = n(row, field)
        if value > peak:
            peak, peak_i = value, i
        loss = D(peak - value) / peak * 100
        if loss > largest:
            largest, pair = loss, (peak_i, i)
    return {"percent": str(largest), "peakDate": rows[pair[0]]["date"],
            "troughDate": rows[pair[1]]["date"], "peakIndex": pair[0], "troughIndex": pair[1]}


def audit_export(directory: Path, result: dict, groups: dict, rows: list, profiles: dict, depths: list, log: str, check) -> dict:
    document = json.loads((directory / "results.json").read_text())
    exported = [json.loads(line) for line in read_bytes(directory / "daily.jsonl.gz").decode().splitlines()]
    row_key = lambda row: (*key(row), n(row, "index"))
    raw_map = {row_key(row): row for row in rows}
    export_map = {row_key(row): row for row in exported}
    check(len(exported) == len(export_map) == 12750 and set(raw_map) == set(export_map), "export_exact_daily_keyset")
    check(hashlib.sha256(read_bytes(directory / "forge.log.gz")).hexdigest() == hashlib.sha256(log.encode()).hexdigest(), "export_archived_log_exact")
    for name in ("daily.jsonl.gz", "forge.log.gz"):
        check((directory / name).read_bytes()[4:8] == b"\0\0\0\0", "export_deterministic_gzip_mtime", name)
    provenance = document["provenance"]
    for export_field, evidence in (("normalizedDataSha256", "data"), ("harnessSha256", "harness")):
        check(provenance[export_field] == result["evidence"][evidence]["sha256"], "export_provenance", export_field)
    check(provenance["rawUncompressedLogSha256"] == result["evidence"]["log"]["uncompressedSha256"], "export_raw_log_provenance")
    check(provenance["reportToolSha256"] == sha(ROOT / "contractV2/tools/all_strategy_history_report.py"), "export_tool_provenance")
    scalar_tolerance, percentage_tolerance = D("5e-19"), D("5.1e-11")
    max_errors: dict[str, D] = defaultdict(lambda: D(0))
    derived_count = raw_count = 0
    for k, r in raw_map.items():
        e, first = export_map[k], groups[k[:3]][0]
        for field, value in r.items():
            check(str(e[field]) == str(value), "export_raw_field_exact", (*k, field))
            raw_count += 1
        derived = {
            "stockOracleUsd": D(n(r, "stockOracleUsdE18")) / E18,
            "funUsdOracleMark": D(n(r, "funOracleUsdE18")) / E18,
            "funUsdActualSpotMark": D(n(r, "funStockE18")) * n(r, "stockSpotUsdE18") / E18**2,
            "treasuryNavUsdOracleMark": D(n(r, "treasuryNavOracleUsdE18")) / E18,
            "externalAssetsUsdg": D(n(r, "externalAssetsUsdgRaw")) / 10**6,
            "traderCashChangeUsdg": D(n(r, "traderUsdgBalanceRaw") - 100_000 * 10**6) / 10**6,
            "spotDeviationFromOracleBps": D(n(r, "stockSpotUsdE18") - n(r, "stockOracleUsdE18")) / n(r, "stockOracleUsdE18") * 10000,
        }
        for label, field in (("stockIndex", "stockOracleUsdE18"), ("funStockIndex", "funStockE18"),
                             ("funOracleMarkIndex", "funOracleUsdE18"), ("treasuryNavIndex", "treasuryNavOracleUsdE18"),
                             ("externalAssetsIndex", "externalAssetsUsdgRaw")):
            derived[label] = D(n(r, field)) / n(first, field) * 100
        for label, value in derived.items():
            error = abs(D(e[label]) - value)
            max_errors[label] = max(error, max_errors[label])
            tolerance = percentage_tolerance if label.endswith("Index") or label == "spotDeviationFromOracleBps" else scalar_tolerance
            check(error <= tolerance, "export_derived_field_independent", (*k, label, str(error)))
            derived_count += 1
        check(e["valuationBasis"] == "historical_close_oracle_mark", "export_daily_valuation_label")
    export_profiles = {key(p): p for p in document["profiles"]}
    check(set(export_profiles) == set(profiles), "export_profile_keys")
    for k, p in profiles.items():
        check(all(str(export_profiles[k][field]) == str(value) for field, value in p.items()), "export_actual_profile_exact", k)
    depth_id = lambda q: (*key(q), q["buy"], n(q, "inputNotionalUsdgRaw"))
    export_depth = {depth_id(q): q for q in document["actualForkDepthProbes"]}
    check(len(export_depth) == len(depths) == 408, "export_depth_count")
    for q in depths:
        check(all(str(export_depth[depth_id(q)][f]) == str(v) for f, v in q.items()), "export_actual_depth_exact", depth_id(q))
    summaries = {key(s): s for s in document["summaries"]}
    check(set(summaries) == set(groups), "export_summary_group_keys")
    metric_labels = (("stockClose", "stockOracleUsdE18"), ("funStock", "funStockE18"),
                     ("funOracleMark", "funOracleUsdE18"), ("treasuryNav", "treasuryNavOracleUsdE18"),
                     ("externalAssets", "externalAssetsUsdgRaw"))
    for k, rs in groups.items():
        summary, first, last = summaries[k], rs[0], rs[-1]
        check(summary["firstDate"] == first["date"] and summary["lastDate"] == last["date"] and summary["observations"] == 250,
              "export_summary_dates", k)
        for label, field in metric_labels:
            check(abs(D(summary[label + "ChangePercent"]) - D(change(rs, field))) <= percentage_tolerance,
                  "export_return_independent", (k, label))
            drawdown = mdd(rs, field)
            reported = summary[label + "MaxDailyDrawdown"]
            check(abs(D(reported["percent"]) - D(drawdown["percent"])) <= percentage_tolerance, "export_mdd_independent", (k, label))
            for f in ("peakDate", "troughDate", "peakIndex", "troughIndex"):
                check(reported[f] == drawdown[f], "export_mdd_dates_indices", (k, label, f))
            check(n(reported, "peakMarkRaw") == n(rs[drawdown["peakIndex"]], field)
                  and n(reported, "troughMarkRaw") == n(rs[drawdown["troughIndex"]], field), "export_mdd_raw_marks", (k, label))
        conversions = {
            "initialTreasuryNavUsd": (first, "treasuryNavOracleUsdE18", E18),
            "finalTreasuryNavUsd": (last, "treasuryNavOracleUsdE18", E18),
            "initialExternalAssetsUsdg": (first, "externalAssetsUsdgRaw", 10**6),
            "finalExternalAssetsUsdg": (last, "externalAssetsUsdgRaw", 10**6),
            "cumulativeBuybackStock": (last, "buybackStockSpentRaw", E18),
            "cumulativeBuybackUsdAtExecutionMarks": (last, "buybackOracleUsdgValueRaw", 10**6),
            "buybackBurnedFun": (last, "burnedRaw", E18), "collectedLpBurnedFun": (last, "collectedLpFunBurnedRaw", E18),
            "hookBurnedFun": (last, "hookTokenBurnRaw", E18), "hookConvertedFun": (last, "convertedFeeTokensRaw", E18),
            "hookConvertedStock": (last, "convertedFeeStockRaw", E18), "hookStockDeliveredToTreasury": (last, "hookStockDeliveredRaw", E18),
            "collectedLpStock": (last, "collectedLpStockRaw", E18), "collectedLpUsdAtExecutionMarks": (last, "collectedLpOracleUsdgValueRaw", 10**6),
            "terminalClaimableLpStock": (last, "uncollectedLpStockRaw", E18), "terminalClaimableLpFun": (last, "uncollectedLpFunRaw", E18),
        }
        for label, (row, field, denominator) in conversions.items():
            check(D(summary[label]) == D(n(row, field)) / denominator, "export_summary_raw_unit_conversion", (k, label))
        for label, positive, negative in (("priceMarkContributionUsdg", "priceMarkDeltaPositiveUsdgRaw", "priceMarkDeltaNegativeUsdgRaw"),
                                           ("actionContributionUsdg", "actionDeltaPositiveUsdgRaw", "actionDeltaNegativeUsdgRaw"),
                                           ("traderCashChangeUsdg", "traderUsdgOutRaw", "traderUsdgInRaw")):
            check(D(summary[label]) == D(n(last, positive) - n(last, negative)) / 10**6, "export_cashflow_contribution_units", (k, label))
        for f in ("actions", "stopActions", "tpActions", "dipActions", "rebalanceBuyActions", "rebalanceSellActions", "recoveryActions", "buybackBookCalls", "buybacks", "executeNotDue", "buybackNotDue", "flowPairs", "conversionCount", "estimatedActionGas"):
            check(n(summary, f) == n(last, f), "export_summary_counts", (k, f))
        check({f:str(v) for f,v in summary["terminalRaw"].items()} == {f:str(v) for f,v in export_map[(*k,249)].items()}, "export_terminal_raw_exact", k)
        check(summary["strategyFamily"] == first["strategyFamily"], "export_strategy_family", k)
        for source in ("external", "conversion", "buyback"):
            raw = sum((n(b, source+"GeneratedLpStockRaw")-n(a, source+"GeneratedLpStockRaw"))*n(b,"stockOracleUsdE18")//E30 for a,b in zip(rs,rs[1:]))
            check(D(summary[source+"LpStockFeeAtDailyOracleMarksUsdg"]) == D(raw)/10**6, "export_fee_source_daily_usd_marks", (k,source))
        check(D(summary["finalTreasuryCashUsdg"]) == D(n(last,"treasuryUsdgRaw"))/10**6, "export_final_cash_usd_units", k)
        for field,rawfield in (("finalTreasuryStockOracleUsdg","treasuryStockRaw"),("finalLpStockOracleUsdg","lpStockRaw")):
            amount=D(n(last,rawfield))*n(last,"stockOracleUsdE18")/10**36
            check(abs(D(summary[field])-amount)<=D("0.00000051"), "export_final_stock_usd_units", (k,field,str(amount),summary[field]))
    matched = document["matchedComparisons"]
    check({k:len(v) for k,v in matched.items()} == {"harvest":24,"strategyVsFeeOnly":42,"payout50Vs0":12}, "export_matched_pair_counts")
    for category,pairs in matched.items():
        for comparison in pairs:
            t,p,h=comparison["ticker"],comparison["profile"],comparison["harvestEnabled"]
            lhs=groups[(t,p,h)]
            rhs=groups[(t,p,False)] if category=="harvest" else groups[(t,"fee_only",h)] if category=="strategyVsFeeOnly" else groups[(t,p.replace("payout50","payout0"),h)]
            exact={"externalAssetsDeltaUsdg":D(n(lhs[-1],"externalAssetsUsdgRaw")-n(rhs[-1],"externalAssetsUsdgRaw"))/10**6,
                "traderCashChangeDeltaUsdg":D(n(lhs[-1],"traderUsdgBalanceRaw")-n(rhs[-1],"traderUsdgBalanceRaw"))/10**6,
                "funOracleChangeDeltaPercentagePoints":D(change(lhs,"funOracleUsdE18"))-D(change(rhs,"funOracleUsdE18"))}
            for field,value in exact.items():
                error=abs(D(comparison[field])-value)
                check(error <= (D("1.01e-10") if field.endswith("Points") else D(0)), "export_matched_counterfactual_independent", (category,t,p,h,field))
    return {"status": "PASS", "rawRows": 12750, "rawFields": raw_count, "derivedFields": derived_count,
            "summaries": 51, "harvestMatchedPairs": 24, "strategyMatchedPairs": 42, "payoutMatchedPairs": 12, "actualDepthProbes": 408,
            "tolerances": {"decimalMonetaryMarks": str(scalar_tolerance), "displayedPercentagesAndIndices": str(percentage_tolerance), "differenceOfTwoRoundedPercentages": "1.01e-10 percentage points"},
            "maximumDerivedRoundingErrors": {f: str(v) for f, v in sorted(max_errors.items())},
            "hashes": {name: sha(directory / name) for name in ("results.json", "daily.jsonl.gz", "forge.log.gz", "GENERATED_REPORT.md")}}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", type=Path, default=ROOT / "artifacts/all-strategy-income-20261003/spot/forge.log")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/all-strategy-income-20261003/spot/independent-review.json")
    parser.add_argument("--export-dir", type=Path, help="Cross-check final exported daily fields, summary metrics and matched comparisons")
    args = parser.parse_args()
    data_path = ROOT / "contractV2/data/equity-history-2025.json"
    harness_path = ROOT / "contractV2/test/AllStrategyHistoricalReplayFork.t.sol"
    data = json.loads(data_path.read_text())
    log = read_bytes(args.log).decode()
    counters: Counter = Counter()

    def check(condition: bool, label: str, context=None) -> None:
        if not condition:
            raise AssertionError(f"{label}: {context}")
        counters[label] += 1

    def records(prefix: str) -> list[dict]:
        return [json.loads(line.split(prefix + " ", 1)[1])
                for line in log.splitlines() if prefix + " " in line]

    rows, profiles, depths, venues = (records(label) for label in
        ("STRATEGY_ROW", "STRATEGY_PROFILE", "STRATEGY_DEPTH", "STRATEGY_VENUE"))
    check(len(rows) == 12750, "12750_raw_rows", len(rows))
    check(len(profiles) == 51 and len(venues) == 51, "51_profile_and_venue_records")
    check(len(re.findall(r"^\[PASS\] testStrategy_", log, re.M)) == 51
          and "[FAIL" not in log and "STRATEGY_UNEXPECTED_" not in log, "51_tests_pass_no_unexpected_reverts")
    grouped: dict[tuple, list] = defaultdict(list)
    for row in rows:
        grouped[key(row)].append(row)
    meta = {key(p): p for p in profiles}
    expected = {(ticker, profile, harvest) for ticker in TICKERS for profile in PROFILES
                for harvest in ([False] if profile == "baseline" else [False, True])}
    check(set(grouped) == set(meta) == expected, "full_frozen_51_case_matrix")
    manifest_path = ROOT / "artifacts/all-strategy-income-20261003/spot/compiled-source-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    check(manifest["harnessSha256"] == sha(harness_path), "harness_matches_compiled_manifest")
    check(manifest["inputDataSha256"] == sha(data_path), "data_matches_compiled_manifest")
    for path, evidence in manifest["sources"].items():
        check(sha(ROOT / "contractV2" / path) == evidence["sha256"], "local_source_matches_compiled_manifest", path)
    initial_by_ticker: dict[str, list] = defaultdict(list)
    metrics = []
    max_fun_ratio_error = 0
    buyback_fee_rounding = []
    conversion_fee_rounding = []
    total_actions = total_buybacks = total_flow_pairs = total_conversions = 0
    ledger_cumulative = (
        "actions", "stopActions", "tpActions", "dipActions", "rebalanceBuyActions", "rebalanceSellActions", "recoveryActions", "buybackBookCalls", "buybacks", "burnedRaw",
        "buybackStockSpentRaw", "buybackOracleUsdgValueRaw", "executeNotDue", "buybackNotDue",
        "collectedLpStockRaw", "collectedLpFunBurnedRaw", "collectedLpOracleUsdgValueRaw",
        "externalGeneratedLpStockRaw", "externalGeneratedLpFunRaw", "buybackGeneratedLpStockRaw",
        "buybackGeneratedLpFunRaw", "conversionGeneratedLpStockRaw", "conversionGeneratedLpFunRaw",
        "convertedFeeTokensRaw", "convertedFeeStockRaw", "hookStockDeliveredRaw", "hookTokenBurnRaw",
        "hookProtocolStockPaidRaw", "hookCreatorStockPaidRaw", "hookSweeperStockPaidRaw",
        "conversionCount", "traderUsdgInRaw", "traderUsdgOutRaw", "flowPairs",
        "priceMarkDeltaPositiveUsdgRaw", "priceMarkDeltaNegativeUsdgRaw",
        "actionDeltaPositiveUsdgRaw", "actionDeltaNegativeUsdgRaw",
        "keeperStockRaw", "keeperUsdgRaw", "keeperFunRaw",
    )
    for case, rs in sorted(grouped.items()):
        ticker, profile, harvest = case
        p = meta[case]
        first, last = rs[0], rs[-1]
        window = data["windows"][ticker]
        active = profile != "baseline"
        strategy = profile not in ("baseline", "fee_only", "pure_buyback")
        engine = profile.startswith(("fixed", "nav"))
        check(len(rs) == 250, "250_rows_per_case", case)
        check(p["flowEnabled"] == active and p["strategyEnabled"] == strategy, "profile_flags", case)
        check(n(p, "forkBlock") == 128172359, "fixed_fork", case)
        check([n(p, f) for f in ("tp1Bps", "tp2Bps", "dipBps", "stopBps", "lotBps")]
              == [500, 1000, 500, 500, 2000], "frozen_rungs", case)
        family = "all_in" if profile in ("baseline", "fee_only", "all_in_p500") else "pure_buyback" if profile == "pure_buyback" else "fixed_rebalance" if profile.startswith("fixed") else "nav_rebalance" if profile.startswith("nav") else "cycle"
        check(p["strategyFamily"] == family, "strategy_family_identity", case)
        base_kinds = n(p, "candidateCountBefore")
        expected_kind = 0 if family == "all_in" else base_kinds + ("pure_buyback", "fixed_rebalance", "nav_rebalance", "cycle").index(family)
        check(n(p, "registeredKindId") == expected_kind and p["candidateForkOnly"] == (family != "all_in"), "appended_registry_kind_identity", case)
        schema = 1 if family == "fixed_rebalance" else 2 if family == "nav_rebalance" else 0
        check(n(p, "engineSchema") == schema and n(p, "engineVersion") == int(engine)
              and n(p, "engineCapabilities") == (3 if engine else 0), "engine_schema_capability_identity", case)
        payout = 5000 if profile.endswith("payout50") else 0
        word0 = (7000 | (500 << 16) | (600 << 32) | (payout << 64)) if engine else 0
        cap = 1_000 * 10**6 if family == "fixed_rebalance" else 500 if family == "nav_rebalance" else 0
        check(int(p["configWord0"], 16) == word0 and n(p, "configWord1") == n(p, "configWord2") == cap
              and n(p, "payoutBps") == payout, "immutable_configuration_exact_words", case)
        check(n(p, "traderInitialUsdgRaw") == 100_000 * 10**6 and n(p, "dailyBuyUsdgRaw") == 100 * 10**6,
              "fixed_funded_flow_budget", case)
        check(abs(n(first, "externalAssetsUsdgRaw") - 20_000 * 10**6) <= 2, "equal_20k_initial_external_assets", case)
        check(abs(n(first, "treasuryStockRaw") * n(first, "stockOracleUsdE18") // E30 - 10_000 * 10**6) <= 1,
              "equal_10k_initial_treasury", case)
        check(abs(n(first, "lpStockRaw") * n(first, "stockOracleUsdE18") // E30 - 10_000 * 10**6) <= 2,
              "equal_10k_initial_lp_stock", case)
        initial_by_ticker[ticker].append(first)
        for i, r in enumerate(rs):
            ctx = (*case, i)
            price = n(r, "stockOracleUsdE18")
            check(n(r, "index") == i and r["date"] == window["dates"][i]
                  and price == int(window["pricesE18"][i]), "daily_close_mapping", ctx)
            check(n(r, "elapsedSeconds") == window["elapsedSeconds"][i]
                  and n(r, "evmTimestamp") - n(first, "evmTimestamp") == window["elapsedSeconds"][i], "daily_synthetic_clock", ctx)
            check(n(r, "stockPoolLiquidity") == n(first, "stockPoolLiquidity"), "constant_v3_L_full_path", ctx)
            check(r["healthyAfterAction"] is True, "healthy_after_actions", ctx)
            check(n(r, "treasuryStockRaw") == sum(n(r, f) for f in
                  ("treasuryBookedRaw", "treasuryUnbookedRaw", "treasuryBuybackRaw")), "treasury_stock_bucket_identity", ctx)
            stock_assets = n(r, "treasuryStockRaw") + n(r, "lpStockRaw") + n(r, "uncollectedLpStockRaw")
            check(n(r, "externalAssetsUsdgRaw") == stock_assets * price // E30 + n(r, "treasuryUsdgRaw"), "external_assets_integer_formula", ctx)
            check(n(r, "treasuryNavOracleUsdE18") == n(r, "treasuryStockRaw") * price // E18 + n(r, "treasuryUsdgRaw") * 10**12,
                  "treasury_nav_integer_formula", ctx)
            check(n(r, "funOracleUsdE18") == n(r, "funStockE18") * price // E18, "fun_usd_integer_formula", ctx)
            ratio_error = n(r, "funStockE18") - n(r, "lpStockRaw") * E18 // n(r, "lpFunRaw")
            max_fun_ratio_error = max(max_fun_ratio_error, abs(ratio_error))
            check(abs(ratio_error) <= 1, "fun_stock_independent_lp_principal_ratio", ctx)
            check(n(r, "holderFunRaw") == n(first, "holderFunRaw"), "holder_units_unchanged", ctx)
            check(n(r, "totalSupplyRaw") + n(r, "burnedRaw") + n(r, "collectedLpFunBurnedRaw") + n(r, "hookTokenBurnRaw")
                  == n(first, "totalSupplyRaw"), "all_burn_sources_reconcile_supply", ctx)
            for asset, collected, generated in (
                ("Stock", "collectedLpStockRaw", "Stock"), ("Fun", "collectedLpFunBurnedRaw", "Fun")
            ):
                check(n(r, collected) + n(r, f"uncollectedLp{asset}Raw") == sum(n(r, f"{s}GeneratedLp{generated}Raw")
                      for s in ("external", "buyback", "conversion")), "lp_fee_all_sources_conserved", ctx)
            check(n(r, "traderUsdgBalanceRaw") == 100_000 * 10**6 - n(r, "traderUsdgInRaw") + n(r, "traderUsdgOutRaw"),
                  "funded_trader_cash_ledger", ctx)
            check(n(r, "traderFunBalanceRaw") == 0 and n(r, "traderStockBalanceRaw") == 0, "trader_roundtrip_no_hidden_inventory", ctx)
            check(n(r, "flowPairs") == (i if active else 0) and n(r, "traderUsdgInRaw") == n(r, "flowPairs") * 100 * 10**6,
                  "same_daily_100_usdg_order_intents", ctx)
            check(n(r, "traderUsdgOutRaw") <= n(r, "traderUsdgInRaw"), "roundtrip_fees_paid_by_trader", ctx)
            check(n(r, "actions") == sum(n(r, f) for f in ("stopActions", "tpActions", "dipActions", "rebalanceBuyActions", "rebalanceSellActions", "recoveryActions")), "action_count_breakdown", ctx)
            check(n(r, "actions") + n(r, "executeNotDue") == (i if strategy else 0), "one_strategy_opportunity_per_day", ctx)
            check(n(r, "buybacks") + n(r, "buybackNotDue") == (i if active else 0), "one_buyback_opportunity_per_day", ctx)
            check(n(r, "maxBuybackExternalAssetDustUsdgRaw") <= 1, "self_buyback_external_assets_conserved", ctx)
            check(n(r, "maxStrategyStockInputOracleUsdRaw") <= 10_000 * 10**6
                  and n(r, "maxStrategyUsdgInputRaw") <= 10_000 * 10**6, "actual_strategy_sizes_covered_by_depth_probes", ctx)
            check(n(r, "curveUnclaimedFeesRaw") == n(first, "curveUnclaimedFeesRaw"), "initial_curve_fee_separate_unchanged", ctx)
            check(all(n(r, f) == 0 for f in ("hookUnsettledFunRaw", "hookUnsettledStockRaw", "hookOwedTreasuryStockRaw")),
                  "hook_stock_settlement_completed", ctx)
            delta_accounted = (n(r, "priceMarkDeltaPositiveUsdgRaw") - n(r, "priceMarkDeltaNegativeUsdgRaw")
                               + n(r, "actionDeltaPositiveUsdgRaw") - n(r, "actionDeltaNegativeUsdgRaw"))
            check(n(r, "externalAssetsUsdgRaw") - n(first, "externalAssetsUsdgRaw") == delta_accounted,
                  "price_and_action_effects_telescope", ctx)
            if not harvest:
                check(n(r, "collectedLpStockRaw") == n(r, "collectedLpFunBurnedRaw") == 0, "harvest_off_preserves_uncollected_fees", ctx)
            if not strategy:
                check(n(r, "actions") == 0, "non_strategy_controls_no_execute", ctx)
            if profile == "fee_only" and not harvest:
                check(n(r, "buybacks") == 0 and n(r, "treasuryBuybackRaw") == 0, "fee_only_off_no_artificial_buyback_budget", ctx)
            if profile == "baseline":
                for f in ("funStockE18", "lpStockRaw", "lpFunRaw", "treasuryStockRaw", "treasuryUsdgRaw", "totalSupplyRaw"):
                    check(n(r, f) == n(first, f), "no_flow_baseline_conserved", ctx)
            check(r["strategyFamily"] == family, "daily_family_identity", ctx)
            check(n(r, "buybackBookCalls") == (i if profile == "pure_buyback" else 0), "pure_buyback_daily_booking", ctx)
            if profile == "pure_buyback":
                check(n(r, "lotCount") == n(r, "treasuryBookedRaw") == n(r, "treasuryUnbookedRaw") == 0, "pure_buyback_no_strategy_lots", ctx)
            if engine:
                check(n(r, "strategyNonce") == n(r, "actions") == n(r, "rebalanceBuyActions") + n(r, "rebalanceSellActions"), "engine_nonce_action_identity", ctx)
                check(n(r, "lotCount") == 0, "engine_no_lot_ledger", ctx)
                expected_cap = cap if schema == 1 else n(r, "externalAssetsUsdgRaw") * cap // 10000
                check(n(r, "currentMaxTradeUsdgRaw") == min(expected_cap, n(p, "sellChunkUsdg"))
                      and n(r, "currentMaxDailyUsdgRaw") == expected_cap, "current_nav_or_fixed_caps", ctx)
            else:
                check(n(r, "strategyNonce") == n(r, "strategyAvgCostE18") == n(r, "turnoverInEpochUsdgRaw") == 0, "non_engine_fields_zero", ctx)
            if profile != "cycle_p500":
                check(not r["cycleReentryPending"] and n(r, "cycleReentrySaleAt") == n(r, "cycleReentrySalePriceE18") == n(r, "recoveryActions") == 0, "non_cycle_fields_zero", ctx)
            else:
                check(r["cycleReentryPending"] == (n(r, "cycleReentrySaleAt") != 0)
                      and r["cycleReentryPending"] == (n(r, "cycleReentrySalePriceE18") != 0), "cycle_recovery_state_consistency", ctx)
            if not i:
                continue
            prev = rs[i - 1]
            for f in ledger_cumulative:
                check(n(r, f) >= n(prev, f), "cumulative_ledger_monotonic", (*ctx, f))
            check(n(r, "actions") - n(prev, "actions") == int(r["executeSuccess"])
                  and n(r, "buybacks") - n(prev, "buybacks") == int(r["buybackSuccess"]), "daily_success_flags", ctx)
            for j, f in enumerate(("stopActions", "tpActions", "dipActions", "rebalanceBuyActions", "rebalanceSellActions", "recoveryActions")):
                check(n(r, f) - n(prev, f) == int(r["executeSuccess"] and n(r, "actionCode") == j), "daily_action_enum", ctx)
            if engine:
                if not r["executeSuccess"]:
                    check(all(n(r, f) == n(prev, f) for f in ("strategyNonce", "strategyAvgCostE18", "turnoverEpoch", "turnoverInEpochUsdgRaw")), "engine_rejected_action_keeps_state", ctx)
                else:
                    check(n(r, "strategyNonce") == n(prev, "strategyNonce") + 1 and n(r, "turnoverInEpochUsdgRaw") > 0, "engine_success_commits_nonce_and_turnover", ctx)
            if profile == "cycle_p500" and r["executeSuccess"] and n(r, "actionCode") == 5:
                check(prev["cycleReentryPending"] and n(r, "evmTimestamp") - n(prev, "cycleReentrySaleAt") >= 600,
                      "cycle_recovery_requires_prior_sale_and_cooldown", ctx)
                check(price * 10000 >= n(prev, "cycleReentrySalePriceE18") * 10500 and not r["cycleReentryPending"],
                      "cycle_recovery_threshold_and_consumption", ctx)
            if strategy:
                check(r["executeRevert"] == (ZERO if r["executeSuccess"] else NOT_DUE), "only_execute_notdue_expected", ctx)
            if active:
                check(r["buybackRevert"] == (ZERO if r["buybackSuccess"] else NOT_DUE), "only_buyback_notdue_expected", ctx)
            check(n(r, "buybackStockSpentRaw") - n(prev, "buybackStockSpentRaw") == n(r, "dailyBuybackStockSpentRaw"), "daily_buyback_stock_sum", ctx)
            check(n(r, "dailyBuybackOracleUsdgValueRaw") == n(r, "dailyBuybackStockSpentRaw") * price // E30
                  and n(r, "buybackOracleUsdgValueRaw") - n(prev, "buybackOracleUsdgValueRaw") == n(r, "dailyBuybackOracleUsdgValueRaw"),
                  "daily_buyback_usd_marks", ctx)
            for cumulative, daily in (("collectedLpStockRaw", "dailyCollectedLpStockRaw"), ("collectedLpFunBurnedRaw", "dailyCollectedLpFunBurnedRaw")):
                check(n(r, cumulative) - n(prev, cumulative) == n(r, daily), "daily_lp_harvest_sums", ctx)
            check(n(r, "collectedLpOracleUsdgValueRaw") - n(prev, "collectedLpOracleUsdgValueRaw") == n(r, "dailyCollectedLpStockRaw") * price // E30,
                  "daily_harvest_usd_marks", ctx)
            budget_credit = n(r, "treasuryBuybackRaw") - n(prev, "treasuryBuybackRaw") + n(r, "dailyBuybackStockSpentRaw") - n(r, "dailyCollectedLpStockRaw")
            check(budget_credit >= 0, "buyback_budget_nonnegative_credit", ctx)
            can_credit = profile == "pure_buyback" or (r["executeSuccess"] and n(r, "actionCode") in (1, 4))
            if not can_credit or (engine and payout == 0):
                check(budget_credit == 0, "buyback_budget_source_restriction", ctx)
            if profile == "pure_buyback":
                check(budget_credit == n(r, "hookStockDeliveredRaw") - n(prev, "hookStockDeliveredRaw"), "pure_buyback_tax_stock_booked_exact", ctx)
            self_fee_delta = n(r, "buybackGeneratedLpStockRaw") - n(prev, "buybackGeneratedLpStockRaw")
            self_fee_err = self_fee_delta - n(r, "dailyBuybackStockSpentRaw") * n(p, "funPoolFee") // 10**6
            buyback_fee_rounding.append(self_fee_err)
            check(abs(self_fee_err) <= 2 and n(r, "buybackGeneratedLpFunRaw") == 0, "self_paid_stock_lp_fee_matches_v4_rate", ctx)
            converted = n(r, "convertedFeeTokensRaw") - n(prev, "convertedFeeTokensRaw")
            conversion_fee_delta = n(r, "conversionGeneratedLpFunRaw") - n(prev, "conversionGeneratedLpFunRaw")
            conversion_fee_err = conversion_fee_delta - converted * n(p, "funPoolFee") // 10**6
            conversion_fee_rounding.append(conversion_fee_err)
            check(abs(conversion_fee_err) <= 2 and n(r, "conversionGeneratedLpStockRaw") == 0, "conversion_paid_fun_lp_fee_matches_v4_rate", ctx)
            paid = {f: n(r, f) - n(prev, f) for f in (
                "hookStockDeliveredRaw", "hookProtocolStockPaidRaw", "hookCreatorStockPaidRaw", "hookSweeperStockPaidRaw")}
            total_paid = sum(paid.values())
            converted_stock = n(r, "convertedFeeStockRaw") - n(prev, "convertedFeeStockRaw")
            check(total_paid >= converted_stock and paid["hookSweeperStockPaidRaw"] == 0,
                  "hook_stock_payouts_cover_conversion_and_external_sell_tax", ctx)
            # Frozen deployment default protocol 20%, creator request 10%, sweep tip 0%.
            # Two actual settlements (sell tax and converted buy tax) floor the cuts separately.
            check(0 <= total_paid * 2000 // 10000 - paid["hookProtocolStockPaidRaw"] <= 1
                  and 0 <= total_paid * 1000 // 10000 - paid["hookCreatorStockPaidRaw"] <= 1,
                  "hook_protocol_creator_actual_role_split", ctx)
            old_stock_assets = n(prev, "treasuryStockRaw") + n(prev, "lpStockRaw") + n(prev, "uncollectedLpStockRaw")
            repriced = old_stock_assets * price // E30 + n(prev, "treasuryUsdgRaw")
            price_delta = repriced - n(prev, "externalAssetsUsdgRaw")
            action_delta = n(r, "externalAssetsUsdgRaw") - repriced
            check((n(r, "priceMarkDeltaPositiveUsdgRaw") - n(prev, "priceMarkDeltaPositiveUsdgRaw"),
                   n(r, "priceMarkDeltaNegativeUsdgRaw") - n(prev, "priceMarkDeltaNegativeUsdgRaw"))
                  == (max(price_delta, 0), max(-price_delta, 0)), "price_mark_delta_independent_reprice", ctx)
            check((n(r, "actionDeltaPositiveUsdgRaw") - n(prev, "actionDeltaPositiveUsdgRaw"),
                   n(r, "actionDeltaNegativeUsdgRaw") - n(prev, "actionDeltaNegativeUsdgRaw"))
                  == (max(action_delta, 0), max(-action_delta, 0)), "action_delta_independent_remainder", ctx)
        total_actions += n(last, "actions")
        total_buybacks += n(last, "buybacks")
        total_flow_pairs += n(last, "flowPairs")
        total_conversions += n(last, "conversionCount")
        metric = {"ticker": ticker, "profile": profile, "harvestEnabled": harvest,
                  "firstDate": first["date"], "lastDate": last["date"], "observations": len(rs)}
        for f in ("stockOracleUsdE18", "funOracleUsdE18", "funStockE18", "treasuryNavOracleUsdE18", "externalAssetsUsdgRaw"):
            metric[f] = {"initialRaw": str(n(first, f)), "finalRaw": str(n(last, f)), "changePercent": change(rs, f), "maximumDailyDrawdown": mdd(rs, f)}
        for f in ledger_cumulative:
            metric[f] = str(n(last, f))
        metric["finalTraderLossUsdgRaw"] = str(n(last, "traderUsdgInRaw") - n(last, "traderUsdgOutRaw"))
        metric["finalUncollectedLpStockRaw"] = str(n(last, "uncollectedLpStockRaw"))
        metric["finalUncollectedLpFunRaw"] = str(n(last, "uncollectedLpFunRaw"))
        metric["pendingHookTokenFeesRaw"] = str(n(last, "pendingHookTokenFeesRaw"))
        metrics.append(metric)
    baseline_fields = ("holderFunRaw", "lpStockRaw", "lpFunRaw", "treasuryStockRaw", "treasuryUsdgRaw", "totalSupplyRaw", "curveUnclaimedFeesRaw", "funStockE18", "externalAssetsUsdgRaw")
    for ticker, initial in initial_by_ticker.items():
        for f in baseline_fields:
            check(len({n(r, f) for r in initial}) == 1, "same_ticker_all_profiles_initial_economics_equal", (ticker, f))
    matched = []
    for ticker in TICKERS:
        for profile in PROFILES[1:]:
            off, on = grouped[(ticker, profile, False)], grouped[(ticker, profile, True)]
            mo, mn = meta[(ticker, profile, False)], meta[(ticker, profile, True)]
            for f in ("token", "treasury", "vault", "nonce", "treasuryRuntimeHash", "factoryRuntimeHash"):
                check(mo[f] == mn[f], "matched_on_off_identical_deployment", (ticker, profile, f))
            for f in baseline_fields:
                check(n(off[0], f) == n(on[0], f), "matched_on_off_identical_initial_state", (ticker, profile, f))
            for ro, rn in zip(off, on):
                check(ro["date"] == rn["date"] and n(ro, "stockOracleUsdE18") == n(rn, "stockOracleUsdE18")
                      and n(ro, "traderUsdgInRaw") == n(rn, "traderUsdgInRaw") and n(ro, "flowPairs") == n(rn, "flowPairs"),
                      "matched_on_off_identical_price_and_order_intents", (ticker, profile, ro["date"]))
            matched.append({"ticker": ticker, "profile": profile,
                            "finalExternalAssetsOnMinusOffUsdgRaw": str(n(on[-1], "externalAssetsUsdgRaw") - n(off[-1], "externalAssetsUsdgRaw")),
                            "finalTreasuryNavOnMinusOffUsdE18": str(n(on[-1], "treasuryNavOracleUsdE18") - n(off[-1], "treasuryNavOracleUsdE18")),
                            "funMarkReturnOnMinusOffPercentagePoints": str(D(change(on, "funOracleUsdE18")) - D(change(off, "funOracleUsdE18"))),
                            "traderLossOnMinusOffUsdgRaw": str((n(on[-1], "traderUsdgInRaw") - n(on[-1], "traderUsdgOutRaw")) - (n(off[-1], "traderUsdgInRaw") - n(off[-1], "traderUsdgOutRaw")))})
    check(len(depths) == 51 * 8, "all_eight_actual_depth_probes_per_case", len(depths))
    for case in expected:
        probes = [q for q in depths if key(q) == case]
        check({(n(q, "inputNotionalUsdgRaw"), q["buy"]) for q in probes}
              == {(size * 10**6, direction) for size in (100, 1000, 2000, 10000) for direction in (False, True)},
              "all_depth_sizes_both_directions", case)
    for v in venues:
        case_liquidity = n(grouped[(v["ticker"], "baseline", False)][0], "stockPoolLiquidity")
        check(all(n(v, f) == case_liquidity for f in ("oldPositionLiquidity", "newPositionLiquidity", "activeLiquidityBefore", "activeLiquidityAfter")),
              "venue_original_and_rebuilt_L_match_entire_path", v["ticker"])
        check(n(v, "newTickLower") < n(v, "oldTickLower") and n(v, "newTickUpper") > n(v, "oldTickUpper"),
              "venue_widened_both_bounds", v["ticker"])
    for q in depths:
        p = meta[key(q)]
        price = n(p, "initialPriceE18")
        usd_in = n(q, "inputRaw") if q["buy"] else n(q, "inputRaw") * price // E30
        usd_out = n(q, "outputRaw") * price // E30 if q["buy"] else n(q, "outputRaw")
        check(usd_in == n(q, "inputOracleUsdgRaw") and usd_out == n(q, "outputOracleUsdgRaw"), "actual_depth_probe_usd_units", key(q))
        check((usd_in - usd_out) * 10**6 // usd_in == n(q, "effectiveCostPpm"), "actual_depth_probe_all_in_cost", key(q))
        check(n(q, "effectiveCostPpm") <= n(q, "feePpm") + 1000, "actual_depth_probe_incremental_cost_at_most_10bps", key(q))
    result = {
        "schema": "hedgefun-all-strategy-history-independent-review-v1", "status": "PASS_RAW_LEDGER",
        "reviewedAtUtc": datetime.now(timezone.utc).isoformat(),
        "method": "Independent raw-log integer/Decimal reconstruction. Does not import producer code or call exporter formulas. Source-reviewed harness assertions complement logged fields; no public transactions.",
        "evidence": {"harness": {"path": str(harness_path.relative_to(ROOT)), "sha256": sha(harness_path)},
                     "log": {"path": str(args.log), "fileSha256": sha(args.log), "uncompressedSha256": hashlib.sha256(log.encode()).hexdigest()},
                     "data": {"path": str(data_path.relative_to(ROOT)), "sha256": sha(data_path)},
                     "verifier": {"path": str(Path(__file__).resolve().relative_to(ROOT)), "sha256": sha(Path(__file__))}},
        "totals": {"cases": 51, "snapshots": len(rows), "actions": total_actions, "buybacks": total_buybacks,
                   "externalRoundtripPairs": total_flow_pairs, "ownerFeeConversions": total_conversions, "actualDepthProbes": len(depths)},
        "checks": dict(sorted(counters.items())), "assertionCount": sum(counters.values()),
        "rounding": {"maximumFunStockPriceVsLpReserveRatioError": max_fun_ratio_error,
                     "buybackStockFeeErrorWeiRange": [min(buyback_fee_rounding), max(buyback_fee_rounding)],
                     "conversionFunFeeErrorWeiRange": [min(conversion_fee_rounding), max(conversion_fee_rounding)]},
        "independentMetrics": metrics, "matchedHarvestComparisons": matched,
        "scopeLimits": [
            "Daily Close replay; after-close constant-price 601-second wait and forced-open calendar are synthetic assumptions.",
            "Full-year 2025 volatility/volume selection is in-sample; profile comparisons are not out-of-sample parameter validation.",
            "Initial $20k means $10k treasury stock plus $10k locked LP stock, not gross acquisition/launch cost or redeemable investor equity.",
            "External-asset measure excludes endogenous FUN, pending unconverted FUN taxes, and initial unclaimed curve fees; these are separately logged.",
            "The synthetic funded trader supplies 249 $100 buy/sell roundtrips per active case. Comparisons are conditional on this flow, not historical FUN demand.",
            "Matched harvest on/off changes only LP collection. Actual fills, taxes and later strategy state may diverge as effects of the intervention.",
            "V3 depth and fee tier differ by ticker; raw liquidity L is not a comparable USD depth measure. Actual $100/$1000/$2000/$10000 probes are separate evidence.",
            "Owner-authorized hook fee conversion is locally impersonated. Permissionless LP harvest/keeper calls do not make the full fee pipeline permissionless.",
            "Hook role payments are actual cumulative recipient STOCK balance changes, checked against aggregate split rounding. Sell-tax stock is inferred from total paid minus conversion output; original per-trade gross hook-tax bases were not separately logged, so this is not an independent replay of every individual tax calculation.",
            "Self-paid buyback LP fees recycle existing external stock; external-asset NAV is conserved at buyback within a raw USDG unit and fees are not added as new profit.",
            "Marginal FUN marks and locked LP asset marks are not full-position exit proceeds. Dividends are not paid. Gas measurements are harness estimates, not charged execution costs.",
        ], "blockingFindings": [], "exportCrosscheck": {"status": "NOT_REQUESTED"},
    }
    if args.export_dir:
        result["exportCrosscheck"] = audit_export(args.export_dir,result,grouped,rows,meta,depths,log,check)
        result["status"] = "PASS"
        result["checks"] = dict(sorted(counters.items()))
        result["assertionCount"] = sum(counters.values())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"status": result["status"], "totals": result["totals"], "assertionCount": result["assertionCount"], "output": str(args.output)}))


if __name__ == "__main__":
    main()

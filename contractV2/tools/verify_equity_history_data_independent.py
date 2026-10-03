#!/usr/bin/env python3
"""Independently inspect saved Yahoo source bars; never imports producer code."""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_EVEN, getcontext
import hashlib
import json
from pathlib import Path
from zoneinfo import ZoneInfo

getcontext().prec = 80
ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "contractV2/data/equity-history-2025-sources"
OUTPUT = ROOT / "artifacts/equity-history-20261003/independent-data-review.json"
CANONICAL = ROOT / "contractV2/data/equity-history-2025.json"
DEPTH = ROOT / "contractV2/data/equity-depth-snapshot.json"


def inspect(path: Path) -> tuple[dict, list[dict]]:
    payload = path.read_bytes()
    response = json.loads(payload, parse_float=Decimal)
    assert response["chart"]["error"] is None
    series = response["chart"]["result"][0]
    meta = series["meta"]
    assert meta["symbol"] == path.stem
    assert meta["currency"] == "USD"
    assert meta["exchangeTimezoneName"] == "America/New_York"
    times = series["timestamp"]
    assert len(times) == 250 and times == sorted(set(times))
    quote = series["indicators"]["quote"][0]
    adjusted = series["indicators"]["adjclose"][0]["adjclose"]
    assert len(adjusted) == len(times)
    assert all(len(quote[k]) == len(times) for k in ("open", "high", "low", "close", "volume"))
    rows = []
    errors = []
    for i, timestamp in enumerate(times):
        local = datetime.fromtimestamp(timestamp, ZoneInfo("America/New_York"))
        assert local.year == 2025 and local.weekday() < 5
        assert (local.hour, local.minute) == (9, 30)
        o, h, low, close, adj = [Decimal(x) for x in (
            quote["open"][i], quote["high"][i], quote["low"][i],
            quote["close"][i], adjusted[i],
        )]
        assert all(x.is_finite() and x > 0 for x in (o, h, low, close, adj))
        assert low <= min(o, close) <= max(o, close) <= h
        assert isinstance(quote["volume"][i], int) and quote["volume"][i] >= 0
        rounded = close.quantize(Decimal("0.00000001"), rounding=ROUND_HALF_EVEN)
        errors.append(abs(rounded - close))
        rows.append({
            "date": local.date().isoformat(), "providerTimestamp": timestamp,
            "openUsd": str(o), "highUsd": str(h), "lowUsd": str(low),
            "closeUsd": str(close), "adjustedCloseUsd": str(adj),
            "volume": quote["volume"][i], "replayCloseUsd": str(rounded),
            "replayPriceE18": int(rounded * 10**18),
        })
    assert len({row["date"] for row in rows}) == 250
    assert (rows[0]["date"], rows[-1]["date"]) == ("2025-01-02", "2025-12-31")
    dividends = list(series.get("events", {}).get("dividends", {}).values())
    splits = list(series.get("events", {}).get("splits", {}).values())
    first, last = rows[0], rows[-1]
    return {
        "source": str(path.relative_to(ROOT)),
        "sourceSha256": hashlib.sha256(payload).hexdigest(),
        "symbol": path.stem, "observations": len(rows),
        "firstDate": first["date"], "lastDate": last["date"],
        "sourceTimestampMeaning": "09:30 America/New_York session OPEN; Close is not available then",
        "closeAdjustedCloseDifferentCount": sum(r["closeUsd"] != r["adjustedCloseUsd"] for r in rows),
        "dividends": dividends, "dividendAmountSumPerShare": str(sum((Decimal(x["amount"]) for x in dividends), Decimal(0))),
        "splits": splits,
        "maximumEightDecimalRoundingErrorUsd": str(max(errors)),
        "firstToLastCloseChangePercent": str((Decimal(last["closeUsd"]) / Decimal(first["closeUsd"]) - 1) * 100),
        "firstToLastAdjustedCloseChangePercent": str((Decimal(last["adjustedCloseUsd"]) / Decimal(first["adjustedCloseUsd"]) - 1) * 100),
    }, rows


def main() -> None:
    files = sorted(SOURCE.glob("*.json"))
    assert {p.stem for p in files} == {"AAPL", "AMZN", "GME", "GOOGL", "META", "MSFT", "NVDA", "TSLA"}
    reports, bars = {}, {}
    for path in files:
        report, rows = inspect(path)
        reports[path.stem], bars[path.stem] = report, rows
    assert len({tuple(r["date"] for r in rows) for rows in bars.values()}) == 1
    canonical = json.loads(CANONICAL.read_text())
    assert canonical["sourceBars"] == 2000 and canonical["selectedBars"] == 750
    assert set(canonical["candidates"]) == set(bars) == set(canonical["windows"])
    computed_screen = {}
    for ticker, rows in bars.items():
        candidate = canonical["candidates"][ticker]
        assert candidate["sourceSha256"] == reports[ticker]["sourceSha256"]
        assert len(candidate["rows"]) == len(rows)
        for source_row, exported_row in zip(rows, candidate["rows"]):
            assert all(exported_row[k] == value for k, value in source_row.items())
            assert Decimal(exported_row["dollarVolumeProxyUsd"]) == Decimal(source_row["closeUsd"]) * source_row["volume"]
        window = canonical["windows"][ticker]
        first = date.fromisoformat(rows[0]["date"])
        assert window["dates"] == [r["date"] for r in rows]
        assert window["pricesE18"] == [r["replayPriceE18"] for r in rows]
        assert window["elapsedSeconds"] == [(date.fromisoformat(r["date"]) - first).days * 86400 for r in rows]
        closes = [Decimal(r["closeUsd"]) for r in rows]
        returns = [(closes[i] / closes[i - 1]).ln() for i in range(1, len(closes))]
        n = Decimal(len(returns))
        variance = (sum(x * x for x in returns) - sum(returns) ** 2 / n) / (n - 1)
        daily_std = variance.sqrt() * 100
        annual_std = daily_std * Decimal(252).sqrt()
        dollars = sorted(Decimal(r["closeUsd"]) * r["volume"] for r in rows)
        median = (dollars[124] + dollars[125]) / 2
        mean = sum(dollars) / len(dollars)
        screen = candidate["screening"]
        for label, value in (
            ("dailyLogReturnSampleStdDevPercent", daily_std),
            ("annualizedDailyLogReturnVolatilityPercent", annual_std),
            ("medianEquityDollarVolumeUsd", median),
            ("averageEquityDollarVolumeUsd", mean),
        ):
            assert abs(Decimal(screen[label]) - value) <= Decimal("0.0000000000005")
        eligible = annual_std >= 30 and median >= 2_000_000_000
        assert screen["passesThresholds"] is eligible
        assert screen["logReturnSamples"] == 249
        assert candidate["dividendEventCount"] == len(reports[ticker]["dividends"])
        assert candidate["closeDiffersFromAdjustedCloseDays"] == reports[ticker]["closeAdjustedCloseDifferentCount"]
        computed_screen[ticker] = {"annualizedVolatilityPercent": str(annual_std), "medianEquityDollarVolumeUsd": str(median), "eligible": eligible}
    ranking = sorted((t for t in computed_screen if computed_screen[t]["eligible"]),
                     key=lambda t: (-Decimal(computed_screen[t]["annualizedVolatilityPercent"]),
                                    -Decimal(computed_screen[t]["medianEquityDollarVolumeUsd"]), t))
    selected = ranking[:3]
    assert selected == canonical["selectedTickers"] == ["TSLA", "NVDA", "META"]
    for ticker in bars:
        screening = canonical["candidates"][ticker]["screening"]
        assert screening["selected"] == (ticker in selected)
        assert screening["eligibleVolatilityRank"] == (ranking.index(ticker) + 1 if ticker in ranking else None)
    book_path = ROOT / "contractV2/deploy/testnet-v2-fresh-creator.json"
    book = json.loads(book_path.read_text())
    assert canonical["deployment"]["addressBookSha256"] == hashlib.sha256(book_path.read_bytes()).hexdigest()
    for ticker, window in canonical["windows"].items():
        contract = book["stocks"][ticker]
        for field in ("stock", "pool", "oracle", "feed"):
            assert window[field].lower() == contract["token" if field == "stock" else field].lower()
        assert window["fee"] == contract["fee"] and window["decimals"] == contract["decimals"]
    depth = json.loads(DEPTH.read_text())
    assert canonical["onchainDepthSnapshot"] == depth
    assert depth["chainId"] == 46630 and set(depth["pools"]) == set(bars)
    depth_probe_count = 0
    depth_probes = []
    q96 = 2**96
    for ticker, pool in depth["pools"].items():
        assert pool["enabled"] is True
        assert pool["stock"].lower() == canonical["windows"][ticker]["stock"].lower()
        assert pool["pool"].lower() == canonical["windows"][ticker]["pool"].lower()
        assert pool["feeMillionths"] == canonical["windows"][ticker]["fee"]
        sqrt_price, liquidity = int(pool["sqrtPriceX96"]), int(pool["activeLiquidity"])
        ratio = (Decimal(sqrt_price) / q96) ** 2
        spot = ratio * 10**12 if pool["stockIsToken0"] else Decimal(10**12) / ratio
        assert abs(Decimal(pool["stockSpotUsd"]) - spot) <= Decimal("0.0000000000000000005")
        for probe in pool["constantActiveLiquidityProbes"]:
            zero_for_one = (probe["direction"] == "stock_to_USDG") == pool["stockIsToken0"]
            gross = int(probe["inputRaw"])
            net = gross * (10**6 - pool["feeMillionths"]) // 10**6
            if zero_for_one:
                denominator = liquidity * q96 + net * sqrt_price
                next_sqrt = (liquidity * q96 * sqrt_price + denominator - 1) // denominator
                output_raw = liquidity * (sqrt_price - next_sqrt) // q96
            else:
                next_sqrt = sqrt_price + net * q96 // liquidity
                output_raw = liquidity * q96 * (next_sqrt - sqrt_price) // (next_sqrt * sqrt_price)
            assert output_raw == int(probe["estimatedOutputRaw"])
            is_buy = probe["direction"] == "USDG_to_stock"
            input_usd = Decimal(gross) / 10**6 if is_buy else Decimal(gross) / 10**18 * spot
            output_usd = Decimal(output_raw) / 10**18 * spot if is_buy else Decimal(output_raw) / 10**6
            all_in_cost = (1 - output_usd / input_usd) * 10_000
            assert abs(Decimal(probe["inputUsdAtInitialSpot"]) - input_usd) <= Decimal("0.0000000000000000005")
            assert abs(Decimal(probe["outputUsdAtInitialSpot"]) - output_usd) <= Decimal("0.0000000000000000005")
            assert abs(Decimal(probe["estimatedFeeAndAverageImpactBps"]) - all_in_cost) <= Decimal("0.0000000000005")
            next_ratio = (Decimal(next_sqrt) / q96) ** 2
            next_spot = next_ratio * 10**12 if pool["stockIsToken0"] else Decimal(10**12) / next_ratio
            assert abs(Decimal(probe["estimatedPostSwapSpotChangeBps"]) - (next_spot / spot - 1) * 10_000) <= Decimal("0.0000000000005")
            lower = Decimal("1.0001") ** (Decimal(pool["seedTickLower"]) / 2) * q96
            upper = Decimal("1.0001") ** (Decimal(pool["seedTickUpper"]) / 2) * q96
            assert probe["staysInsideKnownPositionRange"] == (lower < next_sqrt < upper)
            depth_probe_count += 1
            depth_probes.append({"ticker": ticker, "direction": probe["direction"], "notionalUsd": probe["notionalUsd"], "independentEstimatedOutputRaw": str(output_raw)})
    output = {
        "schema": "hedgefun-equity-history-independent-data-review-v1",
        "status": "PASS_DATA_AND_SCREENING",
        "reviewedAtUtc": datetime.now(timezone.utc).isoformat(),
        "method": "Independent Decimal parse, raw OHLCV/range/timestamp checks and HALF_EVEN quantization; no producer imports or network mutations",
        "symbolCount": len(reports), "sourceBarCount": sum(len(x) for x in bars.values()),
        "matchingSessionDatesAcrossSymbols": True,
        "symbols": reports,
        "canonical": {"path": str(CANONICAL.relative_to(ROOT)), "sha256AtReview": hashlib.sha256(CANONICAL.read_bytes()).hexdigest(), "all2000RowsExact": True, "allEightWindowsExact": True},
        "independentScreening": computed_screen,
        "selectedTickers": selected,
        "screeningInterpretation": "Full-year 2025 volatility and volume select the universe in-sample. A fixed formula is auditable but not a prospective universe selection or out-of-sample validation.",
        "depthAlgebraReview": {"status": "PASS", "path": str(DEPTH.relative_to(ROOT)), "sha256": hashlib.sha256(DEPTH.read_bytes()).hexdigest(), "block": depth["block"], "blockHash": depth["blockHash"], "probeCount": depth_probe_count, "allRawIntegerOutputsExact": True, "probes": depth_probes, "scope": "Independent constant-L integer swap algebra on supplied pinned state. Not actual swap execution; does not independently prove absence of other initialized ticks. Historical harness must validate actual execution and constant L across its full path."},
        "requiredInterpretation": [
            "Replay split-adjusted Close prices, not dividend-adjusted Close as a tradable price.",
            "Adjusted Close may be shown only as a separately labeled dividend-adjusted benchmark; it is not the stock-token's achieved return without distributions.",
            "If no dividend cash is credited, both strategy and Close-price benchmark are explicitly price-only; dividends are not silently reinvested.",
            "First observed Close to last observed Close is not a standard previous-year-end annual return.",
            "Daily timestamps label session opens. An after-close plus synthetic 601-second holding assumption must not be represented as morning execution.",
        ],
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(output, indent=2, default=str) + "\n")
    print(json.dumps({"status": output["status"], "sourceBars": output["sourceBarCount"], "symbols": list(reports), "output": str(OUTPUT)}))


if __name__ == "__main__":
    main()

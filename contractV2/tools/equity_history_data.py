#!/usr/bin/env python3
"""Freeze and screen 2025 daily equity inputs without selecting on returns.

Reads saved Yahoo responses and a verified deployment address book. Historical
equity dollar volume and current testnet execution depth are separate measures.
Close is the split-adjusted price input; dividend-adjusted Close is retained but
never passed to the stock price feed. No transactions or wallet access.
"""
from __future__ import annotations

import argparse
import csv
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_EVEN, getcontext
import hashlib
import json
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
D = Decimal
getcontext().prec = 70
E18 = D(10) ** 18
MIN_MEDIAN_DOLLAR_VOLUME = D("2000000000")
MIN_ANNUALIZED_VOL_PERCENT = D("30")
TOP_N = 3
EXPECTED_UNIVERSE = ("AAPL", "AMZN", "GME", "GOOGL", "META", "MSFT", "NVDA", "TSLA")


def require(value, message):
    if not value:
        raise ValueError(message)


def display(value, places=12):
    return format(D(value), f".{places}f").rstrip("0").rstrip(".") or "0"


def source_url(ticker):
    return (f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?period1=1735689600"
            "&period2=1767225600&interval=1d&events=div%2Csplits&includeAdjustedClose=true")


def normalize(raw: bytes, ticker: str) -> dict:
    document = json.loads(raw, parse_float=D)
    require(not document["chart"].get("error"), "provider error")
    require(len(document["chart"]["result"]) == 1, "ambiguous provider result")
    series = document["chart"]["result"][0]
    meta = series["meta"]
    require((meta["symbol"], meta["currency"], meta["exchangeTimezoneName"]) ==
            (ticker, "USD", "America/New_York"), "wrong instrument, currency or timezone")
    times = series["timestamp"]
    require(len(times) == 250 and times == sorted(set(times)), "2025 requires 250 unique sessions")
    quote = series["indicators"]["quote"][0]
    adjusted = series["indicators"]["adjclose"][0]["adjclose"]
    require(all(len(quote[key]) == len(times) for key in ("open", "high", "low", "close", "volume"))
            and len(adjusted) == len(times), "inconsistent source array lengths")
    rows = []
    for index, timestamp in enumerate(times):
        day = datetime.fromtimestamp(timestamp, ZoneInfo("America/New_York")).date()
        require(day.year == 2025 and day.weekday() < 5, "invalid session date")
        prices = [D(quote[key][index]) for key in ("open", "high", "low", "close")]
        require(all(price.is_finite() and price > 0 for price in prices), "invalid OHLC price")
        opening, high, low, close = prices
        require(low <= min(opening, close) <= max(opening, close) <= high, "invalid OHLC range")
        adj = D(adjusted[index])
        require(adj.is_finite() and adj > 0, "invalid adjusted Close")
        volume = quote["volume"][index]
        require(type(volume) is int and volume >= 0, "invalid daily share volume")
        replay = close.quantize(D("0.00000001"), rounding=ROUND_HALF_EVEN)
        rows.append({"date": day.isoformat(), "providerTimestamp": timestamp,
                     "openUsd": str(opening), "highUsd": str(high), "lowUsd": str(low),
                     "closeUsd": str(close), "adjustedCloseUsd": str(adj), "volume": volume,
                     "dollarVolumeProxyUsd": str(close * volume), "replayCloseUsd": str(replay),
                     "replayPriceE18": int(replay * E18)})
    require(len({row["date"] for row in rows}) == 250, "duplicate session date")
    require((rows[0]["date"], rows[-1]["date"]) == ("2025-01-02", "2025-12-31"), "incomplete 2025 window")
    closes = [D(row["closeUsd"]) for row in rows]
    log_returns = [(b / a).ln() for a, b in zip(closes, closes[1:])]
    mean_log = sum(log_returns) / len(log_returns)
    variance = sum((value - mean_log)**2 for value in log_returns) / (len(log_returns) - 1)
    annual_vol = variance.sqrt() * D(252).sqrt() * 100
    volumes = sorted(D(row["dollarVolumeProxyUsd"]) for row in rows)
    median_dollar = (volumes[124] + volumes[125]) / 2
    average_dollar = sum(volumes) / len(volumes)
    reasons = []
    if median_dollar < MIN_MEDIAN_DOLLAR_VOLUME:
        reasons.append("median_equity_dollar_volume_below_2b")
    if annual_vol < MIN_ANNUALIZED_VOL_PERCENT:
        reasons.append("annualized_daily_log_volatility_below_30_percent")
    events = json.loads(json.dumps(series.get("events", {}), default=str))
    return {
        "ticker": ticker, "rows": rows, "rowCount": 250,
        "sourceUrl": source_url(ticker), "sourcePage": f"https://finance.yahoo.com/quote/{ticker}/history/",
        "sourceSha256": hashlib.sha256(raw).hexdigest(),
        "events": events, "dividendEventCount": len(events.get("dividends", {})),
        "closeDiffersFromAdjustedCloseDays": sum(D(row["closeUsd"]) != D(row["adjustedCloseUsd"]) for row in rows),
        "screening": {"medianEquityDollarVolumeUsd": display(median_dollar),
                      "averageEquityDollarVolumeUsd": display(average_dollar),
                      "dailyLogReturnSampleStdDevPercent": display(variance.sqrt() * 100),
                      "annualizedDailyLogReturnVolatilityPercent": display(annual_vol),
                      "logReturnSamples": 249, "passesThresholds": not reasons, "exclusionReasons": reasons},
    }


def build(sources: Path, book_path: Path, depth_path: Path | None = None) -> dict:
    book = json.loads(book_path.read_text())
    require(book.get("broadcast") and book.get("chainId") == 46630, "verified testnet deployment required")
    require(tuple(sorted(book["stocks"])) == EXPECTED_UNIVERSE, "deployed universe changed; review before screening")
    candidates = {ticker: normalize((sources / f"{ticker}.json").read_bytes(), ticker) for ticker in EXPECTED_UNIVERSE}
    dates = candidates["TSLA"]["rows"]
    dates = [row["date"] for row in dates]
    for ticker, series in candidates.items():
        require([row["date"] for row in series["rows"]] == dates, f"session alignment differs for {ticker}")
    eligible = [ticker for ticker in EXPECTED_UNIVERSE if candidates[ticker]["screening"]["passesThresholds"]]
    eligible.sort(key=lambda ticker: (-D(candidates[ticker]["screening"]["annualizedDailyLogReturnVolatilityPercent"]),
                                     -D(candidates[ticker]["screening"]["medianEquityDollarVolumeUsd"]), ticker))
    selected = eligible[:TOP_N]
    require(len(selected) == TOP_N, "insufficient eligible tickers; do not relax thresholds after seeing returns")
    windows = {}
    for ticker, series in candidates.items():
        series["screening"]["selected"] = ticker in selected
        series["screening"]["eligibleVolatilityRank"] = eligible.index(ticker) + 1 if ticker in eligible else None
        if ticker in eligible and ticker not in selected:
            series["screening"]["exclusionReasons"].append("eligible_but_outside_top_three_volatility_rank")
        contract = book["stocks"][ticker]
        windows[ticker] = {
            "stock": contract["token"], "pool": contract["pool"], "oracle": contract["oracle"],
            "feed": contract["feed"], "fee": contract["fee"], "decimals": contract["decimals"],
            "dates": dates, "pricesE18": [row["replayPriceE18"] for row in series["rows"]],
            "elapsedSeconds": [(date.fromisoformat(day) - date.fromisoformat(dates[0])).days * 86400 for day in dates],
        }
    depth = json.loads(depth_path.read_text()) if depth_path and depth_path.exists() else None
    if depth:
        require(depth["chainId"] == 46630 and set(depth["pools"]) == set(EXPECTED_UNIVERSE), "incomplete onchain screening snapshot")
        require(all(row["enabled"] for row in depth["pools"].values()), "disabled deployed listing in universe")
        for ticker in EXPECTED_UNIVERSE:
            row = depth["pools"][ticker]
            require(row["stock"].lower() == windows[ticker]["stock"].lower() and row["pool"].lower() == windows[ticker]["pool"].lower(), "snapshot/book binding mismatch")
    return {
        "schema": "hedgefun-equity-daily-history-2025-v1", "year": 2025, "currency": "USD",
        "sourceBars": 2000, "selectedBars": 750, "universe": list(EXPECTED_UNIVERSE), "selectedTickers": selected,
        "unsupportedRequestedCandidates": ["AMD", "PLTR", "COIN"],
        "deployment": {"chainId": 46630, "factory": book["factory"], "tradeRouter": book["tradeRouter"],
                       "addressBookSha256": hashlib.sha256(book_path.read_bytes()).hexdigest()},
        "selectionPolicy": {"minimumMedianEquityDollarVolumeUsd": str(MIN_MEDIAN_DOLLAR_VOLUME),
                            "minimumAnnualizedDailyLogVolatilityPercent": str(MIN_ANNUALIZED_VOL_PERCENT),
                            "maximumTickers": TOP_N, "ranking": "descending annualized sample standard deviation of daily Close log returns; median equity dollar volume then ticker break ties",
                            "selectionUsesReturnsPerformance": False,
                            "sampleStatus": "descriptive in-sample selection using full-year 2025 volatility/volume, not a universe known prospectively on 2025-01-02",
                            "dollarVolumeDefinition": "daily split-adjusted Close times reported share volume; indicative equity-market turnover, not executable USD depth or testnet liquidity",
                            "volatilityDefinition": "sample standard deviation (n-1) of 249 daily Close log returns times sqrt(252), in percent",
                            "onchainDepthPolicy": "measured separately at a pinned chain block; full historical fork then probes actual swaps at first-2025 Close and fixed active liquidity; equity dollar volume is never a chain-liquidity proxy"},
        "priceBasis": "Yahoo Close already adjusted for splits; do not divide price or multiply token holdings again on split dates",
        "dividendPolicy": "Dividends are not paid to modeled stock-token holders or treasuries. Adj Close is preserved solely as source data and is never a trade/feed price; the replay is price-only, not a dividend-reinvested total-return backtest.",
        "timestampPolicy": "Provider daily timestamps label New York session open, not Close availability. Synthetic date offsets preserve calendar-day gaps but omit real intraday, DST and early-close timing.",
        "feedPrecision": "8 decimal places, half-even; encoded at E18 for the Solidity feed",
        "onchainDepthSnapshot": depth,
        "windows": windows, "candidates": candidates,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", type=Path, default=ROOT / "contractV2/data/equity-history-2025-sources")
    parser.add_argument("--book", type=Path, default=ROOT / "contractV2/deploy/testnet-v2-fresh-creator.json")
    parser.add_argument("--depth", type=Path, default=ROOT / "contractV2/data/equity-depth-snapshot.json")
    parser.add_argument("--output", type=Path, default=ROOT / "contractV2/data/equity-history-2025.json")
    args = parser.parse_args()
    result = build(args.sources, args.book, args.depth)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    rows = [{"ticker": ticker, **{key: json.dumps(value) if isinstance(value, list) else value for key, value in item["screening"].items()},
             "dividendEvents": item["dividendEventCount"], "closeDifferentFromAdjustedDays": item["closeDiffersFromAdjustedCloseDays"]}
            for ticker, item in result["candidates"].items()]
    with args.output.with_name("equity-screening-2025.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader(); writer.writerows(rows)
    print(json.dumps({"selected": result["selectedTickers"], "sourceBars": result["sourceBars"], "output": str(args.output)}, indent=2))


if __name__ == "__main__":
    main()

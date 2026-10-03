#!/usr/bin/env python3
"""Export actual local-fork cash allocation evidence, never infer option fair prices."""
import argparse
import gzip
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path

TICKERS = ("TSLA", "NVDA", "META")
SPLITS = (0, 5000, 10000)


def read_log(path):
    raw = Path(path).read_bytes()
    if raw.startswith(b"\x1f\x8b"):
        raw = gzip.decompress(raw)
    text = raw.decode()
    if "9 tests passed, 0 failed, 0 skipped" not in text:
        raise ValueError("requires complete clean nine-case execution")
    groups = defaultdict(list)
    profiles = {}
    epochs = defaultdict(list)
    offers = defaultdict(list)
    for line in text.splitlines():
        match = re.search(r"INCOME_(ROW|PROFILE|EPOCH|OPTION) (\{.*\})", line)
        if not match:
            continue
        kind, encoded = match.groups()
        row = json.loads(encoded)
        row = {k: int(v) if isinstance(v, str) and re.fullmatch(r"-?\d+", v) else v for k, v in row.items()}
        key = (row["ticker"], row["dividendBps"])
        if kind == "PROFILE":
            if key in profiles:
                raise ValueError("duplicate profile")
            profiles[key] = row
        else:
            {"ROW": groups, "EPOCH": epochs, "OPTION": offers}[kind][key].append(row)
    expected = {(t, s) for t in TICKERS for s in SPLITS}
    if set(groups) != expected or set(profiles) != expected:
        raise ValueError("missing or unexpected ticker/split")
    for key, rows in groups.items():
        if len(rows) != 250 or [r["dayIndex"] for r in rows] != list(range(250)):
            raise ValueError("incomplete chronological annual rows")
        if rows[-1]["hasOpenOption"]:
            raise ValueError("terminal return cannot carry unvalued option liability")
        if len(epochs[key]) != len(offers[key]) or rows[-1]["optionsSettled"] != len(epochs[key]):
            raise ValueError("not all funded options settled")
        if rows[0]["date"] != "2025-01-02" or rows[-1]["date"] != "2025-12-31":
            raise ValueError("unexpected date window")
        for row in rows:
            if row["stakingFundedRaw"] != row["stakingCashRaw"] + row["dividendsPaidARaw"] + row["dividendsPaidBRaw"]:
                raise ValueError("staking funding conservation")
            if row["totalAllocatedRaw"] != row["stakingFundedRaw"] + row["sponsorBuybackCashRaw"]:
                raise ValueError("allocation conservation")
    for ticker in TICKERS:
        # Allocation ratio is the only economic intervention; sponsor eligibility is identical.
        ref = epochs[(ticker, 0)]
        for split in SPLITS[1:]:
            for a, b in zip(ref, epochs[(ticker, split)]):
                if a["eligibleIncomeRaw"] != b["eligibleIncomeRaw"] or a["cumulativeRealizedNetRaw"] != b["cumulativeRealizedNetRaw"]:
                    raise ValueError("mismatched sponsor economics across splits")
    return raw, groups, profiles, epochs, offers


def summarize(groups, profiles, epochs):
    out = []
    for ticker in TICKERS:
        for split in SPLITS:
            key = ticker, split
            rows, profile = groups[key], profiles[key]
            first, final = rows[0], rows[-1]
            percent = lambda end, start: (end / start - 1) * 100
            out.append({
                "ticker": ticker, "dividendBps": split, "days": len(rows),
                "initialProductCapitalUsd": profile["productInitialExternalAssetsRaw"] / 1e6,
                "stockReturnPct": percent(final["stockOracleUsdE18"], first["stockOracleUsdE18"]),
                "funMarkReturnPct": percent(final["funOracleUsdE18"], first["funOracleUsdE18"]),
                "funVsStockRatioChangePct": percent(final["funStockE18"], first["funStockE18"]),
                "terminalProductExternalAssetsPlusPaidUsd": final["productGrossPlusPaidRaw"] / 1e6,
                "terminalProductReturnPct": percent(final["productGrossPlusPaidRaw"], profile["productInitialExternalAssetsRaw"]),
                "lpStockUnitsChangePct": percent(final["lpStockRaw"], first["lpStockRaw"]),
                "lpStockUnits": final["lpStockRaw"] / 1e18,
                "lpFunUnits": final["lpFunRaw"] / 1e18,
                "premiumsReceivedUsd": final["optionPremiumsRaw"] / 1e6,
                "settledPortfolioRealizedNetUsd": final["cumulativeRealizedNetRaw"] / 1e6,
                "eligibleIncomeAllocatedUsd": final["totalAllocatedRaw"] / 1e6,
                "sponsorBuybackUsd": final["sponsorBuybackCashRaw"] / 1e6,
                "sponsorFunBurned": final["sponsorBurnedRaw"] / 1e18,
                "stakingFundedUsd": final["stakingFundedRaw"] / 1e6,
                "stakingPaidUsd": (final["dividendsPaidARaw"] + final["dividendsPaidBRaw"]) / 1e6,
                "stakingRemainingCashUsd": final["stakingCashRaw"] / 1e6,
                "stakerACashUsd": final["dividendsPaidARaw"] / 1e6,
                "stakerBCashUsd": final["dividendsPaidBRaw"] / 1e6,
                "sponsorFinalCashUsd": final["sponsorCashRaw"] / 1e6,
                "sponsorFinalNavUsd": final["sponsorGrossAssetsRaw"] / 1e6,
                "settlements": final["optionsSettled"],
                "blockedAllocationEpochs": sum(e["eligibleIncomeRaw"] == 0 for e in epochs[key]),
                "sponsorLpStockFeesUnits": final["sponsorLpStockFeesRaw"] / 1e18,
            })
    return out


def charts(groups, rows, out):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    colors = {0: "#2563eb", 5000: "#7c3aed", 10000: "#059669"}
    labels = {0: "100% buyback", 5000: "50% buyback / 50% staking", 10000: "100% staking"}
    fig, axes = plt.subplots(3, 3, figsize=(14, 11), constrained_layout=True)
    for i, ticker in enumerate(TICKERS):
        for split in SPLITS:
            data = groups[(ticker, split)]
            start = data[0]
            axes[i, 0].plot([100 * r["funOracleUsdE18"] / start["funOracleUsdE18"] for r in data], color=colors[split], label=labels[split])
            axes[i, 1].plot([100 * r["lpStockRaw"] / start["lpStockRaw"] for r in data], color=colors[split])
            axes[i, 2].plot([(r["dividendsPaidARaw"] + r["dividendsPaidBRaw"]) / 1e6 for r in data], color=colors[split])
        for j, title in enumerate(("FUN spot mark (start=100)", "Locked LP stock units (start=100)", "Stakers: cash actually received ($)")):
            axes[i, j].set_title(ticker + " | " + title, fontsize=10)
            axes[i, j].set_xlabel("2025 trading-day index")
            axes[i, j].grid(alpha=.2)
    axes[0, 0].legend(fontsize=8)
    fig.suptitle("Actual cash allocation on local fork | synthetic covered-call quotes, external $10k sponsor", fontsize=12)
    fig.savefig(out / "income-allocation.png", dpi=170)
    fig.savefig(out / "income-allocation.svg")
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--charts", action="store_true")
    args = parser.parse_args()
    raw, groups, profiles, epochs, offers = read_log(args.log)
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    rows = summarize(groups, profiles, epochs)
    report = {"schemaVersion": 1, "scope": "2025 daily local testnet fork; synthetic 50bps/week covered call; independent $10k sponsor plus $20k FUN fund; experimental actual-funded staking",
              "limitations": ["Not native integration into a deployed FUN treasury", "Synthetic premiums and settlement oracle, no historical option chain", "Daily product gross assets exclude full option MTM; only terminal closed-option return is net of remaining obligations", "FUN spot marks are not executable liquidation returns or NAV redemption rights", "No stock dividends, transaction gas, real order books, spreads or actual user demand", "No future principal guarantee; gate only tests current marked capital at distribution", "7-day lock and reward stream; year-end unvested cash remains reserved"],
              "executionLogSha256": hashlib.sha256(raw).hexdigest(), "cases": rows}
    (output / "summary.json").write_text(json.dumps(report, indent=2) + "\n")
    payload = {"profiles": list(profiles.values()), "daily": [r for k in sorted(groups) for r in groups[k]], "epochs": [r for k in sorted(epochs) for r in epochs[k]], "offers": [r for k in sorted(offers) for r in offers[k]]}
    with open(output / "ledger.json.gz", "wb") as stream:
        stream.write(gzip.compress(json.dumps(payload, separators=(",", ":")).encode(), mtime=0))
    (output / "forge.log.gz").write_bytes(gzip.compress(raw, mtime=0))
    if args.charts:
        charts(groups, rows, output)
    print(json.dumps({"cases": len(rows), "dailyRows": sum(len(x) for x in groups.values()), "output": str(output)}))


if __name__ == "__main__":
    main()

"""2025 screening rejects data/selection mistakes, without live network calls."""
import copy
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]


def load_tool(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    return tool


DATA = load_tool("equity_history_data")
DEPTH = load_tool("equity_depth_snapshot")


class EquityDataTest(unittest.TestCase):
    def test_dividend_adjusted_close_is_retained_but_not_used_as_feed_price(self):
        raw = (ROOT / "data/equity-history-2025-sources/NVDA.json").read_bytes()
        normalized = DATA.normalize(raw, "NVDA")
        self.assertEqual(normalized["dividendEventCount"], 4)
        self.assertEqual(normalized["closeDiffersFromAdjustedCloseDays"], 250)
        for row in normalized["rows"]:
            self.assertEqual(row["replayPriceE18"], int(DATA.D(row["replayCloseUsd"]) * DATA.E18))
            self.assertNotEqual(row["closeUsd"], row["adjustedCloseUsd"])

    def test_all_eight_sources_reproduce_selection_and_exclusions(self):
        result = DATA.build(ROOT / "data/equity-history-2025-sources",
                            ROOT / "deploy/testnet-v2-fresh-creator.json", ROOT / "data/equity-depth-snapshot.json")
        self.assertEqual(result["selectedTickers"], ["TSLA", "NVDA", "META"])
        self.assertEqual(sum(item["rowCount"] for item in result["candidates"].values()), 2000)
        self.assertIn("median_equity_dollar_volume_below_2b", result["candidates"]["GME"]["screening"]["exclusionReasons"])
        self.assertIn("annualized_daily_log_volatility_below_30_percent", result["candidates"]["MSFT"]["screening"]["exclusionReasons"])
        self.assertEqual(result["candidates"]["AMZN"]["screening"]["eligibleVolatilityRank"], 4)
        self.assertFalse(result["selectionPolicy"]["selectionUsesReturnsPerformance"])

    def test_duplicate_dates_wrong_instrument_and_missing_bar_are_rejected(self):
        original = json.loads((ROOT / "data/equity-history-2025-sources/TSLA.json").read_text())
        bad = copy.deepcopy(original)
        bad["chart"]["result"][0]["timestamp"][1] = bad["chart"]["result"][0]["timestamp"][0]
        with self.assertRaises(ValueError):
            DATA.normalize(json.dumps(bad).encode(), "TSLA")
        with self.assertRaises(ValueError):
            DATA.normalize(json.dumps(original).encode(), "NVDA")
        bad = copy.deepcopy(original)
        bad["chart"]["result"][0]["timestamp"].pop()
        with self.assertRaises(ValueError):
            DATA.normalize(json.dumps(bad).encode(), "TSLA")

    def test_analytical_depth_uses_pair_direction_and_fee(self):
        snapshot = json.loads((ROOT / "data/equity-depth-snapshot.json").read_text())
        for ticker, row in snapshot["pools"].items():
            initial = int(row["sqrtPriceX96"])
            for probe in row["constantActiveLiquidityProbes"]:
                buying = probe["direction"] == "USDG_to_stock"
                zero_for_one = not row["stockIsToken0"] if buying else row["stockIsToken0"]
                after, out = DEPTH.analytical_swap(initial, int(row["activeLiquidity"]), int(probe["inputRaw"]), row["feeMillionths"], zero_for_one)
                with self.subTest(ticker=ticker, side=buying, notional=probe["notionalUsd"]):
                    self.assertEqual(out, int(probe["estimatedOutputRaw"]))
                    price_delta = DEPTH.spot_usd(after, row["stockIsToken0"]) - DEPTH.spot_usd(initial, row["stockIsToken0"])
                    self.assertGreater(price_delta if buying else -price_delta, 0)
                    self.assertGreaterEqual(DEPTH.D(probe["estimatedFeeAndAverageImpactBps"]), DEPTH.D(row["feeMillionths"])/100)


if __name__ == "__main__":
    unittest.main()

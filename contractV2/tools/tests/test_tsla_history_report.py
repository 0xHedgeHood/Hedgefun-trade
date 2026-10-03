"""Reporting invariants: historical windows, drawdown and evidence rejection."""

import copy
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("tsla_history_report", ROOT / "tools/tsla_history_report.py")
TOOL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(TOOL)


class MetricsTest(unittest.TestCase):
    def test_drawdown_uses_running_peak_not_start_or_endpoint(self):
        result = TOOL.drawdown([100, 200, 150, 180, 90, 120], [f"day-{i}" for i in range(6)])
        self.assertEqual(result["percent"], "55")
        self.assertEqual((result["peakIndex"], result["troughIndex"]), (1, 4))
        self.assertEqual(TOOL.drawdown([100, 110, 110], ["a", "b", "c"])["percent"], "0")
        self.assertEqual(TOOL.change_percent(100, 120), 20)
        with self.assertRaises(ValueError):
            TOOL.change_percent(0, 120)

    def test_invalid_drawdown_marks_do_not_silently_drop_days(self):
        for values, dates in (([], []), ([100, 90], ["a"]), ([100, 0], ["a", "b"])):
            with self.subTest(values=values), self.assertRaises(ValueError):
                TOOL.drawdown(values, dates)

    def test_historical_window_rejects_wrong_elapsed_day_or_double_split(self):
        data = json.loads((ROOT / "data/tsla-history-2022-2025.json").read_text())
        TOOL.validate_data(data)
        wrong_day = copy.deepcopy(data)
        wrong_day["windows"]["2022"]["elapsedSeconds"][5] += 601
        with self.assertRaises(ValueError):
            TOOL.validate_data(wrong_day)
        split_twice = copy.deepcopy(data)
        split_twice["windows"]["2022"]["pricesE18"][0] //= 3
        with self.assertRaises(ValueError):
            TOOL.validate_data(split_twice)


class SavedEvidenceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        evidence = ROOT / "deploy/tsla-history-2026-10-03/forge.log"
        if not evidence.exists():
            raise unittest.SkipTest("versioned raw historical replay evidence is not present")
        cls.raw = evidence.read_text()
        cls.rows, cls.venues = TOOL.parse_log(cls.raw)
        cls.data = json.loads((ROOT / "data/tsla-history-2022-2025.json").read_text())
        cls.liquidity = cls.venues[0]["activeLiquidityBefore"]
        cls.block = cls.rows[0]["forkBlock"]

    def test_all_saved_days_validate(self):
        groups = TOOL.validate_rows(self.rows, self.data, self.block, self.liquidity)
        self.assertEqual(len(groups), 12)
        self.assertEqual(sum(map(len, groups.values())), 3009)

    def test_failures_and_duplicate_days_are_rejected(self):
        with self.assertRaises(ValueError):
            TOOL.parse_log(self.raw.replace("12 passed; 0 failed; 0 skipped", "11 passed; 1 failed; 0 skipped"))
        rows = copy.deepcopy(self.rows)
        rows[1] = copy.deepcopy(rows[0])
        with self.assertRaises(ValueError):
            TOOL.validate_rows(rows, self.data, self.block, self.liquidity)

    def test_wrong_liquidity_or_valuation_is_rejected(self):
        for key, delta in (("stockPoolLiquidity", 1), ("funOracleUsdE18", 1), ("treasuryNavOracleUsdE18", 10**12)):
            rows = copy.deepcopy(self.rows)
            rows[8][key] += delta
            with self.subTest(key=key), self.assertRaises(ValueError):
                TOOL.validate_rows(rows, self.data, self.block, self.liquidity)

    def test_summary_matches_saved_baseline_and_buyback_spend(self):
        groups = TOOL.validate_rows(self.rows, self.data, self.block, self.liquidity)
        group = groups[(2022, "keeper500")]
        daily = [TOOL.enrich(row, group[0]) for row in group]
        summary = TOOL.summarize(daily)
        self.assertEqual(summary["firstDate"], "2022-01-03")
        self.assertEqual(summary["lastDate"], "2022-12-30")
        self.assertEqual(summary["actions"], 33)
        self.assertEqual(summary["buybacks"], 11)
        self.assertEqual(summary["cumulativeBuybackUsdAtExecutionOracleMarks"], "299.165856")
        self.assertEqual(TOOL.D(summary["treasuryNavFirstUsdOracleMark"]),
                         TOOL.D(group[0]["treasuryNavOracleUsdE18"]) / TOOL.E18)


if __name__ == "__main__":
    unittest.main()

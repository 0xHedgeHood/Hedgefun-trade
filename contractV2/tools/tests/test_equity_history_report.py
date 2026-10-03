"""Saved-evidence and rejection tests for the 39-case equity fee replay."""

import gzip
import importlib.util
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
SPEC = importlib.util.spec_from_file_location("equity_history_report", ROOT / "tools/equity_history_report.py")
TOOL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(TOOL)


class EncodingTest(unittest.TestCase):
    def test_gzip_is_deterministic_and_round_trips_exact_raw_bytes(self):
        raw = b'raw event "100000000000000000000000001"\n'
        one, two = TOOL.packed_gzip(raw), TOOL.packed_gzip(raw)
        self.assertEqual(one, two)
        self.assertEqual(one[4:8], b"\0\0\0\0")
        self.assertEqual(gzip.decompress(one), raw)

    def test_parser_preserves_big_integers_and_rejects_ambiguous_json(self):
        value = 10**35 + 1
        self.assertEqual(TOOL.event_json('{"amount":"%s","enabled":false}' % value),
                         {"amount": value, "enabled": False})
        for raw in ('{"amount":1.5}', '{"amount":1e18}', '{"amount":NaN}',
                    '{"amount":1,"amount":2}', '[]'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                TOOL.event_json(raw)


class SavedEquityEvidenceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        evidence = ROOT / "deploy/equity-history-2025-2026-10-03/forge.log.gz"
        if not evidence.exists():
            raise unittest.SkipTest("versioned equity replay archive not present")
        cls.raw = TOOL.read_log(evidence)
        cls.events = TOOL.parse(cls.raw)
        cls.data = json.loads((ROOT / "data/equity-history-2025.json").read_text())

    def mutate_row(self, name, value):
        events = dict(self.events)
        events["ROW"] = list(events["ROW"])
        events["ROW"][100] = {**events["ROW"][100], name: value}
        return events

    def test_all_saved_rows_and_actual_depth_validate(self):
        groups, metadata, liquidity, block = TOOL.validate(self.events, self.data)
        self.assertEqual((len(groups), len(metadata), sum(map(len, groups.values())), block),
                         (39, 39, 9750, 128172359))
        self.assertEqual(set(liquidity), {"TSLA", "NVDA", "META"})
        coverage = TOOL.depth_coverage(self.events, groups)
        self.assertTrue(all(row["coversObservedStrategySizeByNotional"] for row in coverage))
        self.assertTrue(all(TOOL.D(row["maxStrategyCashReductionUsdgIncludingBounty"]) > 2000 for row in coverage))

    def test_missing_conversion_source_cannot_silently_be_zero(self):
        events = dict(self.events)
        events["ROW"] = [{key: value for key, value in row.items() if key != "conversionGeneratedLpFunRaw"}
                         for row in events["ROW"]]
        with self.assertRaisesRegex(ValueError, "missing critical raw fields"):
            TOOL.validate(events, self.data)

    def test_mutated_burn_depth_valuation_or_fee_balances_are_rejected(self):
        for field in ("totalSupplyRaw", "stockPoolLiquidity", "funOracleUsdE18",
                      "treasuryNavOracleUsdE18", "externalAssetsUsdgRaw", "conversionGeneratedLpFunRaw"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                TOOL.validate(self.mutate_row(field, self.events["ROW"][100][field] + 1), self.data)

    def test_missing_days_failed_suite_and_changed_gate_are_rejected(self):
        with self.assertRaises(ValueError):
            TOOL.parse(self.raw.replace("39 passed; 0 failed; 0 skipped", "38 passed; 1 failed; 0 skipped"))
        events = dict(self.events)
        events["ROW"] = [self.events["ROW"][1]] + self.events["ROW"][1:]
        with self.assertRaises(ValueError):
            TOOL.validate(events, self.data)
        events = dict(self.events)
        events["PROFILE"] = [{**row, "maxSlippageBps": 101} for row in events["PROFILE"]]
        with self.assertRaisesRegex(ValueError, "execution gates"):
            TOOL.validate(events, self.data)

    def test_summary_and_matched_controls_reconcile_independently(self):
        groups, _, _, _ = TOOL.validate(self.events, self.data)
        summaries = []
        for group in groups.values():
            enriched = [TOOL.enrich(row, group[0]) for row in group]
            summary = TOOL.summary(enriched)
            direct = (TOOL.D(group[-1]["externalAssetsUsdgRaw"]) / group[0]["externalAssetsUsdgRaw"] - 1) * 100
            self.assertEqual(summary["externalAssetsChangePercent"], TOOL.display(direct))
            self.assertEqual(summary["flowPairs"], 0 if group[0]["profile"] == "baseline" else 249)
            summaries.append(summary)
        comparisons = TOOL.matched_comparisons(summaries)
        self.assertEqual(len(comparisons["harvestMatchedPairs"]), 18)
        self.assertEqual(len(comparisons["strategyVersusMatchedFlowControl"]), 30)
        pair = next(row for row in comparisons["harvestMatchedPairs"] if row["ticker"] == "TSLA" and row["profile"] == "p500")
        on, off = groups[("TSLA", "p500", True)][-1], groups[("TSLA", "p500", False)][-1]
        self.assertEqual(TOOL.D(pair["externalAssetsDeltaUsdg"]) * 10**6,
                         on["externalAssetsUsdgRaw"] - off["externalAssetsUsdgRaw"])
        self.assertLess(TOOL.D(pair["externalAssetsDeltaUsdg"]), TOOL.D(".05"))
        self.assertGreater(TOOL.D(pair["funOracleMarkChangeDeltaPercentagePoints"]), 2)


if __name__ == "__main__":
    unittest.main()

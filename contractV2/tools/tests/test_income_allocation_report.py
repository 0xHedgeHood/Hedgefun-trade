import gzip
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('income_report', ROOT / 'tools/income_allocation_report.py')
report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report)


class IncomeEvidenceGuards(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = gzip.decompress((ROOT / 'deploy/all-strategy-income-2026-10-03/income/forge.log.gz').read_bytes()).decode()

    def parse(self, text):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'forge.log'
            path.write_text(text)
            return report.read_log(path)

    def changed_row(self, predicate, change):
        lines = self.text.splitlines()
        for i, line in enumerate(lines):
            if 'INCOME_ROW {' in line:
                prefix, encoded = line.split('INCOME_ROW ', 1)
                row = json.loads(encoded)
                if predicate(row):
                    change(row)
                    lines[i] = prefix + 'INCOME_ROW ' + json.dumps(row)
                    return '\n'.join(lines)
        self.fail('fixture row not found')

    def test_terminal_uses_contributed_capital_not_first_gross_row(self):
        _, groups, profiles, epochs, _ = self.parse(self.text)
        summary = report.summarize(groups, profiles, epochs)
        self.assertEqual(len(summary), 9)
        for row in summary:
            key = row['ticker'], row['dividendBps']
            first, last = groups[key][0], groups[key][-1]
            initial = profiles[key]['productInitialExternalAssetsRaw']
            self.assertGreater(first['productGrossPlusPaidRaw'], initial)
            self.assertAlmostEqual(row['terminalProductReturnPct'], (last['productGrossPlusPaidRaw'] / initial - 1) * 100)

    def test_unsettled_terminal_cannot_be_reported_as_net(self):
        text = self.changed_row(lambda row: row['dayIndex'] == 249, lambda row: row.update(hasOpenOption=True))
        with self.assertRaisesRegex(ValueError, 'unvalued option'):
            self.parse(text)

    def test_missing_daily_row_rejected(self):
        lines = self.text.splitlines()
        for i, line in enumerate(lines):
            if 'INCOME_ROW {' in line:
                del lines[i]
                break
        with self.assertRaisesRegex(ValueError, 'incomplete chronological'):
            self.parse('\n'.join(lines))

    def test_fake_reward_cash_rejected(self):
        text = self.changed_row(lambda row: row['dayIndex'] == 249, lambda row: row.update(stakingCashRaw=int(row['stakingCashRaw']) + 1))
        with self.assertRaisesRegex(ValueError, 'staking funding conservation'):
            self.parse(text)

    def test_failed_or_skipped_execution_rejected(self):
        with self.assertRaisesRegex(ValueError, 'clean nine-case'):
            self.parse(self.text.replace('9 tests passed, 0 failed, 0 skipped', '8 tests passed, 1 failed, 0 skipped'))


if __name__ == '__main__':
    unittest.main()

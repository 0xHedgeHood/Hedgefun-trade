from decimal import Decimal
import importlib.util
import json
from pathlib import Path
import unittest

TOOLS = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('tsla_history_data', TOOLS/'tsla_history_data.py')
history = importlib.util.module_from_spec(spec)
spec.loader.exec_module(history)
RAW = (TOOLS.parent/'data/tsla-history-2022-2025-source.json').read_bytes()


class TslaHistoricalDataTest(unittest.TestCase):
    def edited(self, change):
        data = json.loads(RAW)
        change(data['chart']['result'][0])
        return json.dumps(data).encode()

    def test_full_saved_sample_is_valid_and_feed_representable(self):
        data = history.normalize(RAW)
        self.assertEqual(data['rowCount'], 1003)
        self.assertEqual([len(w['dates']) for w in data['windows'].values()], [251, 250, 252, 250])
        for row in data['rows']:
            self.assertEqual(row['replayPriceE18'] % 10**10, 0)
            self.assertLessEqual(abs(Decimal(row['replayCloseUsd'])-Decimal(row['closeUsd'])), Decimal('0.000000005'))

    def test_split_is_not_applied_twice(self):
        rows = {r['date']: r for r in history.normalize(RAW)['rows']}
        before = Decimal(rows['2022-08-24']['closeUsd'])
        after = Decimal(rows['2022-08-25']['replayCloseUsd'])
        self.assertTrue(Decimal('0.99') < after/before < Decimal('1.01'))
        self.assertTrue(Decimal('290') < after < Decimal('300'))

    def test_weekend_gap_preserved_without_forward_filled_prices(self):
        w = history.normalize(RAW)['windows']['2022']
        friday, monday = w['dates'].index('2022-01-07'), w['dates'].index('2022-01-10')
        self.assertEqual(monday, friday+1)
        self.assertEqual(w['elapsedSeconds'][monday]-w['elapsedSeconds'][friday], 3*86400)

    def test_missing_price_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Missing'):
            history.normalize(self.edited(lambda s: s['indicators']['quote'][0]['close'].__setitem__(2, None)))

    def test_duplicate_session_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'unique'):
            history.normalize(self.edited(lambda s: s['timestamp'].__setitem__(2, s['timestamp'][1])))

    def test_changed_adjustment_basis_requires_review(self):
        with self.assertRaisesRegex(ValueError, 'corporate-action'):
            history.normalize(self.edited(lambda s: s['indicators']['adjclose'][0]['adjclose'].__setitem__(2, 42)))

    def test_mismatched_symbol_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Wrong instrument'):
            history.normalize(self.edited(lambda s: s['meta'].__setitem__('symbol', 'NVDA')))

    def test_inconsistent_ohlc_range_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'OHLC range'):
            history.normalize(self.edited(lambda s: s['indicators']['quote'][0]['high'].__setitem__(2, 1)))


if __name__ == '__main__':
    unittest.main()

"""Economic/state transitions, without network, signing or future-price inputs."""
import unittest

from tools import v2_cycle_backtest as v


def ledger(*, rule=None, cycle=True, initial=1000., friction=0., limits=None):
    return v.Ledger(100., rule or v.RULES['live'], cycle=cycle, initial=initial,
                    friction=friction, limits=limits)


class CycleTransitions(unittest.TestCase):
    def test_rising_price_without_a_sale_cannot_arm_or_buy(self):
        book = ledger(rule=v.RULES['trend'])
        book.cash = 1000.
        event, _ = book.step(110., 2000, 2000)
        self.assertIsNone(event)
        self.assertIsNone(book.pending)

    def test_stop_recovery_requires_600_seconds_and_a_new_quote_exactly(self):
        book = ledger()
        event, _ = book.step(95., 1000, 900)
        self.assertEqual(event['kind'], 'stop')
        self.assertIsNotNone(book.pending)
        self.assertFalse(book.recovery_due(100., 1599, 1000))
        self.assertFalse(book.recovery_due(100., 1600, 900))
        self.assertTrue(book.recovery_due(99.75, 1600, 1000))
        event, _ = book.step(99.75, 1600, 1000)
        self.assertEqual(event['kind'], 'recovery')
        self.assertIsNone(book.pending)
        self.assertIsNone(book.last_stop)
        # The gate is one-shot; a further rise under TP1 cannot buy again.
        event, _ = book.step(100., 2500, 2000)
        self.assertIsNone(event)

    def test_failed_or_subminimum_recovery_preserves_pending_and_books(self):
        book = ledger()
        book.step(95., 1000, 1000)
        before = (book.cash, book.buyback, list(book.lots), book.pending, book.last_stop)
        for fill in (0., .001):
            event, _ = book.step(100., 2000, 2000, fill_fraction=fill)
            self.assertIsNone(event)
            self.assertEqual((book.cash, book.buyback, book.lots, book.pending, book.last_stop), before)
        event, _ = book.step(100., 2000, 2000)
        self.assertEqual(event['kind'], 'recovery')

    def test_recovery_is_capped_but_original_dip_is_not_capped(self):
        rule = v.Rule('single TP', .05, 0., .05, 0., .50)
        book = ledger(rule=rule, initial=100000.)
        book.lots = [v.Lot(1., 100.)]
        book.cash = 10000.
        book.step(105., 1000, 1000)
        event, _ = book.step(111., 2000, 2000)
        self.assertEqual(event['kind'], 'recovery')
        self.assertAlmostEqual(event['cashSpent'], 2000. * .995 * 1.005)
        # Any successful buy closes the upward gate, but the old dip ladder lives on.
        event, _ = book.step(105., 3000, 3000)
        self.assertEqual(event['kind'], 'dip')
        self.assertGreater(event['cashSpent'], 2000.)

    def test_stop_then_profit_then_buy_priority_and_tp1_chunk_accounting(self):
        book = ledger(rule=v.RULES['scalp'], initial=10000.)
        for at in (1000, 2000, 3000):
            event, _ = book.step(130., at, at)
            self.assertEqual(event['kind'], 'tp1')
        self.assertFalse(book.lots[0].half)
        event, _ = book.step(130., 4000, 4000)
        self.assertEqual(event['kind'], 'tp1')
        self.assertTrue(book.lots[0].half)
        # Recovery is price-due but profit still wins until the remaining lot exits.
        event, _ = book.step(140., 5000, 5000)
        self.assertEqual(event['kind'], 'tp2')
        book.rule = v.RULES['live']
        book.lots.append(v.Lot(1., 200.))
        event, _ = book.step(140., 6000, 6000)
        self.assertEqual(event['kind'], 'stop')

    def test_partial_tp_and_stop_only_update_state_for_actual_fills(self):
        book = ledger(rule=v.RULES['scalp'])
        event, _ = book.step(110., 1000, 1000, fill_fraction=.25)
        self.assertAlmostEqual(event['qty'], 1.25)
        self.assertAlmostEqual(book.lots[0].tp1_left, 3.75)
        self.assertFalse(book.lots[0].half)
        book.rule = v.RULES['live']
        book.step(90., 2000, 2000, fill_fraction=.75)
        self.assertLessEqual(book.lots[0].tp1_left, book.lots[0].qty)

    def test_new_lot_uses_fill_cost_without_keeper_bounty(self):
        book = ledger(friction=.01)
        book.step(95., 1000, 1000)
        before = book.cash
        event, _ = book.step(100., 2000, 2000, fill_fraction=.5)
        self.assertEqual(event['kind'], 'recovery')
        self.assertAlmostEqual(book.lots[-1].cost, 100. / .99)
        self.assertAlmostEqual(before - book.cash, event['cashSpent'])
        self.assertAlmostEqual(book.lots[-1].qty * book.lots[-1].cost * 1.005, event['cashSpent'])

    def test_existing_dip_consumes_pending_without_new_tp_cooldown(self):
        book = ledger(rule=v.RULES['scalp'])
        book.step(105., 1000, 1000)
        self.assertIsNotNone(book.pending)
        event, _ = book.step(99.75, 1001, 1001)
        self.assertEqual(event['kind'], 'dip')
        self.assertIsNone(book.pending)

    def test_original_stop_dip_still_requires_new_lower_quote_and_cooldown(self):
        for cycle in (False, True):
            book = ledger(cycle=cycle)
            book.step(95., 1000, 1000)
            self.assertIsNone(book.step(90.25, 1599, 1599)[0])
            self.assertIsNone(book.step(90.25, 1600, 1000)[0])
            event, _ = book.step(90.25, 1600, 1600)
            self.assertEqual(event['kind'], 'dip')
            self.assertIsNone(book.pending)

    def test_later_successful_sale_arms_again(self):
        book = ledger()
        book.step(95., 1000, 1000)
        book.step(100., 2000, 2000)
        self.assertIsNone(book.pending)
        event, _ = book.step(110., 3000, 3000)
        self.assertEqual(event['kind'], 'tp1')
        self.assertEqual(book.pending.price, 110.)

    def test_closed_market_sale_cancels_pending_and_small_sale_does_not_reanchor(self):
        book = ledger(rule=v.RULES['scalp'])
        book.step(105., 1000, 1000)
        old = book.pending
        book.lots = [v.Lot(.01, 100.)]
        book.step(106., 2000, 2000)
        self.assertEqual(book.pending, old)  # Actual principal sale is below $5.
        self.assertEqual(book.last_sale_price, 106.)  # Recovery uses its separate 105 anchor.
        book.lots = [v.Lot(1., 100.)]
        book.step(105., 3000, 3000, live=False)
        self.assertIsNone(book.pending)

    def test_subminimum_later_stop_still_renews_cooldown_and_quote_gate(self):
        book = ledger()
        book.step(95., 1000, 1000)
        old = book.pending
        book.lots = [v.Lot(.01, 200.)]
        book.step(100., 2000, 2000)
        self.assertEqual(book.pending, old)
        self.assertFalse(book.recovery_due(101., 2500, 2500))
        self.assertFalse(book.recovery_due(101., 2600, 2000))
        self.assertTrue(book.recovery_due(101., 2600, 2600))

    def test_small_profit_cannot_erase_recovery_wait_after_a_later_small_stop(self):
        book = ledger(initial=10.)
        book.cash = 100.
        self.assertEqual(book.step(94., 1000, 1000)[0]['kind'], 'stop')
        old = book.pending
        # A $6 donation is legitimately bookable, but each half TP sells $3 principal.
        book.lots.append(v.Lot(.05, 120.))
        self.assertEqual(book.step(126., 1601, 1601)[0]['kind'], 'tp1')
        book.lots.append(v.Lot(.06, 100.))
        self.assertEqual(book.step(100., 1602, 1602)[0]['kind'], 'stop')
        self.assertEqual(book.step(106., 1603, 1603)[0]['kind'], 'tp1')
        self.assertIsNone(book.last_stop)  # Keep the original TP-to-dip behavior.
        self.assertEqual(book.pending, old)
        self.assertFalse(book.recovery_due(106., 1603, 1603))
        self.assertIsNone(book.step(106., 1603, 1603)[0])
        self.assertIsNone(book.step(106., 2201, 2201)[0])
        self.assertIsNone(book.step(106., 2202, 1602)[0])
        self.assertEqual(book.step(106., 2202, 1603)[0]['kind'], 'recovery')

    def test_small_profit_after_stop_keeps_original_dip_available(self):
        book = ledger(initial=10.)
        book.cash = 100.
        book.step(94., 1000, 1000)
        book.lots = [v.Lot(.01, 120.), v.Lot(.06, 100.)]
        book.step(100., 1602, 1602)
        book.step(106., 1603, 1603)
        # Recovery still waits, while the shipped TP-to-dip rule stays immediate.
        self.assertEqual(book.step(100., 1604, 1604)[0]['kind'], 'dip')
        self.assertIsNone(book.pending)


class ReplayEvidence(unittest.TestCase):
    def test_future_rows_do_not_change_past_actions_or_states(self):
        series = [{'date': f'2026-01-{i + 1:02}', 'price': p}
                  for i, p in enumerate([100., 90., 100., 110., 90., 120., 1000.])]
        for cycle in (False, True):
            full = v.replay(series, v.RULES['live'], .003, cycle=cycle)
            short = v.replay(series[:4], v.RULES['live'], .003, cycle=cycle)
            self.assertEqual(full['points'][:4], short['points'])
            self.assertEqual([e for e in full['events'] if e['i'] < 4], short['events'])

    def test_twenty_day_diagnostic_excludes_truncated_sales(self):
        series = [{'date': f'2026-01-{i + 1:02}', 'price': 100. + i * 10} for i in range(23)]
        result = v.replay(series, v.RULES['scalp'], .0005)
        summary = v.summarize(result, series)
        self.assertEqual(summary['eligibleSales20'], sum(e['i'] <= 2 for e in result['events']))
        self.assertLess(summary['eligibleSales20'], len(result['events']))

    def test_frozen_windows_are_aligned_and_baseline_does_not_trade(self):
        _, prices, dates, _ = v.load_prices(v.ROOT / 'data/v2-cycle-prices.json')
        self.assertEqual(dates[-1], '2026-09-30')
        for ticker, fee in v.STOCK_FEES.items():
            series = [prices[ticker][date] for date in dates[-22:]]
            result = v.replay(series, v.RULES['live'], fee, cycle=True)
            self.assertAlmostEqual(result['points'][0]['nav'], 10000.)
            self.assertTrue(all(e['i'] >= 1 for e in result['events']))
            self.assertEqual(v.summarize(result, series)['bars'], 21)

    def test_original_mode_matches_prior_frozen_return_and_action_references(self):
        # Values independently produced by the previously reviewed daily replay,
        # before adding the recovery branch. They cover all four stocks and stop,
        # staged TP, dip and the passive wide-TP behavior, not this implementation.
        expected = {'NVDA': (-5.716, 8), 'TSLA': (-8.850, 15),
                    'GME': (10.491, 16), 'AAPL': (-8.113, 5)}
        _, prices, dates, _ = v.load_prices(v.ROOT / 'data/v2-cycle-prices.json')
        selected = [date for date in dates if date >= '2025-12-31']
        for ticker, (return_pct, actions) in expected.items():
            series = [prices[ticker][date] for date in selected]
            result = v.replay(series, v.RULES['live'], v.STOCK_FEES[ticker])
            summary = v.summarize(result, series)
            self.assertAlmostEqual(summary['returnPct'], return_pct, delta=.001)
            self.assertEqual(summary['actions'], actions)
        series = [prices['NVDA'][date] for date in selected]
        original = v.replay(series, v.RULES['trend'], .0005)
        cycle = v.replay(series, v.RULES['trend'], .0005, cycle=True)
        self.assertEqual(original['events'], [])
        self.assertEqual(cycle['events'], [])
        self.assertEqual(original['points'], cycle['points'])


if __name__ == '__main__':
    unittest.main()

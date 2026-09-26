"""Economic regression checks for the research-only opening batch model."""

import unittest

from lab.batch_opening import simulate_batch_opening


def run(bot: str = "0.1", user: str = "140", slippage_bps: int = 100) -> dict:
    return simulate_batch_opening(
        {"bot_stock": bot, "user_stock": user, "slippage_bps": slippage_bps}
    )


class OpeningBatchModelTest(unittest.TestCase):
    def test_small_bot_profits_in_naive_batch_but_loses_after_price_alignment(self):
        result = run()
        current = result["current_second_one"]
        naive = result["naive_batch"]
        aligned = result["aligned_batch"]
        self.assertTrue(all(case["user_filled"] for case in (current, naive, aligned)))
        self.assertAlmostEqual(current["bot_pnl_stock"], 0.340310261340061735, places=12)
        self.assertAlmostEqual(naive["bot_pnl_stock"], 0.20740866440407266, places=12)
        self.assertLess(aligned["bot_pnl_stock"], 0)
        self.assertGreater(naive["spot_to_clearing_ratio"], 1)
        self.assertAlmostEqual(aligned["spot_to_clearing_ratio"], 1, places=12)
        for case in (current, naive, aligned):
            self.assertGreaterEqual(case["user_out_fun"], case["user_min_out_fun"])
            self.assertEqual(case["user_refund_stock"], 0)

    def test_large_bot_exposes_naive_post_batch_arbitrage_and_cost_of_alignment(self):
        result = run("20", "140", 2000)
        naive, aligned = result["naive_batch"], result["aligned_batch"]
        self.assertTrue(naive["user_filled"])
        self.assertTrue(aligned["user_filled"])
        self.assertAlmostEqual(naive["bot_pnl_stock"], 30.029411764705884, places=10)
        self.assertAlmostEqual(aligned["bot_pnl_stock"], -5.078947368421052, places=10)
        self.assertLess(aligned["user_out_fun"], naive["user_out_fun"])
        self.assertGreater(aligned["user_loss_pct"], naive["user_loss_pct"])

    def test_min_out_refunds_user_before_settlement(self):
        result = run("20", "140", 100)
        for name in ("current_second_one", "naive_batch", "aligned_batch"):
            case = result[name]
            self.assertFalse(case["user_filled"])
            self.assertIsNone(case["user_out_fun"])
            self.assertEqual(case["user_refund_stock"], 140)
            self.assertLess(case["bot_pnl_stock"], 0)

    def test_tenth_percent_min_out_can_still_fill_and_late_demand_remains_profitable(self):
        result = run("0.01", "140", 10)
        self.assertTrue(result["current_second_one"]["user_filled"])
        self.assertGreater(result["current_second_one"]["bot_pnl_stock"], 0)
        self.assertTrue(result["naive_batch"]["user_filled"])
        self.assertGreater(result["naive_batch"]["bot_pnl_stock"], 0)
        self.assertTrue(result["aligned_batch"]["user_filled"])
        self.assertLess(result["aligned_batch"]["bot_pnl_stock"], 0)
        self.assertGreater(result["late_user"]["aligned_batch"]["bot_pnl_stock"], 0)

    def test_cross_batch_stale_min_out_can_refund_while_fresh_quote_fills(self):
        result = run("50", "140", 100)
        for method in ("naive_batch", "aligned_batch"):
            late = result["late_user"][method]
            self.assertTrue(late["user_filled"])
            self.assertGreaterEqual(late["user_out_fun"], late["fresh_user_min_out_fun"])
            self.assertFalse(late["stale_user_filled"])
            self.assertEqual(late["stale_user_refund_stock"], 140)
            self.assertLess(late["stale_bot_pnl_stock"], 0)

    def test_same_batch_allocations_conserve_net_tokens_and_stock(self):
        for method in ("naive_batch", "aligned_batch"):
            bot, user = 0.1, 140
            result = run(str(bot), str(user), 100)[method]
            self.assertTrue(result["user_filled"])
            total = bot + user
            gross = (1_000_000 - 50_000_000 / (50 + total)) if method == "naive_batch" else (
                total * 1_000_000 / (50 + 2 * total))
            self.assertAlmostEqual(result["bot_tokens_fun"] + result["user_out_fun"],
                                   gross * 0.9, places=8)
            self.assertAlmostEqual(result["bot_tokens_fun"] / bot,
                                   result["user_out_fun"] / user, places=9)
            self.assertEqual(result["user_refund_stock"], 0)

    def test_retaining_opening_tax_keeps_min_out_and_changes_sniper_economics(self):
        result = run("0.1", "140", 100)
        for method in ("naive_batch", "aligned_batch"):
            plain = result[method]
            taxed = result["retained_opening_tax"][method]
            self.assertEqual(taxed["opening_buy_tax_pct"], 66)
            self.assertTrue(taxed["user_filled"])
            self.assertGreaterEqual(taxed["user_out_fun"], taxed["user_min_out_fun"])
            self.assertLess(taxed["bot_pnl_stock"], plain["bot_pnl_stock"])
            self.assertAlmostEqual(taxed["user_out_fun"] / plain["user_out_fun"], 0.34 / 0.9)

    def test_rejects_unbounded_or_malformed_inputs(self):
        invalid = (
            {},
            {"bot_stock": True, "user_stock": "140", "slippage_bps": 100},
            {"bot_stock": "NaN", "user_stock": "140", "slippage_bps": 100},
            {"bot_stock": "20", "user_stock": "180", "slippage_bps": 100},
            {"bot_stock": "0.1", "user_stock": "140", "slippage_bps": "1.5"},
            {"bot_stock": "0.1", "user_stock": "140", "slippage_bps": 100, "extra": 1},
        )
        for payload in invalid:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                simulate_batch_opening(payload)


if __name__ == "__main__":
    unittest.main()

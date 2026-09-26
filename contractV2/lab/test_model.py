"""Deterministic economic checks for the browser lab's approximation."""

import json
import math
import unittest

from lab.model import simulate


BASE = {"supply": 1_000_000, "virtual_stock": 100}


class V2ModelTest(unittest.TestCase):
    def test_documented_graduation_example(self):
        result = simulate(BASE)
        g = result["graduation"]
        self.assertEqual(g["real_stock"], 400)
        self.assertEqual(g["terminal_price"], 0.0025)
        self.assertEqual(g["lp_stock"], 200)
        self.assertEqual(g["treasury_stock"], 200)
        self.assertEqual(g["lp_token"], 80_000)
        self.assertEqual(g["token_burned"], 120_000)
        self.assertEqual(g["curve_tax_burned_estimate"], 80_000)
        self.assertEqual(g["sold_user_token_estimate"], 720_000)

    def test_no_fee_buy_and_sell_have_closed_form(self):
        result = simulate({**BASE, "lp_fee_bps": 0, "trade_tax_bps": 0})
        buy, sell = result["buy"], result["sell"]
        self.assertAlmostEqual(buy["fun_out"], 80_000 * 10 / 210)
        self.assertAlmostEqual(buy["spot_after"], 0.0025 * (210 / 200) ** 2)
        self.assertAlmostEqual(buy["avg_slippage_pct"], 5)
        self.assertEqual(sell["fun_in"], 80_000)
        self.assertEqual(sell["stock_out"], 100)
        self.assertAlmostEqual(sell["price_after"], 0.0025 / 4)
        self.assertEqual(sell["price_ratio_pct"], 25)
        self.assertEqual(sell["avg_slippage_pct"], 50)

    def test_fees_reduce_receipts_without_exaggerating_spot_move(self):
        charged = simulate(BASE)
        free = simulate({**BASE, "lp_fee_bps": 0, "trade_tax_bps": 0})
        self.assertLess(charged["buy"]["fun_out"], free["buy"]["fun_out"])
        self.assertGreater(charged["buy"]["avg_slippage_pct"], free["buy"]["avg_slippage_pct"])
        self.assertLess(charged["buy"]["spot_after"], free["buy"]["spot_after"])
        self.assertGreater(charged["buy"]["lp_fee_stock"], 0)
        self.assertGreater(charged["buy"]["hook_tax_fun"], 0)
        self.assertGreater(charged["sell"]["lp_fee_fun"], 0)
        self.assertGreater(charged["sell"]["hook_tax_stock"], 0)

    def test_push_capital_is_not_net_attack_cost(self):
        result = simulate({**BASE, "lp_fee_bps": 0})
        capital = result["manipulation"]["stock_for_20pct_up"]
        self.assertAlmostEqual(capital, 200 * (math.sqrt(1.2) - 1))
        self.assertTrue(result["manipulation"]["capital_not_cost"])
        self.assertGreater(simulate(BASE)["manipulation"]["stock_for_20pct_up"], capital)

    def test_split_conserves_real_reserve_and_inventory(self):
        for lp_bps in (1, 1000, 3000, 5000, 7000, 10_000):
            result = simulate({**BASE, "lp_bps": lp_bps})
            g = result["graduation"]
            self.assertAlmostEqual(g["lp_stock"] + g["treasury_stock"], g["real_stock"])
            self.assertAlmostEqual(g["lp_token"] + g["token_burned"], 200_000)
            self.assertAlmostEqual(g["lp_stock"] / g["lp_token"], g["terminal_price"])
            self.assertAlmostEqual(
                g["sold_user_token_estimate"] + g["curve_tax_burned_estimate"]
                + g["lp_token"] + g["token_burned"], BASE["supply"]
            )

    def test_scenarios_are_independent_and_zero_inputs_are_finite(self):
        baseline = simulate(BASE)
        larger_buy = simulate({**BASE, "buy_stock": 20})
        self.assertEqual(larger_buy["sell"], baseline["sell"])
        larger_sell = simulate({**BASE, "sell_fraction_bps": 2000})
        self.assertEqual(larger_sell["buy"], baseline["buy"])
        zero = simulate({**BASE, "buy_stock": 0, "sell_fraction_bps": 0})
        self.assertEqual(zero["buy"]["fun_out"], 0)
        self.assertEqual(zero["buy"]["avg_slippage_pct"], 0)
        self.assertEqual(zero["sell"]["stock_out"], 0)
        self.assertEqual(zero["sell"]["price_ratio_pct"], 100)
        json.dumps(zero, allow_nan=False)

    def test_invalid_inputs_are_rejected(self):
        bad = [
            [],
            {"virtual_stock": 100},
            {"supply": True, "virtual_stock": 100},
            {**BASE, "virtual_stock": 0},
            {**BASE, "buy_stock": -1},
            {**BASE, "buy_stock": "NaN"},
            {**BASE, "buy_stock": "1e-1000"},
            {**BASE, "supply": float("inf")},
            {**BASE, "sale_bps": 999},
            {**BASE, "sale_bps": 9001},
            {**BASE, "lp_bps": 0},
            {**BASE, "lp_bps": 10001},
            {**BASE, "lp_fee_bps": 31},
            {**BASE, "trade_tax_bps": 1501},
            {**BASE, "sell_fraction_bps": 10001},
            {**BASE, "lp_bps": 5000.5},
        ]
        for payload in bad:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                simulate(payload)


if __name__ == "__main__":
    unittest.main()

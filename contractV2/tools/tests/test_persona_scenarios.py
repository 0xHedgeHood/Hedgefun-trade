"""Offline semantic checks of executable actor budgets and phase boundaries."""

import copy
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("persona_scenarios", ROOT / "tools/persona_scenarios.py")
TOOL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(TOOL)


def ceil_div(a, b):
    return (a + b - 1) // b


def graduate_refund(plan):
    """Budget check using curve integer arithmetic, with the latest sniper fill.

    This is an offline sizing check, not evidence of a live transaction. Actual
    trades must be quoted again because other wallets can affect the public pool.
    """
    supply = 10**27
    virtual = ceil_div(int(plan["launch"]["expectedOpenPriceE18"]) * supply, 10**18)
    minimum = supply * (10000 - plan["launch"]["curve"]["saleBps"]) // 10000
    invariant = virtual * supply
    terminal = ceil_div(invariant, minimum)
    reserve = supply
    stock = 0
    balances = dict.fromkeys(TOOL.ACTOR_IDS, 0)
    tax = plan["launch"]["taxBps"]
    for row in plan["actions"]:
        if row["phase"] == "post_graduation":
            break
        actor = row["actor"]
        if row["action"] == "buy":
            remaining = terminal - virtual - stock
            cap = (remaining - 1) * 10000 // (10000 - tax) + 1
            offer = int(row["amount_in"])
            payment = min(offer, cap)
            net = payment - payment * tax // 10000
            after = minimum if net == remaining else ceil_div(invariant, virtual + stock + net)
            principal = ceil_div(invariant, after) - virtual - stock
            spent = (principal - 1) * 10000 // (10000 - tax) + 1
            elapsed = row["latest_elapsed_seconds"] or row["earliest_elapsed_seconds"]
            rate = TOOL.opening_rate_bps(elapsed)
            burned = (reserve - after) * (rate - tax) // (10000 - tax)
            balances[actor] += reserve - after - burned
            reserve = after
            stock += spent - spent * tax // 10000
            if row["phase"] == "graduate":
                if reserve != minimum:
                    raise AssertionError("graduation budget is insufficient")
                return offer - spent
        elif row["action"] == "sell":
            amount = balances[actor] * row["sell_fraction_bps"] // 10000
            gross = virtual + stock - ceil_div(invariant, reserve + amount)
            reserve += amount
            stock -= gross
            balances[actor] -= amount
    raise AssertionError("no graduation action")


class PersonaPlanTest(unittest.TestCase):
    def test_deterministic_actor_variation_and_fixed_launch_profile(self):
        plan = TOOL.build_plan()
        self.assertEqual(plan, TOOL.build_plan())
        self.assertNotEqual(plan["actions"], TOOL.build_plan(20261004)["actions"])
        self.assertEqual(plan["launch"]["nonce"], "2026100304")
        self.assertEqual(plan["launch"]["symbol"], "HFPERS2")
        self.assertEqual(plan["launch"]["name"], "Hedgefun Persona TSLA Test")
        self.assertEqual([plan["launch"][key] for key in ("tp1Bps", "tp2Bps", "dipBps", "stopBps")],
                         [500, 1000, 500, 500])
        self.assertEqual(plan["launch"]["openingTaxExemptions"], [])
        self.assertEqual(sum(int(row["native_funding_wei"]) for row in plan["actors"]),
                         int(plan["execution"]["gas_funding_total_wei"]))
        self.assertEqual(int(plan["execution"]["gas_funding_total_wei"]), 570_000_000_000_000)

    def test_opening_rate_boundary_and_creator_control(self):
        self.assertEqual([TOOL.opening_rate_bps(t) for t in (0, 30, 90, 179, 180, 1000)],
                         [9900, 8300, 5100, 354, 300, 300])
        self.assertEqual(TOOL.opening_rate_bps(0, exempt=True), 300)
        self.assertEqual(TOOL.opening_rate_bps(0, window=0), 300)
        self.assertEqual(TOOL.opening_rate_bps(0, snipe_bps=0), 300)
        with self.assertRaises(ValueError):
            TOOL.opening_rate_bps(-1)

    def test_graduation_is_funded_for_one_hundred_seeded_actor_plans(self):
        for seed in range(20261003, 20261103):
            with self.subTest(seed=seed):
                plan = TOOL.build_plan(seed)
                self.assertGreater(graduate_refund(plan), 0)
                self.assertEqual(len(plan["actors"]), 8)
                self.assertEqual(sum(row["execution"] == "broadcast" for row in plan["actions"]), 19)
                diamond = next(row for row in plan["actors"] if row["id"] == "diamond_hands")
                self.assertEqual(int(diamond["maximum_gross_buy_budget"]["STOCK"]), 1475 * 10**16)

    def test_kol_signal_and_negative_probes_have_no_broadcast(self):
        rows = TOOL.build_plan()["actions"]
        self.assertEqual(len([row for row in rows if row["action"] == "probe"]), 3)
        self.assertTrue(all(row["execution"] == "eth_call" for row in rows if row["action"] == "probe"))
        signal = next(row for row in rows if row["action"] == "signal")
        self.assertFalse(signal["expected"]["external_post"])
        self.assertEqual(signal["execution"], "record_only")
        self.assertTrue(all(row["id"] > signal["id"] for row in rows
                            if row["actor"].startswith("follower_") and row["action"] == "buy"))

    def test_buy_plan_permits_one_wei_curve_refund_with_minimum_output(self):
        plan = TOOL.build_plan()
        first = plan["actions"][0]
        supply = 10**27
        virtual = ceil_div(int(plan["launch"]["expectedOpenPriceE18"]) * supply, 10**18)
        offer = int(first["amount_in"])
        tax = plan["launch"]["taxBps"]
        principal_budget = offer - offer * tax // 10000
        reserve_after = ceil_div(supply * virtual, virtual + principal_budget)
        principal_used = ceil_div(supply * virtual, reserve_after) - virtual
        stock_spent = (principal_used - 1) * 10000 // (10000 - tax) + 1
        self.assertEqual(offer - stock_spent, 1, "the actual first offer requires a rounding refund")
        self.assertTrue(all(row["allow_partial_fill"] for row in plan["actions"] if row["action"] == "buy"))
        self.assertEqual(plan["execution"]["slippage_bps"], 100)
        self.assertIn("absolute minFinalOut", plan["execution"]["partial_fill_policy"])
        first["allow_partial_fill"] = False
        with self.assertRaises(ValueError):
            TOOL.validate_plan(plan)

    def test_budget_stage_and_persona_mutations_are_rejected(self):
        base = TOOL.build_plan()
        mutations = []
        plan = copy.deepcopy(base)
        plan["actions"][0]["amount_in"] = str(16 * 10**18)
        mutations.append(plan)
        plan = copy.deepcopy(base)
        plan["actions"][-1]["expected_stage"] = 0
        mutations.append(plan)
        plan = copy.deepcopy(base)
        next(row for row in plan["actions"] if row["action"] == "probe")["execution"] = "broadcast"
        mutations.append(plan)
        plan = copy.deepcopy(base)
        next(row for row in plan["actions"] if row["phase"] == "graduate")["allow_partial_fill"] = False
        mutations.append(plan)
        for plan in mutations:
            with self.subTest(plan=plan["actions"][0]["amount_in"]), self.assertRaises(ValueError):
                TOOL.validate_plan(plan)

    def test_unverified_or_wrong_chain_deployment_is_rejected(self):
        for book in ({"broadcast": False, "chainId": 46630}, {"broadcast": True, "chainId": 1}):
            with self.assertRaises(ValueError):
                TOOL.build_plan(address_book=book)


if __name__ == "__main__":
    unittest.main()

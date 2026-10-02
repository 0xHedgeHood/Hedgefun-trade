"""Offline tests for tools/v2_launch_check.py: the Rg arithmetic, the M4-1 chunk rule, the price and gate helpers
for both token orderings, and the verdicts. No network, no forge.

Reference values come from the contracts, not from this file's formula: the curve terms were read off real
HedgeFunBondingCurve deployments (a throwaway Foundry test, 2026-09-28), and tests/data/v2_launch_check_rows.json
holds lines script/CheckV2Listings.s.sol printed on a fork of Robinhood Chain.
"""
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

from tools import v2_launch_check as v

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = json.loads((ROOT / "tests" / "data" / "v2_launch_check_rows.json").read_text())


def row(symbol, **changes):
    r = copy.deepcopy(FIXTURE["rows"][symbol])
    r.update(changes)
    return r


class CurveTermsTests(unittest.TestCase):
    """(openPriceE18 or V, supply, saleBps) -> V, Tmin, Yg, Rg as HedgeFunBondingCurve computed them."""

    def test_docs_worked_example(self):
        # docs/V2_BONDING_CURVE.md: S = 1,000,000, V = 100, an 80% sale -> Tmin = 200,000, Yg = 500, Rg = 400
        t = v.curve_terms_from_virtual(1_000_000, 100, 8000)
        self.assertEqual(t, {"virtualStock": 100, "minTokenReserve": 200_000, "terminalStock": 500, "rg": 400})

    def test_values_read_from_the_deployed_curve(self):
        cases = [  # openPriceE18, supply, saleBps, V, Tmin, Yg, Rg
            (44200000000, 10**27, 8000, 44200000000000000000, 200000000000000000000000000,
             221000000000000000000, 176800000000000000000),                    # NVDA's listing: Rg 176.8
            (423000000000, 10**27, 7000, 423000000000000000000, 300000000000000000000000000,
             1410000000000000000000, 987000000000000000000),
            (12345678901, 10**27 - 1, 7777, 12345678901000000000, 222299999999999999999999999,
             55536117413405308143, 43190438512405308143),                      # every rounding in play
            (1, 10**18 + 1, 9000, 2, 100000000000000000, 21, 19),               # V rounds UP from 1.000..1
            (333333333333, 10**27, 1000, 333333333333000000000, 900000000000000000000000000,
             370370370370000000000, 37037037037000000000),
        ]
        for open_p, supply, sale, V, tmin, yg, rg in cases:
            with self.subTest(open=open_p, sale=sale):
                self.assertEqual(v.curve_terms(open_p, supply, sale),
                                 {"virtualStock": V, "minTokenReserve": tmin, "terminalStock": yg, "rg": rg})

    def test_round_three_shortcut_agrees_when_everything_divides(self):
        # All18.t.sol used Rg = 4 * openPriceE18 * 1e27 / 1e18, exact only at an 80% sale and a divisible V
        for open_p in (44200000000, 82700000000, 423000000000):
            self.assertEqual(v.curve_terms(open_p, 10**27, 8000)["rg"], 4 * open_p * 10**27 // 10**18)

    def test_curve_constructor_bounds(self):
        for sale in (999, 9001, 0, 10000):
            with self.assertRaises(ValueError):
                v.curve_terms(44200000000, 10**27, sale)
        with self.assertRaises(ValueError):                      # the curve reverts BadConfig on Tmin == 0 too
            v.curve_terms_from_virtual(3, 7, 8000)

    def test_fixture_rows_carry_the_contract_values(self):
        for sym, r in FIXTURE["rows"].items():
            with self.subTest(sym=sym):
                self.assertEqual(v.check_curve_arithmetic(r), [])


class ChunkRuleTests(unittest.TestCase):
    def test_ten_percent_of_the_thinner_side(self):
        self.assertEqual(v.max_chunk_usdg(21_647_000_000, 21_186_000_000), 2_118_600_000)
        self.assertEqual(v.max_chunk_usdg(21_186_000_000, 21_647_000_000), 2_118_600_000)
        self.assertEqual(v.max_chunk_usdg(13_500_000_000, 99_000_000_000), 1_350_000_000)   # GME, round 4's read
        self.assertEqual(v.max_chunk_usdg(19, 100), 1)                                      # floors

    def test_only_the_005_percent_tier(self):
        self.assertTrue(v.chunk_rule_applies(500))
        for fee in (100, 3000, 10000):
            self.assertFalse(v.chunk_rule_applies(fee))

    def test_boundary_is_inclusive(self):
        r = row("NVDA")
        cap = v.max_chunk_usdg(r["upUsdg"], r["downUsdg"])
        self.assertEqual(v.judge(row("NVDA", sellChunkUsdg=cap))[0], "PASS")
        verdict, fails, _, _ = v.judge(row("NVDA", sellChunkUsdg=cap + 1))
        self.assertEqual(verdict, "FAIL")
        self.assertEqual(len(fails), 1)
        self.assertIn("rule 2 M4-1", fails[0])

    def test_not_applied_to_a_030_percent_pool(self):
        verdict, fails, _, _ = v.judge(row("INTC", sellChunkUsdg=10**15))
        self.assertFalse(any("rule 2" in f for f in fails))

    def test_unmeasured_depth_fails_on_a_005_percent_pool(self):
        verdict, fails, _, _ = v.judge(row("NVDA", downOk=False, downErr="SPL"))
        self.assertEqual(verdict, "FAIL")
        self.assertIn("could not be measured", fails[0])


class PriceAndGateTests(unittest.TestCase):
    """The two token orderings: USDG is token0 in some stock pools and token1 in others."""

    def test_price_at_sqrt_matches_pooltrader_stock_is_token1(self):
        for sym in ("NVDA", "INTC"):
            r = FIXTURE["rows"][sym]
            self.assertFalse(r["stockIsToken0"])
            self.assertEqual(v.price_at_sqrt(r["sqrt0"], False, 18, 6), r["spot0"])
            self.assertEqual(v.price_at_sqrt(r["rgSqrtAfter"], False, 18, 6), r["rgSpotAfter"])

    def test_price_at_sqrt_matches_pooltrader_stock_is_token0(self):
        r = FIXTURE["rows"]["GME"]
        self.assertTrue(r["stockIsToken0"])
        self.assertEqual(v.price_at_sqrt(r["sqrt0"], True, 18, 6), r["spot0"])
        self.assertEqual(v.price_at_sqrt(r["rgSqrtAfter"], True, 18, 6), r["rgSpotAfter"])

    def test_the_wrong_ordering_is_nonsense_not_close(self):
        r = FIXTURE["rows"]["GME"]
        wrong = v.price_at_sqrt(r["sqrt0"], False, 18, 6)
        self.assertGreater(abs(wrong - r["spot0"]) * 100, r["spot0"])     # off by far more than 1%

    def test_buying_stock_raises_its_price_in_either_ordering(self):
        for sym in ("NVDA", "GME", "INTC"):
            r = FIXTURE["rows"][sym]
            with self.subTest(sym=sym):
                self.assertGreater(r["rgSpotAfter"], r["spot0"])
                self.assertGreater(v.stock_tick_move(r["rgTickAfter"], r["tick0"], r["stockIsToken0"]), 0)

    def test_oracle_gate_boundary(self):
        p = 10**20
        self.assertFalse(v.gate_exceeded(p + p * 50 // 10000, p, 50))
        self.assertTrue(v.gate_exceeded(p + p * 50 // 10000 + 1, p, 50))
        self.assertFalse(v.gate_exceeded(p - p * 50 // 10000, p, 50))
        self.assertTrue(v.gate_exceeded(p - p * 50 // 10000 - 1, p, 50))

    def test_tick_gate_boundary_and_sign(self):
        self.assertFalse(v.tick_gate_exceeded(1050, 1000, 50))
        self.assertTrue(v.tick_gate_exceeded(1051, 1000, 50))
        self.assertTrue(v.tick_gate_exceeded(949, 1000, 50))
        self.assertEqual(v.stock_tick_move(1051, 1000, True), 51)
        self.assertEqual(v.stock_tick_move(1051, 1000, False), -51)


class VerdictTests(unittest.TestCase):
    def test_deep_pool_passes(self):
        verdict, fails, notes, d = v.judge(row("NVDA"))
        self.assertEqual((verdict, fails), ("PASS", []))
        self.assertAlmostEqual(d["rg"], 176.8)
        self.assertLess(abs(d["after"]), 50)

    def test_rule_1b_both_orderings(self):
        for sym in ("GME", "INTC"):   # stock token0, stock token1
            with self.subTest(sym=sym):
                verdict, fails, _, d = v.judge(row(sym))
                self.assertEqual(verdict, "FAIL")
                self.assertEqual(len(fails), 1)
                self.assertIn("rule 1(b) M-2", fails[0])
                self.assertIn("gate 50 bps", fails[0])
                self.assertGreater(d["after"], 50)
                self.assertGreater(d["ticks"], 50)

    def test_rule_1b_names_the_edge_that_broke(self):
        # the same post-trade spot, but the 600 s mean moved with it: only the oracle edge is exceeded
        r = row("INTC")
        r["meanTick"] = r["rgTickAfter"]
        fails = v.judge(r)[1]
        self.assertIn("exceeded against the oracle", fails[-1])
        self.assertNotIn("mean tick", fails[-1].split("exceeded")[1])

    def test_rule_1b_tick_edge_alone(self):
        # spot back on the oracle, but 60 ticks away from its own 600 s mean: the TWAP half of _health shuts
        r = row("NVDA", rgHealthAfter=False)
        r["oraclePrice"] = r["rgSpotAfter"]
        r["meanTick"] = r["rgTickAfter"] + 60
        verdict, fails, _, _ = v.judge(r)
        self.assertEqual(verdict, "FAIL")
        self.assertIn("exceeded against the 600 s mean tick", fails[0])

    def test_rule_1b_hint_scales_open_price_to_the_gate(self):
        r = row("GME")
        fails = v.judge(r)[1]
        self.assertIn(f"openPriceE18 <= {r['openPriceE18'] * r['gateStock'] // r['rg']:,}", fails[0])

    def test_rule_1a_short_fill_and_revert(self):
        r = row("INTC", rgOut=FIXTURE["rows"]["INTC"]["rg"] - 1, deliverable=262_320_000_000_000_000_000)
        verdict, fails, _, _ = v.judge(r)
        self.assertEqual(verdict, "FAIL")
        self.assertIn("rule 1(a) M-3", fails[0])
        self.assertIn("262.32", fails[0])
        self.assertIn("could never graduate", fails[0])
        self.assertFalse(any("rule 1(b)" in f for f in fails))     # 1(b) presumes 1(a)
        # a short fill ran the swap to its price limit: that USDG input is no price for Rg
        self.assertIsNone(v.judge(row("INTC", rgOut=FIXTURE["rows"]["INTC"]["rg"] - 1, rgUsdgIn=10**36))[3]["cost"])
        self.assertIsNotNone(v.judge(row("INTC"))[3]["cost"])

        verdict, fails, _, _ = v.judge(row("INTC", rgOk=False, rgErr="T", drainOk=False, drainErr="T"))
        self.assertIn("deliverable unknown", fails[0])

    def test_config_error_is_a_fail(self):
        verdict, fails, _, _ = v.judge({"symbol": "X", "configError": "pool is not the stock/USDG pair"})
        self.assertEqual(verdict, "FAIL")
        self.assertIn("config: pool is not the stock/USDG pair", fails[0])

    def test_stale_oracle_fails_unless_allowed(self):
        r = row("NVDA", oracleLive=False, oracleUpdatedAt=1790600000, rgHealthAfter=False)
        verdict, fails, notes, _ = v.judge(r)
        self.assertEqual(verdict, "FAIL")
        self.assertIn("oracle:", fails[0])
        verdict, fails, notes, _ = v.judge(r, allow_stale_oracle=True)
        self.assertEqual(verdict, "PASS")
        self.assertIn("last print", notes[0])
        self.assertEqual(v.judge(row("NVDA", oracleLive=False, oraclePrice=0))[0], "FAIL")

    def test_short_ring_fails(self):
        fails = v.judge(row("NVDA", meanOk=False))[1]
        self.assertTrue(any("600 s window" in f for f in fails))

    def test_contract_and_formula_must_agree(self):
        fails = v.judge(row("NVDA", rg=FIXTURE["rows"]["NVDA"]["rg"] + 1))[1]
        self.assertTrue(any(f.startswith("internal:") and "rg" in f for f in fails))

    def test_gate_replication_must_agree_with_pooltrader(self):
        fails = v.judge(row("NVDA", rgHealthAfter=False))[1]
        self.assertTrue(any("disagrees with PoolTrader._health" in f for f in fails))

    def test_v1_factory_sale_bps_fallback_is_a_note(self):
        r = row("NVDA", source="factory",
                saleBpsSource="DEFAULT_SALE_BPS (no curve deployer default to read: not a V2 factory)")
        verdict, _, notes, _ = v.judge(r)
        self.assertEqual(verdict, "PASS")
        self.assertIn("not a V2 factory", notes[0])

    def test_creator_sale_bps_verdict_is_advisory(self):
        # the verdict itself is unchanged; the note says nothing on chain refuses the launch
        for changes in ({"creatorSaleBps": True}, {"source": "factory", "saleBpsSource": "creator"}):
            with self.subTest(**changes):
                verdict, fails, notes, _ = v.judge(row("GME", **changes))
                self.assertEqual(verdict, "FAIL")
                self.assertIn("rule 1(b) M-2", fails[0])
                self.assertTrue(any("advisory" in n and "nothing on chain refuses" in n for n in notes))
        self.assertFalse(any("advisory" in n for n in v.judge(row("GME"))[2]))

    def test_rule_1a_tells_the_creator_the_curve_strands_its_buyers(self):
        r = row("INTC", rgOut=FIXTURE["rows"]["INTC"]["rg"] - 1, deliverable=262_320_000_000_000_000_000)
        fail = v.judge(r)[1][0]
        self.assertIn("at saleBps 8000", fail)
        self.assertIn("only sell back to the curve", fail)

    def test_disabled_listing_is_a_note(self):
        verdict, _, notes, _ = v.judge(row("NVDA", enabled=False))
        self.assertEqual(verdict, "PASS")
        self.assertIn("disabled", notes[0])


class PlanAndEncodingTests(unittest.TestCase):
    def test_plan_has_the_eighteen_v1_listings_with_a_source_for_every_field(self):
        plan, entries = v.load_plan(v.DEFAULT_PLAN)
        self.assertEqual(len(entries), 18)
        self.assertEqual([s for s, _ in entries][:3], ["NVDA", "SPCX", "CRCL"])
        for sym, st in plan["stocks"].items():
            for field in v.PLAN_FIELDS:
                with self.subTest(sym=sym, field=field):
                    self.assertIn(st["source"][field], plan["sources"])
        for sym, e in entries:
            self.assertEqual(v.as_int(e["supply"]), 10**27)
            self.assertEqual(e["saleBps"], v.DEFAULT_SALE_BPS)   # CurveDeployer.DEFAULT_SALE_BPS, no creator's value
            self.assertFalse(e["creatorSaleBps"])
        self.assertEqual(v.DEFAULT_SALE_BPS, 4400)
        self.assertEqual(plan["defaults"]["saleBps"], {"value": 4400, "source": "v2.default"})
        mstr = dict(entries)["MSTR"]
        self.assertEqual((mstr["maxDeviationBps"], mstr["maxSlippageBps"]), (125, 175))
        self.assertEqual(dict(entries)["INTC"]["sellChunkUsdg"], 1_000_000_000)

    def test_plan_filter(self):
        _, entries = v.load_plan(v.DEFAULT_PLAN, ["gme", "INTC"])
        self.assertEqual([s for s, _ in entries], ["GME", "INTC"])
        with self.assertRaises(ValueError):
            v.load_plan(v.DEFAULT_PLAN, ["NOPE"])

    def test_abi_encoding_of_the_entry_array(self):
        _, entries = v.load_plan(v.DEFAULT_PLAN, ["NVDA", "GME"])
        h = v.encode_entries([e for _, e in entries])[2:]
        words = [int(h[i:i + 64], 16) for i in range(0, len(h), 64)]
        self.assertEqual(len(words), 2 + 2 * 9)
        self.assertEqual(words[:2], [0x20, 2])
        nvda = words[2:11]
        self.assertEqual(nvda[0], int("0xd0601CE157Db5bdC3162BbaC2a2C8aF5320D9EEC", 16))
        self.assertEqual(nvda[3:], [44200000000, 10**27, 4400, 50, 100, 2_000_000_000])
        only = v.encode_entries([e for _, e in entries], factory_mode=True)[2:]
        w2 = [int(only[i:i + 64], 16) for i in range(0, len(only), 64)]
        self.assertEqual(w2[2], nvda[0])
        self.assertEqual(w2[3:11], [0] * 8, "no creator's saleBps: the factory's curve deployer default")

    def test_creator_sale_bps_reaches_the_script_in_both_modes(self):
        _, entries = v.load_plan(v.DEFAULT_PLAN, ["NVDA", "GME"])
        v.apply_sale_bps(entries, 8000)
        for factory_mode in (False, True):
            with self.subTest(factory_mode=factory_mode):
                h = v.encode_entries([e for _, e in entries], factory_mode=factory_mode)[2:]
                words = [int(h[i:i + 64], 16) for i in range(0, len(h), 64)]
                self.assertEqual([words[2 + 5], words[11 + 5]], [8000, 8000])

    def test_a_plan_entry_carries_a_creators_sale_bps(self):
        plan = json.loads(Path(v.DEFAULT_PLAN).read_text())
        plan["stocks"]["AMD"]["saleBps"] = 6000
        plan["stocks"]["AMD"]["source"]["saleBps"] = "creator"
        self.assertIn("creator", plan["sources"])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "plan.json"
            path.write_text(json.dumps(plan))
            _, entries = v.load_plan(str(path), ["AMD", "NVDA"])
        e = dict(entries)
        self.assertEqual((e["AMD"]["saleBps"], e["AMD"]["creatorSaleBps"]), (6000, True))
        self.assertEqual((e["NVDA"]["saleBps"], e["NVDA"]["creatorSaleBps"]), (4400, False))
        h = v.encode_entries([e["NVDA"], e["AMD"]], factory_mode=True)[2:]
        words = [int(h[i:i + 64], 16) for i in range(0, len(h), 64)]
        self.assertEqual((words[2 + 5], words[11 + 5]), (0, 6000))

    def test_sale_bps_argument_takes_the_curves_bounds_and_nothing_tighter(self):
        for ok in ("1000", "4400", "9000", "0x1f40"):
            self.assertEqual(v.sale_bps_arg(ok), int(ok, 0))
        for bad in ("999", "9001", "0"):
            with self.assertRaises(v.argparse.ArgumentTypeError):
                v.sale_bps_arg(bad)

    def test_parse_output(self):
        text = ("== Logs ==\n  V2CHECK_META {\"chainId\":4663,\"block\":7}\n  V2CHECK {\"symbol\":\"A\",\"rg\":1}\n"
                "  noise\n  V2CHECK {\"symbol\":\"B\",\"rg\":2}\n  V2CHECK_DONE 2\n")
        meta, rows = v.parse_output(text)
        self.assertEqual(meta["block"], 7)
        self.assertEqual([r["symbol"] for r in rows], ["A", "B"])


class RpcTests(unittest.TestCase):
    def test_default_rpc_is_never_the_signing_endpoint(self):
        with patch.dict("os.environ", {"RH_RPC": "https://signing.example"}, clear=True):
            self.assertEqual(v.resolve_rpc(None), v.OFFICIAL_RPC)
        with patch.dict("os.environ", {"RH_READ_RPC": "https://read.example"}, clear=True):
            self.assertEqual(v.resolve_rpc(None), "https://read.example")
        self.assertEqual(v.resolve_rpc("robinhood"), "https://rpc.mainnet.chain.robinhood.com")
        self.assertEqual(v.resolve_rpc("https://x.example"), "https://x.example")

    def test_http_429_retries_with_backoff(self):
        ok = io.BytesIO(b'{"jsonrpc":"2.0","id":1,"result":"0x1234"}')
        busy = urllib.error.HTTPError("u", 429, "Too Many Requests", {}, None)
        sleeps = []
        with patch("urllib.request.urlopen", side_effect=[busy, busy, ok]):
            self.assertEqual(v.rpc_call("https://x.example", "eth_blockNumber", [], sleep=sleeps.append), "0x1234")
        self.assertEqual(sleeps, [1.0, 2.0])

    def test_json_rpc_rate_limit_body_retries(self):
        limited = io.BytesIO(b'{"jsonrpc":"2.0","id":1,"error":{"code":-32005,"message":"rate limit exceeded"}}')
        ok = io.BytesIO(b'{"jsonrpc":"2.0","id":1,"result":"0x1"}')
        sleeps = []
        with patch("urllib.request.urlopen", side_effect=[limited, ok]):
            self.assertEqual(v.rpc_call("https://x.example", "eth_chainId", [], sleep=sleeps.append), "0x1")
        self.assertEqual(sleeps, [1.0])

    def test_other_http_errors_are_not_retried(self):
        denied = urllib.error.HTTPError("u", 403, "Forbidden", {}, None)
        with patch("urllib.request.urlopen", side_effect=[denied]):
            with self.assertRaises(urllib.error.HTTPError):
                v.rpc_call("https://x.example", "eth_call", [], sleep=lambda s: None)


class TestnetBookTests(unittest.TestCase):
    BOOK = {"chainId": 46630, "factory": "0x" + "ab" * 20, "stocks": {
        "NVDA": {"token": "0x" + "01" * 20, "oracle": "0x" + "02" * 20, "pool": "0x" + "03" * 20},
        "GME": {"token": "0x" + "04" * 20, "oracle": "0x" + "05" * 20, "pool": "0x" + "06" * 20}}}

    def _write(self, book):
        f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        json.dump(book, f)
        f.close()
        return f.name

    def test_book_gives_the_factory_and_each_token(self):
        factory, entries = v.load_book(self._write(self.BOOK))
        self.assertEqual(factory, self.BOOK["factory"])
        self.assertEqual([s for s, _ in entries], ["GME", "NVDA"])
        encoded = v.encode_entries([e for _, e in entries], factory_mode=True)
        self.assertIn("04" * 20, encoded)

    def test_book_for_another_chain_is_refused(self):
        with self.assertRaises(ValueError):
            v.load_book(self._write(dict(self.BOOK, chainId=4663)))

    def test_book_stock_filter(self):
        _, entries = v.load_book(self._write(self.BOOK), ["nvda"])
        self.assertEqual([s for s, _ in entries], ["NVDA"])
        with self.assertRaises(ValueError):
            v.load_book(self._write(self.BOOK), ["AAPL"])

    def test_testnet_default_rpc_is_the_testnets(self):
        self.assertEqual(v.resolve_rpc(None, testnet=True), v.TESTNET_RPC)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Deterministic, unsigned actor plans for the public chain 46630 test deployment.

This module does not use a network, create keys, or send transactions. An executor
must attach separate wallets, quote against live state, and journal receipts. The
KOL signal is an internal simulation event, never an instruction to publish posts.
Amounts are integer base units encoded as strings for JavaScript consumers.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHAIN_ID = 46630
STOCK_UNIT = 10**18
USDG_UNIT = 10**6
ACTOR_IDS = (
    "sniper", "opening_buyer", "diamond_hands", "paper_hands", "kol",
    "follower_1", "follower_2", "late_fomo",
)


def opening_rate_bps(elapsed: int, tax_bps: int = 300, snipe_bps: int = 9900,
                     window: int = 180, exempt: bool = False) -> int:
    """Match the curve's ceil-rounded combined nominal rate, not stock fees."""
    if elapsed < 0 or not 0 <= tax_bps < 10000 or not 0 <= snipe_bps <= 9900 or window < 0:
        raise ValueError("invalid opening rate inputs")
    if exempt or not window or snipe_bps <= tax_bps or elapsed >= window:
        return tax_bps
    return tax_bps + ((snipe_bps - tax_bps) * (window - elapsed) + window - 1) // window


def _variation(seed: int, actor: str, label: str, span: int) -> int:
    material = f"{seed}:{actor}:{label}".encode()
    return int.from_bytes(hashlib.sha256(material).digest()[:8], "big") % span


def build_plan(seed: int = 20261003, *, address_book: dict | None = None,
               launch_profile: dict | None = None) -> dict:
    book = address_book or json.loads((ROOT / "deploy/testnet-v2-fresh-creator.json").read_text())
    profile = launch_profile or json.loads((ROOT / "deploy/fresh-creator-launch-profile.json").read_text())
    if not book.get("broadcast") or book.get("chainId") != CHAIN_ID:
        raise ValueError("a verified public testnet deployment is required")
    if profile.get("chainId") != CHAIN_ID or profile.get("kind") != 0:
        raise ValueError("the creator-selected all-in launch profile is required")
    if book.get("featureVersion") != "v2-creator-selected-fresh-wallet-v1":
        raise ValueError("unexpected core feature version")
    if profile["taxBps"] != 300 or profile["curve"] != {"saleBps": 4000, "snipeSeconds": 180}:
        raise ValueError("this budget is calibrated for 300 bps, 40% sale and 180 seconds")

    actions = []

    def add(actor, phase, action, *, elapsed=None, latest=None, **fields):
        row = {
            "id": f"{len(actions) + 1:02d}-{actor}-{action}", "actor": actor,
            "phase": phase, "action": action,
            "execution": "eth_call" if action == "probe" else (
                "record_only" if action in ("signal", "hold") else "broadcast"),
            "expected_stage": 2 if phase == "post_graduation" else 0,
            "earliest_elapsed_seconds": elapsed, "latest_elapsed_seconds": latest,
            "allow_partial_fill": action == "buy", "expected": {}, **fields,
        }
        actions.append(row)
        return row

    def stock_buy(actor, phase, amount, **fields):
        # Actor-local SHA256 jitter is reproducible and does not reorder phases.
        if actor != "diamond_hands":
            amount = amount * (9900 + _variation(seed, actor, phase, 201)) // 10000
        return add(actor, phase, "buy", asset="STOCK", amount_in=str(amount), **fields)

    stock_buy("sniper", "opening", STOCK_UNIT // 2, elapsed=0, latest=45,
              expected={"opening_burn": "positive", "recipient_exempt": False})
    stock_buy("opening_buyer", "opening", STOCK_UNIT, elapsed=30, latest=100,
              expected={"opening_burn": "positive", "recipient_exempt": False})
    add("sniper", "opening", "sell", elapsed=100, latest=175, asset="FUN",
        sell_fraction_bps=8000,
        expected={"sell_tax_bps": 300, "note": "Opening surcharge affects buys; early resale does not recover burned FUN."})
    add("opening_buyer", "opening", "probe", probe="expired_deadline",
        expected={"revert": "Expired", "broadcast": False})

    stock_buy("diamond_hands", "after_window", 19 * STOCK_UNIT // 10, elapsed=181,
              expected={"opening_burn": "zero"})
    stock_buy("paper_hands", "after_window", 5 * STOCK_UNIT // 4, elapsed=190,
              expected={"opening_burn": "zero"})
    stock_buy("kol", "after_window", 3 * STOCK_UNIT // 2, elapsed=200,
              expected={"opening_burn": "zero"})
    add("kol", "after_window", "signal", elapsed=205,
        expected={"audience": "simulation_only", "external_post": False,
                  "message": "Synthetic attention event; subsequent follower buys model demand."})
    followers = sorted(("follower_1", "follower_2"),
                       key=lambda actor: _variation(seed, actor, "order", 2**32))
    for index, actor in enumerate(followers):
        stock_buy(actor, "after_window", 3 * STOCK_UNIT // 4, elapsed=210 + index * 10,
                  expected={"opening_burn": "zero", "trigger": "internal_kol_signal"})
    add("paper_hands", "after_window", "sell", elapsed=235, asset="FUN",
        sell_fraction_bps=9000, expected={"trigger": "simulated_profit_taking"})
    add("diamond_hands", "after_window", "hold", elapsed=240,
        expected={"token_balance_must_not_decrease": True})
    add("follower_1", "after_window", "probe", probe="impossible_min_final_out",
        expected={"revert_required": True, "broadcast": False})
    stock_buy("diamond_hands", "graduate", 51 * STOCK_UNIT // 4, elapsed=250,
              allow_partial_fill=True,
              expected={"stage_after": 2, "stock_refund": "positive",
                        "graduation_atomic": True, "opening_burn": "zero"})
    add("sniper", "post_graduation", "probe", probe="stale_curve_stage",
        expected={"revert": "StageChanged(2)", "broadcast": False})
    stock_buy("late_fomo", "post_graduation", STOCK_UNIT // 2,
              expected={"trade_venue": "V4", "opening_burn": "zero"})
    # Two small tUSDG routes cover routing without moving the shared stock market
    # enough to masquerade as the separate synthetic TSLA price experiment.
    for actor in ("opening_buyer", "late_fomo"):
        add(actor, "post_graduation", "buy", asset="USDG", amount_in=str(25 * USDG_UNIT),
            expected={"trade_venue": "V3_then_V4", "opening_burn": "zero"})
    for actor, fraction in (("kol", 2500), ("follower_1", 5000),
                            ("paper_hands", 10000), ("sniper", 10000), ("late_fomo", 2500)):
        add(actor, "post_graduation", "sell", asset="FUN", sell_fraction_bps=fraction,
            expected={"output_asset": "STOCK", "trade_venue": "V4"})
    stock_buy("diamond_hands", "post_graduation", STOCK_UNIT // 10,
              expected={"trigger": "accumulate_after_other_actors_sell", "trade_venue": "V4"})
    for actor in ("diamond_hands", "follower_2"):
        add(actor, "post_graduation", "hold",
            expected={"token_balance_positive": True, "mark_to_market_required": True})

    actors = []
    for actor in ACTOR_IDS:
        mine = [row for row in actions if row["actor"] == actor]
        buys = [row for row in mine if row["action"] == "buy"]
        budget = {asset: sum(int(row["amount_in"]) for row in buys if row["asset"] == asset)
                  for asset in ("STOCK", "USDG")}
        actors.append({
            "id": actor, "requires_distinct_wallet": True, "opening_exempt": False,
            "native_funding_wei": str(150_000_000_000_000 if actor == "diamond_hands" else 60_000_000_000_000),
            "funding": {"stock_drips": 1, "stock_amount": str(15 * STOCK_UNIT),
                        "usdg_drips": int(budget["USDG"] > 0),
                        "usdg_amount": str(10000 * USDG_UNIT if budget["USDG"] else 0)},
            "maximum_gross_buy_budget": {key: str(value) for key, value in budget.items()},
            "trade_count": sum(row["execution"] == "broadcast" for row in mine),
        })

    nonce = 2026100304 if seed == 20261003 else int.from_bytes(
        hashlib.sha256(f"hedgefun-personas:{seed}".encode()).digest()[:12], "big")
    launch = {key: value for key, value in profile.items()
              if key not in ("notes", "creatorParameterBounds")}
    launch.update(name="Hedgefun Persona TSLA Test", symbol="HFPERS2", nonce=str(nonce),
                  creator=book["owner"], curve={"saleBps": 4000, "snipeSeconds": 180},
                  tp1Bps=500, tp2Bps=1000, dipBps=500, stopBps=500,
                  openingTaxExemptions=[])
    result = {
        "schema": "hedgefun-persona-plan-v1", "seed": seed, "chain_id": CHAIN_ID,
        "test_only": True, "execution_mode": "public_testnet_separate_wallets",
        "deployment": {key: book[key] for key in ("factory", "tradeRouter", "curveDeployer", "hook", "owner")},
        "assets": {"STOCK": {"address": profile["stock"], "decimals": 18},
                   "USDG": {"address": "0x539574139BB4Ac74Fd2183ba0E38ed777E30B58d", "decimals": 6},
                   "FUN": {"address": None, "decimals": 18}},
        "launch": launch, "actors": actors, "actions": actions,
        "parameter_rationale": "Use the latest creator-selectable mechanism with 500/1000/500/500 bps rungs for observable persona behavior. The separate price experiment also tests the 1/2/1/1 bps boundary profile; no global defaults are changed.",
        "execution": {
            "slippage_bps": 100, "deadline_seconds": 300,
            "sell_fraction_basis": "current FUN balance immediately before quoting",
            "sell_output_asset": "STOCK", "direct_stock_path": [],
            "gas_funding_total_wei": str(7 * 60_000_000_000_000 + 150_000_000_000_000),
            "gas_topup_policy": "Before each signature require native balance >= gasLimit * gasPrice. Diamond receives 0.00015 ETH because the prior canonical graduation used 2,644,722 gas with a 3,783,494 gas limit; each other actor receives 0.00006 ETH.",
            "expected_supply": str(10**27),
            "expected_snipe_bps": 9900, "verify_live_defaults_before_launch": True,
            "missed_opening_deadline": "Stop the opening scenario; do not label a late trade as a sniper trade.",
            "preapprove_before_launch": "Approve each actor's bounded STOCK budget and optional USDG budget before launch.",
            "quote_policy": "Requote immediately before every trade and check receipt deltas; never reuse another actor's quote.",
            "partial_fill_policy": "All buys permit STOCK refunds, including a 1 wei exact-in curve rounding refund. Compute and enforce an absolute minFinalOut from the fresh quote; a refund never waives the token-output floor. Record actual STOCK spent and returned separately.",
            "ordering": "Sequential confirmed receipts; no promise of same-block inclusion or front-running.",
            "completion": "Graduate buy must produce stage 2 before any post_graduation action.",
        },
        "opening_control": {
            "method": "At one historical block call quoteBuyFor with equal STOCK input for sniper and creator.",
            "creator_exempt": True, "base_stock_fee_bps": 300,
            "opening_surcharge": "Burned FUN, not an additional STOCK fee; record Bought.taxTokens.",
            "nominal_rate_examples": {str(t): opening_rate_bps(t) for t in (0, 30, 90, 179, 180)},
        },
        "evidence": [
            "Separate canonical receipt and public wallet address for each broadcast action.",
            "Record block timestamp, opening rate, gross stock spent, stock fee, FUN output and FUN burned.",
            "Record FUN/STOCK spot and executable quote, TSLA/tUSDG spot and oracle, and FUN/tUSDG product separately.",
            "Report realized and unrealized actor results including remaining STOCK, FUN and native gas.",
            "Record quote-only failures separately from mined transactions.",
            "Weekend treasury funding is not booked strategy execution; retain calendar state in snapshots.",
        ],
    }
    validate_plan(result)
    return result


def validate_plan(plan: dict) -> None:
    """Reject funding, staging and persona mistakes before any executor sees a plan."""
    if plan["chain_id"] != CHAIN_ID or not plan["test_only"]:
        raise ValueError("not a testnet plan")
    actors = {row["id"]: row for row in plan["actors"]}
    if tuple(actors) != ACTOR_IDS or any(row["opening_exempt"] for row in actors.values()):
        raise ValueError("unexpected actor roster or opening exemptions")
    phases = {name: i for i, name in enumerate(("opening", "after_window", "graduate", "post_graduation"))}
    seen_phase = -1
    ids = set()
    graduate_count = 0
    for row in plan["actions"]:
        if row["id"] in ids or row["actor"] not in actors:
            raise ValueError("duplicate action or unknown actor")
        ids.add(row["id"])
        phase = phases[row["phase"]]
        if phase < seen_phase:
            raise ValueError("phases cannot go backwards")
        seen_phase = phase
        if row["expected_stage"] != (2 if row["phase"] == "post_graduation" else 0):
            raise ValueError("wrong stage")
        if row["action"] == "sell" and not 0 < row["sell_fraction_bps"] <= 10000:
            raise ValueError("invalid current-balance sell fraction")
        if row["action"] == "buy" and int(row["amount_in"]) <= 0:
            raise ValueError("nonpositive buy amount")
        if row["action"] == "buy" and not row["allow_partial_fill"]:
            raise ValueError("buys must permit integer-rounding STOCK refunds and retain the absolute output floor")
        if row["action"] in ("probe", "hold", "signal") and row["execution"] == "broadcast":
            raise ValueError("nontransaction action cannot be broadcast")
        if row["phase"] == "graduate":
            graduate_count += 1
            if not row["allow_partial_fill"] or row["expected"].get("stage_after") != 2:
                raise ValueError("graduation requires partial-fill handling and a stage-2 receipt")
    if graduate_count != 1:
        raise ValueError("expected exactly one graduation buy")
    for actor_id, actor in actors.items():
        for asset, funding in (("STOCK", "stock_amount"), ("USDG", "usdg_amount")):
            required = sum(int(row["amount_in"]) for row in plan["actions"]
                           if row["actor"] == actor_id and row["action"] == "buy" and row["asset"] == asset)
            if required != int(actor["maximum_gross_buy_budget"][asset]) or required > int(actor["funding"][funding]):
                raise ValueError("actor buy budget exceeds funding")
    diamond_sells = [row for row in plan["actions"] if row["actor"] == "diamond_hands" and row["action"] == "sell"]
    if diamond_sells:
        raise ValueError("diamond hands must retain all FUN")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=20261003)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    data = json.dumps(build_plan(args.seed), indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(data)
    else:
        print(data, end="")


if __name__ == "__main__":
    main()

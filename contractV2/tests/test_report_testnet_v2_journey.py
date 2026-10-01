"""Offline checks that a receipt file proves the named V2 journey phase."""

import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location(
    "journey_report", Path(__file__).resolve().parents[1] / "tools/report_testnet_v2_journey.py")
report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report)

ID = 7
TOKEN = "0x11bea94fa971962f998f79217d37c39555c13910"
BOOK = {"factory": report.FACTORY, "tradeRouter": "0xdab0b58c3a790e04071a9d55a76530681ffc2be2",
        "usdg": "0x5e154042fcd529d8089422d2aa2cd90c65e6782b",
        "stocks": {"GME": {"token": report.GME, "pool": "0x08f1d88505e10d5e4f9138fee7ad192af5cddf6c"}}}
BLOCK = "0x1234"
BLOCK_HASH = "0x" + "ab" * 32


def encoded(value):
    if isinstance(value, str):
        value = int(value, 16)
    return f"{value:064x}"


def tx_and_receipt(number, target, data, topics):
    hash_ = f"0x{number:064x}"
    tx = {"hash": hash_, "from": report.CREATOR, "to": target, "input": data,
          "blockNumber": BLOCK, "blockHash": BLOCK_HASH}
    receipt = {"transactionHash": hash_, "status": "0x1", "from": report.CREATOR,
               "to": target, "blockNumber": BLOCK, "blockHash": BLOCK_HASH,
               "transactionIndex": hex(number), "logs": [
                   {"address": address, "topics": [topic, encoded(ID)]} for address, topic in topics]}
    logged = {"hash": hash_, "transaction": {"to": target, "input": data}}
    return logged, tx, receipt


def bundle(phase):
    if phase == "launch":
        request = [64, 0, 0, 0, BOOK["stocks"]["GME"]["token"], report.CREATOR]
        action_to = BOOK["factory"]
        action_data = report.LAUNCH + "".join(encoded(value) for value in request)
        topics = [(BOOK["factory"], report.LAUNCHED_EVENT)]
        approval_to, spender = BOOK["usdg"], BOOK["factory"]
        amount = 25_000_000
    else:
        buying = phase in ("curveBuy", "graduate", "v4Buy")
        amount = 20_000_000_000 if phase == "graduate" else 100_000_000
        stage = 2 if phase.startswith("v4") else 0
        route = [ID, BOOK["usdg"], amount, 1 if buying else 0, 1, 123456789,
                 stage, 1 if phase == "graduate" else 0, 288, 1,
                 BOOK["stocks"]["GME"]["pool"],
                 BOOK["stocks"]["GME"]["token"] if buying else BOOK["usdg"]]
        action_to = BOOK["tradeRouter"]
        action_data = (report.BUY if buying else report.SELL) + "".join(encoded(value) for value in route)
        topics = [(action_to, report.BOUGHT_EVENT if buying else report.SOLD_EVENT)]
        if phase == "graduate":
            topics.append((BOOK["factory"], report.GRADUATED_EVENT))
        approval_to, spender = (BOOK["usdg"] if buying else TOKEN), BOOK["tradeRouter"]
    approval = tx_and_receipt(1, approval_to, report.APPROVE + encoded(spender) + encoded(amount), [])
    action = tx_and_receipt(2, action_to, action_data, topics)
    return [[approval[0], action[0]], [approval[1], action[1]], [approval[2], action[2]]]


class PhaseReceipts(unittest.TestCase):
    def test_all_six_phase_shapes_are_accepted(self):
        for phase in report.PHASE_STAGE:
            with self.subTest(phase=phase):
                self.assertEqual(report.verify_phase_bundle(phase, ID, BOOK, TOKEN, *bundle(phase)), int(BLOCK, 16))

    def test_unrelated_successful_creator_transaction_is_rejected(self):
        logged, transactions, receipts = bundle("curveBuy")
        unrelated = "0x9999999999999999999999999999999999999999"
        transactions[-1]["to"] = unrelated
        receipts[-1]["to"] = unrelated
        logged[-1]["transaction"]["to"] = unrelated
        with self.assertRaisesRegex(ValueError, "trade action target or selector differs"):
            report.verify_phase_bundle("curveBuy", ID, BOOK, TOKEN, logged, transactions, receipts)

    def test_wrong_strategy_or_phase_is_rejected(self):
        logged, transactions, receipts = bundle("curveBuy")
        with self.assertRaisesRegex(ValueError, "strategy, asset, amount or stage differs"):
            report.verify_phase_bundle("curveBuy", ID + 1, BOOK, TOKEN, logged, transactions, receipts)
        with self.assertRaisesRegex(ValueError, "strategy, asset, amount or stage differs"):
            report.verify_phase_bundle("v4Buy", ID, BOOK, TOKEN, logged, transactions, receipts)

    def test_missing_matching_event_is_rejected(self):
        logged, transactions, receipts = bundle("graduate")
        receipts[-1]["logs"] = receipts[-1]["logs"][:1]
        with self.assertRaisesRegex(ValueError, "graduation receipt lacks"):
            report.verify_phase_bundle("graduate", ID, BOOK, TOKEN, logged, transactions, receipts)

    def test_forged_broadcast_calldata_is_rejected(self):
        logged, transactions, receipts = bundle("curveSell")
        logged[-1]["transaction"]["input"] = report.APPROVE + encoded(BOOK["tradeRouter"]) + encoded(1)
        with self.assertRaisesRegex(ValueError, "broadcast log calldata"):
            report.verify_phase_bundle("curveSell", ID, BOOK, TOKEN, logged, transactions, receipts)


if __name__ == "__main__":
    unittest.main()

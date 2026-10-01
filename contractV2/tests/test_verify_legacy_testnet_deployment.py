"""Offline checks for the old 67726d6 deployment's read-only verifier."""

import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "legacy_verifier", Path(__file__).resolve().parents[1] / "tools/verify_legacy_testnet_deployment.py")
v = importlib.util.module_from_spec(spec)
spec.loader.exec_module(v)


def addr(number):
    return f"0x{number:040x}"


def txhash(number):
    return f"0x{number:064x}"


class LegacyReceipts(unittest.TestCase):
    def setUp(self):
        self.operator = addr(1000)
        self.book = {"chainId": 46630, "broadcast": True, "commit": v.LEGACY_COMMIT,
                     "operator": self.operator, "stocks": {}}
        next_address = 1
        for key in v.CODE_KEYS:
            self.book[key] = addr(next_address)
            next_address += 1
        for symbol in ("NVDA", "TSLA", "GME", "AAPL"):
            self.book["stocks"][symbol] = {}
            for key in ("token", "feed", "oracle", "pool"):
                self.book["stocks"][symbol][key] = addr(next_address)
                next_address += 1
        creations = [self.book[key] for key in v.CODE_KEYS if key not in ("poolManager", "weth", "hook")]
        creations += [stock[key] for stock in self.book["stocks"].values()
                      for key in ("token", "feed", "oracle")]
        self.run = {"chain": 46630, "transactions": [{"hash": txhash(i + 1)} for i in range(72)]}
        self.receipts = {}
        self.blocks = {}
        for i in range(72):
            h = txhash(i + 1)
            block_hash = txhash(1000 + i)
            number = 100 + i
            self.receipts[h] = {"transactionHash": h, "status": "0x1", "from": self.operator,
                                "blockNumber": hex(number), "blockHash": block_hash,
                                "contractAddress": creations[i] if i < len(creations) else None}
            self.blocks[hex(number)] = {"hash": block_hash, "number": hex(number), "transactions": [h]}
        self.head = {"hash": txhash(5000), "number": hex(200), "transactions": []}

    def rpc(self, method, params):
        if method == "eth_chainId":
            return hex(46630)
        if method == "eth_getTransactionReceipt":
            return self.receipts.get(params[0])
        if method == "eth_getBlockByNumber":
            return self.head if params[0] in ("latest", hex(200)) else self.blocks.get(params[0])
        self.fail(f"unexpected RPC method {method}")

    def test_all_72_canonical_receipts_are_required(self):
        hashes, block, block_hash = v.verify_receipts(self.book, self.run, self.rpc, 2)
        self.assertEqual(len(hashes), 72)
        self.assertEqual((block, block_hash), (200, self.head["hash"]))

        self.receipts[txhash(72)] = None
        with self.assertRaisesRegex(ValueError, "pending or missing"):
            v.verify_receipts(self.book, self.run, self.rpc, 2)

    def test_wrong_sender_failed_and_noncanonical_receipts_fail(self):
        original = self.receipts[txhash(1)]
        for change, message in (({"from": addr(999)}, "wrong receipt sender"),
                                ({"status": "0x0"}, "failed receipt"),
                                ({"blockHash": txhash(999)}, "noncanonical receipt")):
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, message):
                self.receipts[txhash(1)] = dict(original, **change)
                v.verify_receipts(self.book, self.run, self.rpc, 2)
        self.receipts[txhash(1)] = original

    def test_book_and_creation_addresses_are_bound_to_this_run(self):
        with self.assertRaisesRegex(ValueError, "expected the unverified"):
            v.verify_receipts(dict(self.book, commit="wrong"), self.run, self.rpc, 2)
        with self.assertRaisesRegex(ValueError, "72 transactions"):
            v.verify_receipts(self.book, dict(self.run, transactions=self.run["transactions"][:-1]), self.rpc, 2)
        original = self.receipts[txhash(1)]
        self.receipts[txhash(1)] = dict(original, contractAddress=addr(999))
        with self.assertRaisesRegex(ValueError, "deployment receipts"):
            v.verify_receipts(self.book, self.run, self.rpc, 2)

    def test_verification_rechecks_the_readback_head_hash(self):
        report = v.verify(self.book, self.run, self.rpc, readback=lambda *_: {"measured": True})
        self.assertEqual(report["transactionHashes"], [txhash(i + 1) for i in range(72)])
        self.assertEqual(report["verificationBlockNumber"], 200)
        self.assertIn("after broadcast", report["limitation"])

        def reorg(*_):
            self.head["hash"] = txhash(5001)
        with self.assertRaisesRegex(ValueError, "changed during readback"):
            v.verify(self.book, self.run, self.rpc, readback=reorg)

    def test_cast_rpc_uses_only_the_read_method_and_json_parameters(self):
        with patch.object(v.subprocess, "check_output", return_value='"0xb626"') as call:
            self.assertEqual(v.Rpc("https://rpc.testnet.chain.robinhood.com")("eth_chainId", []), "0xb626")
        self.assertEqual(call.call_args.args[0][:3], ["cast", "rpc", "eth_chainId"])
        self.assertIn("--raw", call.call_args.args[0])


class SourceDefaults(unittest.TestCase):
    def test_defaults_are_compared_to_67726d6_source_not_echoed_from_chain(self):
        v.source_defaults(list(v.DEFAULT_WORDS))
        changed = list(v.DEFAULT_WORDS)
        changed[-1] += 1
        with self.assertRaisesRegex(ValueError, "defaults differ"):
            v.source_defaults(changed)


if __name__ == "__main__":
    unittest.main()

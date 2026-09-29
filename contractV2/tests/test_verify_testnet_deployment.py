import copy
import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("verifier", Path(__file__).resolve().parents[1] / "tools/verify_testnet_deployment.py")
v = importlib.util.module_from_spec(spec)
spec.loader.exec_module(v)
TX = "0x" + "11" * 32
BLOCK = "0x" + "22" * 32
HEAD = "0x" + "33" * 32
OPERATOR = "0x" + "44" * 20


class ReceiptTests(unittest.TestCase):
    def setUp(self):
        self.book = {"chainId": 46630, "broadcast": False, "broadcastRequested": True, "operator": OPERATOR}
        self.book.update({key: OPERATOR for key in v.CODE_KEYS})
        self.book["stocks"] = {}
        self.run = {"chain": 46630, "transactions": [{"hash": TX}]}
        self.receipt = {"transactionHash": TX, "status": "0x1", "from": OPERATOR,
                        "blockNumber": "0xa", "blockHash": BLOCK, "contractAddress": OPERATOR}
        self.block = {"hash": BLOCK, "number": "0xa", "transactions": [TX]}
        self.head = {"hash": HEAD, "number": "0xb", "transactions": []}
        self.reads = []

    def rpc(self, method, params):
        if method == "eth_chainId":
            return "0xb626"
        if method == "eth_getTransactionReceipt":
            return self.receipt
        if method == "eth_getBlockByNumber":
            return self.block if params[0] == "0xa" else self.head
        self.fail("unexpected RPC " + method)

    def readback(self, book, rpc, block):
        self.reads.append(block)

    def verify(self):
        return v.verify(self.book, self.run, self.rpc, readback=self.readback)

    def test_confirmed_book_only_after_pinned_readback(self):
        result = self.verify()
        self.assertTrue(result["broadcast"])
        self.assertFalse(self.book["broadcast"])
        self.assertEqual(self.reads, [11])
        self.assertEqual(result["verification"]["blockHash"], HEAD)

    def test_failed_missing_pending_wrong_sender_and_reorg_receipts(self):
        original = copy.deepcopy(self.receipt)
        variants = [None, dict(original, status="0x0"), dict(original, blockHash=HEAD),
                    dict(original, **{"from": "0x" + "55" * 20})]
        for bad in variants:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.receipt = bad
                self.verify()
        self.assertEqual(self.reads, [])

    def test_receipt_hash_must_be_in_canonical_block(self):
        self.block["transactions"] = []
        with self.assertRaisesRegex(ValueError, "absent from canonical"):
            self.verify()

    def test_unrelated_successful_run_cannot_promote_candidate(self):
        self.receipt["contractAddress"] = "0x" + "55" * 20
        with self.assertRaisesRegex(ValueError, "candidate's deployment receipts"):
            self.verify()

    def test_null_hash_from_dry_run_and_incomplete_broadcast_refused(self):
        for transactions in ([], [{"hash": None}], [{"hash": TX}, {"hash": TX}]):
            self.run["transactions"] = transactions
            with self.subTest(transactions=transactions), self.assertRaises(ValueError):
                self.verify()

    def test_wrong_chain_and_dryrun_manifest_refused(self):
        for patch in ({"chainId": 4663}, {"broadcastRequested": False}, {"broadcast": True}):
            old = self.book
            self.book = dict(old, **patch)
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                self.verify()
            self.book = old
        self.run["chain"] = 4663
        with self.assertRaisesRegex(ValueError, "log has wrong chain"):
            self.verify()

    def test_insufficient_confirmations(self):
        self.head["number"] = "0xa"
        with self.assertRaisesRegex(ValueError, "wait for requested"):
            self.verify()

    def test_readback_failure_never_promotes(self):
        def failing(*args):
            raise ValueError("wrong factory binding")
        with self.assertRaisesRegex(ValueError, "binding"):
            v.verify(self.book, self.run, self.rpc, readback=failing)
        self.assertNotIn("verification", self.book)
        self.assertFalse(self.book["broadcast"])

    def test_reorg_during_readback_refused(self):
        def reorg(*args):
            self.head["hash"] = BLOCK
        with self.assertRaisesRegex(ValueError, "changed during readback"):
            v.verify(self.book, self.run, self.rpc, readback=reorg)

    def test_atomic_output_contains_complete_verification(self):
        import json
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "book.json"
            out.write_text('{"old":true}')
            result = self.verify()
            v.promote(result, out)
            self.assertEqual(json.loads(out.read_text()), result)
            self.assertEqual(list(Path(directory).iterdir()), [out])


class ReadbackTests(unittest.TestCase):
    def test_missing_code_and_wrong_runtime_hash_rejected(self):
        book = {"poolManager": OPERATOR, "codeHashes": {"poolManager": BLOCK}}
        for runtime, expected in (("0x", "missing code"), ("0x1234", "code hash mismatch")):
            with self.subTest(runtime=runtime), self.assertRaisesRegex(ValueError, expected):
                v.verify_bindings(book, lambda method, params: runtime, 123, hash_code=lambda code: HEAD)


if __name__ == "__main__":
    unittest.main()

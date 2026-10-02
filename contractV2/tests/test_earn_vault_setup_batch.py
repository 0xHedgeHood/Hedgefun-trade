"""Offline calldata and Safe file checks for earn_vault_setup_batch.py."""

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import earn_safe_codec as build  # noqa: E402
import earn_vault_setup_batch as earn  # noqa: E402

CALL = "0x0000000000000000000000000000000000000101"
VAULT = "0x0000000000000000000000000000000000000102"
ADAPTER = "0x0000000000000000000000000000000000000103"
PUT = "0x0000000000000000000000000000000000000104"
BUYER = "0x0000000000000000000000000000000000000105"
DEPOSITOR = "0x0000000000000000000000000000000000000106"
ADDRESSES = {
    "chainId": 4663,
    "safe": "0x0000000000000000000000000000000000000201",
    "stocks": {"NVDA": "0x0000000000000000000000000000000000000202"},
    "rhNvdaFeed": "0x0000000000000000000000000000000000000203",
    "legacyCallDesk": "0x0000000000000000000000000000000000000204",
}


class EarnVaultSetupBatchTest(unittest.TestCase):
    def calls(self):
        return earn.plan(CALL, VAULT, ADAPTER, PUT, BUYER, [DEPOSITOR], ADDRESSES)

    def test_setup_targets_and_calldata(self):
        calls = self.calls()
        self.assertEqual(len(calls), 10)
        self.assertEqual([c["to"] for c in calls[:9]],
                         [build.checksum_addr(CALL)] * 3 + [build.checksum_addr(PUT)] * 3 +
                         [build.checksum_addr(VAULT)] * 3)
        self.assertEqual(calls[0]["values"],
                         [build.checksum_addr(ADDRESSES["stocks"]["NVDA"]),
                          build.checksum_addr(ADDRESSES["rhNvdaFeed"]), True])
        self.assertEqual(calls[6]["values"], [build.checksum_addr(PUT)])
        self.assertEqual(calls[7]["values"], [build.checksum_addr(ADAPTER)])
        self.assertEqual(calls[9]["values"], [build.checksum_addr(DEPOSITOR), True])
        for call in calls:
            sig = earn.METHODS[call["name"]][0]
            decoded = json.loads(build.cast("calldata-decode", "--json", sig, call["data"]))
            self.assertEqual([build._norm(v) for v in decoded], call["values"])

    def test_refuses_old_desk_and_bad_roles(self):
        with self.assertRaisesRegex(build.Refuse, "NetShare"):
            earn.plan(ADDRESSES["legacyCallDesk"], VAULT, ADAPTER, PUT, BUYER, [DEPOSITOR], ADDRESSES)
        with self.assertRaisesRegex(build.Refuse, "NetShare"):
            earn.plan(earn.KNOWN_LEGACY_CALL_DESK, VAULT, ADAPTER, PUT, BUYER, [DEPOSITOR], ADDRESSES)
        with self.assertRaisesRegex(build.Refuse, "reused"):
            earn.plan(CALL, VAULT, ADAPTER, PUT, CALL, [DEPOSITOR], ADDRESSES)
        with self.assertRaisesRegex(build.Refuse, "duplicate depositor"):
            earn.plan(CALL, VAULT, ADAPTER, PUT, BUYER, [DEPOSITOR, DEPOSITOR], ADDRESSES)
        with self.assertRaisesRegex(build.Refuse, "at least one"):
            earn.plan(CALL, VAULT, ADAPTER, PUT, BUYER, [], ADDRESSES)
        with self.assertRaisesRegex(build.Refuse, "not an address"):
            earn.plan("0xhello", VAULT, ADAPTER, PUT, BUYER, [DEPOSITOR], ADDRESSES)

    def test_explicit_address_file_and_check_only_cli(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "verified-addresses.json"
            path.write_text(json.dumps(ADDRESSES))
            self.assertEqual(earn.load_addresses(path), ADDRESSES)
            with contextlib.redirect_stdout(io.StringIO()) as output:
                result = earn.main([
                    "build", "--addresses", str(path), "--call-desk", CALL,
                    "--vault", VAULT, "--adapter", ADAPTER, "--put-desk", PUT,
                    "--buyer", BUYER, "--depositor", DEPOSITOR, "--check",
                ])
            self.assertEqual(result, 0)
            self.assertIn("CHECK ONLY: no file written", output.getvalue())
            path.write_text(json.dumps({**ADDRESSES, "chainId": 46630}))
            with self.assertRaisesRegex(build.Refuse, "Chain 4663"):
                earn.load_addresses(path)

    def test_safe_file_round_trip_and_tamper_detection(self):
        batch = earn.bundle(self.calls(), ADDRESSES)
        self.assertEqual(batch["meta"]["checksum"], build.safe_checksum(batch))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "batch.json"
            path.write_text(json.dumps(batch))
            with contextlib.redirect_stdout(io.StringIO()):
                earn.decode(path, ADDRESSES)
            batch["transactions"][0]["contractInputsValues"]["feed"] = BUYER
            path.write_text(json.dumps(batch))
            with self.assertRaisesRegex(build.Refuse, "checksum"):
                earn.decode(path, ADDRESSES)
            batch["meta"]["checksum"] = build.safe_checksum(batch)
            path.write_text(json.dumps(batch))
            with self.assertRaisesRegex(build.Refuse, "round-trip"):
                earn.decode(path, ADDRESSES)


if __name__ == "__main__":
    unittest.main()

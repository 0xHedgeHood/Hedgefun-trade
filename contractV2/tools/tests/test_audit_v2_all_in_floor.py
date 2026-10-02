"""Offline public-evidence guards; real local ABI/Keccak helpers, no RPC/signing.

The synthetic book/hash allowlist replaces only the production identity pins.
Every calldata, event word, constructor and runtime comparison uses the auditor's
real helpers. A complete accepted fixture prevents vacuous rejection tests.
"""
import argparse
import copy
import hashlib
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tools import audit_v2_all_in_floor as audit


def addr(value):
    return "0x" + format(value, "040x")


def packed(*values):
    return "0x" + audit.encode_values(values).hex()


class PublicRpc:
    """Strict fixture: unexpected methods/addresses fail, never use a network."""
    def __init__(self, fixture):
        self.f = fixture

    def request(self, method, params):
        f = self.f
        if method == "eth_chainId":
            return hex(f.chain)
        if method == "eth_blockNumber":
            return hex(f.pin + 10)
        if method == "eth_getBlockByNumber":
            number = int(params[0], 16)
            f.header_reads[number] = f.header_reads.get(number, 0) + 1
            changed = f.reorg_block == number and f.header_reads[number] >= (2 if number == f.pin else 3)
            return {"hash": packed(999999) if changed else f.block_hash(number), "timestamp": hex(1000 + number),
                    "transactions": [h for h, tx in f.txs.items() if int(tx["blockNumber"], 16) == number]}
        if method == "eth_getTransactionByHash":
            return copy.deepcopy(f.txs[params[0]])
        if method == "eth_getTransactionReceipt":
            return copy.deepcopy(f.receipts[params[0]])
        if method == "eth_call":
            expected = audit.cast("calldata", "predict(" + audit.REQUEST + ")", f.request)
            if params[0]["to"].lower() == f.book["factory"] and params[0]["data"] == expected:
                return packed(int(f.token, 16), int(f.treasury, 16), f.terms)
            curve_data = audit.cast("calldata", "predictCurve(" + audit.REQUEST + ")", f.request)
            if params[0]["to"].lower() == f.book["factory"] and params[0]["data"] == curve_data:
                return packed(int(f.curve, 16))
        raise AssertionError("unmocked public RPC: " + method + " " + repr(params))

    def call(self, target, signature, block, *args):
        key = (target.lower(), signature, block, tuple(args))
        if key in self.f.calls:
            return list(self.f.calls[key])
        raise AssertionError("unmocked public getter: " + repr(key))

    def code(self, target, block):
        return self.f.codes[target.lower()]


class PublicFixture:
    def __init__(self, directory):
        self.root = pathlib.Path(directory)
        self.pin, self.before, self.chain = 200, 99, 46630
        self.source = "1" * 40
        self.creation = bytes.fromhex("600060016002")
        self.runtime = bytes.fromhex("60aa6000")
        self.chunk_creation = "0x6010"
        self.hashes = [audit.digest(bytes([i + 1, 0])) for i in range(4)] + [audit.digest(self.creation)]
        self.codes, self.calls, self.txs, self.receipts = {}, {}, {}, {}
        self.header_reads, self.reorg_block = {}, None
        self.book = {"chainId": 46630, "broadcast": True, "factory": addr(100),
                     "treasuryDeployer": addr(101), "hook": addr(102), "curveDeployer": addr(103),
                     "usdg": addr(104), "poolManager": addr(105),
                     "stocks": {"TSLA": {"token": addr(42), "pool": addr(106), "oracle": addr(107)}}}
        self.token, self.treasury, self.curve, self.vault = map(addr, [108, 109, 110, 111])
        self.terms = 1234
        self.params = [1, 2, 1, 0, 2000, 50, 100, 30, 0, 600, 10_000_000, 100_000_000, 500_000_000, 0]
        self.capital, self.lp = 12 * 10**18, 8 * 10**18
        self.token_released, self.token_seeded = 560_000_000 * 10**18, 200_000_000 * 10**18
        self.liquidity = 123456
        self.request = "(" + ",".join(["TSLA Steady", "HFSTEADY", self.stock, audit.CREATOR,
                                       "300", "1000", "1", "2", "1", "0", "2000", "0",
                                       "202609300310", "25000000", "26500000000"]) + ")"
        self._artifact()
        self._state()
        self._phases()
        self.book_path, self.manifest_path, self.output = [self.root / p for p in ["fee-book.json", "manifest.json", "extension-proof.json"]]
        self.book_bytes = json.dumps(self.book, sort_keys=True).encode()
        self.book_path.write_bytes(self.book_bytes)
        self.save_manifest()
        self.output.write_text("previous accepted proof\n")
        self.args = argparse.Namespace(book=str(self.book_path), manifest=str(self.manifest_path),
                                       output=str(self.output), block=self.pin, rpc="mock://public-only")

    @property
    def stock(self):
        return self.book["stocks"]["TSLA"]["token"]

    @staticmethod
    def block_hash(number):
        return "0x" + format(number, "064x")

    def put(self, target, sig, values, *args, block=None):
        self.calls[(target.lower(), sig, self.pin if block is None else block, tuple(args))] = values

    def log(self, emitter, signature, indexed=(), values=()):
        return {"address": emitter, "topics": [audit.cast("keccak", signature)] + [packed(v) for v in indexed],
                "data": packed(*values)}

    def transfer(self, asset, sender, recipient, amount):
        return self.log(asset, "Transfer(address,address,uint256)", [int(sender, 16), int(recipient, 16)], [amount])

    def constructor(self):
        return audit.encode_values([int(a, 16) for a in [self.book["usdg"], self.stock,
                                  self.book["stocks"]["TSLA"]["pool"], self.book["stocks"]["TSLA"]["oracle"],
                                  self.token, self.book["poolManager"], self.book["factory"]]] + self.params)

    def _artifact(self):
        source = self.root / "src/PublicMock.sol"
        source.parent.mkdir(parents=True)
        source.write_text("// synthetic public source fixture\n")
        self.artifact = {"metadata": {"compiler": {"version": "0.8.26+commit.8a97fa7a"},
                         "settings": {"optimizer": {"enabled": True, "runs": 1}, "evmVersion": "cancun",
                                      "metadata": {"bytecodeHash": "none"}},
                         "sources": {"src/PublicMock.sol": {"keccak256": audit.digest(source.read_bytes())}}},
                         "bytecode": {"object": "0x" + self.creation.hex()},
                         "deployedBytecode": {"object": "0x60006000", "immutableReferences": {"0": [{"start": 1, "length": 1}]}}}
        self.artifact_path = self.root / "out/HedgeFunV2AllInTreasury.sol/HedgeFunV2AllInTreasury.json"
        self.artifact_path.parent.mkdir(parents=True)
        self.save_artifact()
        chunks = self.root / "out/V2TreasuryDeployer.sol/V2InitCodeChunk.json"
        chunks.parent.mkdir(parents=True)
        self.chunk_artifact_path = chunks
        self.chunk_artifact = {"bytecode": {"object": self.chunk_creation}, "metadata": copy.deepcopy(self.artifact["metadata"])}
        chunks.write_text(json.dumps(self.chunk_artifact))
        for name in ["script/TestnetV2AllInFloor.s.sol", "tools/audit_v2_all_in_floor.py", "tools/audit_v2_keeper_reward.py"]:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("// synthetic public tool fixture\n")

    def save_artifact(self):
        self.artifact_path.write_text(json.dumps(self.artifact))

    def _state(self):
        f, r = self.book["factory"], self.book["treasuryDeployer"]
        core = {}
        for i, key in enumerate(["factory", "treasuryDeployer", "hook", "curveDeployer"]):
            self.codes[self.book[key]] = bytes([0x60, i + 10])
            core[key] = audit.digest(self.codes[self.book[key]])
        self.book["verification"] = {"upgradeProof": {"codeHashes": {"core": core}}}
        self.put(f, "owner()", [int(audit.OP, 16)])
        self.put(r, "factory()", [int(f, 16)])
        self.put(r, "kindCount()", [5])
        self.put(r, "kindCount()", [4], block=self.before)
        self.chunks = []
        for kind, code_hash in enumerate(self.hashes):
            a, b = addr(200 + kind * 2), addr(201 + kind * 2)
            if kind == 4:
                a, b = [audit.cast("compute-address", audit.OP, "--nonce", str(n)).split()[-1].lower() for n in [0, 1]]
            code = bytes([kind + 1, 0]) if kind < 4 else self.creation
            half = len(code) // 2
            self.codes[a], self.codes[b] = code[:half], code[half:]
            self.put(r, "kinds(uint8)", [int(a, 16), int(b, 16)], kind)
            self.put(r, "kindManifest(uint8)", [1, 1, int(code_hash, 16), 3] if kind in [2, 3] else [0, 0, int(code_hash, 16), 0], kind)
            if kind == 4:
                self.chunks = [a, b]
        self.put(f, "strategyCount()", [3], block=self.before)
        self.put(f, "strategyCount()", [4])
        for id_ in range(3):
            old_treasury = addr(300 + id_)
            values = [int(addr(400 + id_), 16), int(old_treasury, 16), int(self.book["hook"], 16), int(self.stock, 16), int(audit.CREATOR, 16)]
            self.put(f, "strategies(uint256)", values, id_, block=self.before)
            self.put(f, "strategies(uint256)", values, id_)
            self.codes[old_treasury] = bytes([0x60, id_])
        self.put(f, "strategies(uint256)", [int(a, 16) for a in [self.token, self.treasury, self.book["hook"], self.stock, audit.CREATOR]], 3)
        self.codes[self.treasury] = self.runtime
        self.put(self.treasury, "params()", self.params)
        self.put(self.treasury, "poolFeeBps()", [30])
        for getter, target in {"factory": f, "token": self.token, "hook": self.book["hook"], "stock": self.stock,
                               "usdg": self.book["usdg"], "pool": self.book["stocks"]["TSLA"]["pool"],
                               "oracle": self.book["stocks"]["TSLA"]["oracle"], "poolManager": self.book["poolManager"]}.items():
            self.put(self.treasury, getter + "()", [int(target, 16)])
        self.put(self.treasury, "liquidityVault()", [int(self.vault, 16)])
        self.put(f, "curves(uint256)", [int(self.curve, 16)], 3)
        for getter, target in {"factory": f, "token": self.token, "treasury": self.treasury, "stock": self.stock}.items():
            self.put(self.curve, getter + "()", [int(target, 16)])
        self.put(self.curve, "status()", [2])
        self.put(self.stock, "balanceOf(address)", [self.capital], self.treasury)
        for sig, value in [("bookedStock()", self.capital), ("buybackStock()", 0), ("unbookedStock()", 0)]:
            self.put(self.treasury, sig, [value])

    def _phases(self):
        f, r = self.book["factory"], self.book["treasuryDeployer"]
        half = len(self.creation) // 2
        inputs = [self.chunk_creation + audit.cast("abi-encode", "constructor(bytes)", "0x" + part.hex())[2:]
                  for part in [self.creation[:half], self.creation[half:]]]
        self.init_hash = int(audit.digest(self.creation + self.constructor()), 16)
        bound = self.log(r, "TreasuryCodeBound(address,uint8,bytes32,bytes32,bytes32)",
                         [int(self.treasury, 16), 4], [self.init_hash, int(audit.digest(self.runtime), 16), 0])
        graduation = [self.log(self.curve, "Bought(address,address,uint256,uint256,uint256)",
                              [int(audit.CREATOR, 16)] * 2, [self.capital + self.lp, 440_000_000 * 10**18, 0]),
                      self.log(self.curve, "Released(uint256,uint256)", values=[self.capital + self.lp, self.token_released]),
                      self.log(f, "GraduationCapitalSplit(uint256,uint256,uint256,bool)", [3], [self.lp, self.capital, 1]),
                      self.log(f, "Graduated(uint256,uint160,uint128,uint256,uint256,uint256)", [3],
                               [2**96, self.liquidity, self.lp, self.token_seeded, self.token_released - self.token_seeded]),
                      self.log(self.vault, "Seeded(uint128,uint256,uint256)", values=[self.liquidity, self.lp, self.token_seeded]),
                      self.transfer(self.stock, self.curve, f, self.capital + self.lp),
                      self.transfer(self.stock, f, self.treasury, self.capital),
                      self.transfer(self.stock, f, self.vault, self.lp),
                      self.transfer(self.token, self.curve, audit.CREATOR, 440_000_000 * 10**18),
                      self.transfer(self.token, self.curve, f, self.token_released),
                      self.transfer(self.token, f, self.vault, self.token_seeded),
                      self.transfer(self.token, f, addr(0), self.token_released - self.token_seeded)]
        operations = [
            [(None, inputs[0], [], self.chunks[0]), (None, inputs[1], [], self.chunks[1]),
             (r, audit.cast("calldata", "registerKind(address,address)", *self.chunks),
              [self.log(r, "KindRegistered(uint8,address,address)", [4], [int(a, 16) for a in self.chunks])], None)],
            [(r, audit.cast("calldata", "setStrategyKind(string,uint96,uint8)", "HFSTEADY", "202609300310", "4"), [], None),
             (self.book["curveDeployer"], audit.cast("calldata", "setCurveConfig(string,uint96,uint16,uint8)", "HFSTEADY", "202609300310", "4400", "0"), [], None),
             (f, audit.cast("calldata", "launch(" + audit.REQUEST + ",bytes32)", self.request, packed(self.terms)),
              [self.log(f, "Launched(uint256,string,address,address,address,address,address)", [3]),
               self.transfer(self.book["usdg"], audit.CREATOR, audit.OP, 25_000_000), bound], None)],
            [(self.curve, audit.cast("calldata", "buy(uint256,uint256,address,uint256)", str(24 * 10**18),
                                    str(440_000_000 * 10**18), audit.CREATOR, str(1000 + self.before + 300)), graduation, None)]]
        self.manifest = {"sourceCommit": self.source, "phases": []}
        number, nonce = 100, {audit.OP: 0, audit.CREATOR: 0}
        for i, (phase, ops) in enumerate(zip(audit.PHASES, operations), 1):
            sender = audit.OP if i == 1 else audit.CREATOR
            run = {"chain": 46630, "pending": [], "transactions": [], "receipts": []}
            for to, data, logs, deployed in ops:
                h = "0x" + format(number + 1000, "064x")
                tx = {"hash": h, "from": sender, "to": to, "input": data, "nonce": hex(nonce[sender]),
                      "chainId": hex(46630), "value": "0x0", "blockNumber": hex(number), "blockHash": self.block_hash(number),
                      "transactionIndex": "0x0"}
                receipt = {"transactionHash": h, "from": sender, "to": to, "status": "0x1", "blockNumber": hex(number),
                           "blockHash": tx["blockHash"], "transactionIndex": "0x0", "gasUsed": "0x1234", "logs": logs,
                           "contractAddress": deployed}
                self.txs[h], self.receipts[h] = tx, receipt
                run["transactions"].append({"hash": h, "transaction": copy.deepcopy(tx)})
                run["receipts"].append(copy.deepcopy(receipt))
                number += 1
                nonce[sender] += 1
            name = f"phase-{i:02d}-{phase}.json"
            raw = json.dumps(run, sort_keys=True).encode()
            (self.root / name).write_bytes(raw)
            self.manifest["phases"].append({"phase": phase, "file": name, "dryRunPin": self.before,
                                            "sha256": hashlib.sha256(raw).hexdigest()})

    def save_manifest(self):
        self.manifest_path.write_text(json.dumps(self.manifest))


class AllInAuditGuards(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="all-in-public-audit-")
        self.addCleanup(self.temp.cleanup)
        self.f = PublicFixture(self.temp.name)
        self.git_diff = b""
        real_check, real_sha = subprocess.check_output, hashlib.sha256

        def public_process(command, **kwargs):
            if command[:3] == ["git", "cat-file", "-e"]:
                return b""
            if command[:2] == ["git", "diff"]:
                return self.git_diff
            if command[0] == "cast":
                self.assertNotIn(command[1], ["send", "wallet", "rpc", "call"])
                return real_check(command, **kwargs)
            raise AssertionError("unexpected process: " + repr(command))

        def book_sha(data=b""):
            if data == self.f.book_bytes:
                return mock.Mock(hexdigest=lambda: "ada428eb04ac0f982fd6a002e8745b4100c6db99ce620adcf254e42ac1cbc82c")
            return real_sha(data)

        for patch in [mock.patch.object(audit, "__file__", str(self.f.root / "tools/audit.py")),
                      mock.patch.object(audit, "Rpc", lambda _: PublicRpc(self.f)),
                      mock.patch.object(audit, "NEW_HASH", self.f.hashes[-1]),
                      mock.patch.object(audit, "OLD_HASHES", self.f.hashes[:4]),
                      mock.patch.object(subprocess, "check_output", side_effect=public_process),
                      mock.patch.object(hashlib, "sha256", side_effect=book_sha)]:
            patch.start()
            self.addCleanup(patch.stop)

    def reject(self, expression, args=None):
        before = {p: p.read_bytes() for p in [self.f.book_path, self.f.manifest_path, self.f.output]}
        with self.assertRaisesRegex((ValueError, KeyError), expression):
            audit.audit(args or self.f.args)
        for path, content in before.items():
            self.assertEqual(path.read_bytes(), content, "rejection changed evidence: " + str(path))

    def test_complete_public_fixture_reaches_exact_constructor_proof(self):
        result = audit.audit(self.f.args)
        self.assertTrue(result["verified"])
        self.assertEqual(result["strategy"]["initCodeHash"], packed(self.f.init_hash))
        self.assertEqual(len(result["transactions"]), 7)
        self.assertEqual(self.f.output.read_text(), "previous accepted proof\n")

    def test_rejects_overwriting_inputs_and_reserved_books(self):
        protected = [self.f.book_path, self.f.manifest_path] + [self.f.root / p["file"] for p in self.f.manifest["phases"]]
        protected += [self.f.root / name for name in ["testnet-v2-fees.json", "testnet-v2-whitelist.json", "testnet-v2.json", "testnet-v2-keeper-reward.json"]]
        alias = self.f.root / "book-alias.json"
        alias.symlink_to(self.f.book_path)
        protected.append(alias)
        for path in protected:
            with self.subTest(path=path.name):
                args = copy.copy(self.f.args)
                args.output = str(path)
                self.reject("overwrite protected evidence", args)

    def test_phase_symlink_target_is_also_protected_evidence(self):
        phase = self.f.root / self.f.manifest["phases"][0]["file"]
        target = self.f.root / "canonical-append-evidence.json"
        phase.rename(target)
        phase.symlink_to(target)
        args = copy.copy(self.f.args)
        args.output = str(target)
        self.reject("overwrite protected evidence", args)

    def test_missing_or_reordered_phase_is_rejected_before_rpc(self):
        for phases in [self.f.manifest["phases"][:-1], list(reversed(self.f.manifest["phases"]))]:
            with self.subTest(phases=[p["phase"] for p in phases]):
                self.f.manifest["phases"] = phases
                self.f.save_manifest()
                self.reject("incomplete phase manifest")

    def test_phase_path_cannot_escape_archive(self):
        self.f.manifest["phases"][0]["file"] = "../phase-01-appendPlain.json"
        self.f.save_manifest()
        self.reject("unexpected phase file")

    def test_changed_book_and_source_revision_are_rejected(self):
        self.f.book_path.write_bytes(self.f.book_bytes + b"\n")
        self.reject("fee core book changed")
        self.f.book_path.write_bytes(self.f.book_bytes)
        self.f.manifest["sourceCommit"] = "not-a-commit"
        self.f.save_manifest()
        self.reject("invalid source revision")
        self.f.manifest["sourceCommit"] = self.f.source
        self.f.save_manifest()
        self.git_diff = b"different production source\n"
        self.reject("source differs from release")

    def test_compiler_settings_and_stale_artifact_source_are_rejected(self):
        self.f.artifact["metadata"]["settings"]["viaIR"] = True
        self.f.save_artifact()
        self.reject("wrong compiler settings")
        self.f.artifact["metadata"]["settings"].pop("viaIR")
        self.f.artifact["metadata"]["sources"]["src/PublicMock.sol"]["keccak256"] = packed(1)
        self.f.save_artifact()
        self.reject("stale artifact source")

    def test_chunk_artifact_is_checked_independently(self):
        self.f.chunk_artifact["metadata"]["settings"]["optimizer"]["runs"] = 200
        self.f.chunk_artifact_path.write_text(json.dumps(self.f.chunk_artifact))
        self.reject("wrong compiler settings")

    def test_unreviewed_creation_and_wrong_chain_are_rejected(self):
        self.f.artifact["bytecode"]["object"] += "00"
        self.f.save_artifact()
        self.reject("unreviewed creation code")
        self.f.artifact["bytecode"]["object"] = "0x" + self.f.creation.hex()
        self.f.save_artifact()
        self.f.chain = 4663
        self.reject("wrong chain")

    def test_missing_receipt_and_archived_calldata_mismatch_are_rejected(self):
        path = self.f.root / self.f.manifest["phases"][0]["file"]
        original = path.read_bytes()
        run = json.loads(original)
        run["receipts"].pop()
        path.write_text(json.dumps(run))
        self.reject("phase lacks receipts|archived phase changed")
        path.write_bytes(original)
        self.f.txs[next(iter(self.f.txs))]["input"] += "00"
        self.reject("transaction differs from archived plan")

    def test_chunk_constructor_is_checked_after_plan_matches(self):
        h = next(iter(self.f.txs))
        self.f.txs[h]["input"] += "00"
        path = self.f.root / self.f.manifest["phases"][0]["file"]
        run = json.loads(path.read_bytes())
        run["transactions"][0]["transaction"]["input"] = self.f.txs[h]["input"]
        raw = json.dumps(run, sort_keys=True).encode()
        path.write_bytes(raw)
        self.f.manifest["phases"][0]["sha256"] = hashlib.sha256(raw).hexdigest()
        self.f.save_manifest()
        self.reject("wrong chunk CREATE input")

    def test_constructor_hash_uses_all_frozen_params_and_address_order(self):
        launch = next(r for r in self.f.receipts.values() if any(l["topics"][0] == audit.cast("keccak", "TreasuryCodeBound(address,uint8,bytes32,bytes32,bytes32)") for l in r["logs"]))
        bound = launch["logs"][-1]
        wrong = self.f.constructor()
        wrong = wrong[32:64] + wrong[:32] + wrong[64:]
        bound["data"] = packed(int(audit.digest(self.f.creation + wrong), 16), int(audit.digest(self.f.runtime), 16), 0)
        self.reject("exact creation/constructor/runtime binding mismatch")
        bound["data"] = packed(self.f.init_hash, int(audit.digest(self.f.runtime), 16), 0)
        self.f.params[9] += 1
        self.reject("exact creation/constructor/runtime binding mismatch")

    def test_runtime_match_does_not_allow_differences_outside_immutable_slots(self):
        self.f.codes[self.f.treasury] = bytes.fromhex("60aa6001")
        for receipt in self.f.receipts.values():
            for log in receipt["logs"]:
                if log["topics"][0] == audit.cast("keccak", "TreasuryCodeBound(address,uint8,bytes32,bytes32,bytes32)"):
                    log["data"] = packed(self.f.init_hash, int(audit.digest(self.f.codes[self.f.treasury]), 16), 0)
        self.reject("runtime differs outside immutables")

    def test_truncated_abi_and_duplicate_binding_event_are_rejected(self):
        for receipt in self.f.receipts.values():
            for log in receipt["logs"]:
                if log["topics"][0] == audit.cast("keccak", "TreasuryCodeBound(address,uint8,bytes32,bytes32,bytes32)"):
                    original = log["data"]
                    log["data"] = original[:-2]
                    self.reject("bad ABI words")
                    log["data"] = original
                    receipt["logs"].append(copy.deepcopy(log))
                    self.reject("missing/duplicate TreasuryCodeBound")
                    return
        self.fail("fixture has no binding event")

    def test_strategy_address_word_must_be_canonical_uint160(self):
        self.f.calls[(self.f.book["factory"], "strategies(uint256)", self.f.pin, (3,))][0] = 2**160
        self.reject("noncanonical ABI address")

    def test_pin_and_before_block_reorg_are_rejected_after_all_accounting_checks(self):
        for block in [self.f.pin, self.f.before]:
            with self.subTest(block=block):
                self.f.header_reads = {}
                self.f.reorg_block = block
                self.reject("pin reorg")

    def test_graduation_seed_and_capital_transfers_must_both_conserve(self):
        receipt = list(self.f.receipts.values())[-1]
        seed_topic = audit.cast("keccak", "Seeded(uint128,uint256,uint256)")
        seed = next(l for l in receipt["logs"] if l["topics"][0] == seed_topic)
        seed["data"] = packed(self.f.liquidity, self.f.lp + 1, self.f.token_seeded)
        self.reject("wrong actual vault seed")
        seed["data"] = packed(self.f.liquidity, self.f.lp, self.f.token_seeded)
        transfer_topic = audit.cast("keccak", "Transfer(address,address,uint256)")
        capital = next(l for l in receipt["logs"] if l["address"] == self.f.stock and l["topics"][0] == transfer_topic
                       and l["topics"][2] == packed(int(self.f.treasury, 16)))
        capital["data"] = packed(self.f.capital - 1)
        self.reject("graduation principal transfer/status")


if __name__ == "__main__":
    unittest.main()

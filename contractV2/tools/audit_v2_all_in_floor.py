#!/usr/bin/env python3
"""Read-only receipt/source audit of the three TestnetV2AllInFloor phases.

Never signs or broadcasts. Consumes public Forge transaction/receipt JSON and
publishes a separate extension proof; the fee core's original book is protected.
"""
import argparse
import hashlib
import json
import pathlib
import subprocess

try:
    from .audit_v2_keeper_reward import Rpc, cast, digest, words, address, encode_values, event, exact_calls
except ImportError:
    from audit_v2_keeper_reward import Rpc, cast, digest, words, address, encode_values, event, exact_calls

OP = "0x75cee941b0ef3a83fea0397bbf903c12c1d7e96d"
CREATOR = "0xd4f69d180a9bc36f27d307e90e365d1e012816d5"
NEW_HASH = "0x009bc6eaf9730c8339e40b736087e64e31ec4c2542a2ec41dcf05fc781050ac4"
OLD_HASHES = ["0xaec5bc5cdaeef801f738c564cc0aca2f1d4405a38e218d4cad9ad66ccc810b93",
              "0x20d84b868ef45cf6a4fa3f11d90432fa8b6e8d375edcfd668856770b12199720",
              "0x67d0657c52fdd7fbe540435539f44a46caa159e82b2c74a91cb7c8b1a3db151f",
              "0x21db9a11b19dfe73eb5e372972f0dc7057595360c989d92012ba4b638e0d271f"]
PHASES = ["appendPlain", "launchPlain", "graduatePlain"]
REQUEST = "(string,string,address,address,uint16,uint16,uint32,uint32,uint16,uint16,uint16,uint16,uint96,uint256,uint256)"


def require(ok, message):
    if not ok:
        raise ValueError(message)


def check_artifact(root, artifact):
    metadata = artifact["metadata"]
    settings = metadata["settings"]
    require(metadata["compiler"]["version"] == "0.8.26+commit.8a97fa7a" and settings["optimizer"] == {"enabled": True, "runs": 1}
            and settings["evmVersion"] == "cancun" and settings["metadata"]["bytecodeHash"] == "none" and not settings.get("viaIR", False), "wrong compiler settings")
    for path, description in metadata["sources"].items():
        require(digest((root / path).read_bytes()) == description["keccak256"], "stale artifact source: " + path)


def transferred(receipt, asset, sender, recipient):
    signature = cast("keccak", "Transfer(address,address,uint256)").lower()
    total = 0
    for log in receipt["logs"]:
        if log["address"].lower() != asset.lower() or len(log["topics"]) != 3 or log["topics"][0].lower() != signature:
            continue
        if address(int(log["topics"][1], 16)) == sender.lower() and address(int(log["topics"][2], 16)) == recipient.lower():
            amount = words(log["data"])
            require(len(amount) == 1, "malformed transfer")
            total += amount[0]
    return total


def audit(args):
    root = pathlib.Path(__file__).resolve().parents[1]
    book_path, manifest_path, output = map(lambda p: pathlib.Path(p).resolve(), [args.book, args.manifest, args.output])
    book_bytes = book_path.read_bytes()
    require(hashlib.sha256(book_bytes).hexdigest() == "ada428eb04ac0f982fd6a002e8745b4100c6db99ce620adcf254e42ac1cbc82c", "fee core book changed")
    book, manifest = json.loads(book_bytes), json.loads(manifest_path.read_bytes())
    require(book["chainId"] == 46630 and book["broadcast"] is True, "not a live testnet book")
    require([p["phase"] for p in manifest["phases"]] == PHASES, "incomplete phase manifest")
    files = []
    for i, phase in enumerate(manifest["phases"]):
        require(phase["file"] == f"phase-{i + 1:02d}-{phase['phase']}.json", "unexpected phase file")
        files.append((manifest_path.parent / phase["file"]).resolve())
    require(output not in {book_path, manifest_path, *files} and output.name not in
            ["testnet-v2-fees.json", "testnet-v2-whitelist.json", "testnet-v2.json", "testnet-v2-keeper-reward.json"], "output would overwrite protected evidence")
    source = manifest["sourceCommit"]
    require(len(source) == 40 and all(c in "0123456789abcdef" for c in source), "invalid source revision")
    subprocess.check_output(["git", "cat-file", "-e", source + "^{commit}"], cwd=root)
    require(not subprocess.check_output(["git", "diff", source, "--", "src", "lib", "foundry.toml"], cwd=root), "source differs from release")
    artifact = json.loads((root / "out/HedgeFunV2AllInTreasury.sol/HedgeFunV2AllInTreasury.json").read_bytes())
    check_artifact(root, artifact)
    chunk_artifact = json.loads((root / "out/V2TreasuryDeployer.sol/V2InitCodeChunk.json").read_bytes())
    check_artifact(root, chunk_artifact)
    metadata = artifact["metadata"]
    settings = metadata["settings"]
    creation = bytes.fromhex(artifact["bytecode"]["object"].removeprefix("0x"))
    require(digest(creation) == NEW_HASH, "unreviewed creation code")
    rpc = Rpc(args.rpc)
    require(int(rpc.request("eth_chainId", []), 16) == 46630, "wrong chain")
    pin = args.block or int(rpc.request("eth_blockNumber", []), 16) - 10
    block_hash = rpc.request("eth_getBlockByNumber", [hex(pin), False])["hash"]
    transactions, receipts = [], {}
    for phase_description, file in zip(manifest["phases"], files):
        phase = phase_description["phase"]
        raw_bytes = file.read_bytes()
        require(hashlib.sha256(raw_bytes).hexdigest() == phase_description["sha256"], "archived phase changed")
        raw = json.loads(raw_bytes)
        require(not raw.get("pending") and raw.get("chain") == 46630 and raw.get("transactions")
                and len(raw["transactions"]) == len(raw.get("receipts", [])), "phase lacks receipts")
        for planned, saved in zip(raw["transactions"], raw["receipts"]):
            h = planned["hash"]
            require(saved["transactionHash"].lower() == h.lower() and h.lower() not in receipts, "duplicate/mismatched transaction")
            tx, receipt = rpc.request("eth_getTransactionByHash", [h]), rpc.request("eth_getTransactionReceipt", [h])
            number = int(receipt["blockNumber"], 16)
            canonical = rpc.request("eth_getBlockByNumber", [hex(number), False])
            index = int(receipt["transactionIndex"], 16)
            require(int(receipt["status"], 16) == 1 and number <= pin and tx["blockHash"] == receipt["blockHash"] == canonical["hash"]
                    and tx["hash"].lower() == receipt["transactionHash"].lower() == h.lower()
                    and int(tx["blockNumber"], 16) == number and int(tx["transactionIndex"], 16) == index
                    and index < len(canonical["transactions"]) and canonical["transactions"][index].lower() == h.lower()
                    and (receipt["to"] or "").lower() == (tx["to"] or "").lower(), "failed or noncanonical receipt")
            sender = OP if phase == "appendPlain" else CREATOR
            require(tx["from"].lower() == sender and receipt["from"].lower() == sender and int(tx["chainId"], 16) == 46630
                    and int(tx["value"], 16) == 0, "wrong caller/chain/value")
            plan = planned["transaction"]
            require((tx["to"] or "").lower() == (plan.get("to") or "").lower() and tx["input"].lower() == plan["input"].lower()
                    and int(tx["nonce"], 16) == int(plan["nonce"], 16), "transaction differs from archived plan")
            receipts[h.lower()] = receipt
            transactions.append({"phase": phase, "hash": h.lower(), "from": sender, "to": tx["to"], "input": tx["input"],
                                 "nonce": int(tx["nonce"], 16), "blockNumber": number, "blockHash": receipt["blockHash"],
                                 "transactionIndex": int(receipt["transactionIndex"], 16), "gasUsed": int(receipt["gasUsed"], 16)})
    order = [(t["blockNumber"], t["transactionIndex"]) for t in transactions]
    require(order == sorted(set(order)), "phase order changed")
    for sender in [OP, CREATOR]:
        nonces = [t["nonce"] for t in transactions if t["from"] == sender]
        require(nonces == list(range(min(nonces), max(nonces) + 1)), "sender nonce gap")
    factory, registry, stock = book["factory"], book["treasuryDeployer"], book["stocks"]["TSLA"]["token"]
    core_hashes = book["verification"]["upgradeProof"]["codeHashes"]["core"]
    for key, expected in core_hashes.items():
        require(digest(rpc.code(book[key], pin)) == expected, "fee core changed: " + key)
    require(address(rpc.call(factory, "owner()", pin)[0]) == OP and address(rpc.call(registry, "factory()", pin)[0]) == factory.lower(), "wrong owner/factory binding")
    append = [t for t in transactions if t["phase"] == "appendPlain"]
    require(len(append) == 3 and append[0]["to"] is None and append[1]["to"] is None, "append must be exactly two owner CREATEs and registration")
    chunk_creation = chunk_artifact["bytecode"]["object"]
    chunks = []
    half = len(creation) // 2
    for tx, raw_code in zip(append[:2], [creation[:half], creation[half:]]):
        require(tx["input"] == chunk_creation + cast("abi-encode", "constructor(bytes)", "0x" + raw_code.hex())[2:], "wrong chunk CREATE input")
        deployed = receipts[tx["hash"]]["contractAddress"]
        expected_address = cast("compute-address", OP, "--nonce", str(tx["nonce"])).split()[-1].lower()
        require(deployed and deployed.lower() == expected_address and rpc.code(deployed, pin) == raw_code, "wrong raw chunk runtime or CREATE address")
        chunks.append(deployed.lower())
    exact_calls(append[2:], [(registry, cast("calldata", "registerKind(address,address)", *chunks))])
    registered = event(receipts[append[2]["hash"]], registry, "KindRegistered(uint8,address,address)")
    require(int(registered["topics"][1], 16) == 4 and [address(w) for w in words(registered["data"])] == chunks, "wrong kind registration event")
    require(rpc.call(registry, "kindCount()", pin) == [5], "unexpected registry inventory")
    kinds = []
    for kind, expected in enumerate(OLD_HASHES + [NEW_HASH]):
        version, schema, code_hash, capabilities = rpc.call(registry, "kindManifest(uint8)", pin, kind)
        a, b = [address(w) for w in rpc.call(registry, "kinds(uint8)", pin, kind)]
        require(digest(rpc.code(a, pin) + rpc.code(b, pin)) == expected == "0x" + f"{code_hash:064x}", "registered creation mismatch")
        require([version, schema, capabilities] == ([1, 1, 3] if kind in [2, 3] else [0, 0, 0]), "changed kind family")
        if kind == 4:
            require([a, b] == chunks, "wrong registered new chunks")
        kinds.append({"kind": kind, "creationCodeHash": expected, "chunkA": a, "chunkB": b})
    before = min(t["blockNumber"] for t in transactions) - 1
    before_hash = rpc.request("eth_getBlockByNumber", [hex(before), False])["hash"]
    require(rpc.call(registry, "kindCount()", before) == [4], "wrong pre-append inventory")
    existing = []
    for id_ in range(3):
        was, now = rpc.call(factory, "strategies(uint256)", before, id_), rpc.call(factory, "strategies(uint256)", pin, id_)
        require(was == now and rpc.code(address(was[1]), before) == rpc.code(address(now[1]), pin), "existing strategy mapping/runtime changed")
        existing.append({"id": id_, "treasury": address(now[1]), "runtimeCodeHash": digest(rpc.code(address(now[1]), pin))})
    require(rpc.call(factory, "strategyCount()", before) == [3] and rpc.call(factory, "strategyCount()", pin) == [4], "unexpected strategy inventory")
    token, treasury, hook, asset, creator = [address(w) for w in rpc.call(factory, "strategies(uint256)", pin, 3)]
    require([hook, asset, creator] == [book["hook"].lower(), stock.lower(), CREATOR], "wrong new strategy scope")
    request = "(" + ",".join(["TSLA Steady", "HFSTEADY", stock, CREATOR, "300", "1000", "1", "2", "1", "0", "2000", "0", "202609300310", "25000000", "26500000000"]) + ")"
    predicted_token, predicted_treasury, terms = words(rpc.request("eth_call", [{"to": factory, "data": cast("calldata", "predict(" + REQUEST + ")", request)}, hex(pin)]))
    require([address(predicted_token), address(predicted_treasury)] == [token, treasury], "wrong fixed launch identity")
    launches = [t for t in transactions if t["phase"] == "launchPlain"]
    expected = [(registry, cast("calldata", "setStrategyKind(string,uint96,uint8)", "HFSTEADY", "202609300310", "4")),
                (book["curveDeployer"], cast("calldata", "setCurveConfig(string,uint96,uint16,uint8)", "HFSTEADY", "202609300310", "4400", "0"))]
    if len(launches) == 4:
        expected.append((book["usdg"], cast("calldata", "approve(address,uint256)", factory, "25000000")))
    expected.append((factory, cast("calldata", "launch(" + REQUEST + ",bytes32)", request, "0x" + f"{terms:064x}")))
    exact_calls(launches, expected)
    launch_receipt = receipts[launches[-1]["hash"]]
    require(int(event(launch_receipt, factory, "Launched(uint256,string,address,address,address,address,address)")["topics"][1], 16) == 3, "wrong new ID")
    require(transferred(launch_receipt, book["usdg"], CREATOR, OP) == 25_000_000, "creation fee missing")
    bound = event(launch_receipt, registry, "TreasuryCodeBound(address,uint8,bytes32,bytes32,bytes32)")
    require(address(int(bound["topics"][1], 16)) == treasury and int(bound["topics"][2], 16) == 4, "wrong bound kind")
    init_hash, runtime_hash, config_hash = words(bound["data"])
    params = rpc.call(treasury, "params()", pin)
    require(len(params) == 14 and params[:6] == [1, 2, 1, 0, 2000, 50] and params[6] == 100 and params[13] == 0, "wrong frozen rule")
    bindings = {"factory": factory, "token": token, "hook": hook, "stock": stock, "usdg": book["usdg"],
                "pool": book["stocks"]["TSLA"]["pool"], "oracle": book["stocks"]["TSLA"]["oracle"], "poolManager": book["poolManager"]}
    for getter, expected_address in bindings.items():
        require(rpc.call(treasury, getter + "()", pin) == [int(expected_address, 16)], "wrong treasury binding: " + getter)
    pool_fee = rpc.call(treasury, "poolFeeBps()", pin)[0]
    require(params[0] > 0 and (params[1] == 0 or params[1] > params[0]) and 0 < params[2] < 10000 and params[3] < 10000, "invalid creator parameters")
    constructor = encode_values([int(book["usdg"], 16), int(stock, 16), int(book["stocks"]["TSLA"]["pool"], 16),
                                 int(book["stocks"]["TSLA"]["oracle"], 16), int(token, 16), int(book["poolManager"], 16), int(factory, 16)] + params)
    require(digest(creation + constructor) == "0x" + f"{init_hash:064x}" and config_hash == 0 and digest(rpc.code(treasury, pin)) == "0x" + f"{runtime_hash:064x}", "exact creation/constructor/runtime binding mismatch")
    actual, template = bytearray(rpc.code(treasury, pin)), bytearray.fromhex(artifact["deployedBytecode"]["object"].removeprefix("0x"))
    require(len(actual) == len(template), "runtime length differs")
    for spans in artifact["deployedBytecode"]["immutableReferences"].values():
        for span in spans:
            start, length = span["start"], span["length"]
            actual[start:start + length] = template[start:start + length] = bytes(length)
    require(actual == template, "runtime differs outside immutables")
    curve = address(rpc.call(factory, "curves(uint256)", pin, 3)[0])
    predicted_curve = words(rpc.request("eth_call", [{"to": factory, "data": cast("calldata", "predictCurve(" + REQUEST + ")", request)}, hex(pin)]))
    require(predicted_curve == [int(curve, 16)], "wrong fixed curve prediction")
    for getter, expected_address in {"factory": factory, "token": token, "treasury": treasury, "stock": stock}.items():
        require(rpc.call(curve, getter + "()", pin) == [int(expected_address, 16)], "wrong curve binding: " + getter)
    grads = [t for t in transactions if t["phase"] == "graduatePlain"]
    expected = []
    if len(grads) == 2:
        expected.append((stock, cast("calldata", "approve(address,uint256)", curve, str(24 * 10**18))))
    phase = manifest["phases"][2]
    deadline = int(rpc.request("eth_getBlockByNumber", [hex(phase["dryRunPin"]), False])["timestamp"], 16) + 300
    expected.append((curve, cast("calldata", "buy(uint256,uint256,address,uint256)", str(24 * 10**18), str(440_000_000 * 10**18), CREATOR, str(deadline))))
    exact_calls(grads, expected)
    receipt = receipts[grads[-1]["hash"]]
    bought = event(receipt, curve, "Bought(address,address,uint256,uint256,uint256)")
    spent, token_output, burned = words(bought["data"])
    require([address(int(t, 16)) for t in bought["topics"][1:]] == [CREATOR, CREATOR] and 0 < spent <= 24 * 10**18
            and token_output == 440_000_000 * 10**18 and burned == 0, "wrong graduation purchase")
    released, released_token = words(event(receipt, curve, "Released(uint256,uint256)")["data"])
    split = event(receipt, factory, "GraduationCapitalSplit(uint256,uint256,uint256,bool)")
    lp, capital, booked = words(split["data"])
    graduated = event(receipt, factory, "Graduated(uint256,uint160,uint128,uint256,uint256,uint256)")
    _, liquidity, lp_stock, lp_token, graduation_burn = words(graduated["data"])
    require(int(split["topics"][1], 16) == int(graduated["topics"][1], 16) == 3 and lp == lp_stock
            and lp + capital == released and booked == 1 and lp_token + graduation_burn == released_token, "wrong graduation capital")
    vault = address(rpc.call(treasury, "liquidityVault()", pin)[0])
    seeded_liquidity, amount0, amount1 = words(event(receipt, vault, "Seeded(uint128,uint256,uint256)")["data"])
    seeded_stock, seeded_token = (amount1, amount0) if int(token, 16) < int(stock, 16) else (amount0, amount1)
    require(liquidity > 0 and seeded_liquidity == liquidity and seeded_stock == lp_stock and seeded_token == lp_token, "wrong actual vault seed")
    require(transferred(receipt, stock, curve, factory) == released and transferred(receipt, stock, factory, treasury) == capital
            and transferred(receipt, stock, factory, vault) - transferred(receipt, stock, vault, factory) == lp_stock
            and rpc.call(curve, "status()", pin) == [2], "graduation principal transfer/status")
    require(transferred(receipt, token, curve, CREATOR) == token_output and transferred(receipt, token, curve, factory) == released_token
            and transferred(receipt, token, factory, vault) - transferred(receipt, token, vault, factory) == lp_token
            and transferred(receipt, token, factory, "0x" + "0" * 40) == graduation_burn, "graduation token transfer/burn conservation")
    stock_balance = rpc.call(stock, "balanceOf(address)", pin, treasury)[0]
    require(rpc.call(treasury, "bookedStock()", pin) == [capital] and rpc.call(treasury, "buybackStock()", pin) == [0]
            and stock_balance == capital and rpc.call(treasury, "unbookedStock()", pin) == [0], "new strategy capital ledger mismatch")
    require(rpc.request("eth_getBlockByNumber", [hex(pin), False])["hash"] == block_hash
            and rpc.request("eth_getBlockByNumber", [hex(before), False])["hash"] == before_hash, "pin reorg")
    return {"schema": "v2-all-in-trigger-floor-readback-v1", "chainId": 46630, "broadcast": True, "verified": True,
            "sourceCommit": source, "contractSourceCommit": source,
            "toolFilesSha256": {p: hashlib.sha256((root / p).read_bytes()).hexdigest() for p in
                                ["script/TestnetV2AllInFloor.s.sol", "tools/audit_v2_all_in_floor.py", "tools/audit_v2_keeper_reward.py"]},
            "blockNumber": pin, "blockHash": block_hash, "factory": factory, "registry": registry,
            "beforeBlockNumber": before, "beforeBlockHash": before_hash,
            "kind": 4, "family": "lots", "creationCodeHash": NEW_HASH, "kinds": kinds, "coreRuntimeHashes": core_hashes,
            "existingStrategies": existing, "transactions": transactions, "compiler": metadata["compiler"], "compilerSettings": settings,
            "sourceInputs": {p: v["keccak256"] for p, v in metadata["sources"].items()},
            "strategy": {"id": 3, "symbol": "HFSTEADY", "token": token, "treasury": treasury, "curve": curve,
                         "params": params, "initCodeHash": "0x" + f"{init_hash:064x}", "runtimeCodeHash": "0x" + f"{runtime_hash:064x}",
                         "capitalStock": str(capital), "liquidityVault": vault, "liquidity": str(liquidity), "lpStock": str(lp_stock), "lpToken": str(lp_token)},
            "scope": "Optional ordinary V2 kind4 append only: creator-selected TP/dip with no TP net-profit guard. The immutable old registry retains its stop friction floor; full TP/dip/stop freedom requires a fresh core and separate deployment proof. Old strategy 0..2 mapping/runtime and core addresses/runtime unchanged; their mutable balances are not claimed unchanged. Controlled launch/graduation does not execute a TP swap or prove profitability. No automated keeper live activation."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--book", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--block", type=int)
    parser.add_argument("--rpc", default="https://rpc.testnet.chain.robinhood.com")
    args = parser.parse_args()
    result = audit(args)
    pathlib.Path(args.output).write_text(json.dumps(result, indent=2) + "\n")
    print("Verified all-in ordinary strategy append, constructor, fee and graduation; original fee core unchanged.")

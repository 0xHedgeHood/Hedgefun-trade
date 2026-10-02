#!/usr/bin/env python3
"""Read-only canonical audit of TestnetV2KeeperReward phases. Never signs transactions.

Consumes archived *public* Forge run JSON files through a seven-phase manifest.
The original fee address book remains immutable; output is a separate extension proof.
All amounts are raw integers. Requires built Foundry artifacts and `cast` on PATH.
"""
import argparse
import concurrent.futures
import functools
import hashlib
import json
import pathlib
import subprocess
import time
import urllib.request

OP = "0x75cee941b0ef3a83fea0397bbf903c12c1d7e96d"
CREATOR = "0xd4f69d180a9bc36f27d307e90e365d1e012816d5"
SECOND = "0xda1aee7018a3925aa06deeb8631fca09e1067614"
OLD_HASHES = ["0xaec5bc5cdaeef801f738c564cc0aca2f1d4405a38e218d4cad9ad66ccc810b93",
              "0x20d84b868ef45cf6a4fa3f11d90432fa8b6e8d375edcfd668856770b12199720",
              "0x67d0657c52fdd7fbe540435539f44a46caa159e82b2c74a91cb7c8b1a3db151f"]
PHASES = ["appendEngine", "launchSell", "graduateSell", "executeSell",
          "launchBuy", "graduateBuy", "executeBuy"]
REQUEST = "(string,string,address,address,uint16,uint16,uint32,uint32,uint16,uint16,uint16,uint16,uint96,uint256,uint256)"
CONFIG = "(uint32,uint32,bytes32,bytes32[3])"


def require(ok, label):
    if not ok:
        raise ValueError(label)


@functools.lru_cache(maxsize=None)
def cast(*args):
    return subprocess.check_output(["cast", *args], text=True).strip()


def digest(data):
    return cast("keccak", "0x" + data.hex()).lower()


def words(data):
    require(data.startswith("0x") and len(data[2:]) % 64 == 0, "bad ABI words")
    return [int(data[i:i + 64], 16) for i in range(2, len(data), 64)]


def address(value):
    require(value < 2**160, "noncanonical ABI address")
    return "0x" + f"{value:040x}"


def encode_values(values):
    return b"".join(v.to_bytes(32, "big") for v in values)


def event(receipt, emitter, signature):
    topic = cast("keccak", signature).lower()
    matches = [l for l in receipt["logs"] if l["address"].lower() == emitter.lower()
               and l["topics"] and l["topics"][0].lower() == topic]
    require(len(matches) == 1, "missing/duplicate " + signature)
    return matches[0]


def exact_calls(actual, expected):
    require(len(actual) == len(expected), "unexpected phase transaction count")
    for tx, (to, data) in zip(actual, expected):
        require((tx["to"] or "").lower() == to.lower() and tx["input"] == data, "phase calldata/destination differs from reviewed operation")


class Rpc:
    def __init__(self, url):
        self.url = url

    def request(self, method, params, allow_revert=False):
        payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
        for attempt in range(5):
            try:
                req = urllib.request.Request(self.url, payload, {"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=25) as response:
                    data = json.load(response)
                if "error" in data:
                    if allow_revert and data["error"].get("code") == 3:
                        return {"error": data["error"]}
                    raise ValueError(data["error"])
                require(data.get("result") is not None, "missing RPC result")
                return data["result"]
            except Exception:
                if attempt == 4:
                    raise
                time.sleep(1 + attempt)

    def call(self, target, sig, block, *args, sender=None):
        encoded = cast("sig", sig) + "".join(f"{int(a, 16) if isinstance(a, str) else a:064x}" for a in args)
        tx = {"to": target, "data": encoded}
        if sender:
            tx["from"] = sender
        return words(self.request("eth_call", [tx, hex(block)]))

    def code(self, target, block):
        return bytes.fromhex(self.request("eth_getCode", [target, hex(block)])[2:])


def snapshot(rpc, book, treasury, keeper, block):
    getters = ["bookedStock", "buybackStock", "avgCost", "strategyNonce", "turnoverEpoch",
               "turnoverInEpoch", "lastStrategyAt", "policyState", "unbookedStock"]
    tasks = {name: (treasury, name + "()", ()) for name in getters}
    stock = book["stocks"]["TSLA"]["token"]
    for name, token, holder in [("stock", stock, treasury), ("usdg", book["usdg"], treasury),
                                ("keeperStock", stock, keeper), ("keeperUsdg", book["usdg"], keeper)]:
        tasks[name] = (token, "balanceOf(address)", (holder,))
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        results = {k: pool.submit(rpc.call, to, sig, block, *args) for k, (to, sig, args) in tasks.items()}
        return {k: v.result()[0] for k, v in results.items()}


def audit(args):
    root = pathlib.Path(__file__).resolve().parents[1]
    rpc = Rpc(args.rpc)
    require(int(rpc.request("eth_chainId", []), 16) == 46630, "wrong chain")
    book_bytes = pathlib.Path(args.book).read_bytes()
    book = json.loads(book_bytes)
    require(book["commit"] == "698e577048fde8681d9263f32a653d5176f888e5", "wrong fee core source")
    require(book["broadcast"] is True, "not a live fee book")
    require(hashlib.sha256(book_bytes).hexdigest() == "ada428eb04ac0f982fd6a002e8745b4100c6db99ce620adcf254e42ac1cbc82c", "fee book changed")
    manifest_path = pathlib.Path(args.manifest)
    manifest = json.loads(manifest_path.read_bytes())
    require([p["phase"] for p in manifest["phases"]] == PHASES, "incomplete phase manifest")
    require(all(p["file"] == f"phase-{i:02d}-{p['phase']}.json" for i, p in enumerate(manifest["phases"])), "unexpected archived proof path")
    protected = {pathlib.Path(args.book).resolve(), manifest_path.resolve()}
    protected.update((manifest_path.parent / p["file"]).resolve() for p in manifest["phases"])
    require(pathlib.Path(args.output).resolve() not in protected and pathlib.Path(args.output).name not in
            ["testnet-v2-fees.json", "testnet-v2-fees.dryrun.json", "testnet-v2-whitelist.json"], "output would overwrite an input/original book")
    source = manifest["sourceCommit"]
    require(len(source) == 40 and all(c in "0123456789abcdef" for c in source), "bad source commit")
    subprocess.check_output(["git", "cat-file", "-e", source + "^{commit}"], cwd=root)
    require(not subprocess.check_output(["git", "diff", source, "--", "src", "foundry.toml", "lib"], cwd=root), "contract source differs from release commit")
    artifact = json.loads((root / "out/HedgeFunV2EngineTreasury.sol/HedgeFunV2EngineTreasury.json").read_bytes())
    metadata = artifact["metadata"]
    require(metadata["compiler"]["version"] == "0.8.26+commit.8a97fa7a", "wrong compiler")
    settings = metadata["settings"]
    require(settings["optimizer"] == {"enabled": True, "runs": 1} and settings["evmVersion"] == "cancun"
            and settings["metadata"]["bytecodeHash"] == "none" and not settings.get("viaIR", False), "wrong build settings")
    for path, description in metadata["sources"].items():
        require(digest((root / path).read_bytes()) == description["keccak256"], "stale artifact source: " + path)
    creation = bytes.fromhex(artifact["bytecode"]["object"].removeprefix("0x"))
    creation_hash = digest(creation)
    require(creation_hash != OLD_HASHES[2], "unpatched Engine build")
    pin = args.block or int(rpc.request("eth_blockNumber", []), 16) - 10
    pinned = rpc.request("eth_getBlockByNumber", [hex(pin), False])
    txs = []
    receipts = {}
    for phase in manifest["phases"]:
        raw = (manifest_path.parent / phase["file"]).read_bytes()
        require(hashlib.sha256(raw).hexdigest() == phase["sha256"], "archived phase changed")
        run = json.loads(raw)
        require(not run.get("pending") and run["chain"] == 46630 and len(run["transactions"]) > 0, "incomplete Forge run")
        sender = SECOND if phase["phase"] == "executeSell" else OP if phase["phase"] in ["appendEngine", "executeBuy"] else CREATOR
        for planned in run["transactions"]:
            h = planned["hash"]
            require(h not in receipts, "duplicate transaction")
            tx = rpc.request("eth_getTransactionByHash", [h])
            receipt = rpc.request("eth_getTransactionReceipt", [h])
            block = int(receipt["blockNumber"], 16)
            require(int(receipt["status"], 16) == 1 and block <= pin, "failed or unfinalized phase")
            canonical = rpc.request("eth_getBlockByNumber", [hex(block), False])
            require(canonical["hash"] == receipt["blockHash"] == tx["blockHash"] and h in canonical["transactions"]
                    and receipt["transactionHash"] == tx["hash"] == h, "noncanonical receipt")
            require(tx["from"].lower() == sender and receipt["from"].lower() == sender, "wrong phase sender")
            require(int(tx["chainId"], 16) == 46630 and int(tx["value"], 16) == 0, "wrong chain/value")
            plan = planned["transaction"]
            require((tx["to"] or "").lower() == (plan.get("to") or "").lower() and tx["input"].lower() == plan["input"].lower(), "broadcast differs from archived plan")
            require(int(tx["nonce"], 16) == int(plan["nonce"], 16), "nonce differs from planned phase")
            receipts[h] = receipt
            txs.append({"phase": phase["phase"], "hash": h, "from": sender, "to": tx["to"],
                        "input": tx["input"], "blockNumber": block, "blockHash": receipt["blockHash"],
                        "nonce": int(tx["nonce"], 16), "transactionIndex": int(receipt["transactionIndex"], 16),
                        "gasUsed": int(receipt["gasUsed"], 16), "gasPrice": int(receipt["effectiveGasPrice"], 16)})
    order = [(t["blockNumber"], t["transactionIndex"]) for t in txs]
    require(order == sorted(set(order)), "phases not in strict canonical order")
    funding = []
    for h in manifest.get("fundingHashes", []):
        tx = rpc.request("eth_getTransactionByHash", [h])
        receipt = rpc.request("eth_getTransactionReceipt", [h])
        block = int(receipt["blockNumber"], 16)
        require(int(receipt["status"], 16) == 1 and block <= pin and receipt["blockHash"] == tx["blockHash"]
                == rpc.request("eth_getBlockByNumber", [hex(block), False])["hash"], "noncanonical funding")
        require(tx["from"].lower() == OP and tx["to"].lower() == CREATOR and tx["input"] == "0x"
                and int(tx["value"], 16) == 2_000_000_000_000_000 and int(tx["chainId"], 16) == 46630, "unexpected funding transfer")
        funding.append({"hash": h, "from": OP, "to": CREATOR, "valueWei": int(tx["value"], 16),
                        "blockNumber": block, "blockHash": receipt["blockHash"], "nonce": int(tx["nonce"], 16)})
    for sender in [OP, CREATOR, SECOND]:
        nonces = sorted(t["nonce"] for t in txs + funding if t["from"] == sender)
        require(nonces == list(range(min(nonces), max(nonces) + 1)), "unaccounted sender nonce gap")
    append = [t for t in txs if t["phase"] == "appendEngine"]
    require(len(append) == 3 and append[0]["to"] is None and append[1]["to"] is None, "expected two EOA CREATE chunks plus registration")
    chunk_artifact = json.loads((root / "out/V2TreasuryDeployer.sol/V2InitCodeChunk.json").read_bytes())
    chunk_creation = chunk_artifact["bytecode"]["object"]
    half = len(creation) // 2
    for tx, data in zip(append[:2], [creation[:half], creation[half:]]):
        require(tx["input"] == chunk_creation + cast("abi-encode", "constructor(bytes)", "0x" + data.hex())[2:], "owner CREATE initcode is not the reviewed source chunk")
    core_hashes = book["verification"]["upgradeProof"]["codeHashes"]["core"]
    for name, expected in core_hashes.items():
        require(digest(rpc.code(book[name], pin)) == expected, "changed fee core: " + name)
    factory, registry = book["factory"], book["treasuryDeployer"]
    require(address(rpc.call(factory, "owner()", pin)[0]) == OP and address(rpc.call(factory, "protocol()", pin)[0]) == OP, "core role changed")
    require(rpc.call(registry, "kindCount()", pin) == [4] and rpc.call(factory, "strategyCount()", pin) == [3], "unexpected kind/strategy count")
    old_pin = append[0]["blockNumber"] - 1
    old_block_hash = rpc.request("eth_getBlockByNumber", [hex(old_pin), False])["hash"]
    require(rpc.call(factory, "strategyCount()", old_pin) == [1] and rpc.call(registry, "kindCount()", old_pin) == [3], "unexpected pre-append state")
    old_strategy = rpc.call(factory, "strategies(uint256)", old_pin, 0)
    require(rpc.call(factory, "strategies(uint256)", pin, 0) == old_strategy, "existing strategy mapping changed")
    old_treasury = address(old_strategy[1])
    old_runtime_hash = digest(rpc.code(old_treasury, old_pin))
    require(digest(rpc.code(old_treasury, pin)) == old_runtime_hash, "existing strategy runtime changed")
    kinds = []
    for kind in range(4):
        version, schema, code_hash, capabilities = rpc.call(registry, "kindManifest(uint8)", pin, kind)
        a, b = map(address, rpc.call(registry, "kinds(uint8)", pin, kind))
        code = rpc.code(a, pin) + rpc.code(b, pin)
        expected = OLD_HASHES[kind] if kind < 3 else creation_hash
        require(digest(code) == expected == "0x" + f"{code_hash:064x}", "wrong raw creation chunks")
        require([version, schema, capabilities] == ([0, 0, 0] if kind < 2 else [1, 1, 3]), "kind manifest changed")
        if kind == 3:
            require(code == creation, "new chunks differ from compiled creation bytes")
            require([receipts[t["hash"]]["contractAddress"].lower() for t in append[:2]] == [a, b], "registered chunks are not the canonical owner CREATE deployments")
            require(append[2]["to"].lower() == registry.lower() and append[2]["input"] == cast("calldata", "registerEngineKind(address,address,uint32,uint32,uint256)", a, b, "1", "1", "3"), "wrong owner registration")
        kinds.append({"kind": kind, "engineVersion": version, "configSchema": schema,
                      "capabilities": capabilities, "creationCodeHash": expected, "chunkA": a, "chunkB": b})
    reward_topic = cast("keccak", "KeeperRewardPaid(uint64,address,address,uint256)").lower()
    execution_topic = cast("keccak", "StrategyExecuted(uint64,uint8,uint256,uint256,uint256,uint256,uint256,bytes32)").lower()
    transfer_topic = cast("keccak", "Transfer(address,address,uint256)").lower()
    def transferred(receipt, asset, source_address, destination):
        return sum(words(l["data"])[0] for l in receipt["logs"] if l["address"].lower() == asset.lower()
                   and l["topics"][0].lower() == transfer_topic
                   and [address(int(t, 16)) for t in l["topics"][1:]] == [source_address.lower(), destination.lower()])
    strategies = []
    stock = book["stocks"]["TSLA"]["token"].lower()
    for strategy_id, phase, keeper, action in [(1, "executeSell", SECOND, 2), (2, "executeBuy", OP, 1)]:
        buy = phase == "executeBuy"
        token, treasury, hook, actual_stock, creator = map(address, rpc.call(factory, "strategies(uint256)", pin, strategy_id))
        require([hook, actual_stock, creator] == [book["hook"].lower(), stock, CREATOR], "strategy binding")
        runtime = bytearray(rpc.code(treasury, pin))
        expected_runtime = bytearray.fromhex(artifact["deployedBytecode"]["object"].removeprefix("0x"))
        require(len(runtime) == len(expected_runtime), "wrong Engine runtime length")
        for spans in artifact["deployedBytecode"]["immutableReferences"].values():
            for span in spans:
                start, size = span["start"], span["length"]
                runtime[start:start + size] = bytes(size)
                expected_runtime[start:start + size] = bytes(size)
        require(runtime == expected_runtime, "Engine runtime differs outside immutable slots")
        bindings = {"factory": factory, "token": token, "hook": book["hook"], "stock": stock,
                    "usdg": book["usdg"], "pool": book["stocks"]["TSLA"]["pool"],
                    "oracle": book["stocks"]["TSLA"]["oracle"], "poolManager": book["poolManager"],
                    "tradingCalendar": book["calendar"], "policyImplementation": book["rebalancePolicy"]}
        for name, expected in bindings.items():
            require(address(rpc.call(treasury, name + "()", pin)[0]) == expected.lower(), "Engine binding: " + name)
        require(rpc.call(treasury, "strategyId()", pin)[0] == int(book["rebalancePolicyKey"], 16), "wrong policy key")
        require(rpc.call(treasury, "engineVersion()", pin) == [1] and rpc.call(treasury, "policyCapabilities()", pin) == [3], "wrong Engine capabilities/version")
        require(rpc.call(treasury, "policyRuntimeCodeHash()", pin)[0] == int(core_hashes["rebalancePolicy"], 16), "wrong frozen policy runtime")
        params = rpc.call(treasury, "params()", pin)
        config = rpc.call(treasury, "engineConfig()", pin)
        require(params[5] == 50 and rpc.call(treasury, "payoutBps()", pin) == [5000], "wrong reward/payout")
        require(config == [1, 1, int(book["rebalancePolicyKey"], 16), 5000 | 500 << 16 | 600 << 32 | 5000 << 64, 100_000_000, 500_000_000], "wrong Engine config")
        require(params[:6] == [300, 600, 500, 0, 2000, 50], "wrong launch strategy parameters")
        pool_fee = rpc.call(treasury, "poolFeeBps()", pin)[0]
        require(500 >= 2 * (params[6] + pool_fee + params[5]), "new bounty floor not met")
        symbol, fixed_nonce = ("HFKBUY", 202609300109) if buy else ("HFKSELL", 202609300108)
        name = "Keeper reward buy proof" if buy else "Keeper reward sell proof"
        request = "(" + ",".join([name, symbol, stock, CREATOR, "300", "1000", "300", "600", "500", "0", "2000", "0", str(fixed_nonce), "25000000", "26500000000"]) + ")"
        predict_data = cast("calldata", "predict(" + REQUEST + ")", request)
        predicted_token, predicted_treasury, terms = words(rpc.request("eth_call", [{"to": factory, "data": predict_data}, hex(pin)]))
        require([address(predicted_token), address(predicted_treasury)] == [token, treasury], "launch does not bind the fixed creator/symbol/nonce")
        config_arg = "(1,1," + book["rebalancePolicyKey"] + ",[" + ",".join("0x" + f"{v:064x}" for v in config[3:]) + "])"
        launch_phase = "launchBuy" if buy else "launchSell"
        launch_txs = [t for t in txs if t["phase"] == launch_phase]
        expected_launch = [(registry, cast("calldata", "setEngineConfig(string,uint96,uint8," + CONFIG + ")", symbol, str(fixed_nonce), "3", config_arg)),
                           (book["curveDeployer"], cast("calldata", "setCurveConfig(string,uint96,uint16,uint8)", symbol, str(fixed_nonce), "4400", "180"))]
        if len(launch_txs) == 4 + int(buy):
            expected_launch.append((book["usdg"], cast("calldata", "approve(address,uint256)", factory, "25000000")))
        launch_data = cast("calldata", "launch(" + REQUEST + ",bytes32)", request, "0x" + f"{terms:064x}")
        expected_launch.append((factory, launch_data))
        if buy:
            expected_launch.append((book["usdg"], cast("calldata", "transfer(address,uint256)", treasury, "10000000000")))
        exact_calls(launch_txs, expected_launch)
        launch_tx = next(t for t in launch_txs if t["to"].lower() == factory.lower())
        launch_receipt = receipts[launch_tx["hash"]]
        launched = event(launch_receipt, factory, "Launched(uint256,string,address,address,address,address,address)")
        require(int(launched["topics"][1], 16) == strategy_id, "wrong launched strategy ID")
        require(transferred(launch_receipt, book["usdg"], CREATOR, OP) == 25_000_000, "creation fee not paid")
        code_bound = event(launch_receipt, registry, "TreasuryCodeBound(address,uint8,bytes32,bytes32,bytes32)")
        init_hash, runtime_hash, frozen_config_hash = words(code_bound["data"])
        require(address(int(code_bound["topics"][1], 16)) == treasury and int(code_bound["topics"][2], 16) == 3, "wrong TreasuryCodeBound")
        constructor = encode_values([int(book["usdg"], 16), int(stock, 16), int(book["stocks"]["TSLA"]["pool"], 16),
                                     int(book["stocks"]["TSLA"]["oracle"], 16), int(token, 16), int(book["poolManager"], 16), int(factory, 16)] + params + config)
        require(digest(creation + constructor) == "0x" + f"{init_hash:064x}" and digest(rpc.code(treasury, pin)) == "0x" + f"{runtime_hash:064x}", "TreasuryCodeBound source/constructor/runtime mismatch")
        config_values = [46630, int(treasury, 16), int(factory, 16), int(stock, 16), int(book["usdg"], 16)] + config
        config_values += [int(book["rebalancePolicy"], 16), int(core_hashes["rebalancePolicy"], 16), 3, 150000, 160]
        require(digest(encode_values(config_values)) == "0x" + f"{frozen_config_hash:064x}"
                and rpc.call(treasury, "configHash()", pin) == [frozen_config_hash], "wrong frozen config hash")
        if buy:
            seed_tx = launch_txs[-1]
            require(transferred(receipts[seed_tx["hash"]], book["usdg"], CREATOR, treasury) == 10_000_000_000, "buy seed transfer not actual")
        curve = address(rpc.call(factory, "curves(uint256)", pin, strategy_id)[0])
        predicted_curve = words(rpc.request("eth_call", [{"to": factory, "data": cast("calldata", "predictCurve(" + REQUEST + ")", request)}, hex(pin)]))[0]
        require(address(predicted_curve) == curve and rpc.call(curve, "status()", pin) == [2], "curve prediction/graduation")
        graduate_phase = "graduateBuy" if buy else "graduateSell"
        graduation_txs = [t for t in txs if t["phase"] == graduate_phase]
        expected_graduation = []
        if len(graduation_txs) == 2:
            expected_graduation.append((stock, cast("calldata", "approve(address,uint256)", curve, str(24 * 10**18))))
        dry_pin = next(p["dryRunPin"] for p in manifest["phases"] if p["phase"] == graduate_phase)
        deadline = int(rpc.request("eth_getBlockByNumber", [hex(dry_pin), False])["timestamp"], 16) + 300
        expected_graduation.append((curve, cast("calldata", "buy(uint256,uint256,address,uint256)", str(24 * 10**18), str(440_000_000 * 10**18), CREATOR, str(deadline))))
        exact_calls(graduation_txs, expected_graduation)
        graduate_tx = graduation_txs[-1]
        graduate_receipt = receipts[graduate_tx["hash"]]
        graduate_block = graduate_tx["blockNumber"]
        bought = event(graduate_receipt, curve, "Bought(address,address,uint256,uint256,uint256)")
        require([address(int(t, 16)) for t in bought["topics"][1:]] == [CREATOR, CREATOR], "wrong graduate purchase buyer")
        spent, token_out, burned_during_purchase = words(bought["data"])
        require(0 < spent <= 24 * 10**18 and token_out == 440_000_000 * 10**18 and burned_during_purchase == 0, "graduation purchase bounds")
        released_stock, released_token = words(event(graduate_receipt, curve, "Released(uint256,uint256)")["data"])
        graduated = event(graduate_receipt, factory, "Graduated(uint256,uint160,uint128,uint256,uint256,uint256)")
        split = event(graduate_receipt, factory, "GraduationCapitalSplit(uint256,uint256,uint256,bool)")
        require(int(graduated["topics"][1], 16) == int(split["topics"][1], 16) == strategy_id, "wrong graduation IDs")
        _, liquidity, lp_stock, lp_token, graduation_burn = words(graduated["data"])
        split_lp, capital, booked = words(split["data"])
        require(split_lp == lp_stock and booked == 1 and lp_stock + capital == released_stock and lp_token + graduation_burn == released_token, "graduation principal split")
        vault = address(rpc.call(treasury, "liquidityVault()", pin)[0])
        seeded_liquidity, amount0, amount1 = words(event(graduate_receipt, vault, "Seeded(uint128,uint256,uint256)")["data"])
        seed_stock, seed_token = (amount1, amount0) if int(token, 16) < int(stock, 16) else (amount0, amount1)
        require(seeded_liquidity == liquidity and seed_stock == lp_stock and seed_token == lp_token, "actual vault seed")
        require(transferred(graduate_receipt, stock, curve, factory) == released_stock
                and transferred(graduate_receipt, stock, factory, treasury) == capital
                and transferred(graduate_receipt, stock, factory, vault) - transferred(graduate_receipt, stock, vault, factory) == lp_stock, "principal transfer conservation")
        graduate_before, graduate_after = [snapshot(rpc, book, treasury, keeper, b) for b in [graduate_block - 1, graduate_block]]
        require(graduate_after["stock"] - graduate_before["stock"] == graduate_after["bookedStock"] - graduate_before["bookedStock"] == capital
                and graduate_after["unbookedStock"] == 0 and graduate_after["usdg"] == graduate_before["usdg"] == (10_000_000_000 if buy else 0), "actual graduation capital/seed ledger")
        phase_txs = [t for t in txs if t["phase"] == phase]
        require(len(phase_txs) == 1, "execute must be one transaction")
        tx = phase_txs[0]
        require(tx["to"].lower() == treasury and tx["input"] == cast("sig", "execute()"), "wrong execute call")
        receipt = receipts[tx["hash"]]
        logs = [l for l in receipt["logs"] if l["address"].lower() == treasury]
        events = [l for l in logs if l["topics"][0].lower() == execution_topic]
        rewards = [l for l in logs if l["topics"][0].lower() == reward_topic]
        require(len(events) == len(rewards) == 1, "missing/duplicate execution/reward")
        executed_event, reward_event = events[0], rewards[0]
        require([int(t, 16) for t in executed_event["topics"][1:]] == [1, action], "wrong nonce/action")
        requested, actual_input, gross, price, turnover, state = words(executed_event["data"])
        nonce, executor, asset = [int(t, 16) for t in reward_event["topics"][1:]]
        asset = address(asset)
        reward = words(reward_event["data"])[0]
        require(nonce == 1 and address(executor) == keeper and asset == (stock if buy else book["usdg"].lower()), "wrong reward recipient/asset")
        require(0 < reward == gross * params[5] // 10000, "reward not based on actual gross fill")
        venue = book["stocks"]["TSLA"]["pool"]
        input_asset, output_asset = (book["usdg"], stock) if buy else (stock, book["usdg"])
        require(transferred(receipt, input_asset, treasury, venue) == actual_input
                and transferred(receipt, output_asset, venue, treasury) == gross, "gross fill does not match actual venue transfers")
        transfers = [l for l in receipt["logs"] if l["address"].lower() == asset and l["topics"][0].lower() == transfer_topic
                     and [address(int(t, 16)) for t in l["topics"][1:]] == [treasury, keeper]]
        require(len(transfers) == 1 and words(transfers[0]["data"]) == [reward], "actual payout transfer missing")
        block = tx["blockNumber"]
        before, after = [snapshot(rpc, book, treasury, keeper, b) for b in [block - 1, block]]
        require(before["strategyNonce"] == 0 and after["strategyNonce"] == 1 and after["policyState"] == state, "execution state not committed")
        timestamp = int(rpc.request("eth_getBlockByNumber", [hex(block), False])["timestamp"], 16)
        require(after["lastStrategyAt"] == timestamp and after["turnoverInEpoch"] == turnover, "cooldown/turnover")
        require(after["turnoverEpoch"] == rpc.call(book["calendar"], "tradingDate(uint256)", block, timestamp)[0], "wrong trading epoch")
        require(after["unbookedStock"] == before["unbookedStock"] == 0 and after["bookedStock"] + after["buybackStock"] == after["stock"], "phantom/unbooked inventory")
        require(params[10] <= turnover <= 100_000_000, "turnover bounds")
        if buy:
            net = gross - reward
            require(after["stock"] - before["stock"] == after["bookedStock"] - before["bookedStock"] == net, "buy gross stock incorrectly booked")
            require(after["keeperStock"] - before["keeperStock"] == reward and after["keeperUsdg"] == before["keeperUsdg"], "keeper buy balance")
            require(before["usdg"] - after["usdg"] == actual_input == turnover and after["buybackStock"] == before["buybackStock"], "buy cash/buyback")
            numerator = before["bookedStock"] * before["avgCost"] + actual_input * 10**30
            denominator = before["bookedStock"] + net
            require(after["avgCost"] == (numerator + denominator - 1) // denominator, "buy cost not full spend over retained shares")
        else:
            moved = after["buybackStock"] - before["buybackStock"]
            require(before["stock"] - after["stock"] == actual_input and before["bookedStock"] - after["bookedStock"] == actual_input + moved, "sell principal/buyback")
            require(after["usdg"] - before["usdg"] == gross - reward and after["keeperUsdg"] - before["keeperUsdg"] == reward, "sell net USDG/keeper payout")
            require(after["keeperStock"] == before["keeperStock"] and after["avgCost"] == before["avgCost"], "sell unrelated ledger")
            require(turnover == (actual_input + moved) * price // 10**30, "sell turnover")
        # Prove block-boundary deltas are attributable to the named transaction, then rule out
        # unknown later activity at either isolated treasury up to the final canonical pin.
        known_hashes = set(receipts)
        first_block = launch_tx["blockNumber"]
        for asset_address in [stock, book["usdg"]]:
            for topics in [[transfer_topic, "0x" + f"{int(treasury, 16):064x}"],
                           [transfer_topic, None, "0x" + f"{int(treasury, 16):064x}"]]:
                activity = rpc.request("eth_getLogs", [{"address": asset_address, "fromBlock": hex(first_block),
                                                        "toBlock": hex(pin), "topics": topics}])
                require(all(l["transactionHash"] in known_hashes and not l.get("removed", False) for l in activity), "unknown treasury token activity")
                same_block = [l for l in activity if int(l["blockNumber"], 16) == block]
                require(all(l["transactionHash"] == tx["hash"] for l in same_block), "execute balance mixed with another transaction in the same block")
        final = snapshot(rpc, book, treasury, keeper, pin)
        require(all(final[k] == after[k] for k in after if k not in ["keeperStock", "keeperUsdg"]), "treasury changed after verified execute")
        require(before["lastStrategyAt"] == before["turnoverInEpoch"] == 0, "not a first execution")
        retry_probe = rpc.request("eth_call", [{"from": keeper, "to": treasury, "data": cast("sig", "execute()")}, hex(block)], allow_revert=True)
        require(isinstance(retry_probe, dict) and retry_probe.get("error", {}).get("data") in
                [cast("sig", "NotDue()"), cast("sig", "Cooldown()")], "second call does not revert with the cooldown guard")
        strategies.append({"strategyId": strategy_id, "treasury": treasury, "token": token, "executor": keeper,
                           "transactionHash": tx["hash"], "blockNumber": block, "blockHash": tx["blockHash"],
                           "action": "buy" if buy else "sell", "rewardAsset": asset, "reward": reward,
                           "requestedInput": requested, "actualInput": actual_input, "grossOutput": gross,
                           "netOutput": gross - reward, "price": price, "turnoverUsdg": turnover,
                           "gasCostWei": tx["gasUsed"] * tx["gasPrice"], "before": before, "after": after,
                           "runtimeMaskedMatch": True, "configWords": config, "params": params,
                           "initCodeHash": "0x" + f"{init_hash:064x}", "runtimeCodeHash": "0x" + f"{runtime_hash:064x}",
                           "configHash": "0x" + f"{frozen_config_hash:064x}", "curve": curve, "vault": vault,
                           "launchHash": launch_tx["hash"], "graduationHash": graduate_tx["hash"],
                           "graduation": {"grossStockPaid": spent, "releasedStock": released_stock, "lpStock": lp_stock,
                                          "treasuryStock": capital, "liquidity": liquidity, "tokenSeeded": lp_token,
                                          "tokenBurned": graduation_burn, "before": graduate_before, "after": graduate_after},
                           "final": final, "knownTreasuryActivityOnly": True,
                           "secondCallProbe": {"blockNumber": block, "broadcast": False,
                                               "revertData": retry_probe["error"]["data"]}})
    require(rpc.request("eth_getBlockByNumber", [hex(pin), False])["hash"] == pinned["hash"]
            and rpc.request("eth_getBlockByNumber", [hex(old_pin), False])["hash"] == old_block_hash, "pin reorg")
    return {"schema": "v2-execute-keeper-reward-proof-v1", "chainId": 46630, "broadcast": True, "verified": True,
            "sourceCommit": source, "coreSourceCommit": book["commit"], "coreBookSha256": hashlib.sha256(book_bytes).hexdigest(),
            "blockNumber": pin, "blockHash": pinned["hash"], "factory": factory, "registry": registry,
            "kind": 3, "creationCodeHash": creation_hash, "kinds": kinds, "coreRuntimeHashes": core_hashes,
            "transactions": txs, "fundingTransactions": funding, "strategies": strategies,
            "compiler": metadata["compiler"], "compilerSettings": settings,
            "sourceInputs": {p: v["keccak256"] for p, v in metadata["sources"].items()},
            "existingStrategy0": {"treasury": old_treasury, "runtimeCodeHash": old_runtime_hash,
                                  "mappingAndRuntimeUnchangedSince": old_pin, "beforeBlockHash": old_block_hash},
            "scope": "New immutable Engine kind 3 only; old kinds and strategy 0 mapping/runtime unchanged, original fee book preserved. Controlled callers, not a deployed automated keeper service. Testnet synthetic TSLA/USDG only. Gross rewards are not net profit after gas/RPC."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rpc", default="https://rpc.testnet.chain.robinhood.com")
    parser.add_argument("--book", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--block", type=int)
    arguments = parser.parse_args()
    result = audit(arguments)
    pathlib.Path(arguments.output).write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"verified": True, "block": result["blockNumber"], "transactions": len(result["transactions"]),
                      "rewards": [{k: s[k] for k in ["strategyId", "action", "reward", "rewardAsset"]} for s in result["strategies"]]}))

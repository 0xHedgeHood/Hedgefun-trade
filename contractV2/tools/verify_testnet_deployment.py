#!/usr/bin/env python3
"""Promote a simulated testnet address book only after canonical receipts and pinned live readback.

Read-only RPC; never signs or broadcasts. Requires Foundry's cast and Python 3.9+.
"""
import argparse
import copy
import datetime
import functools
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import urllib.request

CHAIN_ID = 46630
ROOT = Path(__file__).resolve().parents[1]
CODE_KEYS = ("poolManager", "weth", "usdg", "usdgFeed", "calendar", "v3Factory", "market", "factory",
             "tradeRouter", "nativeRouter", "hook", "treasuryDeployer", "tokenDeployer", "curveDeployer", "rebalancePolicy")


def require(ok, message):
    if not ok:
        raise ValueError(message)


def quantity(value):
    return int(value, 16) if isinstance(value, str) and value.startswith("0x") else int(value)


def address(value):
    require(isinstance(value, str) and re.fullmatch(r"0x[0-9a-fA-F]{40}", value), "invalid address")
    return value.lower()


def digest(value):
    require(isinstance(value, str) and re.fullmatch(r"0x[0-9a-fA-F]{64}", value), "invalid hash")
    return value.lower()


@functools.lru_cache(maxsize=None)
def calldata(signature, *args):
    return subprocess.check_output(["cast", "calldata", signature, *map(str, args)], text=True).strip()


def keccak(code):
    return subprocess.check_output(["cast", "keccak", code], text=True).strip().lower()


class Rpc:
    def __init__(self, url):
        self.url = url

    def __call__(self, method, params):
        request = urllib.request.Request(self.url, json.dumps({"jsonrpc": "2.0", "id": 1,
            "method": method, "params": params}).encode(), {"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=45) as response:
            result = json.load(response)
        require("error" not in result, f"{method}: {result.get('error')}")
        return result["result"]


def verify_receipts(book, run, rpc, confirmations):
    require(confirmations >= 1, "at least one confirmation required")
    require(book.get("chainId") == CHAIN_ID and quantity(rpc("eth_chainId", [])) == CHAIN_ID, "wrong chain")
    require(book.get("broadcast") is False and book.get("broadcastRequested") is True,
            "expected an unverified broadcast candidate, not a dry run or promoted book")
    require(quantity(run.get("chain", 0)) == CHAIN_ID, "broadcast log has wrong chain")
    transactions = run.get("transactions", [])
    require(bool(transactions), "broadcast log has no transactions")
    hashes = [digest(t.get("hash")) for t in transactions]
    require(len(set(hashes)) == len(hashes), "duplicate transaction hashes")
    operator = address(book["operator"])
    blocks = []
    created = set()
    for tx_hash in hashes:
        receipt = rpc("eth_getTransactionReceipt", [tx_hash])
        require(bool(receipt), f"pending or missing receipt: {tx_hash}")
        require(digest(receipt["transactionHash"]) == tx_hash and quantity(receipt["status"]) == 1,
                f"failed receipt: {tx_hash}")
        require(address(receipt["from"]) == operator, f"wrong receipt sender: {tx_hash}")
        number = quantity(receipt["blockNumber"])
        block = rpc("eth_getBlockByNumber", [hex(number), False])
        require(block and digest(block["hash"]) == digest(receipt["blockHash"]), f"noncanonical receipt: {tx_hash}")
        require(tx_hash in [digest(h) for h in block["transactions"]], f"transaction absent from canonical block: {tx_hash}")
        if receipt.get("contractAddress"):
            created.add(address(receipt["contractAddress"]))
        blocks.append(number)
    # These are direct CREATE transactions in DeployV2Testnet; pools and the CREATE2 hook are checked by readback.
    required_creations = {address(book[k]) for k in CODE_KEYS if k not in ("poolManager", "weth", "hook")}
    required_creations.update(address(stock[key]) for stock in book["stocks"].values()
                              for key in ("token", "feed", "oracle"))
    require(required_creations <= created, "broadcast log does not contain this candidate's deployment receipts")
    head = rpc("eth_getBlockByNumber", ["latest", False])
    number = quantity(head["number"])
    require(number - max(blocks) + 1 >= confirmations, "wait for requested confirmations")
    return hashes, number, digest(head["hash"])


def verify_bindings(book, rpc, block, hash_code=keccak, encode=calldata):
    tag = hex(block)

    def raw(target, signature, *args):
        return rpc("eth_call", [{"to": address(target), "data": encode(signature, *args)}, tag]).lower()

    def words(target, signature, *args):
        result = raw(target, signature, *args)
        require(result.startswith("0x") and len(result) > 2 and (len(result) - 2) % 64 == 0, f"bad return: {signature}")
        return [int(result[i:i+64], 16) for i in range(2, len(result), 64)]

    def equals(target, signature, values, *args):
        expected = [int(v, 16) if isinstance(v, str) and v.startswith("0x") else int(v) for v in values]
        require(words(target, signature, *args) == expected, f"readback mismatch: {signature} on {target}")

    def code(target, expected):
        runtime = rpc("eth_getCode", [address(target), tag])
        require(runtime not in (None, "0x", "0x0"), f"missing code: {target}")
        require(hash_code(runtime) == digest(expected), f"code hash mismatch: {target}")

    for key in CODE_KEYS:
        code(book[key], book["codeHashes"][key])
    require(address(book["poolManager"]) == "0x8366a39cc670b4001a1121b8f6a443a643e40951", "wrong PoolManager")
    require(address(book["weth"]) == "0x7943e237c7f95da44e0301572d358911207852fa", "wrong WETH")
    require(address(book["owner"]) == address(book["operator"]), "owner differs from operator")
    factory = book["factory"]
    for signature, key in (("owner()", "owner"), ("protocol()", "protocol"), ("poolManager()", "poolManager"),
                           ("v3Factory()", "v3Factory"), ("usdg()", "usdg"), ("curveDeployer()", "curveDeployer"),
                           ("treasuryDeployer()", "treasuryDeployer"), ("tokenDeployer()", "tokenDeployer"), ("hook()", "hook")):
        equals(factory, signature, [book[key]])
    equals(factory, "publicLaunch()", [1])
    require(raw(factory, "getDefaults()") == book["expectedDefaults"].lower(), "defaults mismatch")
    for key in ("treasuryDeployer", "tokenDeployer", "curveDeployer", "hook", "tradeRouter"):
        equals(book[key], "factory()", [factory])
    equals(book["nativeRouter"], "router()", [book["tradeRouter"]])
    equals(book["nativeRouter"], "wrappedNative()", [book["weth"]])
    equals(book["market"], "owner()", [book["operator"]])
    equals(book["market"], "usdg()", [book["usdg"]])
    equals(book["calendar"], "owner()", [book["operator"]])
    for key in ("usdg", "usdgFeed"):
        equals(book[key], "owner()", [book["operator"]])
    equals(book["usdg"], "operators(address)", [1], book["market"])
    equals(book["treasuryDeployer"], "kindCount()", [3])
    require(book["engineKind"] == 2, "wrong engine kind")
    policy = words(book["treasuryDeployer"], "policy(bytes32)", book["rebalancePolicyKey"])
    require(policy == [int(book["rebalancePolicy"], 16), int(book["codeHashes"]["rebalancePolicy"], 16),
                       1, 1, 150_000, 160, 3, 1], "policy manifest mismatch")
    require(set(book["stocks"]) == {"NVDA", "TSLA", "GME", "AAPL"}, "incomplete stock inventory")
    for stock in book["stocks"].values():
        for key in ("token", "feed", "oracle", "pool"):
            code(stock[key], stock[key + "CodeHash"])
        token, oracle, pool = stock["token"], stock["oracle"], stock["pool"]
        for target in (token, stock["feed"]):
            equals(target, "owner()", [book["operator"]])
            equals(target, "operators(address)", [1], book["market"])
        equals(factory, "listings(address)", [oracle, pool, int(stock["openPriceE18"]), 1], token)
        equals(factory, "listingGates(address)", [50, 100, 2_000_000_000], token)
        equals(book["treasuryDeployer"], "lpBps(address)", [5000], token)
        for signature, target in (("stock()", token), ("stockFeed()", stock["feed"]),
                                  ("usdgFeed()", book["usdgFeed"]), ("calendar()", book["calendar"])):
            equals(oracle, signature, [target])
        equals(oracle, "maxStockAge()", [26 * 3600])
        equals(oracle, "maxUsdgAge()", [26 * 3600])
        equals(book["v3Factory"], "getPool(address,address,uint24)", [pool], token, book["usdg"], stock["fee"])
        equals(pool, "liquidity()", [int(stock["liquidity"])])
        slot = words(pool, "slot0()")
        require(len(slot) == 7 and slot[3] > 0 and slot[4] >= 720, "pool ring not initialized")


def verify(book, run, rpc, confirmations=2, readback=verify_bindings):
    hashes, number, block_hash = verify_receipts(book, run, rpc, confirmations)
    readback(book, rpc, number)
    # Refuse a reorg during our pinned readback instead of recording a stale validation.
    block = rpc("eth_getBlockByNumber", [hex(number), False])
    require(block and digest(block["hash"]) == block_hash, "verification block changed during readback")
    result = copy.deepcopy(book)
    result["broadcast"] = True
    result["verification"] = {"schema": 1, "chainId": CHAIN_ID, "blockNumber": number, "blockHash": block_hash,
        "transactionHashes": hashes, "verifiedAt": datetime.datetime.now(datetime.timezone.utc).isoformat()}
    return result


def promote(result, destination):
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, prefix=path.name + ".", delete=False) as stream:
            temporary = stream.name
            json.dump(result, stream, indent=2)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, default=ROOT / "deploy/testnet-v2.candidate.json")
    parser.add_argument("--broadcast-log", type=Path, required=True)
    parser.add_argument("--rpc", default="https://rpc.testnet.chain.robinhood.com")
    parser.add_argument("--confirmations", type=int, default=2)
    parser.add_argument("--out", type=Path, default=ROOT / "deploy/testnet-v2.json")
    args = parser.parse_args()
    try:
        with args.candidate.open() as stream:
            book = json.load(stream)
        with args.broadcast_log.open() as stream:
            run = json.load(stream)
        result = verify(book, run, Rpc(args.rpc), args.confirmations)
        promote(result, args.out)
    except Exception as error:
        parser.exit(1, f"NOT VERIFIED: {error}\n")
    print(f"Verified {len(result['verification']['transactionHashes'])} receipts and live bindings; wrote {args.out}")


if __name__ == "__main__":
    main()

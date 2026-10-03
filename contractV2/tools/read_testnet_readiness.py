#!/usr/bin/env python3
"""Read a pinned Robinhood testnet state. Never signs or broadcasts transactions."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rpc", default="https://rpc.testnet.chain.robinhood.com")
    parser.add_argument("--cast", default="cast")
    parser.add_argument("--book", type=Path, default=ROOT / "deploy/testnet-v2-keeper-reward-proof/fee-core-book.json")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    counter = 0

    def rpc(method, params):
        nonlocal counter
        if method not in {"eth_chainId", "eth_blockNumber", "eth_getBlockByNumber", "eth_getCode", "eth_getBalance", "eth_call"}:
            raise ValueError("Read-only method allowlist")
        counter += 1
        payload = json.dumps(dict(jsonrpc="2.0", id=counter, method=method, params=params))
        result = subprocess.run(["curl", "--fail", "--silent", "--show-error", "--max-time", "30",
                                 "-H", "Content-Type: application/json", "--data-binary", "@-", args.rpc],
                                input=payload, text=True, capture_output=True, check=True)
        data = json.loads(result.stdout)
        if "error" in data:
            raise RuntimeError(data["error"])
        time.sleep(0.15)
        return data["result"]

    chain = int(rpc("eth_chainId", []), 16)
    if chain != 46630:
        raise ValueError(f"Expected testnet 46630, received {chain}")
    block = rpc("eth_blockNumber", [])
    header = rpc("eth_getBlockByNumber", [block, False])
    raw_book = args.book.read_bytes()
    book = json.loads(raw_book)
    if book["chainId"] != chain:
        raise ValueError("Address book chain mismatch")

    def call(address, signature, *values):
        data = subprocess.check_output([args.cast, "calldata", signature, *map(str, values)], text=True).strip()
        raw = rpc("eth_call", [{"to": address, "data": data}, block])
        return [int(raw[i:i + 64], 16) for i in range(2, len(raw), 64)]

    def address(value):
        return f"0x{value:040x}"

    result = dict(schema="hedgefun-testnet-readiness-v1", chainId=chain, blockNumber=int(block, 16),
                  blockHash=header["hash"], blockTimestamp=int(header["timestamp"], 16),
                  bookSha256=hashlib.sha256(raw_book).hexdigest(), bookSourceCommit=book.get("commit"),
                  sourceCommit=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                  evidence="read-only live state; no transaction signed or broadcast; runtime/source equivalence not certified")
    factory = book["factory"]
    abi = json.loads((ROOT / "abi/HedgeFunV2Factory.json").read_text())
    if isinstance(abi, dict):
        abi = abi["abi"]
    components = next(item for item in abi if item.get("name") == "getDefaults")["outputs"][0]["components"]
    defaults = call(factory, "getDefaults()")
    if len(defaults) != len(components):
        raise ValueError("Unexpected live defaults ABI")
    result["defaults"] = {c["name"]: str(v) for c, v in zip(components, defaults)}
    result["factory"] = factory
    result["owner"] = address(call(factory, "owner()")[0])
    result["publicLaunch"] = bool(call(factory, "publicLaunch()")[0])
    result["strategyCount"] = call(factory, "strategyCount()")[0]
    result["operatorNativeWei"] = str(int(rpc("eth_getBalance", [book["operator"], block]), 16))
    result["calendarClosed"] = bool(call(book["calendar"], "isClosed(uint256)", result["blockTimestamp"])[0])
    result["code"] = {}
    for name in ("factory", "tradeRouter", "nativeRouter", "hook", "curveDeployer", "treasuryDeployer", "poolManager", "weth"):
        code = bytes.fromhex(rpc("eth_getCode", [book[name], block])[2:])
        result["code"][name] = dict(address=book[name], bytes=len(code), sha256=hashlib.sha256(code).hexdigest())
        if not code:
            raise ValueError(f"Missing code for {name}")
    result["routerFactoryMatches"] = address(call(book["tradeRouter"], "factory()")[0]).lower() == factory.lower()
    result["stocks"] = {}
    for symbol, item in book["stocks"].items():
        listing = call(factory, "listings(address)", item["token"])
        health = call(item["oracle"], "tryPrice()")
        result["stocks"][symbol] = dict(token=item["token"], oracle=address(listing[0]), pool=address(listing[1]),
                                          openPriceE18=str(listing[2]), enabled=bool(listing[3]),
                                          oracleHealthy=bool(health[0]), oraclePriceE18=str(health[1]))
    result["nativePools"] = {str(fee): address(call(book["v3Factory"], "getPool(address,address,uint24)",
                                                  book["weth"], book["usdg"], fee)[0]) for fee in (500, 3000)}
    result["strategies"] = []
    for i in range(min(result["strategyCount"], 20)):
        values = call(factory, "strategies(uint256)", i)
        curve = address(call(factory, "curves(uint256)", i)[0])
        result["strategies"].append(dict(id=i, token=address(values[0]), treasury=address(values[1]),
                                         stock=address(values[3]), curve=curve, status=call(curve, "status()")[0]))
    result["strategiesTruncated"] = result["strategyCount"] > 20
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({k: result[k] for k in ("chainId", "blockNumber", "factory", "strategyCount", "publicLaunch", "nativePools")}))


if __name__ == "__main__":
    main()

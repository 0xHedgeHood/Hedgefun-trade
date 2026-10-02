#!/usr/bin/env python3
"""Verify the whitelist testnet book against canonical RPC receipts and live code.

The deploy script writes its JSON during simulation, before broadcast. This verifier is
the publication gate: it never signs or sends a transaction and writes a separate report.
"""

import argparse
import hashlib
import json
import pathlib
import time
import urllib.request
from datetime import datetime, timezone

RPC = "https://rpc.testnet.chain.robinhood.com"
BOOK = pathlib.Path("deploy/testnet-v2-whitelist.json")
LOG = pathlib.Path("broadcast/DeployV2Testnet.s.sol/46630/run-latest.json")
REPORT = pathlib.Path("deploy/testnet-v2-whitelist.verification.json")
OPERATOR = "0x75cee941b0ef3a83fea0397bbf903c12c1d7e96d"


def rpc_batch(url, calls):
    result = {}
    for offset in range(0, len(calls), 20):
        chunk = calls[offset : offset + 20]
        payload = [{"jsonrpc": "2.0", "id": offset + i, "method": method, "params": params}
                   for i, (method, params) in enumerate(chunk)]
        for attempt in range(3):
            try:
                data = json.dumps(payload).encode()
                req = urllib.request.Request(url, data=data,
                    headers={"Content-Type": "application/json", "User-Agent": "HedgefunTestnetVerifier/1.0"})
                with urllib.request.urlopen(req, timeout=30) as response:
                    parsed = json.load(response)
                if not isinstance(parsed, list):
                    raise ValueError("RPC batch returned a non-list response")
                for item in parsed:
                    if "error" in item:
                        raise ValueError(f"RPC error: {item['error']}")
                    result[item["id"]] = item["result"]
                break
            except (OSError, ValueError) as exc:
                if attempt == 2:
                    raise RuntimeError(f"RPC batch at offset {offset} failed") from exc
                time.sleep(1 + attempt)
    if len(result) != len(calls):
        raise ValueError("RPC batch omitted responses")
    return [result[i] for i in range(len(calls))]


def check(condition, message):
    if not condition:
        raise ValueError(message)


def hexint(value):
    return int(value, 16) if isinstance(value, str) and value.startswith("0x") else int(value)


def addresses(book):
    names = ("poolManager", "weth", "usdg", "usdgFeed", "calendar", "v3Factory", "market",
             "factory", "tradeRouter", "nativeRouter", "hook", "treasuryDeployer", "tokenDeployer",
             "curveDeployer", "rebalancePolicy")
    result = {name: book[name] for name in names}
    for symbol, line in book["stocks"].items():
        for name in ("token", "feed", "oracle", "pool"):
            result[f"stocks.{symbol}.{name}"] = line[name]
    return result


def call_address(url, target, selector, block):
    output = rpc_batch(url, [("eth_call", [{"to": target, "data": selector}, block])])[0]
    check(isinstance(output, str) and len(output) >= 66, f"empty role readback on {target}")
    return "0x" + output[-40:].lower()


def call_uint(url, target, selector, block):
    output = rpc_batch(url, [("eth_call", [{"to": target, "data": selector}, block])])[0]
    check(isinstance(output, str) and len(output) >= 3, f"empty numeric readback on {target}")
    return hexint(output)


def verify(url, book_path, log_path):
    raw_book, raw_log = book_path.read_bytes(), log_path.read_bytes()
    book, log = json.loads(raw_book), json.loads(raw_log)
    check(book.get("chainId") == 46630 and book.get("broadcast") is True, "book is not a broadcast on chain 46630")
    check(book.get("featureVersion") == "v2-opening-tax-whitelist-v1", "wrong book feature version")
    check(book.get("operator", "").lower() == OPERATOR, "wrong book operator")
    check(len(book.get("commit", "")) == 40, "book must pin a full source commit")
    check(hexint(log.get("chain")) == 46630, "broadcast log is for another chain")
    check(book["commit"].startswith(log.get("commit", "!")), "book and log source commits differ")
    txs, receipts = log["transactions"], log["receipts"]
    check(len(txs) == len(receipts) == 72, "expected exactly 72 deployment transactions and receipts")
    hashes = [tx["hash"].lower() for tx in txs]
    check(len(set(hashes)) == 72, "duplicate deployment transaction hash")
    chain = rpc_batch(url, [("eth_chainId", [])])[0]
    check(hexint(chain) == 46630, "RPC chain ID mismatch")
    live_txs = rpc_batch(url, [("eth_getTransactionByHash", [h]) for h in hashes])
    live_receipts = rpc_batch(url, [("eth_getTransactionReceipt", [h]) for h in hashes])
    block_numbers = sorted({hexint(r["blockNumber"]) for r in live_receipts})
    blocks = rpc_batch(url, [("eth_getBlockByNumber", [hex(n), False]) for n in block_numbers])
    block_hashes = {n: b["hash"].lower() for n, b in zip(block_numbers, blocks)}
    first_nonce = hexint(txs[0]["transaction"]["nonce"])
    gas_paid = 0
    for i, (saved_tx, saved_receipt, live_tx, live_receipt) in enumerate(zip(txs, receipts, live_txs, live_receipts)):
        check(live_tx is not None and live_receipt is not None, f"missing live tx/receipt {i}")
        check(live_tx["hash"].lower() == hashes[i] == live_receipt["transactionHash"].lower(), f"hash mismatch {i}")
        check(saved_receipt["transactionHash"].lower() == hashes[i], f"saved receipt hash mismatch {i}")
        check(hexint(live_receipt["status"]) == hexint(saved_receipt["status"]) == 1, f"failed tx {i}")
        check(live_tx["from"].lower() == OPERATOR and live_receipt["from"].lower() == OPERATOR, f"sender mismatch {i}")
        check(hexint(live_tx["nonce"]) == hexint(saved_tx["transaction"]["nonce"]) == first_nonce + i,
              f"nonce discontinuity {i}")
        check(hexint(live_tx["chainId"]) == 46630, f"tx on wrong chain {i}")
        check(live_tx["input"].lower() == saved_tx["transaction"]["input"].lower(), f"input mismatch {i}")
        check(hexint(live_tx["value"]) == hexint(saved_tx["transaction"]["value"]), f"value mismatch {i}")
        check((live_tx.get("to") or "").lower() == (saved_tx["transaction"].get("to") or "").lower(), f"target mismatch {i}")
        number = hexint(live_receipt["blockNumber"])
        check(live_receipt["blockHash"].lower() == block_hashes[number], f"noncanonical receipt block {i}")
        check(live_receipt["blockHash"].lower() == saved_receipt["blockHash"].lower(), f"saved block mismatch {i}")
        check((live_receipt.get("contractAddress") or "").lower() ==
              (saved_receipt.get("contractAddress") or "").lower(), f"created address mismatch {i}")
        gas_paid += hexint(live_receipt["gasUsed"]) * hexint(live_receipt["effectiveGasPrice"])
    verification_block = max(block_numbers)
    verification_tag = hex(verification_block)
    all_addresses = addresses(book)
    code = rpc_batch(url, [("eth_getCode", [address, verification_tag]) for address in all_addresses.values()])
    code_hashes = {}
    for (name, address), deployed in zip(all_addresses.items(), code):
        check(isinstance(deployed, str) and deployed != "0x", f"missing live code at {name} {address}")
        code_hashes[name] = "0x" + hashlib.sha256(bytes.fromhex(deployed[2:])).hexdigest()
    roles = {
        "factory.owner": call_address(url, book["factory"], "0x8da5cb5b", verification_tag),
        "factory.protocol": call_address(url, book["factory"], "0x8ce74426", verification_tag),
        "factory.curveDeployer": call_address(url, book["factory"], "0x967f08b9", verification_tag),
        "curveDeployer.factory": call_address(url, book["curveDeployer"], "0xc45a0155", verification_tag),
        "tradeRouter.factory": call_address(url, book["tradeRouter"], "0xc45a0155", verification_tag),
        "nativeRouter.router": call_address(url, book["nativeRouter"], "0xf887ea40", verification_tag),
        "usdg.owner": call_address(url, book["usdg"], "0x8da5cb5b", verification_tag),
        "market.owner": call_address(url, book["market"], "0x8da5cb5b", verification_tag),
    }
    expected = {
        "factory.owner": OPERATOR, "factory.protocol": book["protocol"].lower(),
        "factory.curveDeployer": book["curveDeployer"].lower(), "curveDeployer.factory": book["factory"].lower(),
        "tradeRouter.factory": book["factory"].lower(), "nativeRouter.router": book["tradeRouter"].lower(),
        "usdg.owner": OPERATOR, "market.owner": OPERATOR,
    }
    for name in roles:
        check(roles[name] == expected[name], f"role readback mismatch: {name}: {roles[name]} != {expected[name]}")
    check(call_uint(url, book["factory"], "0x4cdf6b1f", verification_tag) == 1, "public launch is closed")
    check(call_uint(url, book["factory"], "0x22068b44", verification_tag) == 0, "unexpected preexisting strategy")
    return {
        "schema": "whitelist-testnet-readback-v1", "chainId": 46630,
        "featureVersion": book["featureVersion"], "sourceCommit": book["commit"],
        "operator": book["operator"], "addressBookSha256": hashlib.sha256(raw_book).hexdigest(),
        "broadcastLogSha256": hashlib.sha256(raw_log).hexdigest(),
        "verificationBlockNumber": verification_block,
        "verificationBlockHash": block_hashes[verification_block],
        "verifiedAt": datetime.now(timezone.utc).isoformat(),
        "transactionHashes": hashes, "transactionCount": 72,
        "firstNonce": first_nonce, "lastNonce": first_nonce + 71,
        "gasPaidWei": gas_paid, "codeSha256": code_hashes, "roles": roles,
        "limitations": "Code hashes are measured after deployment; no precommitted candidate bytecode manifest.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rpc", default=RPC)
    parser.add_argument("--book", type=pathlib.Path, default=BOOK)
    parser.add_argument("--broadcast", type=pathlib.Path, default=LOG)
    parser.add_argument("--out", type=pathlib.Path, default=REPORT)
    args = parser.parse_args()
    if args.out.resolve() in (args.book.resolve(), args.broadcast.resolve()):
        parser.error("report must not overwrite the book or broadcast log")
    report = verify(args.rpc, args.book, args.broadcast)
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(f"Verified {report['transactionCount']} canonical transactions at block "
          f"{report['verificationBlockNumber']}; paid {report['gasPaidWei']} wei; wrote {args.out}")


if __name__ == "__main__":
    main()

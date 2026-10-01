#!/usr/bin/env python3
"""Read-only canonical-chain receipt and state report for one V2 testnet journey phase."""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

CREATOR = "0xd4f69d180a9bc36f27d307e90e365d1e012816d5"
FACTORY = "0x56144258d92dc7283fd704aaf3372212dd3864e9"
GME = "0xaa55745a01b1f24ba5c191d0a6d07ac0652735d3"
PHASE_STAGE = {"launch": 0, "curveBuy": 0, "curveSell": 0, "graduate": 2, "v4Buy": 2, "v4Sell": 2}
APPROVE = "0x095ea7b3"
LAUNCH = "0x5f75acbe"
BUY = "0xd3f9965c"
SELL = "0x4469d8cb"
LAUNCHED_EVENT = "0x797d1021a44c2a68e4c02160ae40b17e66b2feded79284a0cfe931e9e4c2a61e"
BOUGHT_EVENT = "0xa101afbd2f2109dcfa253f66e003d0f14243f2f21b3d1c668ef016cd3648ca97"
SOLD_EVENT = "0x2938a0a3a4a7c19c3a1fe6ef25340b7acd26dfac11de87836084d42fccc18656"
GRADUATED_EVENT = "0x50d6220751086ae05e08159ab810661bbab6008f0129cbd125dd4b44e2e7c006"


def require(ok: bool, message: str) -> None:
    if not ok:
        raise ValueError(message)


def word(data: str, index: int) -> int:
    start = 10 + index * 64
    require(data.startswith("0x") and len(data) >= start + 64, "truncated transaction calldata")
    return int(data[start:start + 64], 16)


def encoded_address(value: int) -> str:
    require(value < 1 << 160, "invalid calldata address")
    return f"0x{value:040x}"


def event(receipt: dict, emitter: str, topic: str, strategy_id: int) -> bool:
    return any(log.get("address", "").lower() == emitter.lower()
               and len(log.get("topics", [])) > 1
               and log["topics"][0].lower() == topic
               and int(log["topics"][1], 16) == strategy_id
               for log in receipt.get("logs", []))


def verify_phase_bundle(phase: str, strategy_id: int, book: dict, token: str,
                        logged: list[dict], transactions: list[dict], receipts: list[dict]) -> int:
    """Bind one receipt file to the precise canonical calls and strategy events for its phase."""
    require(len(logged) in (1, 2) and len(transactions) == len(logged) == len(receipts),
            "phase requires one action and at most one exact approval")
    for recorded, tx, receipt in zip(logged, transactions, receipts):
        tx_hash = recorded.get("hash", "").lower()
        require(tx.get("hash", "").lower() == tx_hash
                and receipt.get("transactionHash", "").lower() == tx_hash,
                "canonical transaction hash does not match broadcast log")
        require(tx.get("from", "").lower() == CREATOR and receipt.get("from", "").lower() == CREATOR
                and receipt.get("status") == "0x1", "canonical transaction failed or sender differs")
        require(tx.get("to", "").lower() == receipt.get("to", "").lower()
                and tx.get("blockHash", "").lower() == receipt.get("blockHash", "").lower()
                and tx.get("blockNumber") == receipt.get("blockNumber"),
                "canonical transaction and receipt do not share a block or target")
        local = recorded.get("transaction", {})
        require(local.get("to", "").lower() == tx.get("to", "").lower()
                and local.get("input", "").lower() == tx.get("input", "").lower(),
                "broadcast log calldata does not match canonical transaction")

    action, action_receipt = transactions[-1], receipts[-1]
    data = action.get("input", "").lower()
    factory = book["factory"].lower()
    router = book["tradeRouter"].lower()
    usdg = book["usdg"].lower()
    stock = book["stocks"]["GME"]["token"].lower()
    pool = book["stocks"]["GME"]["pool"].lower()
    amount = 0
    if phase == "launch":
        require(action.get("to", "").lower() == factory and data.startswith(LAUNCH),
                "launch action target or selector differs")
        offset = word(data, 0)
        require(offset == 64, "unexpected launch request layout")
        tuple_start = offset // 32
        require(encoded_address(word(data, tuple_start + 2)) == stock
                and encoded_address(word(data, tuple_start + 3)) == CREATOR,
                "launch calldata creator or stock differs")
        require(event(action_receipt, factory, LAUNCHED_EVENT, strategy_id),
                "launch receipt lacks the matching strategy event")
        approval_token, spender = usdg, factory
    else:
        buying = phase in ("curveBuy", "graduate", "v4Buy")
        selector = BUY if buying else SELL
        require(action.get("to", "").lower() == router and data.startswith(selector),
                "trade action target or selector differs")
        amount = word(data, 2)
        require(word(data, 0) == strategy_id and encoded_address(word(data, 1)) == usdg
                and amount > 0 and word(data, 4) > 0
                and word(data, 6) == (2 if phase.startswith("v4") else 0)
                and word(data, 7) == (1 if phase == "graduate" else 0),
                "trade calldata strategy, asset, amount or stage differs")
        require(word(data, 3) > 0 if buying else word(data, 3) == 0,
                "trade stock minimum differs")
        path_offset = word(data, 8)
        require(path_offset == 9 * 32 and word(data, path_offset // 32) == 1
                and encoded_address(word(data, path_offset // 32 + 1)) == pool
                and encoded_address(word(data, path_offset // 32 + 2)) == (stock if buying else usdg),
                "trade route differs from the verified GME/tUSDG pool")
        topic = BOUGHT_EVENT if buying else SOLD_EVENT
        require(event(action_receipt, router, topic, strategy_id),
                "trade receipt lacks the matching strategy event")
        if phase == "graduate":
            require(event(action_receipt, factory, GRADUATED_EVENT, strategy_id),
                    "graduation receipt lacks the matching strategy event")
        approval_token, spender = (usdg if buying else token.lower()), router

    if len(transactions) == 2:
        approval, approval_receipt = transactions[0], receipts[0]
        approval_data = approval.get("input", "").lower()
        require(approval.get("to", "").lower() == approval_token
                and approval_data.startswith(APPROVE) and len(approval_data) == 10 + 2 * 64
                and encoded_address(word(approval_data, 0)) == spender
                and word(approval_data, 1) > 0
                and (phase == "launch" or word(approval_data, 1) == amount),
                "approval target, spender or amount differs from phase")
        before = (int(approval_receipt["blockNumber"], 16), int(approval_receipt["transactionIndex"], 16))
        after = (int(action_receipt["blockNumber"], 16), int(action_receipt["transactionIndex"], 16))
        require(before < after, "approval must precede the phase action")
    return int(action_receipt["blockNumber"], 16)


def cast(rpc: str, *args: str):
    output = subprocess.check_output(["cast", *args, "--rpc-url", rpc, "--json"], text=True)
    return json.loads(output)


def call(rpc: str, to: str, signature: str, *args: str, block: int | None = None):
    command = ["call", to, signature, *args]
    if block is not None:
        command += ["--block", str(block)]
    return cast(rpc, *command)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=PHASE_STAGE, required=True)
    parser.add_argument("--id", type=int, required=True)
    parser.add_argument("--receipts", type=Path, required=True, help="Foundry broadcast <phase>-latest.json")
    parser.add_argument("--book", type=Path, default=Path("deploy/testnet-v2.json"))
    parser.add_argument("--rpc", default="https://rpc.testnet.chain.robinhood.com")
    args = parser.parse_args()

    if args.id < 0:
        parser.error("strategy id must be nonnegative")
    book = json.loads(args.book.read_text())
    if (book.get("chainId") != 46630 or book.get("broadcast") is not True
        or book.get("factory", "").lower() != FACTORY
        or book.get("stocks", {}).get("GME", {}).get("token", "").lower() != GME):
        parser.error("address book is not a real chain-46630 broadcast")
    if int(subprocess.check_output(["cast", "chain-id", "--rpc-url", args.rpc], text=True).strip()) != 46630:
        parser.error("RPC is not Robinhood Chain testnet")

    recorded = json.loads(args.receipts.read_text())
    transactions = recorded.get("transactions", [])
    receipts = recorded.get("receipts", [])
    if not transactions or len(transactions) != len(receipts):
        parser.error("missing or mismatched broadcast transactions and receipts")
    hashes = [tx["hash"].lower() for tx in transactions]
    if len(set(hashes)) != len(hashes) or hashes != [receipt["transactionHash"].lower() for receipt in receipts]:
        parser.error("receipt hashes do not match broadcast transactions")
    chain_transactions = [cast(args.rpc, "tx", tx_hash) for tx_hash in hashes]
    chain_receipts = [cast(args.rpc, "receipt", tx_hash) for tx_hash in hashes]
    action_block = int(chain_receipts[-1]["blockNumber"], 16)
    if int(call(args.rpc, book["factory"], "strategyCount()(uint256)", block=action_block)[0]) <= args.id:
        parser.error("strategy id did not exist at the phase action block")
    token, treasury, hook, stock, creator = call(
        args.rpc, book["factory"], "strategies(uint256)(address,address,address,address,address)", str(args.id), block=action_block
    )
    if creator.lower() != CREATOR or stock.lower() != book["stocks"]["GME"]["token"].lower():
        parser.error("unexpected strategy creator or stock at phase action block")
    curve = call(args.rpc, book["factory"], "curves(uint256)(address)", str(args.id), block=action_block)[0]
    try:
        verify_phase_bundle(args.phase, args.id, book, token, transactions, chain_transactions, chain_receipts)
    except (KeyError, TypeError, ValueError) as error:
        parser.error(f"phase receipts are not verified: {error}")

    result_receipts = []
    for hash_, chain_receipt in zip(hashes, chain_receipts):
        result_receipts.append({
            "hash": hash_,
            "block": int(chain_receipt["blockNumber"], 16),
            "gasUsed": int(chain_receipt["gasUsed"], 16),
            "status": chain_receipt["status"],
            "explorer": f"https://explorer.testnet.chain.robinhood.com/tx/{hash_}",
        })

    stage = int(call(args.rpc, curve, "status()(uint8)", block=action_block)[0])
    if stage != PHASE_STAGE[args.phase]:
        parser.error(f"expected stage {PHASE_STAGE[args.phase]}, got {stage}")

    def balance(asset: str) -> int:
        return int(call(args.rpc, asset, "balanceOf(address)(uint256)", CREATOR, block=action_block)[0])

    result = {
        "chainId": 46630,
        "observedBlock": action_block,
        "phase": args.phase,
        "strategyId": args.id,
        "factory": book["factory"],
        "creator": creator,
        "token": token,
        "treasury": treasury,
        "hook": hook,
        "stock": stock,
        "curve": curve,
        "stage": stage,
        "balancesRaw": {
            "tUSDG": balance(book["usdg"]),
            "GME": balance(stock),
            "strategyToken": balance(token),
        },
        "curveRealStockReserve": int(call(args.rpc, curve, "realStockReserve()(uint256)", block=action_block)[0]),
        "receipts": result_receipts,
    }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

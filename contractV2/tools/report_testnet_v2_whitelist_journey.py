#!/usr/bin/env python3
"""Verify the V2 whitelist testnet journey against canonical transaction receipts."""

import argparse
import json
from pathlib import Path

from verify_testnet_whitelist import hexint, rpc_batch

RPC = "https://rpc.testnet.chain.robinhood.com"
BOOK = Path("deploy/testnet-v2-whitelist.json")
BASE = Path("broadcast/TestnetV2Journey.s.sol/46630")
REPORT = Path("deploy/testnet-v2-whitelist.journey.json")
CREATOR = "0xd4f69d180a9bc36f27d307e90e365d1e012816d5"
OPERATOR = "0x75cee941b0ef3a83fea0397bbf903c12c1d7e96d"
WHITELISTED = "0xda1aee7018a3925aa06deeb8631fca09e1067614"
PHASES = ("launch", "ordinaryBuy", "whitelistBuy", "curveBuy", "curveSell", "graduate", "v4Buy", "v4Sell")
BUY = "0xd3f9965c"
SELL = "0x4469d8cb"
LAUNCH = "0x5f75acbe"
CURVE_BOUGHT = "0x7ce543d1780f3bdc3dac42da06c95da802653cd1b212b8d74ec3e3c33ad7095c"
ROUTER_BOUGHT = "0x7d17b35a27eccb1049c702a9e9818fa7f6fbf283747fe467fcde0ba7b10fe7eb"
ROUTER_SOLD = "0x2938a0a3a4a7c19c3a1fe6ef25340b7acd26dfac11de87836084d42fccc18656"
GRADUATED = "0x50d6220751086ae05e08159ab810661bbab6008f0129cbd125dd4b44e2e7c006"
LAUNCHED = "0x797d1021a44c2a68e4c02160ae40b17e66b2feded79284a0cfe931e9e4c2a61e"


def check(condition, message):
    if not condition:
        raise ValueError(message)


def event(receipt, address, topic):
    matches = [log for log in receipt["logs"]
               if log["address"].lower() == address.lower() and log["topics"]
               and log["topics"][0].lower() == topic]
    check(len(matches) == 1, f"expected one {topic} event at {address}")
    return matches[0]


def address_topic(topic):
    return "0x" + topic[-40:].lower()


def words(data):
    check(len(data) >= 2 and (len(data) - 2) % 64 == 0, "malformed event data")
    return [int(data[i:i + 64], 16) for i in range(2, len(data), 64)]


def verify(url, book, base):
    check(book.get("chainId") == 46630 and book.get("broadcast") is True, "wrong chain/book")
    check(book.get("featureVersion") == "v2-opening-tax-whitelist-v1", "wrong feature version")
    check(hexint(rpc_batch(url, [("eth_chainId", [])])[0]) == 46630, "wrong RPC chain")
    curve = "0x02D953A261306F393A07f03af1eb060b889DFF0b"
    token = "0x708B9EcBbA100B0dbf9CB48c28412e8B0c9661Db"
    senders = {"ordinaryBuy": OPERATOR, "whitelistBuy": WHITELISTED}
    result = []
    seen = set()
    for phase in PHASES:
        saved = json.loads((base / f"{phase}-latest.json").read_text())
        local_txs, local_receipts = saved["transactions"], saved["receipts"]
        expected_count = 4 if phase == "launch" else 2
        check(len(local_txs) == len(local_receipts) == expected_count, f"wrong {phase} transaction count")
        hashes = [tx["hash"].lower() for tx in local_txs]
        check(not seen.intersection(hashes), f"duplicate hash in {phase}")
        seen.update(hashes)
        live_txs = rpc_batch(url, [("eth_getTransactionByHash", [h]) for h in hashes])
        live_receipts = rpc_batch(url, [("eth_getTransactionReceipt", [h]) for h in hashes])
        sender = senders.get(phase, CREATOR)
        for local, saved_receipt, tx, receipt in zip(local_txs, local_receipts, live_txs, live_receipts):
            check(tx is not None and receipt is not None, f"missing {phase} transaction/receipt")
            check(tx["hash"].lower() == local["hash"].lower() == receipt["transactionHash"].lower(),
                  f"{phase} hash mismatch")
            check(saved_receipt["transactionHash"].lower() == tx["hash"].lower(), f"{phase} saved receipt mismatch")
            check(tx["from"].lower() == receipt["from"].lower() == sender, f"{phase} sender mismatch")
            check((tx["to"] or "").lower() == (local["transaction"].get("to") or "").lower(),
                  f"{phase} target mismatch")
            check(tx["input"].lower() == local["transaction"]["input"].lower(), f"{phase} calldata mismatch")
            check(hexint(tx["value"]) == hexint(local["transaction"]["value"]), f"{phase} value mismatch")
            check(hexint(receipt["status"]) == hexint(saved_receipt["status"]) == 1, f"{phase} failed")
            check(receipt["blockHash"].lower() == saved_receipt["blockHash"].lower(), f"{phase} block mismatch")
        action, receipt = live_txs[-1], live_receipts[-1]
        block = rpc_batch(url, [("eth_getBlockByNumber", [receipt["blockNumber"], False])])[0]
        check(block["hash"].lower() == receipt["blockHash"].lower(), f"{phase} noncanonical block")
        if phase == "launch":
            check(action["to"].lower() == book["factory"].lower() and action["input"].startswith(LAUNCH),
                  "launch target/selector mismatch")
            event(receipt, book["factory"], LAUNCHED)
            check(live_txs[0]["to"].lower() == book["curveDeployer"].lower()
                  and live_txs[1]["to"].lower() == book["curveDeployer"].lower(),
                  "launch lacks curve configuration/whitelist calls")
            detail = {"token": token, "curve": curve}
        else:
            buying = phase in ("ordinaryBuy", "whitelistBuy", "curveBuy", "graduate", "v4Buy")
            topic = ROUTER_BOUGHT if buying else ROUTER_SOLD
            check(action["to"].lower() == book["tradeRouter"].lower()
                  and action["input"].startswith(BUY if buying else SELL), f"{phase} action mismatch")
            router_log = event(receipt, book["tradeRouter"], topic)
            check(hexint(router_log["topics"][1]) == 0 and address_topic(router_log["topics"][2]) == sender,
                  f"{phase} wrong strategy or router sender")
            detail = {"routerEvent": topic}
            if buying:
                check("0x" + router_log["data"][26:66].lower() == sender, f"{phase} wrong recipient")
            if phase in ("ordinaryBuy", "whitelistBuy", "curveBuy", "graduate"):
                curve_log = event(receipt, curve, CURVE_BOUGHT)
                check(address_topic(curve_log["topics"][1]) == book["tradeRouter"].lower()
                      and address_topic(curve_log["topics"][2]) == sender, f"{phase} curve recipient mismatch")
                stock_spent, tokens_out, burned = words(curve_log["data"])
                gross = tokens_out + burned
                check(stock_spent > 0 and gross > 0, f"{phase} empty curve buy")
                detail.update({"stockSpentRaw": stock_spent, "tokensOutRaw": tokens_out,
                               "burnedRaw": burned, "effectiveBuyTaxBpsFloor": burned * 10000 // gross})
            if phase == "graduate":
                event(receipt, book["factory"], GRADUATED)
        result.append({"phase": phase, "sender": sender, "actionHash": hashes[-1],
                       "actionBlock": hexint(receipt["blockNumber"]), "actionTimestamp": hexint(block["timestamp"]),
                       "transactionHashes": hashes, "gasPaidWei": sum(
                           hexint(r["gasUsed"]) * hexint(r["effectiveGasPrice"]) for r in live_receipts), **detail})
    by_phase = {row["phase"]: row for row in result}
    check(by_phase["ordinaryBuy"]["actionTimestamp"] < by_phase["launch"]["actionTimestamp"] + 180,
          "ordinary buy outside opening window")
    check(by_phase["whitelistBuy"]["actionTimestamp"] < by_phase["launch"]["actionTimestamp"] + 180,
          "whitelist buy outside opening window")
    check(by_phase["ordinaryBuy"]["effectiveBuyTaxBpsFloor"] > 300,
          "ordinary buy did not pay opening surcharge")
    check(by_phase["whitelistBuy"]["effectiveBuyTaxBpsFloor"] in (299, 300)
          and by_phase["curveBuy"]["effectiveBuyTaxBpsFloor"] in (299, 300),
          "whitelist/creator did not retain base tax")
    launch_block = hex(by_phase["launch"]["actionBlock"])
    end_block = hex(by_phase["v4Sell"]["actionBlock"])
    def read(to, data, block):
        return rpc_batch(url, [("eth_call", [{"to": to, "data": data}, block])])[0]
    curve_word = read(book["factory"], "0x1bf7d749" + "0" * 64, launch_block)
    check("0x" + curve_word[-40:].lower() == curve.lower(), "factory strategy curve differs")
    whitelisted = {}
    for label, recipient in (("operator", OPERATOR), ("whitelisted", WHITELISTED),
                             ("creator", CREATOR), ("router", book["tradeRouter"].lower())):
        raw = read(curve, "0x7b3d64af" + recipient[2:].rjust(64, "0"), launch_block)
        whitelisted[label] = hexint(raw) == 1
    check(whitelisted == {"operator": False, "whitelisted": True, "creator": True, "router": False},
          "fixed launch exemption readback differs")
    check(hexint(read(curve, "0x200d2ed2", end_block)) == 2, "curve did not graduate")
    return {"schema": "v2-whitelist-journey-v1", "chainId": 46630,
            "featureVersion": book["featureVersion"], "factory": book["factory"],
            "strategyId": 0, "token": token, "curve": curve, "phases": result,
            "transactionCount": len(seen), "gasPaidWei": sum(row["gasPaidWei"] for row in result),
            "fixedExemptionsAtLaunch": whitelisted, "finalCurveStatus": 2,
            "openingTaxResult": "ordinary pays surcharge; whitelisted and creator pay only 300 bps base tax"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rpc", default=RPC)
    parser.add_argument("--book", type=Path, default=BOOK)
    parser.add_argument("--broadcast-dir", type=Path, default=BASE)
    parser.add_argument("--out", type=Path, default=REPORT)
    args = parser.parse_args()
    report = verify(args.rpc, json.loads(args.book.read_text()), args.broadcast_dir)
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(f"Verified {len(report['phases'])} phases, {report['transactionCount']} canonical transactions; "
          f"report: {args.out}")


if __name__ == "__main__":
    main()

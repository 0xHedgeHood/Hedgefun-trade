#!/usr/bin/env python3
"""Read-only Robinhood receipt audit and deterministic Anvil same-block replay.

No secret or signing code lives here. The local replay uses Anvil's impersonation only.
"""

import argparse
import json
import pathlib
import subprocess
import time
import urllib.request
from decimal import Decimal

ROOT = pathlib.Path(__file__).resolve().parents[1]
BOOK = json.loads((ROOT / "deploy/testnet-v2-whitelist.json").read_text())
WALLETS = json.loads((ROOT / "deploy/tsla-stress-wallets.json").read_text())["wallets"]
TX = json.loads((ROOT / "deploy/tsla-stress-transactions.json").read_text())
RPC_URL = "https://rpc.testnet.chain.robinhood.com"
CURVE = TX["curve"]
TOKEN = TX["token"]
STOCK = BOOK["stocks"]["TSLA"]["token"]
ROUTER = BOOK["tradeRouter"]
FACTORY = BOOK["factory"]
OPERATOR = BOOK["operator"]
FORK_BLOCK = 126441339
BEFORE_WHALE_BLOCK = 126441940
BLOCK_CACHE = {}


def rpc(url, method, params):
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    request = urllib.request.Request(url, payload, {
        "Content-Type": "application/json", "User-Agent": "cast/1.0"
    })
    for attempt in range(4):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                data = json.load(response)
            if "error" in data:
                raise RuntimeError(f"{method}: {data['error']}")
            return data["result"]
        except (OSError, TimeoutError):
            if attempt == 3:
                raise
            time.sleep(0.5 * (attempt + 1))


def cast(*args):
    return subprocess.check_output(["cast", *map(str, args)], text=True).strip()


def calldata(signature, *args):
    return cast("calldata", signature, *args)


def word(data, index=0):
    start = 2 + index * 64
    return int(data[start:start + 64], 16)


def call(url, to, signature, args=(), block="latest"):
    return rpc(url, "eth_call", [{"to": to, "data": calldata(signature, *args)}, block])


def uint(url, to, signature, args=(), block="latest"):
    return word(call(url, to, signature, args, block))


def address(url, to, signature, args=(), block="latest"):
    return "0x" + call(url, to, signature, args, block)[-40:]


def topic(signature):
    return cast("keccak", signature).lower()


def event_logs(url, emitter, from_block, to_block, topics):
    logs = []
    for start in range(from_block, to_block + 1, 1000):
        logs.extend(rpc(url, "eth_getLogs", [{
            "address": emitter, "fromBlock": hex(start),
            "toBlock": hex(min(start + 999, to_block)), "topics": topics,
        }]))
    return logs


def curve_reserve_from_events(url, from_block, to_block):
    buys = event_logs(url, CURVE, from_block, to_block,
                      [topic("Bought(address,address,uint256,uint256,uint256)")])
    sells = event_logs(url, CURVE, from_block, to_block,
                       [topic("Sold(address,address,uint256,uint256,uint256)")])
    stock_reserve = sum(word(log["data"]) for log in buys) - sum(
        word(log["data"], 1) + word(log["data"], 2) for log in sells)
    token_reserve = uint(url, CURVE, "initialSupply()") - sum(
        word(log["data"], 1) + word(log["data"], 2) for log in buys) + sum(
        word(log["data"]) for log in sells)
    return stock_reserve, token_reserve, buys, sells


def receipt_row(url, tx_hash):
    receipt = rpc(url, "eth_getTransactionReceipt", [tx_hash])
    if receipt is None:
        raise RuntimeError(f"missing canonical receipt: {tx_hash}")
    transaction = rpc(url, "eth_getTransactionByHash", [tx_hash])
    if transaction is None:
        raise RuntimeError(f"missing canonical transaction: {tx_hash}")
    block_number = receipt["blockNumber"]
    cache_key = (url, block_number)
    if cache_key not in BLOCK_CACHE:
        BLOCK_CACHE[cache_key] = rpc(url, "eth_getBlockByNumber", [block_number, False])
    canonical_block = BLOCK_CACHE[cache_key]
    assert canonical_block is not None
    assert receipt["transactionHash"].lower() == tx_hash.lower()
    assert transaction["hash"].lower() == tx_hash.lower()
    assert receipt["blockHash"].lower() == canonical_block["hash"].lower()
    assert transaction["blockHash"].lower() == canonical_block["hash"].lower()
    assert receipt["transactionIndex"] == transaction["transactionIndex"]
    return {
        "hash": tx_hash,
        "blockNumber": int(receipt["blockNumber"], 16),
        "transactionIndex": int(receipt["transactionIndex"], 16),
        "status": int(receipt["status"], 16),
        "gasUsed": int(receipt["gasUsed"], 16),
        "effectiveGasPriceWei": str(int(receipt["effectiveGasPrice"], 16)),
        "from": transaction["from"],
        "to": transaction["to"],
        "selector": transaction["input"][:10],
        "input": transaction["input"],
        "valueWei": str(int(transaction["value"], 16)),
        "logs": receipt["logs"],
    }


def find_event(row, emitter, signature):
    matches = [entry for entry in row["logs"]
               if entry["address"].lower() == emitter.lower()
               and entry["topics"][0].lower() == topic(signature)]
    return matches


def compact(row):
    return {key: value for key, value in row.items() if key not in {"logs", "input"}}


def router_params(row):
    data = row["input"]
    assert len(data) >= 10 + 8 * 64
    return {
        "id": int(data[10:74], 16),
        "asset": "0x" + data[10 + 64 + 24:10 + 128],
        "amountIn": int(data[10 + 128:10 + 192], 16),
        "expectedStage": int(data[10 + 6 * 64:10 + 7 * 64], 16),
        "allowPartialFill": bool(int(data[10 + 7 * 64:10 + 8 * 64], 16)),
    }


def assert_router_trade(row, who, verb, stage, status):
    signature = ("buy" if verb == "buy" else "sell") + (
        "((uint256,address,uint256,uint256,uint256,uint256,uint8,bool),(address,address)[])")
    # A tuple plus a dynamic path is the single router entry point used in this run.
    expected_selector = cast("sig", signature)
    assert row["from"].lower() == who.lower()
    assert row["to"].lower() == ROUTER.lower()
    assert row["selector"].lower() == expected_selector.lower()
    params = router_params(row)
    assert params["id"] == TX["strategyId"] and params["expectedStage"] == stage
    assert params["asset"].lower() == STOCK.lower() and params["amountIn"] > 0
    assert row["status"] == status
    if status == 1:
        event_sig = ("Bought(uint256,address,address,address,uint256,uint256,uint256)" if verb == "buy"
                     else "Sold(uint256,address,address,uint256,uint256,uint256)")
        events = find_event(row, ROUTER, event_sig)
        assert len(events) == 1
        assert int(events[0]["topics"][1], 16) == TX["strategyId"]
        assert ("0x" + events[0]["topics"][2][-40:]).lower() == who.lower()
        assert ("0x" + events[0]["topics"][3][-40:]).lower() == STOCK.lower()
        assert word(events[0]["data"], 2 if verb == "buy" else 1) > 0
    else:
        assert not row["logs"]
    return params


def trace_error(tx_hash, expected_selector):
    replay = subprocess.run(["cast", "run", tx_hash, "--rpc-url", RPC_URL],
                            capture_output=True, text=True, timeout=90)
    output = replay.stdout + replay.stderr
    assert f"custom error {expected_selector.lower()}" in output.lower(), tx_hash


def audit(output):
    assert BOOK["chainId"] == TX["chainId"] == 46630
    assert len(WALLETS) == len({wallet.lower() for wallet in WALLETS}) == 12
    listed_hashes = [h for name, value in TX.items()
                     if name not in {"chainId", "strategyId", "curve", "token"}
                     for h in (value if isinstance(value, list) else [value])]
    assert len(listed_hashes) == len({h.lower() for h in listed_hashes})
    assert all(len(h) == 66 and h.startswith("0x") for h in listed_hashes)
    assert int(rpc(RPC_URL, "eth_chainId", []), 16) == 46630
    audit_block = int(rpc(RPC_URL, "eth_blockNumber", []), 16)
    audit_tag = hex(audit_block)
    strategy = call(RPC_URL, FACTORY, "strategies(uint256)", (TX["strategyId"],), audit_tag)
    assert ("0x" + strategy[26:66]).lower() == TOKEN.lower()
    assert ("0x" + strategy[26 + 3 * 64:66 + 3 * 64]).lower() == STOCK.lower()
    assert address(RPC_URL, FACTORY, "curves(uint256)", (TX["strategyId"],), audit_tag).lower() == CURVE.lower()
    assert address(RPC_URL, CURVE, "factory()", block=audit_tag).lower() == FACTORY.lower()
    assert address(RPC_URL, CURVE, "token()", block=audit_tag).lower() == TOKEN.lower()
    assert address(RPC_URL, CURVE, "stock()", block=audit_tag).lower() == STOCK.lower()
    rows = {}
    for group, hashes in TX.items():
        if group in {"chainId", "strategyId", "curve", "token"}:
            continue
        rows[group] = ([receipt_row(RPC_URL, h) for h in hashes] if isinstance(hashes, list)
                       else receipt_row(RPC_URL, hashes))
    assert all((r["blockNumber"] <= audit_block) for value in rows.values()
               for r in (value if isinstance(value, list) else [value]))

    def all_ok(group):
        assert all(row["status"] == 1 for row in rows[group]), group

    for group in ("launch", "funding", "drips", "openingBuys", "retryBuys"):
        all_ok(group)
    assert len(rows["funding"]) == len(rows["drips"]) == 12
    for i in range(12):
        funding, drip = rows["funding"][i], rows["drips"][i]
        assert funding["from"].lower() == OPERATOR.lower()
        assert funding["to"].lower() == WALLETS[i].lower()
        assert funding["valueWei"] == "100000000000000"
        assert funding["selector"] == "0x"
        assert drip["from"].lower() == WALLETS[i].lower()
        assert drip["to"].lower() == STOCK.lower()
        assert drip["selector"] == cast("sig", "drip()")
        transfers = find_event(drip, STOCK, "Transfer(address,address,uint256)")
        assert len(transfers) == 1
        assert ("0x" + transfers[0]["topics"][2][-40:]).lower() == WALLETS[i].lower()
    registry = BOOK["curveDeployer"]
    creator = address(RPC_URL, CURVE, "creator()", block=audit_tag)
    for i, row in enumerate(rows["launch"]):
        assert row["status"] == 1 and row["from"].lower() == creator.lower()
        expected_to = (registry, registry, BOOK["usdg"], FACTORY)[i]
        assert row["to"].lower() == expected_to.lower()
    assert [row["selector"] for row in rows["launch"]] == [
        cast("sig", "setCurveConfig(string,uint96,uint16,uint8)"),
        cast("sig", "setOpeningTaxExemptions(string,uint96,address[])"),
        cast("sig", "approve(address,uint256)"),
        cast("sig", "launch((string,string,address,address,uint16,uint16,uint32,uint32,uint16,uint16,uint16,uint16,uint96,uint256,uint256),bytes32)"),
    ]
    launched = find_event(rows["launch"][-1], FACTORY,
                          "CurveLaunched(uint256,address,uint16,uint256)")
    assert len(launched) == 1 and int(launched[0]["topics"][1], 16) == TX["strategyId"]
    fixed = find_event(rows["launch"][-1], CURVE, "OpeningTaxExemptionsFixed(address[])")
    assert len(fixed) == 1 and word(fixed[0]["data"], 1) == 1
    assert ("0x" + fixed[0]["data"][-40:]).lower() == WALLETS[0].lower()
    assert address(RPC_URL, CURVE, "openingTaxExemptions(uint256)", (0,), audit_tag).lower() == WALLETS[0].lower()
    assert uint(RPC_URL, CURVE, "isOpeningTaxExempt(address)", (WALLETS[0],), audit_tag) == 1
    assert uint(RPC_URL, CURVE, "isOpeningTaxExempt(address)", (WALLETS[1],), audit_tag) == 0
    assert uint(RPC_URL, CURVE, "isOpeningTaxExempt(address)", (creator,), audit_tag) == 1
    assert len(rows["concurrentBuys"]) == 10
    for i, row in enumerate(rows["openingBuys"]):
        assert_router_trade(row, WALLETS[i], "buy", 0, 1)
    for i, row in enumerate(rows["concurrentBuys"], start=2):
        assert_router_trade(row, WALLETS[i], "buy", 0, 0 if i in (6, 11) else 1)
    concurrent_blocks = {row["blockNumber"] for row in rows["concurrentBuys"]}
    assert concurrent_blocks == set(range(126441734, 126441738))
    for i, row in zip((6, 11), rows["retryBuys"]):
        assert_router_trade(row, WALLETS[i], "buy", 0, 1)
    assert_router_trade(rows["whaleSale"], WALLETS[2], "sell", 0, 1)
    assert_router_trade(rows["staleSaleRevert"], WALLETS[3], "sell", 0, 0)
    assert_router_trade(rows["requotedSale"], WALLETS[3], "sell", 0, 1)
    assert_router_trade(rows["graduation"], creator, "buy", 0, 1)
    v4_buy_params = assert_router_trade(rows["v4Buy"], WALLETS[5], "buy", 2, 1)
    v4_sell_params = assert_router_trade(rows["v4Sell"], WALLETS[5], "sell", 2, 1)
    assert v4_buy_params["allowPartialFill"] and v4_sell_params["allowPartialFill"]
    strict_buy = assert_router_trade(rows["strictV4Buy"], WALLETS[5], "buy", 2, 1)
    strict_sell = assert_router_trade(rows["strictV4Sell"], WALLETS[5], "sell", 2, 1)
    assert not strict_buy["allowPartialFill"] and not strict_sell["allowPartialFill"]
    for name, is_buy in (("v4Buy", True), ("v4Sell", False),
                         ("strictV4Buy", True), ("strictV4Sell", False)):
        row = rows[name]
        event_sig = ("Bought(uint256,address,address,address,uint256,uint256,uint256)" if is_buy
                     else "Sold(uint256,address,address,uint256,uint256,uint256)")
        events = find_event(row, ROUTER, event_sig)
        assert len(events) == 1
        assert word(events[0]["data"], 3 if is_buy else 2) == 0  # no refund
        assert word(events[0]["data"], 1 if is_buy else 0) == router_params(row)["amountIn"]
    assert_router_trade(rows["staleStageRevert"], WALLETS[5], "buy", 0, 0)
    assert rows["graduation"]["blockNumber"] < rows["v4Buy"]["blockNumber"]
    assert rows["graduation"]["blockNumber"] < rows["staleStageRevert"]["blockNumber"]
    for row in (rows["concurrentBuys"][4], rows["concurrentBuys"][9], rows["staleSaleRevert"]):
        trace_error(row["hash"], cast("sig", "TooLittle(uint256)"))
    trace_error(rows["staleStageRevert"]["hash"], cast("sig", "StageChanged(uint8)"))
    for name, expected in (("whaleSale", 1), ("staleSaleRevert", 0),
                           ("requotedSale", 1), ("graduation", 1),
                           ("v4Buy", 1), ("v4Sell", 1),
                           ("strictV4Buy", 1), ("strictV4Sell", 1),
                           ("staleStageRevert", 0)):
        assert rows[name]["status"] == expected, name
    opening = rows["openingBuys"]
    assert opening[0]["blockNumber"] == opening[1]["blockNumber"]
    assert {opening[0]["transactionIndex"], opening[1]["transactionIndex"]} == {1, 2}

    curve_bought = "Bought(address,address,uint256,uint256,uint256)"
    opening_tax = []
    for row in opening:
        event = find_event(row, CURVE, curve_bought)
        assert len(event) == 1
        stock_spent, tokens_out, burned = (word(event[0]["data"], i) for i in range(3))
        opening_tax.append({"recipient": "0x" + event[0]["topics"][2][-40:],
                            "stockSpentWei": str(stock_spent), "tokensOut": str(tokens_out),
                            "burned": str(burned),
                            "effectiveBurnBps": str(Decimal(burned) * 10000 / (tokens_out + burned))})
    assert opening_tax[0]["recipient"].lower() == WALLETS[0].lower()
    assert opening_tax[1]["recipient"].lower() == WALLETS[1].lower()
    assert Decimal(opening_tax[0]["effectiveBurnBps"]) <= 300
    assert Decimal(opening_tax[1]["effectiveBurnBps"]) > 300

    # Robinhood's public RPC may prune old state while retaining canonical logs/receipts.
    # Rebuild the historical balance snapshot from all strategy-token Transfer events.
    launch_block = rows["launch"][-1]["blockNumber"]
    transfers_before = event_logs(RPC_URL, TOKEN, launch_block, BEFORE_WHALE_BLOCK,
                                  [topic("Transfer(address,address,uint256)")])
    balances = {wallet.lower(): 0 for wallet in WALLETS}
    zero = "0x" + "0" * 40
    burned_before = 0
    for entry in transfers_before:
        sender = "0x" + entry["topics"][1][-40:]
        recipient = "0x" + entry["topics"][2][-40:]
        amount = word(entry["data"])
        if sender.lower() in balances:
            balances[sender.lower()] -= amount
        if recipient.lower() in balances:
            balances[recipient.lower()] += amount
        if recipient.lower() == zero:
            burned_before += amount
    holdings = [balances[wallet.lower()] for wallet in WALLETS]
    assert all(amount >= 0 for amount in holdings)
    participant = sum(holdings)
    whale = sum(holdings[2:5])
    initial_supply = uint(RPC_URL, CURVE, "initialSupply()", block=audit_tag)
    supply_before = initial_supply - burned_before
    snap_stock, snap_token, snap_buys, snap_sells = curve_reserve_from_events(
        RPC_URL, launch_block, BEFORE_WHALE_BLOCK)
    assert 100 * whale >= 80 * participant
    assert len(snap_buys) == 12 and not snap_sells
    assert snap_stock == 15100000000000000000
    before_whale = {
        "blockNumber": BEFORE_WHALE_BLOCK,
        "walletTokenBalances": list(map(str, holdings)),
        "participantWalletHoldings": str(participant),
        "threeWhaleHoldings": str(whale),
        "threeWhalePctParticipantWallets": str(Decimal(whale) * 100 / participant),
        "threeWhalePctTotalSupply": str(Decimal(whale) * 100 / supply_before),
        "totalSupply": str(supply_before),
        "curveRealStockReserveWei": str(snap_stock),
        "curveTokenReserve": str(snap_token),
        "curveFeeLiabilityWei": "0",
        "reconstruction": "canonical Transfer/Bought/Sold logs through block",
    }

    stale = rows["staleSaleRevert"]
    assert stale["status"] == 0 and not stale["logs"]

    graduation = rows["graduation"]
    bought = find_event(graduation, CURVE, curve_bought)
    assert len(bought) == 1
    cap_spent = word(bought[0]["data"])
    cap = uint(RPC_URL, CURVE, "terminalStock()", block=audit_tag) - uint(
        RPC_URL, CURVE, "virtualStock()", block=audit_tag)
    pre_reserve, _, _, _ = curve_reserve_from_events(
        RPC_URL, launch_block, graduation["blockNumber"] - 1)
    assert cap_spent == cap - pre_reserve
    router_bought = find_event(graduation, ROUTER,
                               "Bought(uint256,address,address,address,uint256,uint256,uint256)")
    assert len(router_bought) == 1
    assert word(router_bought[0]["data"], 3) == 0  # stockRefund
    graduated = find_event(graduation, FACTORY,
                           "Graduated(uint256,uint160,uint128,uint256,uint256,uint256)")
    split = find_event(graduation, FACTORY,
                       "GraduationCapitalSplit(uint256,uint256,uint256,bool)")
    assert len(graduated) == len(split) == 1
    liquidity, stock_seeded, token_seeded, token_burned = (
        word(graduated[0]["data"], i) for i in (1, 2, 3, 4))
    lp_stock, treasury_stock, booked = (word(split[0]["data"], i) for i in range(3))
    assert stock_seeded == lp_stock and stock_seeded + treasury_stock == cap
    assert liquidity > 0 and booked == 1

    treasury = address(RPC_URL, CURVE, "treasury()", block=audit_tag)
    vault = address(RPC_URL, treasury, "liquidityVault()", block=audit_tag)
    protocol = address(RPC_URL, CURVE, "protocol()", block=audit_tag)
    creator = address(RPC_URL, CURVE, "creator()", block=audit_tag)
    fees = uint(RPC_URL, CURVE, "totalFees()", block=audit_tag)
    claims = {who: uint(RPC_URL, CURVE, "claimable(address)", (who,), audit_tag)
              for who in (protocol, creator, treasury)}
    assert sum(claims.values()) == fees
    assert uint(RPC_URL, STOCK, "balanceOf(address)", (CURVE,), audit_tag) == fees
    assert uint(RPC_URL, CURVE, "realStockReserve()", block=audit_tag) == 0
    assert uint(RPC_URL, CURVE, "tokenReserve()", block=audit_tag) == 0
    assert uint(RPC_URL, CURVE, "status()", block=audit_tag) == 2
    assert uint(RPC_URL, vault, "seeded()", block=audit_tag) == 1
    assert uint(RPC_URL, STOCK, "balanceOf(address)", (ROUTER,), audit_tag) == 0
    assert uint(RPC_URL, TOKEN, "balanceOf(address)", (ROUTER,), audit_tag) == 0
    assert uint(RPC_URL, TOKEN, "balanceOf(address)", (CURVE,), audit_tag) == 0

    initial = uint(RPC_URL, CURVE, "initialSupply()", block=audit_tag)
    current = uint(RPC_URL, TOKEN, "totalSupply()", block=audit_tag)
    transfer_topic = topic("Transfer(address,address,uint256)")
    zero_topic = "0x" + "0" * 64
    burns = []
    for start in range(rows["launch"][-1]["blockNumber"], audit_block + 1, 1000):
        burns.extend(rpc(RPC_URL, "eth_getLogs", [{
            "address": TOKEN, "fromBlock": hex(start), "toBlock": hex(min(start + 999, audit_block)),
            "topics": [transfer_topic, None, zero_topic]
        }]))
    burned_total = sum(word(log["data"]) for log in burns)
    assert initial - current == burned_total

    report = {
        "chainId": 46630, "rpc": RPC_URL, "auditBlock": audit_block,
        "strategyId": TX["strategyId"], "curve": CURVE, "token": TOKEN, "stock": STOCK,
        "receipts": {name: ([compact(row) for row in value] if isinstance(value, list) else compact(value))
                     for name, value in rows.items()},
        "openingTax": opening_tax,
        "concentrationBeforeWhaleSale": before_whale,
        "graduation": {
            "fullRaiseStockWei": str(cap), "preFinalBuyReserveWei": str(pre_reserve),
            "finalInputStockWei": str(cap_spent), "stockRefundWei": "0",
            "v4Liquidity": str(liquidity), "lpStockSeededWei": str(stock_seeded),
            "lpTokenSeeded": str(token_seeded), "treasuryStockWei": str(treasury_stock),
            "tokenBurnedAtGraduation": str(token_burned), "treasuryBooked": True,
        },
        "finalAccounting": {
            "status": 2, "vault": vault, "vaultSeeded": True,
            "curveRealStockReserveWei": "0", "curveTokenReserve": "0",
            "curveStockBalanceWei": str(fees), "curveTotalFeeLiabilityWei": str(fees),
            "claimsWei": {who: str(amount) for who, amount in claims.items()},
            "initialTokenSupply": str(initial), "currentTokenSupply": str(current),
            "burnTransferSum": str(burned_total), "burnEventCount": len(burns),
            "routerStockBalanceWei": "0", "routerTokenBalance": "0",
            "poolManagerStockBalanceWei": str(uint(RPC_URL, STOCK, "balanceOf(address)", (BOOK["poolManager"],), audit_tag)),
            "treasuryStockBalanceWei": str(uint(RPC_URL, STOCK, "balanceOf(address)", (treasury,), audit_tag)),
        },
        "checks": {
            "allListedReceiptsCanonical": True, "fundingAndDripsTwelveWallets": True,
            "openingBuyPairSamePublicBlock": True,
            "tenConcurrentBuysAcrossBlocksWithTwoStaleFloorReverts": True,
            "threeWhalesAtLeastEightyPercentOfTwelveWalletHoldings": True,
            "staleSaleRevertAtomic": True, "exactFinalBuyGraduatedWithoutRefund": True,
            "v4BuyAndSellSucceeded": True, "staleActiveStageRejected": True,
            "curveFeesEqualClaimsAndStockBalance": True,
            "tokenSupplyEqualsInitialLessBurnEvents": True,
            "routerHasNoStockOrStrategyTokenResidue": True,
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n")
    print(f"audited {sum(len(v) if isinstance(v, list) else 1 for v in rows.values())} receipts; report {output}")


def local_replay(output, port):
    url = f"http://127.0.0.1:{port}"
    process = subprocess.Popen([
        "anvil", "--accounts", "0", "--silent", "--fork-url", RPC_URL,
        "--fork-block-number", str(FORK_BLOCK), "--chain-id", "46630",
        "--port", str(port), "--no-mining", "--auto-impersonate"
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(30):
            if process.poll() is not None:
                raise RuntimeError("Anvil fork failed to start")
            try:
                if int(rpc(url, "eth_chainId", []), 16) == 46630:
                    break
            except OSError:
                time.sleep(0.2)
        else:
            raise RuntimeError("Anvil fork did not become ready")
        assert int(rpc(url, "eth_blockNumber", []), 16) == FORK_BLOCK
        launched = uint(url, CURVE, "launchedAt()")
        rpc(url, "evm_setNextBlockTimestamp", [launched + 181])

        def send(sender, to, data, gas):
            return rpc(url, "eth_sendTransaction", [{
                "from": sender, "to": to, "data": data, "gas": hex(gas)
            }])

        for i in range(2, 12):
            rpc(url, "anvil_setBalance", [WALLETS[i], hex(10**17)])
        buys = []
        for i in range(2, 12):
            amount = 45 * 10**17 if i < 5 else 2 * 10**17
            params = f"(1,{STOCK},{amount},{amount},1,{launched + 1000},0,true)"
            h = send(WALLETS[i], ROUTER, calldata(
                "buy((uint256,address,uint256,uint256,uint256,uint256,uint8,bool),(address,address)[])",
                params, "[]"), 1_300_000)
            buys.append((i, h))
        rpc(url, "anvil_mine", [1])
        buy_rows = [compact(receipt_row(url, h)) | {"walletIndex": i} for i, h in buys]
        assert all(row["status"] == 1 for row in buy_rows)
        assert all(row["from"].lower() == WALLETS[row["walletIndex"]].lower()
                   and row["to"].lower() == ROUTER.lower() for row in buy_rows)
        assert len({row["blockNumber"] for row in buy_rows}) == 1
        assert [row["transactionIndex"] for row in buy_rows] == list(range(10))

        approvals = []
        amounts = []
        for i in range(2, 12):
            amount = uint(url, TOKEN, "balanceOf(address)", (WALLETS[i],)) // 4
            amounts.append((i, amount))
            approvals.append(send(WALLETS[i], TOKEN,
                                  calldata("approve(address,uint256)", ROUTER, amount), 100_000))
        rpc(url, "anvil_mine", [1])
        assert all(int(rpc(url, "eth_getTransactionReceipt", [h])["status"], 16) == 1
                   for h in approvals)
        sells = []
        for i, amount in amounts:
            params = f"(1,{STOCK},{amount},0,1,{launched + 1000},0,false)"
            h = send(WALLETS[i], ROUTER, calldata(
                "sell((uint256,address,uint256,uint256,uint256,uint256,uint8,bool),(address,address)[])",
                params, "[]"), 600_000)
            sells.append((i, h))
        rpc(url, "anvil_mine", [1])
        sell_rows = [compact(receipt_row(url, h)) | {"walletIndex": i} for i, h in sells]
        assert all(row["status"] == 1 for row in sell_rows)
        assert all(row["from"].lower() == WALLETS[row["walletIndex"]].lower()
                   and row["to"].lower() == ROUTER.lower() for row in sell_rows)
        assert len({row["blockNumber"] for row in sell_rows}) == 1
        assert [row["transactionIndex"] for row in sell_rows] == list(range(10))
        report = {
            "kind": "local-anvil-fork-only", "forkBlock": FORK_BLOCK,
            "forkChainId": 46630, "buyBatch": buy_rows, "sellBatch": sell_rows,
            "postSellCurveStockReserveWei": str(uint(url, CURVE, "realStockReserve()")),
            "checks": {"tenDistinctSenderBuysOneBlock": True,
                       "tenDistinctSenderSellsOneBlock": True,
                       "allTwentyTransactionsSucceeded": True},
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2) + "\n")
        print(f"local same-block buy/sell replay passed; report {output}")
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()


def main():
    if not __debug__:
        raise RuntimeError("Audit assertions must be enabled; do not run with python -O/PYTHONOPTIMIZE")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("audit", "local"))
    parser.add_argument("--output", type=pathlib.Path)
    parser.add_argument("--port", type=int, default=8548)
    args = parser.parse_args()
    if args.mode == "audit":
        audit(args.output or ROOT / "deploy/tsla-stress-report.json")
    else:
        local_replay(args.output or ROOT / "deploy/tsla-stress-local.json", args.port)


if __name__ == "__main__":
    main()

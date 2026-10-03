#!/usr/bin/env python3
"""Read-only current testnet listing/depth snapshot; never signs transactions.

Constant-active-liquidity quotes are explicitly local analytical estimates, not
RPC-executed swaps. The separate fork harness supplies actual V3 swap probes at
the historical starting Close. Equity-market volume is never used as pool depth.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal, getcontext
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
CAST = ROOT / ".local/bin/cast"
Q96 = 2**96
getcontext().prec = 90
D = Decimal
USDG = "0x539574139BB4Ac74Fd2183ba0E38ed777E30B58d"


def require(value, message):
    if not value:
        raise ValueError(message)


def words(raw):
    require(raw.startswith("0x") and (len(raw)-2) % 64 == 0, "malformed ABI response")
    return [int(raw[index:index+64], 16) for index in range(2, len(raw), 64)]


def address(value):
    return f"0x{value:040x}"


def signed(value):
    return value - 2**256 if value >= 2**255 else value


def display(value, places=18):
    return format(D(value), f".{places}f").rstrip("0").rstrip(".") or "0"


def spot_usd(sqrt_price, stock_is_zero):
    ratio = D(sqrt_price)**2 / D(Q96)**2
    return ratio * 10**12 if stock_is_zero else D(10)**12 / ratio


def analytical_swap(sqrt_price: int, liquidity: int, amount: int, fee: int, zero_for_one: bool):
    require(amount > 0 and liquidity > 0 and 0 <= fee < 1_000_000, "invalid swap estimate inputs")
    net = amount * (1_000_000-fee) // 1_000_000
    if zero_for_one:
        numerator = liquidity * Q96 * sqrt_price
        denominator = liquidity * Q96 + net * sqrt_price
        after = (numerator + denominator - 1) // denominator
        out = liquidity * (sqrt_price-after) // Q96
    else:
        after = sqrt_price + net * Q96 // liquidity
        out = liquidity * Q96 * (after-sqrt_price) // after // sqrt_price
    require(out > 0, "zero estimated output")
    return after, out


def capture(rpc: str, book: dict, block: int | None = None,
            archive_rpc: str = "https://robinhood-testnet.drpc.org") -> dict:
    def cast(*args):
        return subprocess.check_output([str(CAST), *map(str, args)], text=True, timeout=40).strip()

    if block is None:
        block = int(cast("block-number", "--rpc-url", rpc))
    chain = int(cast("chain-id", "--rpc-url", rpc))
    require(chain == 46630, "testnet chain required")
    header = json.loads(cast("block", block, "--json", "--rpc-url", rpc))
    listing_from_block = book["block"]
    archive_start = json.loads(cast("block", listing_from_block, "--json", "--rpc-url", archive_rpc))
    canonical_start = json.loads(cast("block", listing_from_block, "--json", "--rpc-url", rpc))
    require(archive_start["hash"] == canonical_start["hash"], "archive history start is not canonical")
    require(cast("code", book["factory"], "--block", listing_from_block, "--rpc-url", archive_rpc) == "0x",
            "listing history must start before the factory deployment")
    listing_events = json.loads(cast("logs", "--address", book["factory"], "--from-block", listing_from_block,
                                    "--to-block", block, "--json", "--rpc-url", rpc,
                                    "Listed(address,address,address,uint256,bool)"))
    final_listings = {}
    for event in listing_events:
        require(not event["removed"], "removed listing event")
        final_listings[address(int(event["topics"][1], 16))] = bool(words(event["data"])[3])
    require({stock for stock, enabled in final_listings.items() if enabled} ==
            {row["token"].lower() for row in book["stocks"].values()}, "enabled event-derived factory universe differs from address book")

    def read(target, sig, *args):
        return words(cast("call", target, sig, *args, "--block", block, "--rpc-url", rpc))

    market = book["market"]
    pool_count = read(market, "poolCount()")[0]
    market_pools = [address(read(market, "pools(uint256)", index)[0]) for index in range(pool_count)]

    def describe(item):
        ticker, stock = item
        pool, token = stock["pool"], stock["token"]
        listing = read(book["factory"], "listings(address)", token)
        require(address(listing[0]).lower() == stock["oracle"].lower() and address(listing[1]).lower() == pool.lower(), "factory listing differs from book")
        require(listing[3] == 1, "disabled factory listing")
        slot = read(pool, "slot0()")
        liquidity = read(pool, "liquidity()")[0]
        fee = read(pool, "fee()")[0]
        token0, token1 = address(read(pool, "token0()")[0]), address(read(pool, "token1()")[0])
        require({token0.lower(), token1.lower()} == {token.lower(), USDG.lower()}, "pool token pair mismatch")
        stock_zero = token0.lower() == token.lower()
        line = read(market, "lines(address)", pool)
        require(address(line[0]).lower() == token.lower() and bool(line[2]) == stock_zero, "market line binding mismatch")
        lower, upper = signed(line[4]), signed(line[5])
        encoded = "0x" + market[2:].lower() + f"{lower % 2**24:06x}" + f"{upper % 2**24:06x}"
        position_key = cast("keccak", encoded)
        position = read(pool, "positions(bytes32)", position_key)
        require(position[0] == liquidity > 0, "current active depth differs from seeded position")
        price = spot_usd(slot[0], stock_zero)
        min_sqrt = int(D("1.0001")**(D(lower)/2) * Q96)
        max_sqrt = int(D("1.0001")**(D(upper)/2) * Q96)
        probes = []
        for dollars in (100, 1000, 2000):
            for buying in (True, False):
                amount = dollars * 10**6 if buying else int(D(dollars) / price * 10**18)
                zero_for_one = not stock_zero if buying else stock_zero
                after, out = analytical_swap(slot[0], liquidity, amount, fee, zero_for_one)
                require(min_sqrt < after < max_sqrt, "analytical quote exits the known seeded range")
                output_mark = D(out) / 10**18 * price if buying else D(out) / 10**6
                input_mark = D(amount) / 10**6 if buying else D(amount) / 10**18 * price
                after_price = spot_usd(after, stock_zero)
                probes.append({"direction": "USDG_to_stock" if buying else "stock_to_USDG", "notionalUsd": dollars,
                               "inputRaw": str(amount), "estimatedOutputRaw": str(out),
                               "inputUsdAtInitialSpot": display(input_mark), "outputUsdAtInitialSpot": display(output_mark),
                               "estimatedFeeAndAverageImpactBps": display((1-output_mark/input_mark)*10000, 12),
                               "estimatedPostSwapSpotChangeBps": display((after_price/price-1)*10000, 12),
                               "staysInsideKnownPositionRange": True})
        stock_balance = read(token, "balanceOf(address)", pool)[0]
        usd_balance = read(USDG, "balanceOf(address)", pool)[0]
        return ticker, {"stock": token, "pool": pool, "oracle": stock["oracle"], "enabled": True,
                        "feeMillionths": fee, "stockIsToken0": stock_zero, "token0": token0, "token1": token1,
                        "sqrtPriceX96": str(slot[0]), "tick": signed(slot[1]), "activeLiquidity": str(liquidity),
                        "seedPositionLiquidity": str(position[0]), "seedTickLower": lower, "seedTickUpper": upper,
                        "stockSpotUsd": display(price), "poolStockBalanceRaw": str(stock_balance), "poolUsdgBalanceRaw": str(usd_balance),
                        "constantActiveLiquidityProbes": probes}

    with ThreadPoolExecutor(max_workers=4) as executor:
        pool_rows = dict(executor.map(describe, book["stocks"].items()))
    known = {row["pool"].lower() for row in pool_rows.values()}
    extra = []
    for pool in market_pools:
        if pool.lower() in known:
            continue
        line = read(market, "lines(address)", pool)
        stock = address(line[0])
        listing = read(book["factory"], "listings(address)", stock)
        extra.append({"pool": pool, "stock": stock, "factoryListingEnabled": bool(listing[3]),
                      "factoryListingPool": address(listing[1])})
    require(not any(row["factoryListingEnabled"] for row in extra), "extra enabled market listing requires universe review")
    return {"schema": "hedgefun-equity-depth-snapshot-v1", "chainId": chain, "block": block,
            "blockHash": header["hash"], "blockTimestamp": header["timestamp"], "rpc": rpc,
            "factory": book["factory"], "market": market, "marketPoolCount": pool_count,
            "universeProof": "Listed events are replayed from a block where the factory had no code through the pinned block; their final enabled set exactly matches the eight verified address-book listings. All registered market pools were also enumerated; extra stock lines are not enabled in this factory.",
            "listingHistoryFromBlock": listing_from_block, "factoryCodeAtListingHistoryStart": "0x", "listingEvents": listing_events,
            "listingHistoryArchiveRpc": archive_rpc, "listingHistoryStartBlockHash": canonical_start["hash"],
            "extraRegisteredMarketPools": extra,
            "measurementDefinition": "Pinned onchain state plus constant-active-liquidity exact-input algebra, including V3 fees. Quotes are analytical and assume no initialized tick crossed within the local seeded range; this is not a live eth_call swap execution. Historical fork actual probes are separately reported.",
            "notEquityVolume": "Nasdaq/NYSE daily dollar turnover never determines these pool-depth estimates.", "pools": pool_rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rpc", default="https://rpc.testnet.chain.robinhood.com")
    parser.add_argument("--archive-rpc", default="https://robinhood-testnet.drpc.org")
    parser.add_argument("--block", type=int)
    parser.add_argument("--book", type=Path, default=ROOT / "contractV2/deploy/testnet-v2-fresh-creator.json")
    parser.add_argument("--output", type=Path, default=ROOT / "contractV2/data/equity-depth-snapshot.json")
    args = parser.parse_args()
    book = json.loads(args.book.read_text())
    result = capture(args.rpc, book, args.block, args.archive_rpc)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"block": result["block"], "pools": len(result["pools"]), "marketPoolCount": result["marketPoolCount"],
                      "outputSha256": hashlib.sha256(args.output.read_bytes()).hexdigest()}, indent=2))


if __name__ == "__main__":
    main()

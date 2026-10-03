#!/usr/bin/env python3
"""Find the Chainlink round that CoveredCallDesk.settle() needs: the LAST round of a feed with updatedAt <= expiry.

    python3 tools/cc_round_at.py <feed> <expiry-unix> [--rpc URL]
    python3 tools/cc_round_at.py 0x379EC4f7C378F34a1B47E4F3cbeBCbAC3E8E9F15 1790366400

Prints the round id (decimal, what `settle(id, roundId)` takes), its answer and time, and the next round's time.
Binary-searches the feed's current phase only; if the expiry falls before the phase's first round, it says so and
you settle by agreement instead. Standard library only.
"""
import argparse
import datetime
import json
import sys
import time
import urllib.error
import urllib.request

RPC = "https://rpc.mainnet.chain.robinhood.com"
GET_ROUND = "0x9a6fc8f5"   # getRoundData(uint80)
LATEST = "0xfeaf968c"      # latestRoundData()


def call(rpc, to, data):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "eth_call", "params": [{"to": to, "data": data}, "latest"]})
    # the public RPC refuses urllib's default User-Agent, and rate-limits bursts (429): back off and retry
    req = urllib.request.Request(rpc, body.encode(), {"Content-Type": "application/json", "User-Agent": "cc-round-at/1"})
    for attempt in range(6):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                out = json.load(r)
            break
        except urllib.error.HTTPError as e:
            if e.code != 429 or attempt == 5:
                raise
            time.sleep(1.5 * (attempt + 1))
    if "error" in out:
        return None
    return out["result"]


def decode(ret):
    words = [int(ret[2 + 64 * i: 2 + 64 * (i + 1)], 16) for i in range(5)]
    answer = words[1] - (1 << 256) if words[1] >> 255 else words[1]
    return words[0], answer, words[3]   # roundId, answer, updatedAt


def round_data(rpc, feed, rid):
    ret = call(rpc, feed, GET_ROUND + format(rid, "064x"))
    return decode(ret) if ret else (0, 0, 0)


def utc(t):
    return datetime.datetime.fromtimestamp(t, datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("feed")
    ap.add_argument("expiry", type=int)
    ap.add_argument("--rpc", default=RPC)
    a = ap.parse_args()

    latest_id, _, latest_t = decode(call(a.rpc, a.feed, LATEST))
    phase, top = latest_id >> 64, latest_id & ((1 << 64) - 1)
    if latest_t <= a.expiry:
        rid = latest_id
    else:
        lo, hi = 1, top   # find the last aggregator round with t <= expiry
        if round_data(a.rpc, a.feed, (phase << 64) | lo)[2] > a.expiry:
            sys.exit("expiry predates this phase's first round: settle by agreement (proposePrice / acceptPrice)")
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if round_data(a.rpc, a.feed, (phase << 64) | mid)[2] <= a.expiry:
                lo = mid
            else:
                hi = mid - 1
        rid = (phase << 64) | lo

    _, ans, t = round_data(a.rpc, a.feed, rid)
    _, _, tn = round_data(a.rpc, a.feed, rid + 1)
    print(f"roundId   {rid}   (phase {rid >> 64}, round {rid & ((1 << 64) - 1)})")
    print(f"answer    {ans}   at {utc(t)}  ({a.expiry - t}s before expiry)")
    print(f"next      {utc(tn) if tn else 'none yet'}")
    if a.expiry - t > 26 * 3600:
        print("WARNING: older than MAX_SETTLEMENT_AGE (26h) at expiry -- settle() will refuse it; agree a price instead")


if __name__ == "__main__":
    main()

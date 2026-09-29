#!/usr/bin/env python3
"""Pre-launch check for Hedgefun V2 listings and for a creator's proposed raise.

Two rules from the V2 audits exist only as runbook steps, and pool depth on this chain moves ~30% in half an
hour, so they are re-measured here, on a fork of the live chain, immediately before a launch:

  Rule 1 (round 3 M-2 / M-3). A V2 raise buys the listed stock with USDG through that stock's V3 pool, and the
  curve's graduation target Rg is fixed in STOCK units at launch:
      V   = ceil(openPriceE18 * supply / 1e18)                  HedgeFunV2Factory._curveInit
      Tmin = floor(supply * (10000 - saleBps) / 10000)          HedgeFunBondingCurve.minTokenReserve
      Yg  = ceil(supply * V / Tmin)                             HedgeFunBondingCurve.terminalStock
      Rg  = Yg - V
    (a) the pool can deliver Rg at all -- a curve that cannot graduate strands its buyers with no refund;
    (b) one buyer taking Rg out of the pool leaves every treasury on it able to trade: the post-trade spot is
        within maxDeviationBps of the Chainlink oracle AND its tick within maxDeviationBps of the pool's 600 s mean
        (PoolTrader._health, the gate the treasuries run).
  Rule 2 (round 4 M4-1). On a 0.05% (fee 500) pool, sellChunkUsdg <= 10% of the pool's USDG depth per 1% move,
  in the thinner of the two directions.

saleBps is each creator's own choice (CurveDeployer.setCurveConfig, 1000..9000); a launch that registers none gets
CurveDeployer.DEFAULT_SALE_BPS = 4400. No owner limit on it exists on chain, and every listed stock is available, so
for a creator's value the verdict is ADVISORY: the front end shows PASS or FAIL as a warning and nothing refuses
the launch. A rule 1(a) FAIL is the one it must surface: that raise can never graduate, and its buyers can only
sell back to the curve (round 3 M-3). Check a creator's raise with --sale-bps, or give a plan entry its own saleBps.

The measuring is `script/CheckV2Listings.s.sol` under `forge script` on a fork pinned at one block: a real
HedgeFunBondingCurve gives Rg, the pool's own swap (reverted from inside its callback) gives what a buy delivers
and where it leaves the price, and a PoolTrader subclass gives the gate. Nothing is signed or broadcast; the
script refuses a broadcast context. This file pins the block, builds the plan, runs forge, and judges the lines
it prints -- the judging is pure and unit-tested (tests/test_v2_launch_check.py).

  python3 tools/v2_launch_check.py                          # the planned set, deploy/v2-listings-plan.json
  python3 tools/v2_launch_check.py --stock GME              # one stock, right before its launch
  python3 tools/v2_launch_check.py --factory 0x<V2 factory> # read listings/gates/default saleBps from a V2 factory
  python3 tools/v2_launch_check.py --stock AMD --sale-bps 8000  # a creator's proposed raise on one stock
  python3 tools/v2_launch_check.py --block 75036307 --json-out run.json
  python3 tools/v2_launch_check.py --testnet                # the testnet deployment, deploy/testnet-v2.json

--testnet checks the public Robinhood Chain testnet (46630) deployment of docs/TESTNET_V2.md: the chain id, the
default RPC, the stocks and the factory all come from its address book (--book), and the quote token is the
factory's own tUSDG. Its pools are test doubles; a PASS there says nothing about a mainnet listing.

RPC: --rpc (a URL or a foundry.toml alias), else RH_READ_RPC, else the chain's official public RPC. Not
publicnode: a fork reads state at its pinned block, and publicnode answers any block but the head with HTTP 403
"Archive requests require a personal token". The official RPC keeps about 40 minutes of state, enough for a
run. RH_RPC (the signing endpoint) is never read. HTTP 429 is retried with backoff, in forge and here.

Exit status: 0 every stock PASS; 1 at least one FAIL; 2 the check itself could not run.
"""
import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_PLAN = os.path.join(ROOT, "deploy", "v2-listings-plan.json")
SCRIPT = "script/CheckV2Listings.s.sol:CheckV2Listings"
OFFICIAL_RPC = "https://rpc.mainnet.chain.robinhood.com"
CHAIN_ID = 4663
TESTNET_RPC = "https://rpc.testnet.chain.robinhood.com"
TESTNET_CHAIN_ID = 46630
DEFAULT_BOOK = os.path.join(ROOT, "deploy", "testnet-v2.json")
ZERO = "0x" + "0" * 40

BPS = 10_000
SALE_BPS_MIN, SALE_BPS_MAX = 1000, 9000       # HedgeFunBondingCurve constructor bounds = CurveDeployer's MIN/MAX_SALE_BPS
DEFAULT_SALE_BPS = 4400                       # CurveDeployer.DEFAULT_SALE_BPS: a launch whose creator registered none
CHUNK_RULE_FEE = 500                          # M4-1 applies to 0.05% pools only
CHUNK_RULE_BPS = 1000                         # sellChunkUsdg <= 10% of USDG depth per 1% move
RATE_LIMIT = re.compile(r"HTTP error 429|429 Too Many|rate.?limit|retry_after|Too Many Requests|compute units", re.I)


# ---------------------------------------------------------------------------------------------------------------
# Pure math and verdicts. Everything below here to `run_forge` is exercised offline by the unit tests.
# ---------------------------------------------------------------------------------------------------------------

def ceil_div(a, b):
    return -(-a // b)


def mul_div(a, b, c, ceil=False):
    """OpenZeppelin Math.mulDiv: exact a*b/c, floor or ceil."""
    return ceil_div(a * b, c) if ceil else a * b // c


def curve_terms(open_price_e18, supply, sale_bps):
    """The curve's graduation target in raw stock units, with the contracts' own rounding.

    V (virtualStock) is HedgeFunV2Factory._curveInit: mulDiv(openPriceE18, supply, 1e18, Ceil). minTokenReserve and
    terminalStock are HedgeFunBondingCurve's constructor: mulDiv(supply, 10000 - saleBps, 10000) and
    ceilDiv(supply * V, minTokenReserve). Rg = terminalStock - V is the real stock a curve holds when it graduates.
    """
    if not SALE_BPS_MIN <= sale_bps <= SALE_BPS_MAX:
        raise ValueError(f"saleBps {sale_bps} outside the curve's [{SALE_BPS_MIN}, {SALE_BPS_MAX}]")
    if supply <= 0 or open_price_e18 <= 0:
        raise ValueError("supply and openPriceE18 must be positive")
    virtual = mul_div(open_price_e18, supply, 10**18, ceil=True)
    return curve_terms_from_virtual(supply, virtual, sale_bps)


def curve_terms_from_virtual(supply, virtual, sale_bps):
    min_reserve = mul_div(supply, BPS - sale_bps, BPS)
    if min_reserve == 0:
        raise ValueError("minTokenReserve is 0")
    terminal = ceil_div(supply * virtual, min_reserve)
    return {"virtualStock": virtual, "minTokenReserve": min_reserve, "terminalStock": terminal,
            "rg": terminal - virtual}


def max_chunk_usdg(depth_up, depth_down):
    """M4-1: the largest sellChunkUsdg a 0.05% pool admits -- 10% of its thinner side's USDG depth per 1%."""
    return min(depth_up, depth_down) * CHUNK_RULE_BPS // BPS


def chunk_rule_applies(fee):
    return fee == CHUNK_RULE_FEE


def scale(stock_decimals, usdg_decimals):
    """PoolTrader.SCALE: 1e18 * 10^stockDecimals / 10^usdgDecimals."""
    return 10**18 * 10**stock_decimals // 10**usdg_decimals


def price_at_sqrt(sqrt_p, stock_is_token0, stock_decimals, usdg_decimals):
    """PoolTrader._priceAtSqrt: the pool's price in oracle units (1e18 USDG per whole stock token).

    The pool quotes token1 per token0. With the stock as token0 that is USDG per stock; with the stock as token1
    it is stock per USDG and must be inverted. Getting this backwards is the ordering bug this chain has shipped.
    """
    q96 = 2**96
    raw_x96 = sqrt_p * sqrt_p // q96
    if raw_x96 == 0:
        return 0
    s = scale(stock_decimals, usdg_decimals)
    return raw_x96 * s // q96 if stock_is_token0 else s * q96 // raw_x96


def gate_exceeded(spot, oracle, max_deviation_bps):
    """HedgeFunMath.exceeds(|spot - oracle|, oracle, dev): gap > mulDiv(oracle, dev, 10000)."""
    return abs(spot - oracle) > oracle * max_deviation_bps // BPS


def deviation_bps(spot, oracle):
    """Signed distance of spot from the oracle, in bps; positive = the pool prices the stock above the oracle."""
    return (spot - oracle) * BPS / oracle


def stock_tick_move(tick, mean_tick, stock_is_token0):
    """Tick distance from the 600 s mean, signed in the stock price's direction (one tick is ~1 bp).

    The pool's tick tracks token1-per-token0, so a rising stock price raises the tick only when the stock is token0.
    """
    d = tick - mean_tick
    return d if stock_is_token0 else -d


def tick_gate_exceeded(tick, mean_tick, max_deviation_bps):
    """PoolTrader._health's second half: |tick - mean tick| > maxDeviationBps."""
    return abs(tick - mean_tick) > max_deviation_bps


def sale_bps_arg(text):
    """argparse type for --sale-bps: a creator may register anything the curve's constructor accepts."""
    value = int(text, 0)
    if not SALE_BPS_MIN <= value <= SALE_BPS_MAX:
        raise argparse.ArgumentTypeError(f"saleBps {value} outside [{SALE_BPS_MIN}, {SALE_BPS_MAX}] "
                                         f"(CurveDeployer.setCurveConfig refuses it)")
    return value


def apply_sale_bps(entries, sale_bps):
    """A creator's proposed saleBps for every checked stock, over the plan's own values."""
    for _, e in entries:
        e["saleBps"] = sale_bps
        e["creatorSaleBps"] = True
    return entries


def check_curve_arithmetic(row):
    """The script reads Rg off a deployed HedgeFunBondingCurve; the formula above must agree to the unit."""
    mine = curve_terms(row["openPriceE18"], row["supply"], row["saleBps"])
    return [k for k in ("virtualStock", "minTokenReserve", "terminalStock", "rg") if mine[k] != row[k]]


def units(amount, decimals):
    return amount / 10**decimals


def judge(row, allow_stale_oracle=False):
    """Verdict for one stock from its V2CHECK line. Returns (verdict, failures, notes, derived).

    failures: one line each, naming the rule and the number that broke it. derived: the figures the table prints.
    """
    fails, notes, d = [], [], {}
    sym = row.get("symbol", "?")
    if row.get("configError"):
        return "FAIL", [f"config: {row['configError']}"], notes, d
    if not row.get("enabled", True):
        notes.append("listing is disabled on the factory: no launch can use it until it is enabled")
    if row.get("saleBpsSource", "").startswith("DEFAULT_SALE_BPS"):
        notes.append(f"the factory has no curve deployer default, so this is not a V2 factory; Rg assumes saleBps "
                     f"{row['saleBps']}")
    if row.get("creatorSaleBps") or row.get("saleBpsSource") == "creator":
        notes.append(f"saleBps {row['saleBps']} is the creator's choice: this verdict is advisory, shown to the "
                     f"creator as a warning; nothing on chain refuses the launch")
    sd, ud = row["stockDecimals"], row["usdgDecimals"]
    dev = row["maxDeviationBps"]

    drift = check_curve_arithmetic(row)
    if drift:
        fails.append(f"internal: the contract's {', '.join(drift)} differ from the formula; do not trust this run")

    # --- the oracle the gate is measured against ---
    oracle = row["oraclePrice"]
    if not row["oracleLive"]:
        when = row.get("oracleUpdatedAt") or 0
        at = datetime.datetime.fromtimestamp(when, datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC") if when else "?"
        msg = (f"oracle: PriceOracle.tryPrice() serves no price at this block (market closed, oraclePaused or a stale "
               f"feed); rule 1(b) was measured against the last print, {at}")
        if oracle == 0:
            fails.append("oracle: no live price and no last print; rule 1(b) cannot be measured")
        elif allow_stale_oracle:
            notes.append(msg)
        else:
            fails.append(msg)
    if not row["meanOk"]:
        fails.append("config: pool.observe cannot serve the 600 s window at this block, so every treasury on it fails closed")

    rg = row["rg"]
    d["rg"] = units(rg, sd)
    d["deliverable"] = units(row["deliverable"], sd) if row["drainOk"] else None
    # a short fill runs the swap to the price limit, so its USDG input is not the price of Rg: show none
    d["cost"] = units(row["rgUsdgIn"], ud) if row["rgOk"] and row["rgOut"] >= rg else None
    d["gate"] = dev
    d["gateStock"] = units(row["gateStock"], sd) if row["gateOk"] else 0.0
    if oracle:
        d["pre"] = deviation_bps(row["spot0"], oracle)
    if not row["healthBefore"] and row["oracleLive"]:
        notes.append(f"the pool is already outside the {dev} bps gate before any raise "
                     f"(spot {d.get('pre', 0):+.1f} bps vs oracle, tick {stock_tick_move(row['tick0'], row['meanTick'], row['stockIsToken0']):+d} vs mean)")

    # --- rule 1(a): the pool can deliver Rg at all (M-3) ---
    filled = row["rgOk"] and row["rgOut"] >= rg
    if not filled:
        if row["drainOk"]:
            have = row["deliverable"]
            fails.append(f"rule 1(a) M-3: the pool can deliver {units(have, sd):,.2f} {sym} for "
                         f"{units(row['drainUsdgIn'], ud):,.0f} USDG, short of Rg {d['rg']:,.2f} "
                         f"({(rg - have) * 100 / rg:.1f}% short) at saleBps {row['saleBps']}: this curve could "
                         f"never graduate, and its buyers could only sell back to the curve")
        else:
            fails.append(f"rule 1(a) M-3: buying Rg {d['rg']:,.2f} {sym} reverts ({row['rgErr'] or 'short fill'}) "
                         f"and so does the drain ({row['drainErr']}): deliverable unknown")

    # --- rule 1(b): after one buyer takes Rg, the treasuries' gate still passes (M-2) ---
    if filled and oracle:
        spot_after = row["rgSpotAfter"]
        mine = price_at_sqrt(row["rgSqrtAfter"], row["stockIsToken0"], sd, ud)
        if mine != spot_after:
            fails.append(f"internal: post-trade price from sqrtPriceX96 {mine} != PoolTrader.spotPrice {spot_after}")
        move = deviation_bps(spot_after, oracle)
        ticks = stock_tick_move(row["rgTickAfter"], row["meanTick"], row["stockIsToken0"])
        d["after"] = move
        d["move"] = deviation_bps(spot_after, row["spot0"])
        d["ticks"] = ticks
        over_oracle = gate_exceeded(spot_after, oracle, dev)
        over_tick = row["meanOk"] and tick_gate_exceeded(row["rgTickAfter"], row["meanTick"], dev)
        if row["oracleLive"] and row["meanOk"] and (not over_oracle and not over_tick) != row["rgHealthAfter"]:
            fails.append("internal: this file's gate disagrees with PoolTrader._health on the post-trade pool")
        if over_oracle or over_tick:
            which = " and ".join(w for w, on in (("against the oracle", over_oracle),
                                                    ("against the 600 s mean tick", over_tick)) if on)
            hint = ""
            if row["gateOk"] and row["gateStock"] < rg:
                max_open = row["openPriceE18"] * row["gateStock"] // rg
                hint = (f"; the gate admits {d['gateStock']:,.2f} {sym} ({row['gateStock'] * 100 / rg:.0f}% of Rg), "
                        f"about openPriceE18 <= {max_open:,} at saleBps {row['saleBps']}")
            fails.append(f"rule 1(b) M-2: buying Rg {d['rg']:,.2f} {sym} ({d['cost']:,.0f} USDG) moves the pool "
                         f"{d['move']:+.1f} bps and leaves spot {move:+.1f} bps from the oracle (pre-trade "
                         f"{d['pre']:+.1f}), {ticks:+d} ticks from the 600 s mean; gate {dev} bps, exceeded {which}{hint}")

    # --- rule 2: 0.05% pools size the sell chunk to depth (M4-1) ---
    chunk = row["sellChunkUsdg"]
    d["chunk"] = units(chunk, ud)
    d["up"] = units(row["upUsdg"], ud) if row["upOk"] else None
    d["down"] = units(row["downUsdg"], ud) if row["downOk"] else None
    if chunk_rule_applies(row["fee"]):
        if not (row["upOk"] and row["downOk"]):
            fails.append(f"rule 2 M4-1: depth per 1% could not be measured (up: {row['upErr'] or 'ok'}; "
                         f"down: {row['downErr'] or 'ok'})")
        else:
            cap = max_chunk_usdg(row["upUsdg"], row["downUsdg"])
            d["maxChunk"] = units(cap, ud)
            if chunk > cap:
                fails.append(f"rule 2 M4-1: sellChunkUsdg {d['chunk']:,.0f} > {d['maxChunk']:,.0f}, 10% of the pool's "
                             f"USDG depth per 1% (up {d['up']:,.0f}, down {d['down']:,.0f}); set the chunk to at most "
                             f"{d['maxChunk']:,.0f} with setListingGates before launch")
    return ("FAIL" if fails else "PASS"), fails, notes, d


# ---------------------------------------------------------------------------------------------------------------
# Plan and ABI encoding
# ---------------------------------------------------------------------------------------------------------------

PLAN_FIELDS = ("token", "oracle", "pool", "openPriceE18", "supply", "saleBps", "maxDeviationBps", "maxSlippageBps",
               "sellChunkUsdg")


def as_int(v):
    return int(v, 0) if isinstance(v, str) else int(v)


def load_plan(path, only=None):
    """[(symbol, entry)] from a plan file. Each stock's own value wins over the plan-wide `defaults`.

    A stock's own `saleBps` is a creator's proposed raise (a V2 factory has no per-stock value), so the entry is
    marked `creatorSaleBps` and, with --factory, is sent to the script in place of the factory's default."""
    plan = json.load(open(path))
    base = {k: v["value"] if isinstance(v, dict) else v for k, v in plan.get("defaults", {}).items()}
    out = []
    wanted = {s.upper() for s in only} if only else None
    for sym, st in plan["stocks"].items():
        if wanted is not None and sym.upper() not in wanted:
            continue
        e = dict(base)
        e.update({k: v for k, v in st.items() if k in PLAN_FIELDS})
        e["creatorSaleBps"] = "saleBps" in st
        missing = [k for k in PLAN_FIELDS if k not in e]
        if missing:
            raise ValueError(f"{path}: {sym} has no {', '.join(missing)}")
        out.append((sym, e))
    if wanted is not None:
        found = {s.upper() for s, _ in out}
        if wanted - found:
            raise ValueError(f"not in {path}: {', '.join(sorted(wanted - found))}")
    return plan, out


def load_book(path, only=None):
    """(factory, [(symbol, entry)]) from the testnet address book DeployV2Testnet writes. With a factory the check
    reads everything but the stock from the chain, so the entry needs only the token."""
    book = json.load(open(path))
    if book.get("chainId") != TESTNET_CHAIN_ID:
        raise ValueError(f"{path} is for chain {book.get('chainId')}, not the testnet {TESTNET_CHAIN_ID}")
    wanted = {s.upper() for s in only} if only else None
    out = [(sym, {"token": st["token"], "creatorSaleBps": False}) for sym, st in sorted(book["stocks"].items())
           if wanted is None or sym.upper() in wanted]
    if wanted is not None and wanted - {s.upper() for s, _ in out}:
        raise ValueError(f"not in {path}: {', '.join(sorted(wanted - {s.upper() for s, _ in out}))}")
    return book["factory"], out


def _addr_word(a):
    if not re.fullmatch(r"0x[0-9a-fA-F]{40}", a):
        raise ValueError(f"not an address: {a}")
    return int(a, 16)


def encode_entries(entries, factory_mode=False):
    """abi.encode(Entry[]) for CheckV2Listings.run(address,bytes). Entry is nine static words, in this order:
    stock, oracle, pool, openPriceE18, supply, saleBps, maxDeviationBps, maxSlippageBps, sellChunkUsdg.

    With a factory the script reads everything but the stock from it, and saleBps from its curve deployer's default
    unless the entry carries a creator's saleBps: that is the one other word sent (0 = the default)."""
    words = [0x20, len(entries)]
    for e in entries:
        if factory_mode:
            sale = as_int(e["saleBps"]) if e.get("creatorSaleBps") else 0
            words += [_addr_word(e["token"]), 0, 0, 0, 0, sale, 0, 0, 0]
            continue
        words += [_addr_word(e["token"]), _addr_word(e["oracle"]), _addr_word(e["pool"]), as_int(e["openPriceE18"]),
                  as_int(e["supply"]), as_int(e["saleBps"]), as_int(e["maxDeviationBps"]),
                  as_int(e["maxSlippageBps"]), as_int(e["sellChunkUsdg"])]
    for w in words:
        if not 0 <= w < 2**256:
            raise ValueError(f"word out of range: {w}")
    return "0x" + "".join(f"{w:064x}" for w in words)


def parse_output(text):
    """(meta, rows) from forge's log: `V2CHECK_META {json}` once, `V2CHECK {json}` per stock."""
    meta, rows = None, []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("V2CHECK_META "):
            meta = json.loads(line[len("V2CHECK_META "):])
        elif line.startswith("V2CHECK {"):
            rows.append(json.loads(line[len("V2CHECK "):]))
    return meta, rows


# ---------------------------------------------------------------------------------------------------------------
# RPC and forge
# ---------------------------------------------------------------------------------------------------------------

def foundry_aliases():
    try:
        import tomllib
        with open(os.path.join(ROOT, "foundry.toml"), "rb") as f:
            return tomllib.load(f).get("rpc_endpoints", {})
    except Exception:
        return {}


def resolve_rpc(arg, testnet=False):
    if arg:
        return foundry_aliases().get(arg, arg)
    if testnet:
        return TESTNET_RPC
    return os.environ.get("RH_READ_RPC", "").strip() or OFFICIAL_RPC


def host_of(url):
    return urllib.parse.urlparse(url).hostname or "?"


def rpc_call(url, method, params, tries=8, sleep=time.sleep):
    """One JSON-RPC call; HTTP 429, 5xx and JSON-RPC rate-limit errors retry with exponential backoff."""
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    delay = 1.0
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json",
                                                                  "User-Agent": "hedgefun-v2-launch-check"})
            reply = json.load(urllib.request.urlopen(req, timeout=60))
            err = reply.get("error")
            if err and RATE_LIMIT.search(json.dumps(err)) and attempt < tries - 1:
                sleep(delay)
                delay = min(delay * 2, 30)
                continue
            if err:
                raise RuntimeError(f"{method}: {err}")
            return reply["result"]
        except urllib.error.HTTPError as e:
            if (e.code == 429 or e.code >= 500) and attempt < tries - 1:
                retry_after = e.headers.get("Retry-After") if e.headers else None
                sleep(float(retry_after) if retry_after and retry_after.isdigit() else delay)
                delay = min(delay * 2, 30)
                continue
            raise
        except urllib.error.URLError:
            if attempt < tries - 1:
                sleep(delay)
                delay = min(delay * 2, 30)
                continue
            raise
    raise RuntimeError(f"{method}: gave up after {tries} attempts")


def block_info(url, number=None):
    tag = hex(number) if number is not None else "latest"
    b = rpc_call(url, "eth_getBlockByNumber", [tag, False])
    if not b:
        raise RuntimeError(f"block {number} not served by {host_of(url)} (pruned? it keeps about 40 minutes)")
    return int(b["number"], 16), int(b["timestamp"], 16), b["hash"]


def run_forge(rpc, block, factory, encoded, attempts=4, verbose=False):
    cmd = ["forge", "script", SCRIPT, "--sig", "run(address,bytes)", factory, encoded,
           "--fork-url", rpc, "--fork-block-number", str(block), "--fork-retries", "12",
           "--fork-retry-backoff", "2000", "-vv"]
    delay = 30
    for attempt in range(1, attempts + 1):
        p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
        out = p.stdout + p.stderr
        if verbose:
            sys.stderr.write(out)
        if p.returncode == 0 and "V2CHECK_DONE" in out:
            return out
        if attempt < attempts and RATE_LIMIT.search(out):
            sys.stderr.write(f"forge run {attempt} hit the RPC rate limit; retrying in {delay}s at the same block "
                             f"(Foundry's RPC cache keeps what was already read)\n")
            time.sleep(delay)
            delay *= 2
            continue
        tail = "\n".join(l for l in out.splitlines() if "Failed to clone" not in l and "fatal:" not in l)[-3000:]
        raise RuntimeError(f"forge script failed (exit {p.returncode}):\n{tail}")
    raise RuntimeError("forge script: gave up after repeated rate limits")


# ---------------------------------------------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------------------------------------------

def _n(v, fmt="{:,.2f}"):
    return "-" if v is None else fmt.format(v)


def table(results):
    head = ["stock", "fee", "Rg", "deliverable", "USDG for Rg", "move bps", "vs oracle bps", "gate bps", "Rg in gate",
            "depth/1% up", "depth/1% down", "max chunk", "chunk", "verdict"]
    lines = []
    for sym, row, verdict, _, _, d in results:
        if row.get("configError"):
            lines.append([sym, str(row.get("fee", "-"))] + ["-"] * 11 + [verdict])
            continue
        in_gate = d.get("gateStock", 0.0)
        lines.append([
            sym, f"{row['fee'] / 1e4:.2f}%", _n(d.get("rg")), _n(d.get("deliverable")), _n(d.get("cost"), "{:,.0f}"),
            _n(d.get("move"), "{:+.1f}") if "move" in d else "n/a",
            _n(d.get("after"), "{:+.1f}") if "after" in d else "n/a", str(d.get("gate", "-")),
            f"{min(in_gate / d['rg'], 1) * 100:.0f}%" if d.get("rg") else "-",
            _n(d.get("up"), "{:,.0f}"), _n(d.get("down"), "{:,.0f}"),
            _n(d.get("maxChunk"), "{:,.0f}") if chunk_rule_applies(row["fee"]) else "n/a",
            _n(d.get("chunk"), "{:,.0f}"), verdict])
    widths = [max(len(str(x)) for x in col) for col in zip(head, *lines)]
    fmt = lambda r: "| " + " | ".join(str(x).rjust(w) if i else str(x).ljust(w) for i, (x, w) in enumerate(zip(r, widths))) + " |"
    sep = "|" + "|".join("-" * (w + 2) for w in widths) + "|"
    return "\n".join([fmt(head), sep] + [fmt(r) for r in lines])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--plan", default=DEFAULT_PLAN, help="plan JSON (default deploy/v2-listings-plan.json); with "
                    "--factory it supplies only the list of stocks")
    ap.add_argument("--factory", help="a deployed V2 factory: read each stock's listing, gates, supply and the default "
                    "saleBps from it")
    ap.add_argument("--sale-bps", type=sale_bps_arg, help="a creator's proposed saleBps (1000..9000) for every "
                    "checked stock, in place of the default; the verdict is then advisory")
    ap.add_argument("--stock", action="append", help="check only these symbols (repeatable, or comma-separated)")
    ap.add_argument("--rpc", help="RPC URL or foundry.toml alias (default: RH_READ_RPC, else the official RPC)")
    ap.add_argument("--block", type=int, help="fork block (default: the head when the run starts)")
    ap.add_argument("--allow-stale-oracle", action="store_true",
                    help="report a closed-market/stale oracle as a note instead of a FAIL (never before a launch)")
    ap.add_argument("--json-out", help="also write every raw line, verdict and failure to this file")
    ap.add_argument("--verbose", action="store_true", help="echo forge's output")
    ap.add_argument("--testnet", action="store_true", help="check the testnet (46630) deployment in --book: its "
                    "factory, stocks and tUSDG; default RPC the testnet's official one")
    ap.add_argument("--book", default=DEFAULT_BOOK, help="testnet address book (default deploy/testnet-v2.json)")
    a = ap.parse_args(argv)

    only = [s.strip() for x in (a.stock or []) for s in x.split(",") if s.strip()] or None
    try:
        if a.testnet:
            book_factory, entries = load_book(a.book, only)
            a.factory = a.factory or book_factory
            a.plan = a.book
        else:
            plan, entries = load_plan(a.plan, only)
        if a.sale_bps is not None:
            apply_sale_bps(entries, a.sale_bps)
        rpc = resolve_rpc(a.rpc, a.testnet)
        chain = int(rpc_call(rpc, "eth_chainId", []), 16)
        want = TESTNET_CHAIN_ID if a.testnet else CHAIN_ID
        if chain != want:
            raise RuntimeError(f"{host_of(rpc)} is chain {chain}, not {'the testnet' if a.testnet else 'Robinhood Chain'} {want}")
        number, ts, bhash = block_info(rpc, a.block)
        factory = a.factory or ZERO
        if a.factory:
            _addr_word(a.factory)
        encoded = encode_entries([e for _, e in entries], factory_mode=bool(a.factory))
        when = datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        print(f"V2 launch check: {len(entries)} stock(s) from {'factory ' + a.factory if a.factory else os.path.relpath(a.plan, ROOT)}")
        print(f"fork: chain {chain}, block {number} ({when}), hash {bhash}, rpc {host_of(rpc)}")
        print(f"running forge script (read-only, no broadcast); about 40 s per stock on a public RPC...", flush=True)
        t0 = time.time()
        out = run_forge(rpc, number, factory, encoded, verbose=a.verbose)
        meta, rows = parse_output(out)
        if meta is None or len(rows) != len(entries):
            raise RuntimeError(f"expected {len(entries)} V2CHECK lines, got {len(rows)}")
        if meta["block"] != number:
            raise RuntimeError(f"forge forked block {meta['block']}, not the pinned {number}")
    except Exception as e:  # the check could not run: that is not a PASS
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    results = []
    for (sym, entry), row in zip(entries, rows):
        row["creatorSaleBps"] = bool(entry.get("creatorSaleBps"))
        verdict, fails, notes, d = judge(row, a.allow_stale_oracle)
        if row.get("symbol") not in (None, "?", sym):
            notes.append(f"the token calls itself {row['symbol']}")
        results.append((sym, row, verdict, fails, notes, d))

    print(f"\nblock {number}, {when}; {time.time() - t0:.0f} s\n")
    print(table(results))
    print("\nRg and deliverable are in stock; deliverable is what the pool gives for 500M USDG, i.e. at any price. "
          "USDG for Rg is one buyer's exact-output cost. move bps is how far that buy moves the pool; vs oracle bps "
          "is where it leaves spot against Chainlink. Rg in gate is the share of Rg the pool delivers before the gate "
          "shuts. Depth is USDG per 1% move of spot. Max chunk (0.05% pools only) is 10% of the thinner side.")
    failed = [r for r in results if r[2] == "FAIL"]
    for sym, _, _, fails, notes, _ in results:
        for f in fails:
            print(f"FAIL {sym}: {f}")
        for n in notes:
            print(f"note {sym}: {n}")
    print(f"\n{len(results) - len(failed)} PASS, {len(failed)} FAIL"
          + (f" ({', '.join(r[0] for r in failed)})" if failed else ""))
    if a.json_out:
        with open(a.json_out, "w") as f:
            json.dump({"block": number, "timestamp": ts, "hash": bhash, "rpcHost": host_of(rpc),
                       "factory": a.factory, "plan": os.path.relpath(a.plan, ROOT), "meta": meta,
                       "results": [{"symbol": s, "verdict": v, "failures": fl, "notes": n, "derived": d, "raw": r}
                                   for s, r, v, fl, n, d in results]}, f, indent=1)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

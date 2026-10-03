#!/usr/bin/env python3
"""Offline, paired V2 lot-rule / sale-recovery-cycle replay on frozen closes.

No network, wallet or chain execution. Daily prices are observations; optional
same-quote batching is execution-cadence sensitivity, not a liquidity model.
The legacy rule_backtest.Ledger is intentionally not used: it has tax inflows,
unbounded sells and a different per-bar schedule. Here both modes share one
ledger, differ only in the added recovery entry, and account for actual fills.
"""

import argparse
import csv
from dataclasses import asdict, dataclass
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import statistics
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
STOCK_FEES = {"NVDA": .0005, "TSLA": .003, "GME": .0005, "AAPL": .0005}
EPS = 1e-10


@dataclass(frozen=True)
class Rule:
    name: str
    tp1: float
    tp2: float
    dip: float
    stop: float
    lot: float


RULES = {
    "live": Rule("已发测试样例", .05, .10, .05, .05, .20),
    "trend": Rule("宽止盈", .30, .60, .03, 0., .50),
    "range": Rule("区间网格", .20, .40, .15, 0., .20),
    "scalp": Rule("窄止盈／无止损", .05, .10, .05, 0., .20),
    "stop": Rule("止盈＋12%止损", .15, .30, .10, .12, .50),
    "diamond": Rule("高门槛持有", 6.5535, 0., .03, 0., .50),
}


@dataclass(frozen=True)
class Limits:
    sell_chunk: float = 2000.
    min_lot: float = 5.
    bounty: float = .005
    cooldown: int = 600
    max_lots: int = 128


@dataclass
class Lot:
    qty: float
    cost: float
    half: bool = False
    tp1_left: float = 0.


@dataclass(frozen=True)
class SaleReference:
    price: float
    at: int
    quote_at: int


class Ledger:
    """Authenticated observations and execution effects stay separate.

    fill_fraction is a test/fault-injection input, never an inferred history
    value. A failed/sub-minimum buy changes neither lots nor entry gates.
    """

    def __init__(self, price, rule, *, cycle=False, initial=10000.,
                 friction=.0015, limits=None):
        if not math.isfinite(price) or price <= 0 or initial <= 0:
            raise ValueError("positive price and initial value required")
        if not 0 <= friction < 1:
            raise ValueError("invalid friction")
        self.rule = rule
        self.cycle = cycle
        self.friction = friction
        self.limits = limits or Limits()
        self.received = initial / price
        self.lots = [Lot(self.received, price)]
        self.cash = self.buyback = 0.
        self.last_sale_price = price  # Original dip reference; not recovery eligibility.
        self.last_stop = None
        self.recovery_stop = None  # TP clears the old dip gate, never the recovery wait.
        self.pending = None

    def inventory(self):
        return sum(lot.qty for lot in self.lots)

    def value(self, price):
        return (self.inventory() + self.buyback) * price + self.cash

    def _arm(self, price, at, quote_at, sold, output, live):
        # TP's sold principal is smaller than the quantity removed from its lot.
        # Dust/profit-only allocation leave this anchor alone. A qualifying
        # closed-market sale cancels old pending rather than retaining an old price.
        if self.cycle and sold * price >= self.limits.min_lot and output > 0:
            self.pending = SaleReference(price, at, quote_at) if live else None
            if not live:
                self.recovery_stop = None

    def recovery_due(self, price, at, quote_at, *, live=True):
        if not self.cycle or not self.pending or not live:
            return False
        sale = self.pending
        if (at - sale.at < self.limits.cooldown or quote_at <= sale.quote_at
                or price + EPS < sale.price * (1 + self.rule.dip)):
            return False
        # A later sub-minimum stop does not replace pending, but still renews
        # the inherited stop cooldown and required stock observation.
        return (self.recovery_stop is None
                or (at - self.recovery_stop.at >= self.limits.cooldown
                    and quote_at > self.recovery_stop.quote_at))

    def _buy(self, price, kind, fill):
        allowance = self.cash * self.rule.lot
        if kind == "recovery":
            allowance = min(allowance, self.limits.sell_chunk)
        spent = allowance * (1 - self.limits.bounty) * fill
        if spent < self.limits.min_lot or len(self.lots) >= self.limits.max_lots:
            return None, "minimum_buy_or_lot_capacity"
        got = spent * (1 - self.friction) / price
        bounty = spent * self.limits.bounty
        self.cash -= spent + bounty
        self.lots.append(Lot(got, spent / got))  # Bounty is not part of the lot's cost.
        self.last_sale_price = price
        self.last_stop = self.recovery_stop = self.pending = None
        return {"kind": kind, "qty": got, "cost": spent / got,
                "cashSpent": spent + bounty, "cashReceived": 0.}, ""

    def step(self, price, at, quote_at, *, healthy=True, live=True, fill_fraction=1.):
        if not 0 <= fill_fraction <= 1:
            raise ValueError("fill_fraction must be between zero and one")
        if (not healthy or not math.isfinite(price) or price <= 0
                or quote_at <= 0 or quote_at > at):
            return None, "unhealthy_quote"
        rule = self.rule
        stops = [lot for lot in self.lots if live and rule.stop
                 and price <= lot.cost * (1 - rule.stop) + EPS]
        if stops:
            lot = max(stops, key=lambda x: (x.cost, x.qty))
            qty = min(lot.qty, self.limits.sell_chunk / price) * fill_fraction
            if qty <= EPS:
                return None, "no_fill"
            output = qty * price * (1 - self.friction)
            self.cash += output * (1 - self.limits.bounty)
            lot.qty -= qty
            lot.tp1_left = min(lot.tp1_left, lot.qty)
            self.last_sale_price = price
            self.last_stop = SaleReference(price, at, quote_at)
            if self.cycle:
                self.recovery_stop = self.last_stop
            self._arm(price, at, quote_at, qty, output, live)
            self.lots = [x for x in self.lots if x.qty > EPS]
            return {"kind": "stop", "qty": qty, "cost": lot.cost,
                    "cashReceived": output * (1 - self.limits.bounty), "cashSpent": 0.}, ""

        due = []
        for index, lot in enumerate(self.lots):
            second = rule.tp2 == 0 or lot.half
            trigger = rule.tp2 if lot.half and rule.tp2 else rule.tp1
            if price + EPS >= lot.cost * (1 + trigger):
                due.append((not second, lot.cost, -lot.qty, index))
        if due:
            lot = self.lots[min(due)[3]]
            first = bool(rule.tp2 and not lot.half)
            left = (lot.tp1_left or lot.qty / 2) if first else 0.
            requested = min(self.limits.sell_chunk / price, left if first else lot.qty)
            qty = requested * fill_fraction
            if qty <= EPS:
                return None, "no_fill"
            principal = qty * lot.cost / price
            output = principal * price * (1 - self.friction)
            profit = qty - principal
            if first:
                lot.tp1_left = left - qty
                if lot.tp1_left <= EPS:
                    lot.half = True
            lot.qty -= qty
            self.cash += output
            self.buyback += profit * (1 - self.limits.bounty)
            self.last_sale_price = price
            self.last_stop = None
            self._arm(price, at, quote_at, principal, output, live)
            self.lots = [x for x in self.lots if x.qty > EPS]
            return {"kind": "tp1" if first else ("tp2" if rule.tp2 else "tp_all"),
                    "qty": qty, "cost": lot.cost, "cashReceived": output,
                    "cashSpent": 0., "profitStock": profit * (1 - self.limits.bounty)}, ""

        # Preserve the old dip ladder, including its special post-stop gate.
        dip_due = price <= self.last_sale_price * (1 - rule.dip) + EPS
        recovery_due = self.recovery_due(price, at, quote_at, live=live)
        dip_allowed = not (not live and rule.stop)
        if self.last_stop:
            stop = self.last_stop
            dip_allowed = (live and at - stop.at >= self.limits.cooldown
                           and quote_at > stop.quote_at
                           and price <= stop.price * (1 - rule.dip) + EPS) or recovery_due
        if dip_due and dip_allowed:
            return self._buy(price, "dip", fill_fraction)

        # Only the additional upward entry uses pending. A stale print may not
        # consume it; a successful dip or recovery buy consumes it once.
        if self.cycle and self.pending and live:
            if recovery_due:
                return self._buy(price, "recovery", fill_fraction)
            return None, "sale_waiting_for_recovery_or_dip"
        return None, "waiting_for_original_rule"


def close_time(date):
    return int(datetime.fromisoformat(date + "T16:00:00").replace(
        tzinfo=ZoneInfo("America/New_York")).timestamp())


def max_drawdown(values):
    peak = values[0]
    worst = 0.
    for value in values:
        peak = max(peak, value)
        worst = max(worst, 1 - value / peak)
    return worst * 100


def replay(series, rule, fee, *, cycle=False, batch=False, initial=10000., slippage=.001):
    """The baseline close books stock; the first eligible action is next session.

    Only the current row enters step. Forward prices are used afterwards solely
    for diagnostic labels. All normal rows assume an open, healthy market and a
    distinct authenticated close print; fault tests supply other observations.
    """
    if len(series) < 2:
        raise ValueError("baseline and at least one trading close required")
    ledger = Ledger(series[0]["price"], rule, cycle=cycle, initial=initial,
                    friction=fee + slippage)
    events, points = [], []
    for index, row in enumerate(series):
        price = row["price"]
        at = row.get("executionAt", close_time(row["date"]))
        quote_at = row.get("quoteAt", at)
        reason, today = "baseline", []
        if index:
            for attempt in range(32 if batch else 1):
                record, reason = ledger.step(price, at, quote_at)
                if record is None:
                    break
                record |= {"i": index, "date": row["date"], "price": price}
                today.append(record)
                events.append(record)
            else:
                if batch:
                    raise RuntimeError("batch cap exhausted")
        nav = ledger.value(price)
        if ledger.cash < -EPS or ledger.inventory() < -EPS or ledger.buyback < -EPS:
            raise AssertionError("negative ledger bucket")
        points.append({"i": index, "date": row["date"], "price": price,
                       "nav": nav, "hold": initial * price / series[0]["price"],
                       "cash": ledger.cash, "stock": ledger.inventory(),
                       "buyback": ledger.buyback, "cashPct": ledger.cash / nav * 100,
                       "actions": len(today), "reason": reason,
                       "pending": asdict(ledger.pending) if ledger.pending else None})
    return {"points": points, "events": events}


def summarize(result, series):
    points, events = result["points"], result["events"]
    eligible, rise_flags = 0, 0
    for event in events:
        if event["kind"] in ("dip", "recovery"):
            continue
        later = series[event["i"] + 1:event["i"] + 21]
        if len(later) == 20:
            eligible += 1
            rise_flags += max(row["price"] / event["price"] - 1 for row in later) >= .10
    gap = longest = 0
    for point in points[1:]:
        gap = 0 if point["actions"] else gap + 1
        longest = max(longest, gap)
    start, end = points[0]["nav"], points[-1]
    return {"returnPct": (end["nav"] / start - 1) * 100,
            "holdPct": (end["hold"] / start - 1) * 100,
            "relativePct": (end["nav"] / end["hold"] - 1) * 100,
            "maxDdPct": max_drawdown([point["nav"] for point in points]),
            "holdDdPct": max_drawdown([point["hold"] for point in points]),
            "actions": len(events), "activeDays": sum(p["actions"] > 0 for p in points[1:]),
            "waitDays": sum(not p["actions"] for p in points[1:]), "longestWait": longest,
            "recoveryBuys": sum(e["kind"] == "recovery" for e in events),
            "dipBuys": sum(e["kind"] == "dip" for e in events),
            "stopSales": sum(e["kind"] == "stop" for e in events),
            "cashPct": end["cashPct"],
            "meanCashPct": statistics.mean(p["cashPct"] for p in points[1:]),
            "highCashDays": sum(p["cashPct"] >= 80 for p in points[1:]),
            "eligibleSales20": eligible, "salesThenRise20": rise_flags,
            "bars": len(series) - 1}


def compare(series, rule, fee, *, batch=False):
    results = {mode: summarize(replay(series, rule, fee, cycle=mode == "cycle", batch=batch), series)
               for mode in ("original", "cycle")}
    results["differencePp"] = results["cycle"]["returnPct"] - results["original"]["returnPct"]
    return results


def load_prices(path, as_of=None):
    raw_bytes = Path(path).read_bytes()
    fixture = json.loads(raw_bytes)
    prices = {}
    for ticker in STOCK_FEES:
        source = fixture["stocks"][ticker]
        rows = source["data"]
        dates = [row["date"] for row in rows]
        if dates != sorted(set(dates)) or any(not math.isfinite(row["price"]) or row["price"] <= 0 for row in rows):
            raise ValueError("invalid, duplicate or unordered prices: " + ticker)
        prices[ticker] = {row["date"]: row for row in rows if as_of is None or row["date"] <= as_of}
    common = sorted(set.intersection(*(set(rows) for rows in prices.values())))
    if len(common) < 253:
        raise ValueError("need 253 aligned closes for the fixed comparison windows")
    return fixture, prices, common, hashlib.sha256(raw_bytes).hexdigest()


def run_history(path, as_of=None):
    fixture, prices, common, digest = load_prices(path, as_of)
    ytd = [date for date in common if date[:4] == common[-1][:4]]
    start = common.index(ytd[0])
    if not start:
        raise ValueError("previous-year baseline missing")
    dates = {"m1": common[-22:], "m3": common[-64:],
             "ytd": [common[start - 1]] + ytd, "year": common[-253:]}
    output = {"asOf": common[-1], "fixtureSha256": digest,
              "rules": {key: asdict(rule) for key, rule in RULES.items()},
              "methodology": {"initialUsd": 10000, "initialStockPct": 100,
                              "newInflows": 0, "slippageBps": 10, "bountyBps": 50,
                              "sellChunkUsd": 2000, "minLotUsd": 5,
                              "recoveryCap": "min(reserve*lotBps, sellChunkUsdg), before bounty",
                              "recoveryGate": "qualifying live sale, >=600s, newer stock quote, rise by dipBps",
                              "cadence": "one action per close; separate up-to-32-call same-quote sensitivity",
                              "buyback": "held as stock; FUN swaps and dividends excluded",
                              "rolling": "10 overlapping 63-session windows, no parameter selection"},
              "sources": {ticker: {key: value for key, value in fixture["stocks"][ticker].items()
                                    if key != "data"} for ticker in STOCK_FEES},
              "fixed": [], "rolling": []}
    for ticker, fee in STOCK_FEES.items():
        for window, window_dates in dates.items():
            series = [prices[ticker][date] for date in window_dates]
            for key, rule in RULES.items():
                output["fixed"].append({"ticker": ticker, "window": window, "rule": key,
                                        "baseline": window_dates[0], "start": window_dates[1],
                                        "end": window_dates[-1],
                                        "daily": compare(series, rule, fee),
                                        "batch": compare(series, rule, fee, batch=True)})
        latest = common[-253:]
        for offset in range(0, 190, 21):
            window_dates = latest[offset:offset + 64]
            series = [prices[ticker][date] for date in window_dates]
            for key, rule in RULES.items():
                output["rolling"].append({"ticker": ticker, "rule": key,
                                          "baseline": window_dates[0], "start": window_dates[1],
                                          "end": window_dates[-1], **compare(series, rule, fee)})
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=ROOT / "data/v2-cycle-prices.json")
    parser.add_argument("--as-of", help="maximum stored close date; never fetches prices")
    parser.add_argument("--out", type=Path, default=ROOT / "data/v2-cycle-results")
    args = parser.parse_args()
    result = run_history(args.data, args.as_of)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.with_suffix(".json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    rows = []
    for case in result["fixed"]:
        for cadence in ("daily", "batch"):
            for mode in ("original", "cycle"):
                rows.append({**{key: case[key] for key in ("ticker", "window", "rule", "baseline", "start", "end")},
                             "cadence": cadence, "mode": mode, **case[cadence][mode],
                             "cycleMinusOriginalPp": case[cadence]["differencePp"]})
    with args.out.with_suffix(".csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    print(f"{result['asOf']}: {len(result['fixed'])} fixed pairs, "
          f"{len(result['rolling'])} rolling pairs; same-quote batching completed")


if __name__ == "__main__":
    main()

# V2 trend scenarios: a stock that only rises, only falls, or goes nowhere loudly — 2026-09-27

These are historical scenarios using buy-fee burns. [Two-sided fee income](./V2_TWO_SIDED_FEES.md)
changes buy fees, net curve funding and supply; the values below have not been remeasured for that release.

What happens to a V2 launch when its listed stock trends up continuously, down continuously, or stays flat with
volatility. Three stages of one launch are run over synthetic price paths by `lab/trend.py`: the raise, the strategy
treasury, and the token's own price. The tables below are its output (`python3 -m lab.trend`); `lab/test_trend.py`
pins the arithmetic. **Nothing here is a forecast.** The stock path and the trading volume are inputs, and the point
of the exercise is to see what each parameter does under a direction nobody can predict.

The short version: **a trend is a stock-price event first and a mechanism event second.** The curve, the pool and
the treasury are all denominated in the stock, so in stock terms a rising stock changes very little about the
launch and a falling one changes nothing at all. Everything that changes is the translation into dollars — and the
one thing the mechanism does react to, the take-profit, reacts to a rise by selling.

## Assumptions

Listed up front because every number depends on them.

**The launch.** Supply 1,000,000,000 FUN, opening FDV 100 stock (`V`, the virtual reserve: $10,000 at $100 a
share, which is where production listings open), sale 80%, LP 50%, tax 10%, LP fee 0.30%. So the graduation
target is `Rg = 400` stock ($40,000 at $100), the pool opens with 200 stock against 80,000,000 FUN, the treasury
receives 200 stock, and the curve runs 25x from open to terminal — the worked example in
[V2_BONDING_CURVE.md](./V2_BONDING_CURVE.md) scaled to production supply. Sale share is swept over 44 / 60 / 80%,
tax over 1 / 2 / 10%. The sell tax splits 20% protocol (factory default), 10% creator (the creator's pick, at most
30%) and 70% treasury. Curve and pool are continuous `x * y = k` with no raw-unit rounding, no sqrt-price
quantization and no V4 price limits, as in `lab/model.py`. The 3-second 99% opening burn is ignored: a raise that
takes a day or more makes it negligible.

**The stock.** Constant drift per calendar day — +3%, +1%, 0%, −1%, −3% — in 24 hourly steps, seven days a week.
The volatility controls add a daily vol of 2% or 4% from a seeded shock sequence that is normalised so a path carries
exactly the drift asked for: the "flat, 4% vol" path ends where it started. No 24/5 calendar and no weekend freeze:
the rule may act every hour a trigger is met, where on chain it also needs a fresh Chainlink print, the pool-vs-oracle
deviation gate and an open calendar. That makes the model's rule slightly quicker than the contract's.

**The treasury rule.** The launch kit's default template, *Trend holder*: take-profit 30% / 60%, dip 3%, no
stop, 50% lots ([LAUNCH_KIT.md](https://github.com/keyuyuan/hedgefund/blob/64c0adc602bbcbb70c0b4511ac67ee2aa40fceca/docs/LAUNCH_KIT.md)). Three others are compared: *Scalper* 5 / 10, 5, none, 20% (the
"shipped 5/5" rule of [rule-backtest/](https://github.com/keyuyuan/hedgefund/blob/64c0adc602bbcbb70c0b4511ac67ee2aa40fceca/docs/rule-backtest/README.md)), *Range grid* 20 / 40, 15, none, 20%, and
*Take-profit + stop* 15 / 30, 10, 12%, 50%. Around the rule, the defaults of `script/RehearseV2Launchpad.s.sol`:
bounty 0.50%, `minLotUsdg` 5, `buybackChunkUsdg` 500, cooldown 60 s, `maxBuybackImpactBps` 300, `sellChunkUsdg`
2,000. Every stock<->USDG swap loses 35 bps (pool fee plus slippage, the replay tool's default). The rule is
`tools/rule_backtest.py`'s own `Ledger`, factored out so this model and the historical replays cannot disagree; its
documented biases apply (evaluated at the bar's close, `tp2 = 2 x tp1`, a lot leaves in one bar rather than in
`sellChunkUsdg` chunks). V2's ordering — stops, then take-profits, then at most one dip per bar — and its 128-lot
cap are modelled; lots are never coalesced because every booking lands at a distinct price.

**Inflows and booking.** The graduation stock is the first lot, at the graduation price. Sell tax arrives every hour
and is booked once a day by the keeper, plus whenever `execute()` acts (it books first, as on chain); a booking
under 5 USDG or at the lot cap waits as `unbookedStock()`. Stock-side LP fees on buys go to `buybackStock`; the
FUN-side fee on sells is burned by the vault; the hook's buy tax is burned. Sweeps and fee collection are assumed to
run every hour. Buy-backs spend one chunk per cooldown, at most 3% above a 10-minute mean per push (about 9% of the
pool's stock per hour, which never binds here), pay the LP fee back to the treasury and 0.5% of the FUN to the caller,
and refuse a fill under 5 USDG.

**Volume — an input, not a prediction.** The FUN pool's daily volume is set as 5%, 20% or 50% of the raise, in
stock units, both legs together, and held constant in stock terms (so dollar volume follows the stock). It is taken as
round trips: a buy, then a sale of the same size, so nobody accumulates and the token's stock price moves only by the
mechanism. Whose tokens the buy tax burned is the one thing round trips cannot settle, so two bounds are shown: **the
float never sells** (the seller returns only what the buy delivered, so the burned tokens were the pool's and its price
rises) and **the float sells the burn back** (a holder supplies the burned amount, the pool ends each round trip where
it began and only buy-backs and the LP fee move it). Reality is between them.

**Scale.** Everything is quoted at $100 a share and scales linearly with the opening FDV except the two dollar
floors (`minLotUsdg`, `buybackChunkUsdg`), which only matter for a launch far smaller than this one.

## Stage 1: the raise

The graduation target is fixed in stock. What a trend changes is how many dollars it takes to reach it, and what
the dollars of an early buyer's paper gain are.

### Table 1a. The raise under a trend (sale 80%, tax 10%)

The raise fills at a constant rate in stock over the fill period. "Paper" is the terminal curve price, which is also
the V4 opening price; "dump alone" is the first 5% of the raise selling everything into the fresh pool by itself.

| path | fill days | stock at graduation | USD cost to graduate | vs flat | first 5%: paper multiple stock / USD | first 5%: dump alone, USD | last 5%: paper stock / USD |
|---|---|---|---|---|---|---|---|
| +3%/day | 1 | 1.03x | $40,622 | +2% | 18.7x / 19.3x | 6.0x | 0.94x / 0.94x |
| +3%/day | 7 | 1.23x | $44,466 | +11% | 18.7x / 22.9x | 7.2x | 0.94x / 0.94x |
| +3%/day | 30 | 2.43x | $64,420 | +61% | 18.7x / 44.5x | 13.9x | 0.94x / 0.96x |
| +1%/day | 1 | 1.01x | $40,208 | +1% | 18.7x / 18.9x | 5.9x | 0.94x / 0.94x |
| +1%/day | 7 | 1.07x | $41,435 | +4% | 18.7x / 20.1x | 6.3x | 0.94x / 0.94x |
| +1%/day | 30 | 1.35x | $46,621 | +17% | 18.7x / 25.1x | 7.8x | 0.94x / 0.94x |
| +1%/day, 2% vol | 1 | 1.01x | $40,422 | +1% | 18.7x / 18.9x | 5.9x | 0.94x / 0.94x |
| +1%/day, 2% vol | 7 | 1.07x | $40,917 | +2% | 18.7x / 20.1x | 6.3x | 0.94x / 0.94x |
| +1%/day, 2% vol | 30 | 1.35x | $46,652 | +17% | 18.7x / 25.2x | 7.9x | 0.94x / 0.95x |
| flat, 2% vol | 1 | 1.00x | $40,213 | +1% | 18.7x / 18.8x | 5.9x | 0.94x / 0.94x |
| flat, 2% vol | 7 | 1.00x | $39,499 | -1% | 18.7x / 18.8x | 5.9x | 0.94x / 0.94x |
| flat, 2% vol | 30 | 1.00x | $40,009 | +0% | 18.7x / 18.8x | 5.9x | 0.94x / 0.94x |
| flat, 4% vol | 1 | 1.00x | $40,428 | +1% | 18.7x / 18.8x | 5.9x | 0.94x / 0.94x |
| flat, 4% vol | 7 | 1.00x | $39,013 | -2% | 18.7x / 18.8x | 5.9x | 0.94x / 0.94x |
| flat, 4% vol | 30 | 1.00x | $40,036 | +0% | 18.7x / 18.9x | 5.9x | 0.94x / 0.94x |
| -1%/day, 2% vol | 1 | 0.99x | $40,003 | +0% | 18.7x / 18.6x | 5.8x | 0.94x / 0.94x |
| -1%/day, 2% vol | 7 | 0.93x | $38,133 | -5% | 18.7x / 17.5x | 5.5x | 0.94x / 0.94x |
| -1%/day, 2% vol | 30 | 0.74x | $34,516 | -14% | 18.7x / 14.0x | 4.4x | 0.94x / 0.93x |
| -1%/day | 1 | 0.99x | $39,791 | -1% | 18.7x / 18.6x | 5.8x | 0.94x / 0.94x |
| -1%/day | 7 | 0.93x | $38,617 | -3% | 18.7x / 17.5x | 5.5x | 0.94x / 0.94x |
| -1%/day | 30 | 0.74x | $34,526 | -14% | 18.7x / 14.0x | 4.4x | 0.94x / 0.93x |
| -3%/day | 1 | 0.97x | $39,372 | -2% | 18.7x / 18.2x | 5.7x | 0.94x / 0.94x |
| -3%/day | 7 | 0.81x | $36,000 | -10% | 18.7x / 15.2x | 4.8x | 0.94x / 0.93x |
| -3%/day | 30 | 0.40x | $26,204 | -34% | 18.7x / 7.7x | 2.4x | 0.94x / 0.92x |

- **The stock column of every cohort is the same in every row.** The curve prices FUN in stock, so a trend does
  not touch what a buyer gets for their stock or what the last buyer's tokens are worth at graduation (0.94x: the
  buy tax, nothing else). Only the dollar columns move, and they move by the stock's own multiple.
- **A rising stock makes graduation dear in dollars; a falling one makes it cheap.** At +3% a day a 30-day fill costs
  $64,420 to graduate instead of $40,000; at −3% a day, $26,204. A one-day fill barely notices either way (±2%). The
  raise's dollar size is therefore only as fixed as the fill is fast.
- **Early buyers on a falling stock still hold a paper gain, and it is the stock that decides how much.** The
  first 5% of the raise sit on 18.7x in stock at graduation; that is 7.7x in dollars after 30 days of −3%, and 44.5x
  after 30 days of +3%. On a falling stock the last 5% are the only cohort under water in dollars, and by 8%.

### Table 1b. Sale share: what the raise is and what the first buyers get (flat stock)

| sale | open to terminal | Rg stock (USD at $100) | pool / treasury stock | pool FUN as % of supply | first 1% of the raise: paper / dump alone / price after | first 5%: paper / dump alone / price after |
|---|---|---|---|---|---|---|
| 44% | 3.2x | 78.6 ($7,857) | 39.3 / 39.3 | 24% | 2.8x / 2.4x / 90% | 2.8x / 1.9x / 61% |
| 60% | 6.3x | 150.0 ($15,000) | 75.0 / 75.0 | 18% | 5.5x / 4.5x / 81% | 5.2x / 3.1x / 43% |
| 80% | 25.0x | 400.0 ($40,000) | 200.0 / 200.0 | 10% | 21.6x / 13.6x / 49% | 18.7x / 5.9x / 12% |

The first 1% of the raise is the "first 5% of the sale" of [V2_LP_DEPTH_EXPERIMENT.md](./V2_LP_DEPTH_EXPERIMENT.md),
and reproduces its 13.4x / 48%. The sale share is the lever on all three things a trend amplifies: the raise's dollar
size, the early buyer's multiple, and how thin the pool is against the float.

### Table 1c. Round trip on the curve: buy 1% of the raise halfway through, sell it back

| tax | round-trip loss | (1-t)^2 |
|---|---|---|
| 1% | 1.98% | 1.99% |
| 2% | 3.93% | 3.96% |
| 10% | 18.89% | 19.00% |

A curve exit costs two taxes and almost nothing else. The 10% default makes the curve a place to hold, not to trade;
1–2% makes it tradeable and, as Table 2c shows, starves the treasury.

## Stage 2: the treasury

The treasury's 200 stock is booked at the graduation price; sell tax and LP fees flow in at the assumed volume; the
rule runs. "Multiple vs hold" is the contract's own scorecard: stock held plus stock set aside for buy-backs plus the
USDG reserve at the last price, over stock received — 1.00 is "sat on the stock". "Treasury USD" is what it started
with and what everything it holds is worth at the end, at the end price.

### Table 2a. The treasury over 30 days (base launch, trend_holder, volume 20%/day)

| path | stock | TP fired | TP sales | stops | dips | lots | USDG reserve | buy-back stock spent | FUN burned by buy-backs | multiple vs hold | treasury USD |
|---|---|---|---|---|---|---|---|---|---|---|---|
| +3%/day | 2.43x | yes | 60 | 0 | 0 | 49 | $25,807 | 76.85 | +1.82% | 0.77 | $20,000 -> $33,410 |
| +1%/day | 1.35x | yes | 4 | 0 | 0 | 35 | $10,347 | 27.51 | +0.56% | 0.99 | $20,000 -> $33,454 |
| +1%/day, 2% vol | 1.35x | yes | 6 | 0 | 0 | 35 | $10,592 | 28.21 | +0.61% | 0.99 | $20,000 -> $33,362 |
| flat, 2% vol | 1.00x | no | 0 | 0 | 0 | 31 | $0 | 3.61 | +0.11% | 1.00 | $20,000 -> $27,520 |
| flat, 4% vol | 1.00x | no | 0 | 0 | 0 | 31 | $0 | 3.61 | +0.11% | 1.00 | $20,000 -> $27,520 |
| -1%/day, 2% vol | 0.74x | no | 0 | 0 | 0 | 31 | $0 | 3.61 | +0.11% | 1.00 | $20,000 -> $20,356 |
| -1%/day | 0.74x | no | 0 | 0 | 0 | 31 | $0 | 3.61 | +0.11% | 1.00 | $20,000 -> $20,356 |
| -3%/day | 0.40x | no | 0 | 0 | 0 | 31 | $0 | 3.61 | +0.11% | 1.00 | $20,000 -> $11,036 |

### Table 2a. The treasury over 90 days (base launch, trend_holder, volume 20%/day)

| path | stock | TP fired | TP sales | stops | dips | lots | USDG reserve | buy-back stock spent | FUN burned by buy-backs | multiple vs hold | treasury USD |
|---|---|---|---|---|---|---|---|---|---|---|---|
| +3%/day | 14.30x | yes | 755 | 0 | 0 | 128 | $95,725 | 129.58 | +2.20% | 0.51 | $20,000 -> $140,662 |
| +1%/day | 2.45x | yes | 190 | 0 | 0 | 128 | $37,600 | 109.48 | +1.27% | 0.81 | $20,000 -> $60,378 |
| +1%/day, 2% vol | 2.45x | yes | 185 | 0 | 7 | 128 | $17,156 | 138.24 | +1.70% | 0.94 | $20,000 -> $66,671 |
| flat, 2% vol | 1.00x | no | 0 | 0 | 0 | 91 | $0 | 10.83 | +0.19% | 1.00 | $20,000 -> $42,555 |
| flat, 4% vol | 1.00x | yes | 25 | 0 | 21 | 128 | $5 | 40.97 | +0.49% | 1.01 | $20,000 -> $39,956 |
| -1%/day, 2% vol | 0.40x | no | 0 | 0 | 0 | 91 | $0 | 10.83 | +0.19% | 1.00 | $20,000 -> $17,223 |
| -1%/day | 0.40x | no | 0 | 0 | 0 | 91 | $0 | 10.83 | +0.19% | 1.00 | $20,000 -> $17,223 |
| -3%/day | 0.06x | no | 0 | 0 | 0 | 91 | $0 | 10.83 | +0.19% | 1.00 | $20,000 -> $2,744 |

- **On a rising stock the rule sells early and stays out.** At +3% a day the first take-profit is day 9 (+30%),
  the graduation lot is gone by day 16 (+60%), and every tax lot booked after that is sold the same way. By day 90
  the stock is 14.3x and the treasury holds 0.51 of what sitting still would have — but $140,662 against $20,000,
  because the tax kept arriving (225 stock over 90 days) and the stock did the rest. On a pure trend it never buys
  a dip, because there is none; with 2% of daily vol on top of +1% a day it buys seven and finishes at 0.94.
- **On a falling stock the rule does nothing.** No lot is ever above its cost, there is no stop, and there is no
  reserve to buy dips with, so the treasury holds every stock it receives all the way down: 1.00 in stock, and in
  dollars the stock's own path minus the tax that came in ($17,223 at −1% a day, $2,744 at −3%). Its only buy-back
  budget is the stock-side LP fee, 10.8 stock in 90 days, 0.19% of supply.
- **Flat and volatile is what the rule is for, and even there it earns little.** With 2% of daily vol the
  30% take-profit is never reached in 90 days; with 4% it fires 25 times, buys 21 dips and ends at 1.01 with 41 stock
  spent on buy-backs (0.49% of supply). That is the whole edge of the rule on the tape it likes.
- **The 128-lot cap binds in every rising run and in any run a keeper books hourly.** `execute()` books before it
  acts, so a treasury that acts every hour books every hour; at 128 distinct costs new bookings and dip buys pause
  until a sale frees a slot (rising runs: slots free themselves; the "flat, 4% vol" run hits the cap through dip
  buys). On a falling stock nothing frees a slot: with hourly booking the cap fills on day 5 and 212 of the 225 stock
  of tax received sits unbooked at day 90. It is still the treasury's — `stockEquivalentHeld()` counts it — but it has
  no cost and no trigger. Keepers should book daily, not per hour, on a stock that is not selling.

### Table 2b. Rule templates over 90 days: multiple vs hold (TP sales / stops / dips, FUN burned by buy-backs)

| template | +3%/day | +1%/day | +1%/day, 2% vol | -1%/day, 2% vol | -1%/day | -3%/day |
|---|---|---|---|---|---|---|
| trend_holder (30%/60%, dip 3%, stop 0%, lot 50%) | 0.51 (755/0/0, +2.2%) | 0.81 (190/0/0, +1.3%) | 0.94 (185/0/7, +1.7%) | 1.00 (0/0/0, +0.2%) | 1.00 (0/0/0, +0.2%) | 1.00 (0/0/0, +0.2%) |
| scalper (5%/10%, dip 5%, stop 0%, lot 20%) | 0.29 (1957/0/0, +1.0%) | 0.61 (1620/0/0, +0.8%) | 0.65 (554/0/5, +1.0%) | 1.00 (3/0/8, +0.2%) | 1.00 (0/0/0, +0.2%) | 1.00 (0/0/0, +0.2%) |
| range_grid (20%/40%, dip 15%, stop 0%, lot 20%) | 0.44 (1174/0/0, +2.0%) | 0.75 (336/0/0, +1.3%) | 0.77 (280/0/0, +1.4%) | 1.00 (0/0/0, +0.2%) | 1.00 (0/0/0, +0.2%) | 1.00 (0/0/0, +0.2%) |
| tp_stop (15%/30%, dip 10%, stop 12%, lot 50%) | 0.39 (1624/0/0, +1.8%) | 0.71 (489/0/0, +1.2%) | 0.77 (349/0/1, +1.5%) | 1.80 (0/174/0, +0.2%) | 1.77 (0/235/0, +0.2%) | 8.80 (0/361/0, +0.2%) |

- **No template beats holding in a rise; the wide one loses least and burns most.** The 30% take-profit gives away
  half as much as the 5% one at +3% a day (0.51 against 0.29) and turns twice the supply into buy-backs (2.2% against
  1.0%), for the reason the rule backtest gives: what a sale sends to the buy-back is the profit share of the lot.
- **A stop turns a decline into USDG and then never gets back in.** At −1% a day with a 12% stop the treasury
  stops out 174 to 235 times, holds a reserve worth 1.8x the stock it received, and buys no dip at all: every stop
  resets the dip reference to the stop price, and with tax arriving every hour there is always a fresh lot to stop
  next, so the re-entry rung walks down with the price and is never 10% below it. In dollars that is $20,000 to
  $31,056 at −1% (2% vol) and to $24,125 at −3%, against $17,223 and $2,744 without the stop. The same stop costs
  0.39 against 0.51 at +3% a day. It is a bet on the path, as the rule backtest says, not a safety feature.

### Table 2c. Tax and volume over 90 days: what flows in and what burns

| path | tax | volume / day | volume USD, 90d | sell tax to treasury, stock | stock LP fees | FUN burned by buy tax | FUN burned by buy-backs | multiple vs hold |
|---|---|---|---|---|---|---|---|---|
| +1%/day | 1% | 5% | $288,916 | 6.20 | 2.70 | +0.30% | +2.12% | 0.71 |
| +1%/day | 1% | 20% | $1,155,699 | 24.80 | 10.80 | +1.02% | +2.13% | 0.73 |
| +1%/day | 1% | 50% | $2,889,379 | 62.01 | 27.00 | +2.02% | +2.10% | 0.75 |
| +1%/day | 2% | 5% | $287,471 | 12.27 | 2.70 | +0.58% | +2.10% | 0.72 |
| +1%/day | 2% | 20% | $1,149,939 | 49.10 | 10.80 | +1.86% | +1.99% | 0.75 |
| +1%/day | 2% | 50% | $2,875,038 | 122.78 | 27.00 | +3.34% | +1.79% | 0.78 |
| +1%/day | 10% | 5% | $275,906 | 56.37 | 2.70 | +2.51% | +1.93% | 0.75 |
| +1%/day | 10% | 20% | $1,103,757 | 225.54 | 10.80 | +5.62% | +1.27% | 0.81 |
| +1%/day | 10% | 50% | $2,759,718 | 564.03 | 27.00 | +7.46% | +0.79% | 0.85 |
| -1%/day | 1% | 5% | $117,491 | 6.20 | 2.70 | +0.38% | +0.11% | 1.00 |
| -1%/day | 1% | 20% | $469,979 | 24.80 | 10.80 | +1.27% | +0.38% | 1.00 |
| -1%/day | 1% | 50% | $1,175,013 | 62.01 | 27.00 | +2.37% | +0.72% | 1.00 |
| -1%/day | 2% | 5% | $116,903 | 12.27 | 2.70 | +0.74% | +0.11% | 1.00 |
| -1%/day | 2% | 20% | $467,639 | 49.10 | 10.80 | +2.26% | +0.34% | 1.00 |
| -1%/day | 2% | 50% | $1,169,196 | 122.78 | 27.00 | +3.81% | +0.58% | 1.00 |
| -1%/day | 10% | 5% | $112,201 | 56.37 | 2.70 | +3.04% | +0.09% | 1.00 |
| -1%/day | 10% | 20% | $448,876 | 225.55 | 10.80 | +6.19% | +0.19% | 1.00 |
| -1%/day | 10% | 50% | $1,122,385 | 564.04 | 27.00 | +7.80% | +0.24% | 1.00 |

- **Tax and volume set the inflow, and the inflow is what stands between the treasury and the stock's path.** At
  10% tax and 20% daily volume the treasury receives 225 stock of tax in 90 days, more than its 200-stock
  graduation lot; at 1% tax and 5% volume, 6 stock. The buy-back share of supply on a falling stock is 0.1–0.7%
  whatever the tax, because it is funded by LP fees alone.
- **On a rising stock, more tax raises the multiple.** Later lots are booked higher, so they are sold less far
  from cost and give less away (0.71 at 1% tax, 0.85 at 10%). The buy-back share moves the other way: the burn is a
  fixed quantity of stock bought at a rising FUN price, and more burning from the buy tax means fewer FUN per stock.

## Stage 3: the token's price

The pool is FUN against the stock, so FUN's dollar price is FUN's stock price times the stock. The stock's move is the
stock's business. What the mechanism does is burn: the buy tax, the FUN-side LP fee and the buy-backs. Its effect on the
pool's price depends on whose tokens burned, hence the two bounds.

### Table 3a. The token's price over 90 days (base launch, volume 20%/day)

| path | stock moved | supply burned by buy tax | by buy-backs | FUN in stock, float sells the burn back: total | in USD | FUN in stock, float never sells: burns only | with buy-backs | in USD |
|---|---|---|---|---|---|---|---|---|
| +3%/day | +1330% | +4.8% | +2.2% | +199.8% | +4188% | +703.9% | +1112.7% | +17242% |
| +1%/day | +145% | +5.6% | +1.3% | +162.5% | +543% | +703.9% | +1043.5% | +2700% |
| +1%/day, 2% vol | +145% | +5.3% | +1.7% | +211.5% | +663% | +703.9% | +1142.7% | +2943% |
| flat, 2% vol | +0% | +6.2% | +0.2% | +22.6% | +23% | +703.9% | +734.8% | +735% |
| flat, 4% vol | -0% | +6.1% | +0.5% | +59.1% | +59% | +703.9% | +824.0% | +824% |
| -1%/day, 2% vol | -60% | +6.2% | +0.2% | +22.6% | -50% | +703.9% | +734.8% | +238% |
| -1%/day | -60% | +6.2% | +0.2% | +22.6% | -50% | +703.9% | +734.8% | +238% |
| -3%/day | -94% | +6.2% | +0.2% | +22.6% | -92% | +703.9% | +734.8% | -46% |

- **Which part is the stock and which is the mechanism.** Read the "FUN in stock" columns as the mechanism and the
  "stock moved" column as the stock; "in USD" is their product. On the falling paths the mechanism is identical to
  the flat one — no take-profit ever fires, so the only buy-backs are LP-fee funded — and the dollar price is the
  stock's fall applied to it: FUN finishes −50% in dollars on a −60% stock, −92% on a −94% one, under the float-sells
  bound. On the rising paths the take-profits fund real buy-backs (130 stock spent at +3% a day, 2.2% of supply
  burned) and the mechanism adds +200% in stock, which the stock's own 14.3x then multiplies.
- **The burn is the robust number; the pool price is not.** At 20% daily volume the 10% buy tax burns 5–6% of
  supply in 90 days on every path. What that does to the pool depends on the float: at 80% sale the pool holds 10% of
  supply, so if the burned tokens were the pool's and nobody from the float sells into the rise, FUN is 7x in stock
  terms on wash volume alone. That bound is a statement about float behaviour, not about the mechanism, and the
  [LP depth experiment](./V2_LP_DEPTH_EXPERIMENT.md) shows how little float selling it takes to undo it: 5% of the
  float sold into the fresh pool halves the price.

### Table 3b. Flat stock, 2% vol, 90 days: the mechanism alone, by volume and sale share

| volume / day | sale | supply burned | FUN in stock, float sells the burn back | float never sells: burns only | with buy-backs |
|---|---|---|---|---|---|
| 5% | 44% | +7.6% | +5.5% | +113.2% | +117.1% |
| 5% | 60% | +5.8% | +5.5% | +113.2% | +117.1% |
| 5% | 80% | +3.2% | +5.5% | +113.2% | +117.1% |
| 20% | 44% | +15.5% | +22.6% | +703.9% | +734.8% |
| 20% | 60% | +11.9% | +22.6% | +703.9% | +734.8% |
| 20% | 80% | +6.5% | +22.6% | +703.9% | +734.8% |
| 50% | 44% | +19.6% | +60.6% | +3008.7% | +3161.6% |
| 50% | 60% | +15.0% | +60.6% | +3008.7% | +3161.6% |
| 50% | 80% | +8.2% | +60.6% | +3008.7% | +3161.6% |

Because volume is defined relative to the raise, the pool-price columns are the same at every sale share; what the
sale share changes is how much of the supply the same volume burns (a smaller sale is a smaller supply outside the
pool, so the same burn is a larger share of it) and how thick the pool is against the float (Table 1b).

## Stage 4: the pathological cases

Half the raise fills at $100; the stock halves or doubles over four days; then either nobody else buys, or the other
half fills at the new price, the launch graduates there, and the stock walks back to $100 over 30 days.

### Table 4. Half the raise fills at $100, the stock moves, the rest fills there, then the stock walks back to $100 over 30 days

| stock | rule | reserve USD before | after | USD still needed | group exit vs USD paid | USD cost to graduate | treasury lot cost | TP fired | stops | multiple vs hold | treasury USD |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 0.5x | trend_holder | $20,000 | $10,000 | $10,000 | -55% | $30,000 | $50 | yes | 0 | 0.84 | $10,000 -> $16,128 |
| 0.5x | tp_stop | $20,000 | $10,000 | $10,000 | -55% | $30,000 | $50 | yes | 0 | 0.73 | $10,000 -> $15,633 |
| 2x | trend_holder | $20,000 | $40,000 | $40,000 | +80% | $60,000 | $200 | no | 0 | 1.00 | $40,000 -> $27,520 |
| 2x | tp_stop | $20,000 | $40,000 | $40,000 | +80% | $60,000 | $200 | no | 69 | 1.61 | $40,000 -> $44,402 |

**A stock that halves before the raise fills.** The curve does not know. The 200 stock in its reserve are still 200
stock; the tokens sold still quote the same stock; the remaining 200 stock of raise still buy the same tokens. What
changed is that the reserve is worth $10,000 instead of $20,000 and the rest of the raise costs $10,000 instead of
$20,000. Whether anyone buys the rest is a question about demand for the stock, which this model cannot answer;
what it can say is that a new buyer pays half the dollars for the same position, and that an existing holder who
sells back gets their stock less two taxes, worth half: the group as a whole recovers 45% of the dollars it paid,
none of it from the mechanism. If the raise does complete at $50, the treasury's lot is booked at $50, and a stock
that merely recovers to $100 is a stock that has risen 100% against that lot: the rule takes profit from $65 up and
the treasury finishes at 0.84 in stock and $16,128 in dollars on $10,000 of graduation stock.

**A stock that doubles before the raise fills.** The mirror image, and the one that hurts. Graduating now costs
$60,000 instead of $40,000, the second-half buyers paid $200 a share for stock the treasury will hold, and every
early buyer's paper gain has doubled in dollars on top of the curve — so the dump the LP depth experiment measures
is twice as tempting. The treasury's lot is booked at $200. When the stock walks back to $100 the lot is 50% under
water, and the default rule never sells below cost: it holds, its take-profit sits at $260, and it does nothing
until the stock gets there. The treasury ends at 1.00 in stock (it sold nothing) and $27,520 in dollars against the
$40,000 it graduated with. The stop variant sells the lot on the way down and finishes at 1.61 and $44,402 — the one
regime in which a stop pays, which is the point made about stops above. **A launch that graduates at a top strands
its principal**, and nothing in the rule releases it except the stock returning.

## Findings, in plain language

**A stock that keeps rising.** The launch gets more expensive to complete in dollars, by the stock's own move over the
fill: 2% for a one-day raise, 61% for a 30-day raise at 3% a day. Early buyers do well twice over, on the curve and on
the stock, and their exit into the thin pool is worth twice what it was. The treasury sells: the default rule takes its
first profit at +30% and, on a stock that never comes back, converts the graduation stock into USDG by +60% and every
later tax lot the same way, so after 90 days of +3% a day it holds half of what sitting still would have. That is not a
loss in dollars — it is $140,000 on $20,000 — but it is the treasury underperforming its own stock by half, and the
token holder sees the difference as buy-backs that are smaller than they could have been (2.2% of supply burned). The
token's dollar price rises by the stock's multiple times a mechanism lift of roughly 2–3x in stock terms under the
conservative reading of the float.

**A stock that keeps falling.** Nothing in the launch reacts. The raise gets cheaper to complete (a third cheaper after
30 days at −3% a day), which is either a bargain for late buyers or the reason none come; the model cannot tell. Early
buyers still have a paper gain in stock and a smaller one in dollars. The treasury holds everything it receives, all the
way down: without a stop it never sells, never has a reserve, and never buys a dip. Its dollar value follows the stock,
cushioned only by the tax that keeps arriving. Its buy-backs come from the 0.30% LP fee alone: 0.2% of supply in 90 days.
The token's dollar price is the stock's fall applied to a small mechanical lift. With a stop the treasury converts to
USDG on the way down and keeps it — 1.8x to 8.8x in stock terms — but never re-enters while the decline lasts, and pays
for that protection in every other regime.

**Flat with volatility.** This is the only tape the rule earns on, and it earns about 1% over 90 days at 4% daily
vol while turning 0.5% of supply into buy-backs; at 2% daily vol it never trades at all. The mechanism moves the token's
stock price by +23% (float sells the burn back) to +735% (float never sells), which is the same on every path, and the
dollar price follows.

**What softens each.**

- *A smaller sale share* (44 or 60% instead of 80%) shrinks the raise from $40,000 to $7,900 or $15,000, so it fills
  faster and the stock's move over the fill matters less; it cuts the early buyer's multiple from 19x to 3–5x and the
  dump-alone return from 6x to 2–3x; and it leaves the pool holding 24 or 18% of supply instead of 10%, which is what
  keeps the pool price from being a function of the float's mood. It is the single parameter that touches every
  trend effect in stage 1.
- *A wide take-profit and no stop* lose least on a rising stock and burn most (0.51 and 2.2% of supply for 30/60
  against 0.29 and 1.0% for 5/10). Nothing makes the rule beat holding in a trend; the choice is how much to give
  away, and the answer is "trade rarely".
- *A stop* is the only thing that helps on a falling stock, and it helps by turning the treasury into a USDG account
  that does not re-enter. It costs in every other case. Offer it as the bet it is.
- *The tax level* sets the treasury's inflow and the token's burn together: 10% gives the treasury more stock in 90
  days than it got at graduation and burns 5–6% of supply; 1–2% makes the curve round trip cheap (2–4%) and gives
  the treasury a tenth of that. Under a falling stock the inflow is the only thing slowing the dollar decline.
- *Booking daily, not hourly*, keeps a falling stock's tax from stranding itself outside the 128-lot cap.
- *Graduating slowly* is the exposure: every day of fill is a day the dollar cost and the treasury's lot cost can
  move. A launch that graduates at a top locks a lot the rule cannot release.

## Volume is an input, not a prediction

Every buy-back, burn and tax number above scales with the daily volume assumed for the FUN pool — 5%, 20% or 50% of
the raise, as round trips. Nothing in this repository measures what a V2 launch's volume will be; the first graduated
pools will. Until then read every "supply burned" and "FUN in stock" figure as "per 20% of the raise traded a day",
and read the 20% case against the pool's size: at $40,000 of raise it is $8,000 a day through a pool holding $40,000,
eighteen turnovers of the pool in 90 days.

## What this does not model

Speculative flow of any kind, including holders selling into the buy-backs or the rise — the two bounds of stage 3
are the edges of that omission, not a substitute for it. Sandwiching of the treasury's swaps ([V2_SANDWICH_FORK.md](./V2_SANDWICH_FORK.md))
and pre-pushing of buy-backs beyond the impact cap. The stock pool's depth (35 bps covers fee and slippage on a lot
that fits; a lot that does not leaves in `sellChunkUsdg` pieces). Chainlink freshness, the 24/5 calendar and closure
pricing. Raw-unit rounding, sqrt-price quantization, V4 price limits. The 3-second opening window. Kind-1 buy-back
treasuries. The protocol's and creator's claims. Gas. Every one of these is a reason to run the fixed-block fork
suites in [lab/README.md](../lab/README.md) before trusting a parameter this model likes.

## Regenerating

```sh
python3 -m lab.trend                                  # all tables, base launch
python3 -m lab.trend --sale-bps 6000 --tax-bps 200 --volume 0.05 --rule tp_stop
python3 -m unittest lab.test_trend -v
```

`lab/trend.py` exposes `stock_path`, `pregraduation`, `run_post_graduation`, `fun_price_decomposition` and
`pathological` for other sweeps; the rule it runs is `tools/rule_backtest.py`'s `Ledger`, and `lab/test_trend.py`
pins that tool's historical output so the factoring cannot have changed it.

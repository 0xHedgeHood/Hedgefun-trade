# V2 graduation depth: LP share and sale share — 2026-09-25

What share of a curve's raised stock should seed the V4 pool, and what share should fund the strategy treasury?
Two instruments: the continuous model in `lab/model.py` (a sweep, below) and `test/V2LpDepthExperiment.t.sol`,
which runs the production curve, factory, hook, vault and router on a local V4 PoolManager and logs what the model
leaves out. Neither is a market. Both use the documented example (supply 1,000,000; sale 80% raises 400 stock at
V = 100; the Foundry fixture raises 200 at V = 50 — same shape, half the scale).

## What the numbers say

**LP share decides who pays for the early buyer's exit, not whether they can exit.**

| LP share | pool stock / treasury stock | pool FUN as % of float | sell 1% of float → price | 5% | 10% | 25% |
|---|---|---|---|---|---|---|
| 25% | 100 / 300 | 6% | 72% | 28% | 13% | 3% |
| 50% (default) | 200 / 200 | 11% | 84% | 48% | 28% | 10% |
| 75% | 300 / 100 | 17% | 89% | 59% | 39% | 16% |
| 100% | 400 / 0 | 22% | 92% | 67% | 48% | 22% |

pump.fun and Virtuals put 100% of the raise into the pool and keep no treasury, so the 100% row is their shape; at
50% the pool is half as deep as theirs relative to float.

A buyer of the **first 5%** of the sale (cost 0.0001, terminal 0.0025) who sells everything into the fresh pool:

| LP share | model: net multiple, price after | contracts (fixture, LP 50%): multiple, price after |
|---|---|---|
| 50% | 13.4x, 48% | **13.4x, 47%** |
| 75% | 14.9x, 59% | 14.9x, 58% |

The contract run reproduces the model to the percent. A deeper pool pays the early buyer *more* (they extract more
of the later buyers' stock) while crashing the price *less*. A **last** buyer who dumps loses 20–68% at every LP
share: the 10% buy-tax burn plus the 10% sell tax already make that unprofitable. Depth is not an anti-dump control.

**The sale share is the bigger lever.** Open-to-terminal multiple is `(1 / (1 − sale))²`:

| sale | multiple | raised | early-5% dump (LP 50%) | 5% of float sold → price |
|---|---|---|---|---|
| 60% | 6x | 150 | 4.0x | 67% |
| 70% | 11x | 233 | 6.7x | 59% |
| 80% | 25x | 400 | 13.4x | 48% |
| 90% | 100x | 900 | 41x | 28% |

On the contracts, sale 70% + LP 75% gives the early dumper 7.2x and leaves the price at 69% of graduation; sale
80% + LP 50% (the defaults) gives 13.4x and 47%.

**Historical spike scenario (disabled for graduated V2 pools).** This experiment was run before V2 froze
`spikeBps = 0` to prevent LP-fee-funded, permissionless buybacks from repeatedly raising the sell tax. It remains
a counterfactual measurement of the original configuration, not a current launch outcome. In that scenario,
after a treasury buyback (`noteEvent`) the sell tax opens at
90% and decays to the flat 10% over 120 s. The same early dump at spike+0 s returns 3.1 stock (1.5x) instead of
27.9 (13.4x); at +60 s, 8.2x; at +119 s, back to 13.4x. The pool price lands at 47% in all three: the tokens still
hit the pool, the tax only moves who keeps the stock. There is no spike at graduation itself (the curve was the
price discovery), so the first minutes after graduation are the flat 10%.
The current test replays a buyback notification at +0, +60 and +119 seconds and asserts the V2 rate stays flat;
the historical spike figures above require the pre-fix ref.

**What flows back to the treasury from a dump.** The hook tax on a dump is 10% of gross stock out, of which the
treasury's share is `1 − protocolBps − creatorBps` (70% in the fixture): about 2.2 stock on a 31-stock dump. The
V4 LP fee is charged on the *input* currency, so a sell pays its 0.30% in FUN — burned by the vault — and credits
**zero** stock to the buyback budget. Only buys feed the stock-side LP fee. A treasury cannot buy back at t+0:
graduation stock is strategy principal, and `buybackStock` only fills from profit and stock-side LP fees.

## What this does not measure

Real selling pressure after graduation (how much of the float actually sells, and when), routing through the
stock/USDG V3 leg, sandwiching of the dump itself, treasury strategy P&L, and the stock's own price moves. The
model and the fixture both trade at a fixed stock price. These are the reasons the LP share is a **per-stock
setting** (`V2TreasuryDeployer.setLpBps`) and not a constant: set it from measured post-graduation flow.

## Working hypothesis for the first listings

Sale 70%, LP 60%: pool depth in the pump.fun range, early-buyer multiple under 8x, treasury keeps 40% of the raise
plus all stock-side LP fees and 70% of hook tax. Revisit after the first graduations with the actual sell curve.

```sh
forge test --match-path test/V2LpDepthExperiment.t.sol -vv
python3 -c 'from lab.model import simulate; print(simulate({"supply":1_000_000,"virtual_stock":100,"lp_bps":7500,"sell_fraction_bps":500}))'
```

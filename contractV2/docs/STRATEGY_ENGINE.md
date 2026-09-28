# V2 strategy engine boundary

The strategy engine separates a policy's decision from the treasury's authority. A registered policy can return
only one fixed-width intent: hold, buy stock, or sell stock, plus a nonce-bound next-state word. It cannot choose a
pool, route, recipient, approval, callback, arbitrary calldata, or asset.

`HedgeFunV2EngineTreasury` is the custody and risk boundary. On every execution it independently checks:

- the domain-separated launch config hash and current strategy nonce;
- the registered policy runtime code hash, call gas, and exact 160-byte return size, with an undeclared action word
  refused as `BadPolicyReturn` before the enum decode (the decoder alone would revert with empty data);
- live oracle and venue health;
- policy capability, cooldown, target/deadband direction, per-call notional, and daily turnover;
- actual swap input/output, including the minimum executable lot, before committing inventory, turnover, state, or
  nonce. A price-limit dust fill reverts the swap and provisional inventory update atomically and cannot renew the
  cooldown.

Each check in this list fails a test when it is deleted. The ones the rest of the suite did not pin are in
`test/V2StrategyEngineGuards.t.sol`, each named for the audit round 4 mutation it catches.

The creator commits to a fixed-width `EngineConfig` for its own `(symbol, creator, nonce)` salt. The config is
appended to initcode, so changing it changes the CREATE2 treasury address and therefore the factory terms. Engine
kinds and policy registrations are append-only. Disabling a policy prevents new predictions/launches but cannot
rewrite an already deployed treasury: its policy identity is held in immutables, and its limits in a config struct
the constructor writes once and nothing can set again.

## The config and its floors

`CONFIG_SCHEMA_V1` packs `targetBps` (bits 0-15), `deadbandBps` (16-31), `cooldown` seconds (32-63) and
`payoutBps` (64-79) into `words[0]`, whose higher bits are reserved and must be zero; `words[1]` is `maxTradeUsdg`
and `words[2]` `maxDailyTurnoverUsdg`. A launchable config clears every row below (audit round 4, E-1, E-2, X-5):

| word | bound | why |
|---|---|---|
| `targetBps` | 2,000 to 9,000 | a treasury graduates 100% stock and sells straight down to target: under 20% that is "liquidate the lot at graduation"; over 90% the band has no room |
| `deadbandBps` | at least `2 x (maxSlippageBps + poolFeeBps)`, below `targetBps`, and `targetBps + deadbandBps < 10,000` | V1's `tp1Bps`/`dipBps` rule: a band inside its own execution friction acts on every Chainlink print and each round trip returns to the same price with less value. 260 bps at 100 bps slippage on a 0.30% pool |
| `cooldown` | at least 600 s | one V3 TWAP window: two actions never share one pinned mean, and a day holds at most 144 actions whatever the daily cap says |
| `maxTradeUsdg` | from the listing's `minLotUsdg` to its `sellChunkUsdg` | an action under the minimum lot can never execute, so the treasury would be inert for life; one over the chunk exceeds the owner's per-call sizing |
| `maxDailyTurnoverUsdg` | from `maxTradeUsdg` to `24 x maxTradeUsdg` | a cap under one action is a cap of zero; one over 24 actions no longer bounds a day |
| `payoutBps` | 0 to 10,000 | the share of a sale's gain that funds the burn; see below |

The creator chooses every word; the owner chooses only the listing's lot, chunk, slippage and pool. The floors are
structural, not tuned: they stop a config that cannot work, not one that is merely poor. Where to sit above them
(the economics lane recommends a deadband of 100 bps or more and a cooldown of an hour) is the creator's choice,
visible in the frozen config before anyone buys.

The check is one function, `SpotEngineConfig.valid`, run in two places on the same inputs:

- the engine constructor, which is the authority;
- `V2TreasuryDeployer`, which must refuse by name everything the constructor would refuse, because a constructor's
  revert reason does not survive CREATE2. `setEngineConfig` refuses every bound that needs no listing data;
  `predict` and `deploy` then run the full set with the listing's own `minLotUsdg`, `sellChunkUsdg`,
  `maxSlippageBps` and pool fee. Both revert `BadEngineConfig`, so `predict` never quotes a config that `launch`
  would fail as an opaque `TreasuryDeployFailed`. The deployer applies this layout only to a kind registered as
  (`SPOT_ENGINE_V1`, `CONFIG_SCHEMA_V1`), the one pair the spot constructor accepts.

`test/V2StrategyEngineConfig.t.sol` hands the same words and listing parameters to both paths and asserts they
agree at every floor's edge and under fuzz. `V2RebalancePolicy` validates a looser subset of the same words; it is
advisory, and the engine's check is the binding one.

The factory's `V2TreasuryDeployer` reference is also immutable. A production factory deployed from the pre-registry
PR84 baseline cannot acquire these engine APIs later through `registerKind`; the factory and deployer must be deployed
from the final combined bytecode. Any earlier deployment is a disposable rehearsal, not an upgrade path.

## Gains fund the burn

A strategy token's claim to its holders is the burn, and kind 0 routes the profit share of every take-profit to it.
The engine does the same with one number, `payoutBps`, chosen by the creator and frozen in the config (audit round
4, X-7).

The inventory carries one average cost, `avgCost`, in the oracle's price units. Stock that is booked (the
graduation share, sell tax, anything donated) enters at the live oracle price, the price kind 0 books a lot at; out
of hours there is no such price, so it waits and `execute()` books it first. Stock that is bought enters at its
fill price, the USDG spent over the stock received. Both are weighted by quantity and rounded up, so rounding never
creates a gain. A sale leaves the average unchanged.

A sale of `q` at a price `p` above `avgCost` has a gain of `q x (1 - avgCost / p)`. `payoutBps` of that gain stays
in stock and moves to `buybackStock` instead of being sold, for the inherited, paced `buyback()` to spend and burn.
The principal and the rest of the gain are sold for USDG. Inventory falls by `q` either way, and `q` is what counts
against `maxTradeUsdg`, the daily cap and the minimum lot. A short fill takes out only the share of the gain and of
the payout that the sold amount stands for, so a fill of dust moves dust. A sale at or below the average cost has no
gain and moves nothing to the buy-back. `payoutBps = 0` is a pure rebalance whose buy-back is funded by LP fees
alone.

Each such sale emits `GainToBuyback(gain, toBuyback, avgCost, price)`, so an indexer can show what share of the
strategy's gains has gone to the burn. The policy is handed the config but ignores `payoutBps`; the treasury applies it.

## Policy admission

A release policy must be stateless and non-proxy, with reproducible bytecode. Its dependency and audit manifest
hashes must identify the reviewed source, compiler/settings, dependencies, tests, and audit evidence. Runtime
codehash binding detects code replacement, but cannot prove that an implementation does not read mutable storage or
delegate through a proxy. The spot core therefore treats every policy as adversarial and caps the damage of any
intent; governance review must still reject mutable and proxy policies.

The stock and USDG legs are admitted as exact-transfer, non-rebasing assets. Fee-on-transfer, sender-surcharge,
negative-rebase, and issuer-burn behavior can make a pool-reported fill diverge from the treasury's balance buckets
and must fail asset admission. Their implementations and upgrade beacons require continuous monitoring.

`maxDailyTurnoverUsdg` is a limit per US trading date, not per UTC day and not a rolling 24-hour window (a founder
decision after audit round 4, X-2). The bucket is `tradingCalendar.tradingDate(block.timestamp)`, where
`tradingCalendar` is the listing oracle's own `TradingCalendar`, read once at construction: the date rolls at 20:00
New York time, with US daylight saving, the same session boundary the oracle's open/closed schedule keeps. So one
bucket is one equity session, Sunday 20:00 to Friday 20:00 New York time a day at a time. Midnight UTC is 19:00 in
New York in winter, an hour before the session ends, and no longer starts a new bucket there; in summer it is 20:00
and coincides with the roll. It is still a calendar bucket: two caps can be spent
either side of 20:00 New York, the cooldown (at least 600 s) being all that separates them. The cooldown remains
active across the boundary.

Stock or USDG sent directly to the treasury is an irrevocable donation and becomes part of the next strategy
observation. It can change when an action crosses the deadband, but cannot select a route or recipient and remains
subject to the same minimum lot, cooldown, slippage, per-action, and epoch limits.

## Options extension

The registry, fixed-width config commitment, policy identity, capability model, bounded `STATICCALL`, and
CREATE2/restatement flow can be reused for options. The spot engine itself cannot.

An `OptionsEngineV1` must be a separately registered engine version with option-specific actions and invariants:

- collateral reservation and free-collateral accounting;
- expiry, exercise window, and settlement-source validation;
- short/long position accounting and bounded negative liabilities;
- strike/contract allowlists and per-series concentration limits;
- exercise/assignment/settlement state transitions and emergency expiry handling.

The spot engine rejects all option capability bits. Registering an options policy against it must fail before a
treasury can launch. This keeps later options support inside the same framework without pretending that spot
buy/sell solvency checks cover derivatives.

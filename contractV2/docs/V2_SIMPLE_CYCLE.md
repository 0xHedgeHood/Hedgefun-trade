# V2 simple trading cycle

`HedgeFunV2CycleTreasury` adds one bounded recovery entry to the existing fixed lot rules:

**hold → take profit or stop → wait → buy a dip or make one recovery buy → hold again.**

It has the same constructor and `Params` as kind 0. It is an opt-in, separately registered strategy kind;
there is no new policy engine, indicator history, target-allocation model, configuration ABI or factory.
Already deployed treasuries and the existing kind registrations cannot be changed.

## Frozen rules

| Rule | Behavior |
|---|---|
| Sale priority | The inherited scheduler processes a due stop, then a due take-profit, before any buy. A remaining due partial sale still wins. |
| Recovery reference | A real stock/USDG sale whose actual stock input is worth at least `minLotUsdg` at the sale's oracle price, and which returns positive USDG, opens or refreshes recovery. The reference is the oracle price used by that sale, not its average fill price. |
| Recovery trigger | The live price reaches `saleReference × (1 + dipBps/10,000)`. `dipBps` also keeps its original meaning for ordinary dip buys. |
| Waiting | Recovery requires at least 600 seconds since the qualifying sale and a stock-report timestamp newer than that sale's report. A later actual stop sale also requires its own 600 seconds and newer report, even when too small to refresh recovery. Its independent recovery gate survives a later small or zero-sale TP; the inherited TP-to-dip gate keeps its original behavior. Ledger-only dust retirement creates no stop observation. |
| Dust cleanup | The current scheduler can retire an uneconomic stop/profit tail into unbooked stock without a sale, bounty or new recovery/cooldown reference. It continues to an actual due sale or an eligible dip/recovery buy in the same call. If no dip/recovery passes the readiness checks, including when pending stock fills all 128 lot slots, cleanup and booking are retained and recovery stays pending. An actual swap or minimum-fill failure still reverts the whole call. |
| Recovery sizing | Offer `min(USDG reserve × lotBps/10,000, sellChunkUsdg)`, less the existing bounty provision. The normal venue/slippage, minimum actual fill and 128-lot checks still apply. |
| Successful buy | Both dip and recovery buys consume the recovery reference after a real buy passes the minimum-fill check. The purchased lot uses its actual fill cost and follows the same stop/TP rules. A successful partial fill consumes the entry once; it does not promise to fill an allocation target. |
| Repeated calls | Recovery cannot buy again until another qualifying sale opens it. Ordinary dip rungs remain available under the existing rules. |
| No condition or insufficient cash | Wait. A revert, zero fill or actual buy below `minLotUsdg` rolls back all effects and leaves the reference available. |
| Scheduled closure | Recovery cannot execute or be opened by a closure-priced sale. A qualifying closure TP cancels an older recovery reference. A dust/zero sale neither opens nor refreshes it. |

The original dip behavior is deliberately retained: a TP followed by a qualifying dip has no newly introduced
600-second wait; the original post-stop dip still needs its stop cooldown, a newer stock report and a deeper
price. Stops/profits are checked again on every call. Recovering upward cannot skip them.

Five extra values are stored: qualifying sale reference price, sale time and stock-report time, plus two private
values retaining the latest stop time and report for recovery. A TP can clear the inherited dip gate without
erasing this independent recovery protection. Any successful buy or recovery cancellation clears all five. There is no
need to write state on a `Hold`/`NotDue` observation. Bookings, donations, LP fees and token buybacks do not open
recovery. `buybackStock` remains a separate bucket; the new entry spends only the USDG reserve. This introduces
no redemption or migration mechanism.

## Integration and release

1. Compile the reviewed commit and check runtime/initcode sizes. The added contract must remain below EIP-170,
   and creation code plus the unchanged constructor arguments below EIP-3860.
2. On an existing compatible V2 deployer, create the two code chunks with `makeChunks` using this contract's
   creation code, then have the factory owner register them through `registerKind`. Record the returned kind ID;
   IDs are registration-order dependent. Do not replace the existing deployment address book or kind 0.
3. A creator selects that returned ID with `setStrategyKind(symbol, nonce, kind)`, re-quotes, and launches with
   the new terms. The changed code changes the treasury address. A stale quote must revert `Restated`.
4. Integrations recognize the appended action `BuyRecovery = 5`; values 0–4 keep their existing meanings. Export
   the ABI from the reviewed commit with `tools/export_abi.py`; the new ABI is included in that exporter.
5. Before operating a live signer, verify the exact registered creation code, factory/chain/treasury address,
   frozen `Params`, oracle freshness, and the new launch's normal graduation/wiring. This task does not register,
   launch or broadcast anything on a network.

Deployment and signing remain an operator action under the repository's workflow. The
[registration test](../test/V2CycleTreasury.t.sol) covers code selection, prediction, stale terms, launch and
graduation using the existing factory/deployer locally.

## Checking and execution

The permissionless entry point is still `execute()`. An execution service periodically simulates that exact
call from its intended caller address. A successful simulation returns `(action, lotId)`; the service may then
submit the same call and let the contract recompute all conditions. `NotDue`/`Cooldown` mean wait; unhealthy
pricing means pause. Unknown errors must be surfaced rather than silently classified as waiting.

`reentryPending()` is the stored wait flag. `recoveryDue()` reports only the live price/time/report gate; it is
not proof of available cash, lot capacity, venue health or absence of a higher-priority sale. Always simulate
`execute()` before a transaction.

The included [read-only checker](../tools/v2_cycle_keeper.py) accepts no key and makes no write RPC calls:

```sh
python3 tools/v2_cycle_keeper.py --rpc <public-rpc> --chain-id <expected-chain-id> \
  --treasury <new-cycle-treasury> --caller <intended-caller> --once
```

Without `--once` it checks every 60 seconds (override with `--interval`) and prints changes of state. It is a
checker and transaction-payload producer, not a signer. No background service is started by this change.

## Evidence and limits

Source #110's [audit](V2_SIMPLE_CYCLE_AUDIT.md) and [backtest](V2_SIMPLE_CYCLE_BACKTEST.md) are historical records. Target #12 integrates Cycle with the current dust scheduler on `codex/contract-v1`; current build sizes, fuzz and regression results are recorded separately in [V2_CYCLE_INTEGRATION_REVIEW.md](V2_CYCLE_INTEGRATION_REVIEW.md).

The [dust/recovery fuzz suite](../test/V2CycleDustFuzz.t.sol) defines six properties in each asset order: sale-free cleanup, unchanged recovery cooldown, same-call recovery progress, actual-fill stop observations and rewards, ordinary dip gates, and capacity-128 cleanup/booking. Local fills use a real concentrated-liquidity PoolManager through a V3-ABI mirror; tokens and feeds are synthetic.

The [cycle suite](../test/V2CycleTreasury.t.sol) exercises both asset orders, exact recovery threshold and
cooldown, newer report requirements, repeated calls, two complete cycles, sale priority, preservation of ordinary
dip rungs, bounded funding, stale/paused feeds, closure cancellation, real short/dust fills and atomic rollback.
The inherited scheduler regressions also run on the new kind. Shared primitives retain their old defaults.

The [backtest report](V2_SIMPLE_CYCLE_BACKTEST.md) pairs the old and new rules on the same frozen AAPL, GME, NVDA
and TSLA history. Recovery buys can resume participation; they can also lose more through repeated stop/recovery
cycles. The report includes unchanged cases and adverse results. Daily-close replay is not intraday execution,
and it does not measure realized FUN burns, LP fee inflows, gas or live venue liquidity.

**GO:** review and test this opt-in kind and its reproducible replay. **NO-GO:** automatically switch existing
funds or declare the recovery settings universally better. Live registration and operation need the operator's
release verification above; no production signer is included.

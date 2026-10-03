# Ordinary V2: creator-selected parameters

## Policy

Creators choose ordinary TP1, TP2, dip and stop independently of slippage, pool fees and keeper rewards.
There is no 1x/2x economic trigger minimum and no actual-TP net-profit revert. A price trigger is a creator's
published execution rule; it is not a protocol profitability guarantee. The protocol screens and lists stock
venues, then keeps oracle health, bounded slippage and asset accounting checks during execution.

The filename and `HedgeFunV2AllInTreasury` / `allInTriggerCodeHash` names are retained for client compatibility.
The earlier 1x-plus-net-value proposal is superseded by this creator-selected policy.

## Parameter and execution checks

[V2CreatorParams](../src/v2/strategy/V2CreatorParams.sol) checks only the original rule shape:

- TP1 is positive; TP2 is zero or greater than TP1.
- Dip is positive and below 100%; stop is zero or below 100%.
- Solidity field widths and inherited allocation, reward, band, deviation, slippage and chunk bounds remain.

[The ordinary wrapper](../src/v2/HedgeFunV2AllInTreasury.sol) validates those original rungs before passing a
separate temporary parameter copy through legacy constructor checks. It raises only the temporary legacy
TP/dip rungs, disables the temporary stop and restores the creator's exact original parameters afterward.

Actual TP swaps may realize a loss after venue and reward costs. Caller reward still requires actual nonzero
stock sold and USDG received; partial-fill ledgers and reward sizing use that actual fill. An empty fill reverts.
Zero-value whole lots move to buyback inventory without a reward via `DustCleared`. A selected remainder whose
principal cannot return a raw USDG unit moves to buyback via `RemainderReclassified`, also without a reward.
Neither maintenance operation records a sale price or clears the stop-reentry gate. These rules preserve
inventory and release tail slots without manufacturing a keeper payment.

The [new registry](../src/v2/V2TreasuryDeployer.sol) applies the structural rule and exempts the exact reviewed
ordinary creation-code hash from its economic stop floor, including aliases of that same code. Other registered
kinds keep their existing rules. V1, legacy ordinary V2, buyback and Engine creation/runtime bytes remain
unchanged; the existing Engine policy is a distinct strategy family.

## Screened pools

Factory listing remains an owner action. Each stock is bound to its matching oracle and the canonical V3 pool
for that stock/USDG pair and fee. Launchers choose a listed stock; they cannot inject an arbitrary pool address.
The deployment tool reuses the eight screened testnet venues, checks liquidity and observation history, and
copies listing gates. These venue/execution checks do not impose a TP/dip/stop return target on the creator.

## Existing deployment versus full creator freedom

The existing fee factory and registry are immutable. Appending the new code as kind4 can free TP/dip and remove
the net-profit check, but that old registry still requires stop to be disabled or above its old friction floor.
An append cannot remove that check. **Full TP/dip/stop freedom therefore requires a fresh registry and factory**,
plus their factory-bound hook, deployers and routers. Reuse the existing assets, oracle and pools.

The new frontend uses a separate creator deployment, resource and draft namespace. Full freedom is enabled only
after exact wrapper and registry runtime/binding verification; a fee feature label alone is insufficient.
Existing strategies stay reachable through their original deployment routes.

[The release runbook](V2_ALL_IN_TESTNET_RUNBOOK.md) distinguishes the fresh-core release from the optional,
limited old-core append. Candidate files and local forks are not canonical live deployment evidence.

## Verification

[V2CreatorParameters.t.sol](../test/V2CreatorParameters.t.sol) covers 1-bps creator rungs, structural/field bounds,
constructor/prediction/deployment consistency, aliases and legacy-family isolation.
[V2CreatorTakeProfit.t.sol](../test/V2CreatorTakeProfit.t.sol) covers actual adverse fills that are allowed by the
creator's policy, partial fills, empty-fill rejection, inventory conservation, maintenance rewards, slot capacity
and stop gates. [The fresh-core testnet fork](../test/TestnetV2CreatorCore.t.sol) rehearses deployment on the eight
existing pools, creation with TP1/dip/stop all 1 bps, and graduation. Historical legacy fork scenarios remain
separate compatibility evidence.
[The deployment regression](../test/DeployV2CreatorTestnet.t.sol) injects third-party registry chunk creation
before registration and proves that the new release uses operator-created chunks with exact reviewed code.
It also checks the separate unverified 40-transaction creator candidate while preserving the old 38-transaction plan.

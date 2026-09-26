# Hedgefun contracts V2

This directory is the V2 source snapshot from the integration branch `codex/v2-main-integration` (tip
`03ad70e`), applied on top of the V1 snapshot in [`contractV1/`](../contractV1/README.md). V2 is a **separate
deployment**: nothing launched under V1 changes. Files keep their paths relative to the Foundry project, and the
compiler version, optimizer settings, EVM version, metadata-hash setting and dependency revisions are the same as V1.

## What V2 adds

A launch no longer opens straight into a Uniswap V4 pool. It starts on a stock-denominated **bonding curve**, and the
buy that reaches the curve's terminal price atomically **graduates** it: the real stock reserve is split between a
permanently locked V4 full-range position and the strategy treasury, and the treasury's rule is switched on.

| File | Role |
| --- | --- |
| `src/v2/HedgeFunV2Factory.sol` | V2 listings, `predict`/`launch` with V1's terms commitment, and the authenticated `graduateCurve()` path |
| `src/v2/CurveDeployer.sol` | Holds the curve creation code and the one-time graduation execution (the factory is 143 bytes under EIP-170) |
| `src/v2/HedgeFunBondingCurve.sol` | Per-launch fixed-product curve: buys, sells, launch-window buy tax, fee liabilities, the graduation trigger |
| `src/v2/V2LiquidityVault.sol` | Owns the locked full-range V4 position; fee-only collection, no liquidity removal or upgrade path |
| `src/v2/HedgeFunV2Treasury.sol`, `src/v2/V2TreasuryDeployer.sol` | The V1 rule, inactive until `wire()`; one atomic `execute()` (stop first, then take-profit, then dip) and pluggable strategy kinds |
| `src/v2/HedgeFunV2BuybackTreasury.sol` | Draft kind 1: a pure buy-back treasury (not registered by any deployment) |
| `src/v2/HedgeFunV2TradeRouter.sol`, `src/v2/HedgeFunV2NativeRouter.sol` | Any-ERC20 and native-currency entry and exit through V3 hops, with minimum-out and explicit partial-fill refunds |

The four V1 files that changed (`HedgeFunFactory`, `HedgeFunTreasury`, `HedgeFunTreasuryBase`, `hooks/HedgeFunHook`)
changed to let V2 inherit them; the V1 deployment does not pick those changes up.

Design and review notes are in `docs/`: start with [`V2_BONDING_CURVE.md`](./docs/V2_BONDING_CURVE.md), then
[`V2_DUAL_ENGINE_REVIEW.md`](./docs/V2_DUAL_ENGINE_REVIEW.md) and [`V2_ADVERSARIAL_REVIEW.md`](./docs/V2_ADVERSARIAL_REVIEW.md).
Some links inside those documents point at parts of the main repository that this snapshot omits.
`lab/` is the offline research workbench those documents cite (Python, no chain access needed for the model).

## Build and test

Install [Foundry](https://getfoundry.sh/), then from the repository root:

```sh
git submodule update --init --recursive
cd contractV2
forge build --sizes
forge test
```

The fork tests (`V2LiveVenueFork`, `V2LowFrequencyFork`) skip unless `RH_FORK=1` is set; they then fork Robinhood
Chain at a pinned block through the `robinhood` RPC alias in `foundry.toml` and never broadcast.
`script/RehearseV2Launchpad.s.sol` is the fork-only deployment rehearsal; it reads its addresses from the environment
and is not a deployment record.

## Status

**Not deployed and not approved for launch.** Opening-sniper economics remain a product decision (the market
scenario tests still find profitable sandwiches at seconds 1 and 2 of a launch), the factory sits 143 bytes under
the EIP-170 limit, and this source has not had a third-party audit. Strategy tokens still give holders no claim on
the treasury and no redemption right.

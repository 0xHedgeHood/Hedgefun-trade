# Hedgefun contracts V2

This directory is the self-contained V2 contract snapshot from main at `9b872a2` (including PR #99's public-testnet harness), plus the deployment-manifest verification fix at `1d42241` and expanded testnet scenarios at `c414374`. The Solidity files in `src/` are byte-identical to that source commit. V2 is a **separate deployment**: nothing launched under V1 changes. Compiler settings and pinned dependency revisions remain unchanged.

## What V2 adds

A launch no longer opens straight into a Uniswap V4 pool. It starts on a stock-denominated **bonding curve**, and the
buy that reaches the curve's terminal price atomically **graduates** it: the real stock reserve is split between a
permanently locked V4 full-range position and the strategy treasury, and the treasury's rule is switched on.

| File | Role |
| --- | --- |
| `src/v2/HedgeFunV2Factory.sol` | V2 listings, `predict`/`launch` with V1's terms commitment, and the authenticated `graduateCurve()` path |
| `src/v2/CurveDeployer.sol` | Holds the curve creation code and the one-time graduation execution |
| `src/v2/HedgeFunBondingCurve.sol` | Per-launch fixed-product curve: buys, sells, launch-window buy tax, fee liabilities, the graduation trigger |
| `src/v2/V2LiquidityVault.sol` | Owns the locked full-range V4 position; fee-only collection, no liquidity removal or upgrade path |
| `src/v2/HedgeFunV2Treasury.sol`, `src/v2/V2TreasuryDeployer.sol` | The V1 rule, inactive until `wire()`; one atomic `execute()` (stop first, then take-profit, then dip) and pluggable strategy kinds |
| `src/v2/HedgeFunV2BuybackTreasury.sol` | Kind 1: a pure buy-back treasury, opt-in (production must register its exact code chunks) |
| `src/v2/HedgeFunV2EngineTreasury.sol`, `src/v2/strategy/IStrategyPolicy.sol` | The strategy engine: a treasury that executes a registered, stateless policy's intent (hold / buy / sell) under its own custody, cooldown, per-call and daily-turnover limits; the policy is pinned by runtime code hash and committed in the CREATE2 config |
| `src/v2/strategy/V2RebalancePolicy.sol` | The first policy: keep stock at a target share of treasury value, act outside a deadband |
| `src/v2/HedgeFunV2TradeRouter.sol`, `src/v2/HedgeFunV2NativeRouter.sol` | Any-ERC20 and native-currency entry and exit through V3 hops, with minimum-out and explicit partial-fill refunds |

The four V1 files that changed (`HedgeFunFactory`, `HedgeFunTreasury`, `HedgeFunTreasuryBase`, `hooks/HedgeFunHook`)
changed to let V2 inherit them; the V1 deployment does not pick those changes up.

Design and review notes are in `docs/`: start with [`V2_BONDING_CURVE.md`](./docs/V2_BONDING_CURVE.md), then
[`STRATEGY_ENGINE.md`](./docs/STRATEGY_ENGINE.md),
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

## Public testnet

Run all commands from `contractV2/`. Python tools require **Python 3.11 or newer** (the standard-library TOML reader resolves Foundry RPC aliases). The testnet harness targets Robinhood Chain **46630** and refuses other chains. It uses test USDG, mintable test stocks, operator-set feeds, and its own V3 pools; these assets have no value. See [`TESTNET_V2.md`](./docs/TESTNET_V2.md) for prerequisites, the operator runbook and frontend integration boundaries.

```sh
python3 -m unittest discover -s tests -p 'test_*.py'
forge test --match-contract DeployV2TestnetTest
# Read-only listing checks after a verified deployment:
python3 tools/v2_launch_check.py --testnet
python3 tools/v2_launch_check.py --testnet --sale-bps 9000
```

The operator supplies a testnet-only signer and test ETH. Dry-run output and an intended broadcast are not evidence of deployment; verify receipts and live roles before sharing addresses. No keys or signing credentials are included. Some PR mirrors include already-published testnet deployment records and mined receipts; they are historical source evidence, not a new deployment.

## Build sizes

At this snapshot, `forge build --sizes` reports runtime sizes of 24,397 bytes for `HedgeFunV2Factory` (179 bytes below EIP-170), 16,730 for `CurveDeployer`, 11,445 for `V2TreasuryDeployer` and 19,354 for `HedgeFunHook`. Recheck these after any source or compiler change.

## Status

**Testnet candidate; mainnet launch approval is not established by this snapshot.** Testnet uses much deeper pools than mainnet and deliberately replaces price feeds and assets. Mainnet still requires reviewed owner/recipient addresses, actual listing-depth checks, V2 frontend and keeper integration, and verified deployment receipts. The opening-window economics remain a product choice. Strategy tokens give holders no claim on the treasury and no redemption right.

The [X creator launch and settlement design](./docs/X_CREATOR_LAUNCH_DESIGN.md) describes a separate, unimplemented integration; X Money payouts are not part of this release.

The scoped security review is in [`V2_TESTNET_REVIEW.md`](./docs/V2_TESTNET_REVIEW.md). It is an engineering review, not a claim of independent audit certification.

## Source PR #110

This branch mirrors [Add an opt-in V2 trading cycle with one recovery entry](https://github.com/keyuyuan/hedgefund/pull/110) at source commit `e6a6097ca622da1d7342b48fb0f1771b08832026`. Contract files retain their source bytes. The source [README](https://github.com/keyuyuan/hedgefund/blob/e6a6097ca622da1d7342b48fb0f1771b08832026/README.md) and validation claims belong to that pinned development snapshot; mirror checks are reported separately in the pull request. Run local commands from `contractV2/`.

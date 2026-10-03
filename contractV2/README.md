# Hedgefun contracts V2

This directory began as the self-contained V2 contract snapshot from source main at `9b872a2` (including PR #99's public-testnet harness), plus the deployment-manifest verification fix at `1d42241` and expanded testnet scenarios at `c414374`. The stacked mirrors below add later V2 source changes. V2 is a **separate deployment**: nothing launched under V1 changes. Compiler settings and pinned dependency revisions remain unchanged.

Latest verified creator testnet deployment: [2026-10-03 frontend handoff](docs/TESTNET_LAUNCH_HANDOFF_2026-10-03.md), [address book](deploy/testnet-v2-fresh-creator.json), and [launch parameter profile](deploy/fresh-creator-launch-profile.json). The stock/tUSDG lifecycle, including graduation and fee conversion, was broadcast and verified; Native ETH launch remains gated off for this deployment.

Follow-up: [eight-wallet persona campaign and TSLA price-path experiments](docs/PERSONA_TSLA_EXPERIMENT_2026-10-03.md) distinguish public testnet trades from isolated-fork price shocks, and marked portfolio values from independent liquidation quotes.

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
| `src/v2/HedgeFunV2Treasury.sol`, `src/v2/HedgeFunV2AllInTreasury.sol`, `src/v2/V2TreasuryDeployer.sol` | Ordered `execute()` (stop first, then take-profit, then dip), dust handling, creator-selected ordinary rungs in new registries, and pluggable strategy kinds |
| `src/v2/HedgeFunV2BuybackTreasury.sol` | Kind 1: a pure buy-back treasury, opt-in (production must register its exact code chunks) |
| `src/v2/HedgeFunV2EngineTreasury.sol`, `src/v2/strategy/IStrategyPolicy.sol` | The strategy engine: a treasury that executes a registered, stateless policy's intent (hold / buy / sell) under its own custody, cooldown, per-call and daily-turnover limits; the policy is pinned by runtime code hash and committed in the CREATE2 config |
| `src/v2/strategy/V2RebalancePolicy.sol` | The first policy: keep stock at a target share of treasury value, act outside a deadband |
| `src/v2/HedgeFunV2TradeRouter.sol`, `src/v2/HedgeFunV2NativeRouter.sol` | Any-ERC20 and native-currency entry and exit through V3 hops, with minimum-out and explicit partial-fill refunds |
| `src/v2/HedgeFunV2AssetPercentEngineTreasury.sol`, `src/v2/strategy/V2AssetPercentRebalancePolicy.sol` | Separate percentage Engine: single trade and daily turnover limits follow total fund assets; the target weight uses tradable stock and USDG |
| `src/v2/HedgeFunV2LaunchNativeRouter.sol` | A creator launches and makes the first stock-denominated curve buy using one native ETH payment |

The four V1 files that changed (`HedgeFunFactory`, `HedgeFunTreasury`, `HedgeFunTreasuryBase`, `hooks/HedgeFunHook`)
changed to let V2 inherit them; the V1 deployment does not pick those changes up.

Design and review notes are in `docs/`: start with [`V2_BONDING_CURVE.md`](./docs/V2_BONDING_CURVE.md), then
[`STRATEGY_ENGINE.md`](./docs/STRATEGY_ENGINE.md),
[`V2_DUAL_ENGINE_REVIEW.md`](./docs/V2_DUAL_ENGINE_REVIEW.md) and [`V2_ADVERSARIAL_REVIEW.md`](./docs/V2_ADVERSARIAL_REVIEW.md).
For user, keeper and admin entry points with fork fuzz paths, see [`V2_ACTOR_FLOW_FUZZ_MAP.md`](./docs/V2_ACTOR_FLOW_FUZZ_MAP.md).
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

The integrated local build reports runtime sizes of 24,501 bytes for `HedgeFunV2Factory` (75 bytes below EIP-170), 19,329 for `CurveDeployer`, 11,697 for `V2TreasuryDeployer` and 19,354 for `HedgeFunHook`. Recheck these after any source or compiler change.

## Status

**Testnet candidate; mainnet launch approval is not established by this snapshot.** Testnet uses much deeper pools than mainnet and deliberately replaces price feeds and assets. Mainnet still requires reviewed owner/recipient addresses, actual listing-depth checks, V2 frontend and keeper integration, and verified deployment receipts. The opening-window economics remain a product choice. Strategy tokens give holders no claim on the treasury and no redemption right.

The [X creator launch and settlement design](./docs/X_CREATOR_LAUNCH_DESIGN.md) describes a separate, unimplemented integration; X Money payouts are not part of this release.

The scoped security review is in [`V2_TESTNET_REVIEW.md`](./docs/V2_TESTNET_REVIEW.md). It is an engineering review, not a claim of independent audit certification.

## Source PR #102

This branch mirrors [Add V2 opening-tax recipient whitelist](https://github.com/keyuyuan/hedgefund/pull/102) at source commit `3244fbf6b3108798e4227c863b1cddf7c1ac1267`. Contract files retain their source bytes. The source [README](https://github.com/keyuyuan/hedgefund/blob/3244fbf6b3108798e4227c863b1cddf7c1ac1267/README.md) and validation claims belong to that pinned development snapshot; mirror checks are reported separately in the pull request. Run local commands from `contractV2/`.

## Source PR #103

This branch mirrors [Deploy and verify V2 whitelist stack on Robinhood testnet](https://github.com/keyuyuan/hedgefund/pull/103) at source commit `d7f20e10a24646bcfb2c752d4fbf44d612445498`. Contract files retain their source bytes. The source [README](https://github.com/keyuyuan/hedgefund/blob/d7f20e10a24646bcfb2c752d4fbf44d612445498/README.md) and validation claims belong to that pinned development snapshot; mirror checks are reported separately in the pull request. Run local commands from `contractV2/`.

## Source PR #104

This branch mirrors [Audit TSLA V2 testnet stress journey](https://github.com/keyuyuan/hedgefund/pull/104) at source commit `64c0adc602bbcbb70c0b4511ac67ee2aa40fceca`. Contract files retain their source bytes. The source [README](https://github.com/keyuyuan/hedgefund/blob/64c0adc602bbcbb70c0b4511ac67ee2aa40fceca/README.md) and validation claims belong to that pinned development snapshot; mirror checks are reported separately in the pull request. Run local commands from `contractV2/`.

## Source PR #106

This branch mirrors the historical [V2 wallet-budget and ownership study](https://github.com/keyuyuan/hedgefund/pull/106) at source commit `01976f62c56831c176a0b85db568f9c120cc3504`. Its fork snapshots are pinned to **2026-09-30** on Robinhood testnet and include the old **25 tUSDG launch fee**. Read the [wallet budget](./docs/V2_WALLET_BUDGET.md), [capital simulation](./docs/V2_CAPITAL_SIMULATION.md), and [original-supply participation](./docs/V2_ORIGINAL_SUPPLY_PARTICIPATION.md) reports as archived measurements of those exact paths. They are not current native ETH launch quotes or deployment instructions. The evidence JSON, test and report-tool bytes remain those of the source PR; no on-chain action occurs in this mirror.

## Source PR #108

This mirror adds the historical four-stock testnet extension from [source PR #108](https://github.com/keyuyuan/hedgefund/pull/108) at `4ff128573152c6a6589a64c6636d6f6de1919580`. It targets the earlier whitelist factory and synthetic market, with candidate and dry-run manifests. It does not add listings to the current native ETH deployment or prove a new testnet transaction. See the [extension runbook](./docs/TESTNET_STOCK_EXTENSION.md) for the pinned addresses and verification requirements.

## Source PR #111

This branch mirrors [Add isolated two-sided-fee testnet deployment and TSLA rehearsal](https://github.com/keyuyuan/hedgefund/pull/111) at source commit `4e437ca9e724f2f4b7df74df9ffb100f2d22f7c9`. Contract files retain their source bytes. The source [README](https://github.com/keyuyuan/hedgefund/blob/4e437ca9e724f2f4b7df74df9ffb100f2d22f7c9/README.md) and validation claims belong to that pinned development snapshot; mirror checks are reported separately in the pull request. Run local commands from `contractV2/`.

Keeper proof archives retain the original sourceCommit and receipt hashes, including source PR #112. tools/audit_v2_keeper_reward.py verifies the original repository history and layout: replay that audit from the pinned keyuyuan/hedgefund checkout, not this prefixed mirror. Do not replace the proof source commit with a mirror commit.

## Current V2 source mirrors

The parent [percentage Engine mirror](https://github.com/0xHedgeHood/Hedgefun-trade/pull/14) carries the final source [#114](https://github.com/keyuyuan/hedgefund/pull/114) revision. This branch adds the creator-parameter and ETH-market prerequisite from source [#113](https://github.com/keyuyuan/hedgefund/pull/113), the native launch and first buy from source [#118](https://github.com/keyuyuan/hedgefund/pull/118), the stop/profit dust scheduler change from source [#116](https://github.com/keyuyuan/hedgefund/pull/116), and its capacity and received-stock accounting correction from source [#122](https://github.com/keyuyuan/hedgefund/pull/122). The [actor flow and fuzz map](./docs/V2_ACTOR_FLOW_FUZZ_MAP.md) mirrors merged source [#121](https://github.com/keyuyuan/hedgefund/pull/121) with the #122 follow-up. The source `AuditRound5Treasury` assertion fixture is excluded because its V1 fixture chain is not part of this curated directory; the mirrored `V2Execute` tests exercise the V2 dust paths.

The native launch router and new creator registry require separate operator deployment, registration and live-state verification. Source mirroring and fork rehearsals do not activate them on the public testnet. See [native launch](./docs/V2_NATIVE_LAUNCH.md), [ETH market](./docs/TESTNET_V2_ETH_MARKET.md), and [percentage Engine](./docs/V2_ASSET_PERCENT_ENGINE.md) for the distinct release steps.

## Historical hackathon demo (#117)

The isolated [testnet demo poll](./docs/TESTNET_DEMO_BALLOT.md), its `DemoBallot` contract and the demo script mirror source [#117](https://github.com/keyuyuan/hedgefund/pull/117). The script pins the earlier `0xACEB…` fees factory and a 25 USDG launch fee. It documents that completed demo and is not an activation path for the new native ETH launch default. Deployments and the source repository's roadmap remain outside this mirror.

## Historical deployment review (#100)

The [2026-09-29 release review](./docs/V2_RELEASE_REVIEW_2026_09_29.md) archives source [#100](https://github.com/keyuyuan/hedgefund/pull/100) at `0b886b3ff8f1785c90f8bac4d538d0d103240b64`. Its deployment-verifier files were already present in the original V2 snapshot; later mirrors changed parts of the deployment flow. The old review's deployment status, tool counts and missing ETH venue describe that pinned source version. Use the current native launch and ETH runbooks above for current operator work.

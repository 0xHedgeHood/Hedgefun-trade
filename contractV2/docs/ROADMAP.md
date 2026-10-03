# V2 roadmap

This is the contract mirror's current plan, adapted from [source PR #115](https://github.com/keyuyuan/hedgefund/pull/115) on 2026-10-01. The [source roadmap at that merge](https://github.com/keyuyuan/hedgefund/blob/5cbe62e185d02ceaf4bf0105c7d2e9d6d602a716/docs/ROADMAP.md) retains the earlier V1 positioning, backtests and research links. Those historical materials are not copied into this curated V2 snapshot. This plan does not commit to a date or establish that a feature is deployed.

## 1. Consolidate and verify the V2 release

The V2 code mirrored through source `main` commit `48a41e2` is staged in target [PR #13](https://github.com/0xHedgeHood/Hedgefun-trade/pull/13). That PR is part of a stacked review chain, not the target default branch. Source merges, target branch merges and successful fork tests do not deploy, upgrade or register contracts on chain. The public-testnet deployment records in this repository describe earlier, pinned contract versions.

- Reconcile the final protocol, frontend and keeper branches before release. Keep launch, bonding-curve trading, atomic graduation, permanent LP, fee accounting and rewards tied to actual fills.
- Treat creator parameters, the native ETH market/router and the asset-percentage Engine as distinct activation steps. Verify exact bytecode, owner, recipients, registered kinds, defaults, feeds and pools against live chain state before publishing new addresses or claims.
- Rehearse the combined testnet journey: launch and first buy, multiple traders, partial fills, graduation, V4 trading, fee collection, treasury booking/execution and buyback. Include sniping, concentrated ownership, sudden selling, shallow liquidity, stale or closed markets, and keeper retries.
- Use the [actor and fork-fuzz map](./V2_ACTOR_FLOW_FUZZ_MAP.md) to test paths across user, keeper and admin entry points. Publish only capabilities backed by the corresponding deployment receipts and execution evidence.

## 2. Add Covered Call as a separate immutable strategy kind

The agreed direction is a dedicated options Engine for **new** launches. An existing strategy's Treasury, funds and terms remain bound to the code and dependencies selected at its launch. Registering a later kind must not replace that Treasury or move its assets. The planned protocol Safe governs admission of reviewed engine, policy and adapter versions; verify its chain-specific address and threshold before release.

[`V2TreasuryDeployer.registerEngineKind`](../src/v2/V2TreasuryDeployer.sol) records an Engine version, configuration schema and capabilities. A candidate options kind must prove compatibility with the Factory, Hook, launch and LP interfaces, or use a separately reviewed surface. The current [spot Engine](../src/v2/HedgeFunV2EngineTreasury.sol) rejects options capabilities; reserved bits are not an options implementation.

First delivery should include a single underlying, fully collateralised Covered Call Engine, a fixed policy and a versioned market adapter. Its accounting must separate spendable stock, locked collateral, outstanding calls, premiums and settlement proceeds. Launch terms must commit coverage, strike and expiry bounds, minimum net premium, fees, settlement rules and keeper rewards. Book actual transfers and fills. Expiry, exercise, lapse or cancellation where supported, settlement and collateral release must be callable independently of the spot trading loop, including while the spot market is closed. Net option income may fund a disclosed fixed buyback allocation; cash dividends require their own capability and eligibility rules.

The standalone [CoveredCallDesk mirror PR #8](https://github.com/0xHedgeHood/Hedgefun-trade/pull/8) is a candidate RFQ escrow component. It is not yet a fund-integrated options Engine or evidence of a live counterparty. Its design includes a trusted owner settlement backstop after expiry plus 14 days: the owner may select the settlement price and force NetShare settlement. That pricing authority must be disclosed in launch terms or replaced by a separately designed and audited settlement model.

Before admission, settle the actual desk, adapter, counterparties and testnet venue; audit the Engine and escrow together. Test no or partial fills, duplicate collateral use, in- and out-of-money settlement, early and late exercise, feed failure, market closure, issuer transfer restrictions and actual-fill keeper payment. Check exact EIP-170 and EIP-3860 limits without increasing local limits.

## 3. Expand assets and income one integration at a time

- ERC20 vault or share assets need reviewed custody, valuation, oracle and execution adapters. Native NFTs need their own custody and redemption model; an NFT address is not the current ERC20 stock underlying.
- A venue beyond the current V3 asset execution path needs its own audited adapter and core. Listing a pool address alone does not make it compatible.
- Cash dividends need a defined income bucket and verifiable eligibility and claim rules. Existing strategy tokens acquire no historical checkpoints, redemption claim or distribution rights because a new kind is registered.
- Any future mutable catalogue belongs outside existing strategy custody and Factory ownership. Defer the proposed whole-Treasury proxy and generic feature-vault refactor; preserve creation-bound dependencies for launched strategies.

For earlier research candidates and the original V1 release rationale, read the [source roadmap](https://github.com/keyuyuan/hedgefund/blob/5cbe62e185d02ceaf4bf0105c7d2e9d6d602a716/docs/ROADMAP.md). They are not the current V2 release order.

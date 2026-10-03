# V2 release review — 2026-09-29

> **Historical source review.** This records the pinned development snapshot below. Its tool counts, old
> deployment flow, account-billing status and missing ETH venue are observations from 2026-09-29. Current
> release work is described in the [roadmap](./ROADMAP.md), [native launch](./V2_NATIVE_LAUNCH.md) and
> [ETH market runbook](./TESTNET_V2_ETH_MARKET.md). This archive does not verify a current deployment or
> claim that later deployment scripts still match this snapshot's candidate/verifier workflow.

## Decision

**Conditional GO for the public testnet contract pilot; NO-GO for a mainnet or complete website release.**
No public transaction was broadcast in this review. The operator's dedicated testnet address and signing account
have not been supplied. Local fork transactions and simulated addresses are not a public deployment record.

The review starts from merged PR [#99](https://github.com/keyuyuan/hedgefund/pull/99), commit
`9b872a2e7467f5ec7970b91ef9dca8214f218fd8`. The follow-up changes deployment verification, regression tests and
documentation; production `src/` is unchanged. Independent security and runtime review lanes examined deployment,
recipient permissions, creator identity, oracle/calendar configuration and the launch flow.

## Findings and changes

- **P2, fixed in this follow-up:** Foundry runs deployment scripts before sending their transactions. The old script
  wrote `broadcast=true` before any receipt was confirmed. It now writes an ignored candidate. The separate
  [verifier](../tools/verify_testnet_deployment.py) checks successful canonical receipts, transaction inclusion,
  confirmations, contract code hashes, roles, factory bindings and listings at a pinned block before atomically
  promoting the inventory. It rechecks the block hash after readback. Seed rejects unverified or inconsistent books.
- **P3, residual:** deployers and hook have public one-time `bind()` functions. Separate deployment transactions
  leave a window in which an observer can bind first and force factory construction to fail. This does not transfer
  existing factory assets. A testnet operator must redeploy fresh components on this failure; `--resume` cannot
  repair a component bound to the wrong factory. Mainnet deployment should eliminate the window with a separately
  reviewed atomic deployment plan. The script is not an atomic deployment guarantee.
- Test coverage now exercises all four test stocks through launch, tUSDG buy, curve graduation, V4 trading and sale
  back. Separate cases exercise registered kinds 1 and 2; the latter also executes its registered policy against
  the testnet V3 venue. These supplement, rather than replace, the core adversarial and invariant suites.

## Validation

Baseline local checks on #99: `forge build --sizes` passed; **1,555 passed, 0 failed, 55 skipped** in the ordinary
Foundry suite. The skips are explicit opt-in tests, not successful tests. The V2 archive-fork suites separately passed
**17/17**, and the emergency profile passed **8/8**. Python 3.12 repository tests passed **104/104**, tooling tests
**57/57**, and the documentation/reference check passed. The expanded testnet suite passed **11/11**.

After the fixes, the complete local suite passed again: **1,560 passed, 0 failed, 55 skipped**; Python repository
tests passed **115/115**, including 11 new deployment-verifier tests. Core Solidity source and its ABI are unchanged.

The factory runtime is **24,397 bytes**, leaving **179 bytes** under EIP-170; `CurveDeployer` is 16,730 bytes.
The compiler remains Solidity 0.8.26, optimizer runs 1, Cancun, no metadata hash; Foundry is 1.5.0.

A fresh unsigned public-testnet simulation completed with `public launch true; readback passed`; the estimate was
**198,907,710 gas / 0.0039781544 test ETH**. Its sender was the dummy `0x000000000000000000000000000000000000dEaD`,
not an intended operator. Repeat with the actual operator and exact release commit before public deployment.

On a disposable local fork of chain 46630, all **72 deployment transactions** executed and the new verifier accepted
their actual receipts and live readbacks. The resulting verified file was written outside the repository; neither
it nor a candidate is a public testnet address book. The local fork uses impersonation and valueless simulated ETH.
Seed executed successfully on that local fork with the verified book; the same script rejected an unverified
candidate with `UnverifiedBook()`. After seed and a simulated 660-second warmup, all four listings passed at the
default raise size and at `saleBps=9000` (**4 PASS / 0 FAIL** at each). These are local fixture checks, not
public-chain receipts or mainnet liquidity evidence.

GitHub Actions for baseline `9b872a2` did **not start**: GitHub reported failed account payments or an insufficient
spending limit. See [run 36513463440](https://github.com/keyuyuan/hedgefund/actions/runs/36513463440). This is an external
CI blocker, not a passing gate and not evidence of a Solidity failure. The broader legacy mainnet fork sweep also
encountered HTTP 429 from the archive endpoint and was stopped after more than six minutes; do not present the
complete fork gate as green. The separate 17-test V2 fork run completed successfully before that sweep.

## Remaining launch gates

1. Provide the dedicated **testnet-only** operator public address and usable signer, and fund it with test ETH.
   No private key belongs in chat, source control or a plaintext environment file.
2. Follow [TESTNET_V2](./TESTNET_V2.md): fresh simulation, public broadcast, receipt verification, seed, pool warmup
   and listing checks. Publish only the verified public-chain inventory and explorer transactions.
3. Restore GitHub Actions account capacity and complete the release CI checks, including the archive-fork gate.
4. The existing website is still wired to V1/mainnet. V2 ABI, curve routing, testnet chain configuration, indexer and
   keeper wiring are separate work. There is no WETH/tUSDG testnet pool, so the native router has no funded ETH route.
5. Mainnet additionally needs reviewed Safe/recipient roles, real policy manifests and current stock-specific
   depth/chunk/oracle/replay evidence. Mintable test assets, operator feeds and deep test pools cannot establish this.

## X creator product

The [X creator design](./X_CREATOR_LAUNCH_DESIGN.md) keeps core V2 unchanged and adds creator accounts, identity,
payment orders, an execution worker and distinct fee/payout ledgers. UsePaid currently reports paused X Money
payouts; automatic X Money settlement remains conditional on official access and a supported settlement route.
This proposal does not implement a bot or promise an available payment API.

# V2 testnet release review — 2026-09-29

Scope: the V2 source at main `9b872a2`, PR #99's deployment, seed and listing-check workflow, and manifest-verification fix `1d42241`. This is a scoped engineering review; it is not independent audit certification. No transaction was signed or broadcast during this review. No completed deployment is asserted.

## Findings

- **P2: simulated manifests must not certify deployment.** The original deployment script wrote `broadcast=true` before network receipts were known; its role readback also ran inside simulation. A failed or partial broadcast could leave a complete-looking address book. **Fixed in this snapshot:** deployment writes an unverified candidate; `tools/verify_testnet_deployment.py` checks successful canonical receipts, deployment provenance, pinned live code and role/configuration bindings before promoting it. The seed tool refuses an unverified or wrong-chain book.
- **P3 / Low: permissionless binding leaves a deployment griefing window.** `DeployV2Testnet._deployV2` creates the treasury, token and curve deployers and the hook in separate transactions. `BoundDeployer.bind` and `HedgeFunHook.bind` are first-caller-wins until factory construction. An observer can bind first, causing the factory constructor to revert and requiring fresh components and a hook salt. This does not grant access to a successfully deployed factory's funds. Sequential broadcasting does not make these transactions atomic. Accepted residual risk for a valueless testnet; an atomic V2 deployment bundle is the stronger production solution.

The review found no new High or Critical fund-loss issue in this scope. Historical round-4 engine configuration validation findings must not be treated as current: the source now shares `SpotEngineConfig.valid` between setter, prediction/deployment and constructor validation.

## Release decision

**Testnet: conditional GO** after tests, receipt verification, live role checks, seeding and listing checks. The operator must have a testnet-only signer and sufficient test ETH. Publication of an address book is not a substitute for successful receipts. Binding griefing remains a documented low-severity deployment risk.

**Mainnet: NO-GO on testnet evidence alone.** Outstanding release requirements:

1. Reviewed Safe owner, fee recipients, predicted addresses and exact policy manifests.
2. Current stock-specific mainnet replay and pool-depth / trade-chunk checks.
3. V2 frontend, router, indexer and keeper integration before public launch.
4. Receipt-confirmed inventory and live role verification.

The testnet uses an EOA owner, mintable assets, operator-set always-fresh feeds, placeholder audit manifests and pools much deeper than mainnet. These are deliberate test fixtures and cannot validate mainnet economics or operational readiness.

## Validation in the standalone snapshot

Solidity 0.8.26 with the pinned dependencies builds successfully using `forge build --sizes`. `HedgeFunV2Factory` is 24,397 runtime bytes (179 bytes below EIP-170). The full selected Foundry suite passes **377 tests**, with **0 failures and 17 explicit fork skips**. The expanded testnet flow suite passes 11 tests, covering all four stock listings, kind 1 and kind 2, including an engine policy execution. The Python 3.12 listing-check and deployment-verifier suites pass 57 tests. Live fork suites remain opt-in and are not included in these standalone offline results.

## Creator and fee boundaries

`HedgeFunFactory._launch` accepts the creator or an allowlisted launcher. The creator address also owns token metadata permissions. The curve fixes creator and protocol recipients at construction, and sell fees accrue in the listed **stock token**, not USDT. Graduated hook payouts have their own administration: protocol recipient changes are immediate; creator recovery has a 14-day delay and veto. Hook changes do not rewrite the curve recipient or token metadata authority.

A social-login or Bot integration should retain a stable creator account and independently verify the X user identity. A handle in metadata is not payment authority. Creator income, protocol income and strategy capital must remain separate ledger categories.

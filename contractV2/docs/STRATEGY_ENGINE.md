# V2 strategy engine boundary

The strategy engine separates a policy's decision from the treasury's authority. A registered policy can return
only one fixed-width intent: hold, buy stock, or sell stock, plus a nonce-bound next-state word. It cannot choose a
pool, route, recipient, approval, callback, arbitrary calldata, or asset.

`HedgeFunV2EngineTreasury` is the custody and risk boundary. On every execution it independently checks:

- the domain-separated launch config hash and current strategy nonce;
- the registered policy runtime code hash, call gas, and exact 160-byte return size;
- live oracle and venue health;
- policy capability, cooldown, target/deadband direction, per-call notional, and daily turnover;
- actual swap input/output, including the minimum executable lot, before committing inventory, turnover, state, or
  nonce. A price-limit dust fill reverts the swap and provisional inventory update atomically and cannot renew the
  cooldown.

The creator commits to a fixed-width `EngineConfig` for its own `(symbol, creator, nonce)` salt. The config is
appended to initcode, so changing it changes the CREATE2 treasury address and therefore the factory terms. Engine
kinds and policy registrations are append-only. Disabling a policy prevents new predictions/launches but cannot
rewrite an already deployed treasury, which stores its policy identity and limits as immutables.

The factory's `V2TreasuryDeployer` reference is also immutable. A production factory deployed from the pre-registry
PR84 baseline cannot acquire these engine APIs later through `registerKind`; the factory and deployer must be deployed
from the final combined bytecode. Any earlier deployment is a disposable rehearsal, not an upgrade path.

## Policy admission

A release policy must be stateless and non-proxy, with reproducible bytecode. Its dependency and audit manifest
hashes must identify the reviewed source, compiler/settings, dependencies, tests, and audit evidence. Runtime
codehash binding detects code replacement, but cannot prove that an implementation does not read mutable storage or
delegate through a proxy. The spot core therefore treats every policy as adversarial and caps the damage of any
intent; governance review must still reject mutable and proxy policies.

The stock and USDG legs are admitted as exact-transfer, non-rebasing assets. Fee-on-transfer, sender-surcharge,
negative-rebase, and issuer-burn behavior can make a pool-reported fill diverge from the treasury's balance buckets
and must fail asset admission. Their implementations and upgrade beacons require continuous monitoring.

`maxDailyTurnoverUsdg` is a UTC-aligned on-chain epoch limit (`block.timestamp / 1 days`), not a rolling 24-hour
window. The cooldown remains active across the epoch boundary, but a release must explicitly accept the calendar-day
semantics or replace it with a rolling limiter before production.

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

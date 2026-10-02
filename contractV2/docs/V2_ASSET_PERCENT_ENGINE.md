# V2 asset-percentage spot engine

Status: source and local tests only. No deployment, registration, wallet signature, published deployment proof,
or automated keeper service is supplied by this change.

`HedgeFunV2AssetPercentEngineTreasury` is a separate execution core, and `V2AssetPercentRebalancePolicy` is a separate
stateless policy. Both declare engine version **1**, config schema **2**, spot capabilities **3** (buy + sell).
The deployed schema-1 Engine, policy, deployer, factory, registrations and funds are unchanged. Existing
funds cannot migrate their immutable rules; a new configuration belongs to a new launch. Later source changes,
including the V2 lot dust fix, can change the bytecode built for a fresh registry without changing deployed code.

The new core copies the reviewed rewarded Engine deliberately: its private execution methods cannot be overridden
without changing the old source/creation code. Schema validation, live total-asset percentage sizing, a constructor-created immutable asset reader and the
additive `riskLimits()` view differ. A shared refactor would change the creation commitment of the old deployment.

## Asset denominator and execution

The trade/daily percentage denominator is **this fund's total external assets**, expressed in USDG base units:

```text
stockAssets = stock.balanceOf(treasury) + stock.balanceOf(ownVault)
            + ownFullRangeLpStockPrincipal + ownUncollectedStockFees
NAV = floor(stockAssets * healthyLiveStockPrice / stockToUsdgScale) + treasuryUSDG
allocationNAV = floor((bookedStock + unbookedStock) * healthyLiveStockPrice / stockToUsdgScale) + treasuryUSDG
maxTrade = min(floor(NAV * maxTradeBps / 10000), frozen listing sellChunkUsdg)
dailyLimit = floor(NAV * maxDailyTurnoverBps / 10000)
used = turnoverInEpoch, when turnoverEpoch == calendar.tradingDate(now), otherwise 0
remainingDaily = max(0, dailyLimit - used)
```

**Buyback stock and the fund's own LP stock assets are included in the percentage denominator.** The allocation
70% target and band still use only tradable inventory plus USDG (`allocationNAV`). Locked LP and buyback stock
never become executable inventory. Self-issued FUN held in the treasury, vault or LP has zero external-asset
value and is excluded; unrelated funds, third-party positions and the PoolManager's aggregate balances are also
excluded. USDG/stock donations to the treasury and stock parked in this vault enter the denominator immediately.

Each new treasury constructs its own `V2FundAssetReader` and freezes it as `assetReader`. Its creation code
commits the reader code; creator, keeper and policy cannot select or replace a reader. The reader verifies this
vault's factory, treasury, stock, FUN, manager, seeded status and entire PoolKey against the treasury's fixed
wiring. It reads only the position owned by this vault at `minUsableTick/maxUsableTick`, salt zero. Stock principal
uses that position's liquidity and the current V4 sqrt price clamped to its full range, rounded down. Only that
stock quantity is valued at the same healthy live V3 oracle stock/USDG price used for allocation and execution.
Uncollected stock fee is `floor(unchecked(feeGrowthInside - lastFeeGrowthInside) * ownLiquidity / 2^128)`, matching
V4's fee-growth wrap semantics. No value is assigned to the FUN leg.

Fee collection moves stock from uncollected fees to vault balance, then to treasury buyback stock. Each stage
is counted exactly once: pending fee accounting is already in vault balance and buyback stock is already in
treasury balance. Failed delivery parks stock in the vault and a later retry conserves NAV. Moving a sale's
profit stock into the buyback bucket likewise preserves total NAV while reducing tradable allocation assets.
Actual buyback spends stock to acquire/burn FUN, so it can decrease total external assets.

The reader refuses all snapshots while the PoolManager is unlocked. V4 swap, seed and fee-poke callbacks can
contain temporarily unsettled balances/position growth; they cannot be used to enlarge capacity. A missing,
unseeded, misbound or malformed vault, zero price, quantity/value overflow, or reader failure makes riskLimits
unhealthy and preview wait; execute reverts before booking and never falls back to the tradable denominator.
Both preview and execute read a settled full-asset snapshot before constructing the tradable policy context.
The existing shared context ABI is unchanged. Because that context omits LP assets, the advisory policy proposes
the complete tradable target gap; the core alone clips it by full-NAV percentages and the remaining hard limits.

An action must also be outside the allocation band, obey the cooldown, stay within the amount needed to reach
target, use the fixed venue, pass its health/price-limit checks, and have sufficient actual input. A percentage
cap below `minLotUsdg`, or a daily remainder below it, means **wait**; the core never rounds the cap up to minLot.
The sell-side conversion back to stock units can floor below minLot as well. A small fund may remain idle until
its total external assets grow. A large fund can hit the listing's absolute chunk even with a larger percentage allowance.

The daily cap follows the oracle calendar's **US trading date**, rolling at 20:00 New York time with DST. It is
a cumulative turnover cap, not a rolling 24-hour limit. `used` is an absolute USDG ledger and does not reset when
the price, balances, target or NAV change within a session. A smaller NAV may make the current limit lower than
already-used turnover: remaining becomes zero, without refunding or rewriting history. A subsequent increase
in NAV releases only `newLimit - used`. Only a new trading date starts a fresh ledger.

For example, 1,000 USDG of total external assets with a 10% trade / 50% daily configuration permits up to 100 USDG per
action and 500 USDG cumulative turnover. After 200 USDG has been used, a fall to 300 USDG of NAV produces a 150
USDG daily limit and zero remainder. A recovery to 600 USDG gives a 300 USDG limit and 100 USDG remainder. The
listing chunk and minLot can reduce or prevent a proposed action in all three snapshots.

Swap fees and execution rewards can reduce NAV during an action; reserving stock for buyback alone does not. The successful action is bounded
by its **pre-trade** NAV; historical used turnover may exceed the cap computed from its post-trade NAV. This is
expected. Requiring a post-trade inequality would incorrectly undo otherwise valid actions.

## Schema 2

The existing `EngineConfig` ABI is unchanged:

```solidity
struct EngineConfig {
    uint32 schema;          // 2
    uint32 engineVersion;   // 1
    bytes32 policyKey;      // actual immutable registered policy identity
    bytes32[3] words;
}
```

| Word | Meaning | Bounds |
| --- | --- | --- |
| `words[0]`, bits 0–15 | stock target, bps | 2,000–9,000 |
| bits 16–31 | band, percentage points in bps | creator-selected, including 0 or 1 bps; `< target`; target + band `< 10,000` |
| bits 32–63 | cooldown seconds | 600–`uint32.max` |
| bits 64–79 | profit buyback share, bps | 0–10,000 |
| bits 80–255 | reserved | zero |
| `words[1]` | maximum action as total-external-asset NAV bps | 1–10,000, full-width word; never truncate high bits |
| `words[2]` | maximum daily turnover as total-external-asset NAV bps | `>= words[1]`; `<= 10,000`; `<= 24*words[1]` |

10% = 1,000 bps and 50% = 5,000 bps. Recommended initial UI inputs are target 70%, band 5 percentage points,
cooldown 600 seconds, action cap 10%, daily cap 50%, profit buyback share 0%. They are a starting configuration,
not evidence of profitable trading. The creator may choose a band of 0 or 1 bps independently of slippage,
pool fee and keeper bounty. A narrower band can propose more frequent trading, but does not promise a profitable
fill. The core and policy both validate with a zero optional band minimum; packed-word geometry, cooldown,
percentage limits, live-market checks, actual-fill accounting and rewards still apply. Listing
`sellChunkUsdg >= minLotUsdg` is required by the new constructor.

The word layout is intentionally isolated. Schema 1 continues to interpret `words[1]/[2]` as **absolute USDG
base units**. A schema-1 policy cannot be bound to a schema-2 kind. Upgrading a saved draft must be explicit;
existing fixed-amount words, selected kind, and pending transaction identity must not be silently replaced.

## Settlement, payout and keeper compatibility

The rewarded Engine rules remain:

- Buy turnover is **actual USDG input**; booked stock is actual stock output minus executor reward. Average cost
  includes the complete USDG spend and only the net retained stock.
- Sell turnover is the live-price USDG value of **actual sold stock + actual gain stock reserved for buyback**.
  It is not only the stock passed to the swap. Partial fills allocate the gain/buyback share proportionally.
- A successful executor receives frozen `bountyBps` of actual gross swap output: stock for buys, USDG for sells.
  The execution event retains gross output; `KeeperRewardPaid` identifies the reward separately.
- Profit payout is a stock allocation to future token buyback/burn, not a creator dividend. Moving stock into
  this bucket removes it from tradable allocation assets while keeping it in the full-NAV percentage denominator.
- Dust fills below minLot, unhealthy markets, invalid policy returns, failed reward transfers and reentrancy
  revert atomically; no nonce, cooldown, state or budget is consumed. Hold does not commit a next state.
- Inherited `takeProfit`, `stopLoss` and `buyDip` still require the combined `execute()` path. Inherited paced
  `buyback()` remains separate and spends only the buyback bucket.

The ABI for creator registration remains `setEngineConfig(string,uint96,uint8,EngineConfig)` and
`engineConfigOf(bytes32)`. The treasury still exposes `engineConfig()`, `engineVersion()`, `strategyId()`,
`configHash()`, `preview()`, `execute()` and the existing accounting/reward surface. The added view is:

```solidity
function riskLimits() external view returns (
    bool healthy,
    uint256 navUsdg,
    uint256 maxTradeUsdg,
    uint256 maxDailyTurnoverUsdg,
    uint256 remainingDailyUsdg,
    uint64 epoch,
    uint256 usedUsdg
);
```

Amounts are USDG base units. `healthy` describes a valid live pricing and settled asset snapshot, not whether allocation and
cooldown permit a trade. Unhealthy/overflowing snapshots report false and zero NAV/caps/remainder; epoch and
used remain available. The inherited average-cost ledger retains checked price-times-quantity arithmetic;
extreme balances can fail closed. Full-width percentage `mulDiv` support does not promise every uint256-sized
fund can be booked or traded by the inherited core.

## Existing registry compatibility and validation limit

The deployed `V2TreasuryDeployer` can register future nonzero schemas and append the new policy/kind without
replacing the factory/registry. The kind index is assigned by `registerEngineKind`; **there is no preassigned
new kind number or address**. Existing registrations are immutable.

The old registry validates generic policy metadata/identity for schema 2 but has specialized word validation
only for schema 1. Consequently, it may accept and quote malformed schema-2 words. The new constructor is the
authoritative word validator and rejects them; a failed CREATE2 launch atomically reverts the old registry's
`TreasuryDeployFailed`. Clients must perform exact schema-2 validation before requesting signatures. This
limitation is explicitly tested and does not authorize changing the old deployer's source or deployed code.

## Build and local verification

Use the repository's Foundry configuration: Solidity 0.8.26, optimizer enabled/runs 1, Cancun EVM and
`bytecode_hash = none`. Reproduce with `forge build` and `forge test --match-contract 'V2AssetPercent.*Test'`.
Tests cover actual factory prediction, launch and graduation, live NAV growth/shrink, percentage/chunk sizing,
minLot/dust/rounding, dynamic daily capacity, deposits/own-LP/buyback inclusion, self-issued FUN/unrelated-position exclusion, fee delivery conservation and unlocked-manager refusal, partial settlement/rewards,
constructor and policy bounds, old-code isolation, hostile policies/staticcall/returndata, reentrancy, and
real winter/summer/DST trading sessions. Stateful invariants use the authoritative health price and independently
read the owned V4 position/balances independently of the production reader, and derive turnover from balance
deltas and buyback allocation; they check caps against each action's pre-trade NAV.

### Historical verification and commitments

The following measurements and commitments describe source `aecfd05574888446debe0f4595e23fe9f265648d`
before removing the schema-2 economic band minimum. They remain historical evidence; the revised source
requires newly generated commitments and its own release proof.

Historical local verification: **1,667 passed, zero failed, 62 skipped** across the repository. The eight offline suites
contribute **48 passing tests**, including four 256-case fuzz tests and two stateful invariants, each run for
256 sequences of 500 calls (128,000 calls per invariant). The skipped optional fork/integration cases are not
live deployment evidence. `forge build --sizes` and the subsequent final build passed.

The separate enabled mainnet-state fork suite adds four execution/accounting scenarios, each passing at both
50% and 70% LP shares with no failures or skips. It uses genuine GME/USDG and deployed V3/V4 venues at block
70,786,980, with local fund deployment and explicit oracle-timestamp/calendar test conditions. See the
[fork and scenario report](V2_ASSET_PERCENT_FORK_REPORT.md) for scope, actual amounts, reproduction and CI gates.

Build commitments at the #114 merge (creation code excludes constructor arguments). These are historical
values, not commitments for a fresh build after the V2 lot dust fix:

| Artifact | Bytes | Keccak256 |
| --- | ---: | --- |
| new treasury creation (embeds reader creation) | 37,933 | `0x39ca295fb1c2377b51d44e10388468e3c3f157ef86d97b9c09f71cec0ea9a8ac` |
| new treasury runtime template | 23,642 | contains immutable placeholders; actual fund runtime depends on constructor inputs |
| new policy runtime | 1,913 | `0xffbe2810ce4fba86dc8bdbaa1e80eb30f3f90e58ffde52fc90c0c14c6ef945c5` |
| trusted reader creation | 7,415 | `0x126dbfc4dab74bd6cbce756b2e5c097488fc02403935e2b2f79f32f57e94cf8f` |
| trusted reader runtime template | 6,951 | contains immutable placeholders; actual runtime is fund-specific |
| existing rewarded treasury creation | 29,388 | `0x21db9a11b19dfe73eb5e372972f0dc7057595360c989d92012ba4b638e0d271f` |
| existing policy runtime | 1,799 | `0x703c92e4d169643e9b20eadf00cd53470b95699186a5ce9131feee42299c0b74` |

The new treasury plus frozen constructor arguments/config is 38,797 initcode bytes, below EIP-3860's 49,152;
runtime is 23,642, below EIP-170's 24,576 with 934 bytes remaining. The reader's creation plus its six
constructor arguments is 7,607 bytes, and its runtime is 6,951. Each of the two treasury creation chunks is
below 24,576 bytes. Future source changes must recheck both runtime and complete initcode limits. The immutable reader is created during each new fund launch, not deployed as a user-selected registry
component. The registered policy budget is 150,000 gas with exact 160-byte intent return.
Tests execute the genuine policy through this budget. These commitments must be recomputed after any production
source/configuration change; source commit must identify the committed new source, not just its base revision.

### Creator-selected band revision

The schema-2 constructor and policy now accept zero or one-bp allocation bands with the optional band minimum
set to zero. Regression launches use 300-bp maximum slippage, the 30-bp venue fee and a 200-bp keeper reward;
they prove configuration freezing, actual-output rewards, inventory conservation, percentage caps and cooldown.
Malformed target/band geometry is still rejected before any subtraction. Schema-1 core/policy source and its
creation/runtime commitments remain unchanged from the integrated main baseline.

Current bytecode sizes and callable interfaces are generated in [REFERENCE.md](REFERENCE.md) and
[SURFACE.md](../abi/SURFACE.md). The historical GME fork results above do not verify the revised schema-2 bytes.
No deployment, registration or public-chain execution is claimed for this revision.

## Human deployment/registration and proof publication runbook

This section is a reviewable operator plan, not an executed deployment. No keys or broadcasting script are added.

1. Freeze/review a source commit containing this implementation. Reproduce the build hashes, test results and
   bytecode sizes. Read the verified fees address book and validate chain 46630, owner, two-way factory/registry
   binding, existing manifests and code. Snapshot existing kinds, policy identities and launched strategy runtime
   hashes at a pinned block. Record actual current `kindCount()`; never infer a future index from an old document.
2. A human operator deploys the new stateless policy, verifies its complete runtime hash, and prepares two inert
   `V2InitCodeChunk` deployments containing the first/second halves of the compiled new treasury creation bytes.
   Verify concatenated chunk runtime equals the complete creation bytecode and its reviewed hash. Each chunk must
   fit EIP-170. Chunk deployment itself does not select or change any existing strategy.
3. The factory owner separately authorizes `registerPolicy(newPolicy,150000,160,dependencyManifestHash,
   auditManifestHash)` with reviewed nonzero manifests, and `registerEngineKind(chunkA,chunkB,1,2,3)` on the actual
   bound registry. Capture the returned identities from confirmed receipt events. No old registration is edited
   or disabled. If registry state changed, read back again and use the actual event's kind.
4. Independently verify successful receipts on chain 46630, recipient registry and sender factory owner; match
   `PolicyRegistered` and `EngineKindRegistered` topics/data. Read policy/kind manifests, binding, all chunk code
   and new policy code at the same block. Check schema/version/capabilities, enabled status, 150000 gas/160 return,
   policy runtime hash, creation hash and policy key. Recheck the snapshot of old kinds/funds is unchanged.
5. Exercise new testnet-only funds with schema 2 by direct creator registration, quote, launch, graduation and
   controlled buy/sell calls. Verify exact frozen words, bound config hash, `preview`/`riskLimits`, actual fills,
   keeper rewards, owned LP/BB denominator, fee delivery conservation, unlocked-manager waits, cumulative epoch
   ledger and tiny-NAV waits. This requires additional explicit human signing;
   a local test/fork cannot establish a live deployment proof.
6. Only after real deployment/registration receipt/readback verification, publish
   `/testnet-v2-asset-percent-engine.json` in the frontend. Do not edit the original fee address book or publish a
   simulated/candidate file under this production-readiness URL. Absence/unknown identity means unavailable;
   the UI must not fall back to a fixed-amount Engine for a percentage draft.

The separate published proof is `v2-asset-percent-engine-proof-v1` and must contain:

| Field | Required evidence |
| --- | --- |
| `schema` | `v2-asset-percent-engine-proof-v1` |
| `chainId`, `broadcast` | 46630, true, backed by actual receipts |
| `sourceCommit` | full 40-hex reviewed commit containing these new sources |
| `engineVersion`, `configSchema`, `capabilities` | 1, 2, decimal string `"3"` |
| `factory`, `treasuryDeployer` | actual verified current fees addresses and two-way live binding |
| `kind` | actual appended event index, never guessed |
| `creationCodeHash`, `policyRuntimeHash` | reproduced source commitments and live code/readback |
| `policy`, `policyKey` | actual new policy address and immutable registration key |
| `engineRegistrationTx`, `policyRegistrationTx` | distinct actual successful registration transaction hashes |

A new full-asset reader/policy/treasury creation commitment is required; the earlier local trading-only
percentage commitments cannot be accepted as this implementation. All old deployed schema-1 code remains
unchanged. Optional proof audit material should also record each launched fund's immutable `assetReader`, its
runtime code and constructor binding getters.

The frontend checks registration receipt blocks are no later than its quote block, checks both transactions
target the registry and come from the published factory owner, and matches the two events and live manifests/
chunks/runtime at the pinned quote block. Optional extended deployment audit material may include block hashes,
chunk deployment receipts, dependency/audit manifests, compiler inputs and unchanged-old-fund snapshots. It must
not invent live addresses, a kind index, successful transactions or a proof source commit from a local simulation.

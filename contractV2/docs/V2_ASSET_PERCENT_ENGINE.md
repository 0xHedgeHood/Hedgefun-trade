# V2 asset-percentage spot engine

Status: source and local tests only. No deployment, registration, wallet signature, published deployment proof,
or automated keeper service is supplied by this change.

`HedgeFunV2AssetPercentEngineTreasury` is a separate execution core, and `V2AssetPercentRebalancePolicy` is a separate
stateless policy. Both declare engine version **1**, config schema **2**, spot capabilities **3** (buy + sell).
The existing schema-1 Engine, policy, deployer, factory, registrations and deployed funds are unchanged. Existing
funds cannot migrate their immutable rules; a new configuration belongs to a new launch.

The new core copies the reviewed rewarded Engine deliberately: its private execution methods cannot be overridden
without changing the old source/creation code. Only schema validation, live percentage sizing and the additive
`riskLimits()` view differ. A shared refactor would change the creation commitment of the old deployment.

## Asset denominator and execution

The denominator is **current trading assets**, expressed in USDG base units:

```text
NAV = floor(tradingStock * liveStockPrice / stockToUsdgScale) + reserveUsdg
tradingStock = bookedStock + unbookedStock
maxTrade = min(floor(NAV * maxTradeBps / 10000), frozen listing sellChunkUsdg)
dailyLimit = floor(NAV * maxDailyTurnoverBps / 10000)
used = turnoverInEpoch, when turnoverEpoch == calendar.tradingDate(now), otherwise 0
remainingDaily = max(0, dailyLimit - used)
```

The stock price is the healthy live oracle stock/USDG price. `Math.mulDiv` floors the percentage calculations
without an intermediate multiplication overflow. Both `preview()` and `execute()` calculate limits from the
same context; execute first books incoming stock at the live price. Pending stock donations/taxes are already
included in preview. USDG donations enter the cash balance immediately.

**Buyback stock and every LP asset are excluded.** LP stock fees transferred through `creditLiquidityFee` go into
the buyback bucket. Their transfer does not increase trading NAV. This denominator is the same trading-inventory
denominator used for the allocation target, rather than the total value of all fund assets including locked LP.

An action must also be outside the allocation band, obey the cooldown, stay within the amount needed to reach
target, use the fixed venue, pass its health/price-limit checks, and have sufficient actual input. A percentage
cap below `minLotUsdg`, or a daily remainder below it, means **wait**; the core never rounds the cap up to minLot.
The sell-side conversion back to stock units can floor below minLot as well. A small fund may remain idle until
its trading assets grow. A large fund can hit the listing's absolute chunk even with a larger percentage allowance.

The daily cap follows the oracle calendar's **US trading date**, rolling at 20:00 New York time with DST. It is
a cumulative turnover cap, not a rolling 24-hour limit. `used` is an absolute USDG ledger and does not reset when
the price, balances, target or NAV change within a session. A smaller NAV may make the current limit lower than
already-used turnover: remaining becomes zero, without refunding or rewriting history. A subsequent increase
in NAV releases only `newLimit - used`. Only a new trading date starts a fresh ledger.

For example, 1,000 USDG of trading assets with a 10% trade / 50% daily configuration permits up to 100 USDG per
action and 500 USDG cumulative turnover. After 200 USDG has been used, a fall to 300 USDG of NAV produces a 150
USDG daily limit and zero remainder. A recovery to 600 USDG gives a 300 USDG limit and 100 USDG remainder. The
listing chunk and minLot can reduce or prevent a proposed action in all three snapshots.

Fees, execution reward and gain allocation can reduce NAV during an action. The successful action is bounded
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
| bits 16–31 | band, percentage points in bps | `>= 2*(slippage + floor(poolFee/100) + bounty)`; `< target`; target + band `< 10,000` |
| bits 32–63 | cooldown seconds | 600–`uint32.max` |
| bits 64–79 | profit buyback share, bps | 0–10,000 |
| bits 80–255 | reserved | zero |
| `words[1]` | maximum action as trading-NAV bps | 1–10,000, full-width word; never truncate high bits |
| `words[2]` | maximum daily turnover as trading-NAV bps | `>= words[1]`; `<= 10,000`; `<= 24*words[1]` |

10% = 1,000 bps and 50% = 5,000 bps. Recommended initial UI inputs are target 70%, band 5 percentage points,
cooldown 600 seconds, action cap 10%, daily cap 50%, profit buyback share 0%. They are a starting configuration,
not evidence of profitable trading. Allocation-band validity still depends on the selected listing's actual
slippage/pool fee/bounty. Listing `sellChunkUsdg >= minLotUsdg` is required by the new constructor.

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
  this bucket removes it from subsequent trading NAV.
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

Amounts are USDG base units. `healthy` describes a valid live pricing snapshot, not whether allocation and
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
minLot/dust/rounding, dynamic daily capacity, deposits/LP/buyback exclusion, partial settlement/rewards,
constructor and policy bounds, old-code isolation, hostile policies/staticcall/returndata, reentrancy, and
real winter/summer/DST trading sessions. Stateful invariants use the authoritative health price and independently
derive turnover from balance deltas and buyback allocation; they check caps against each action's pre-trade NAV.

Local final verification: **1,658 passed, zero failed, 58 skipped** across the repository. The seven new suites
contribute **39 passing tests**, including three 256-case fuzz tests and two stateful invariants, each run for
256 sequences of 500 calls (128,000 calls per invariant). The skipped optional fork/integration cases are not
live deployment evidence. `forge build --sizes` and the subsequent final build passed.

Build commitments (creation code excludes constructor arguments):

| Artifact | Bytes | Keccak256 |
| --- | ---: | --- |
| new treasury creation | 29,921 | `0xf8854a3949d510bf3c49243ceb8ee4dd1eeedda5f45640276c542bab30b79caa` |
| new treasury runtime template | 23,205 | contains immutable placeholders; actual fund runtime depends on constructor inputs |
| new policy runtime | 1,973 | `0xf12d579197a13a01e83cbc76abc25df97b14d0cb25860f93dfd48061767215b9` |
| existing rewarded treasury creation | 29,388 | `0x21db9a11b19dfe73eb5e372972f0dc7057595360c989d92012ba4b638e0d271f` |
| existing policy runtime | 1,799 | `0x703c92e4d169643e9b20eadf00cd53470b95699186a5ce9131feee42299c0b74` |

The new treasury plus frozen constructor arguments/config is 30,785 initcode bytes, below EIP-3860's 49,152;
runtime is below EIP-170's 24,576. The registered policy budget is 150,000 gas with exact 160-byte intent return.
Tests execute the genuine policy through this budget. These commitments must be recomputed after any production
source/configuration change; source commit must identify the committed new source, not just its base revision.

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
   keeper rewards, cumulative epoch ledger and tiny-NAV waits. This requires additional explicit human signing;
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

The frontend checks registration receipt blocks are no later than its quote block, checks both transactions
target the registry and come from the published factory owner, and matches the two events and live manifests/
chunks/runtime at the pinned quote block. Optional extended deployment audit material may include block hashes,
chunk deployment receipts, dependency/audit manifests, compiler inputs and unchanged-old-fund snapshots. It must
not invent live addresses, a kind index, successful transactions or a proof source commit from a local simulation.

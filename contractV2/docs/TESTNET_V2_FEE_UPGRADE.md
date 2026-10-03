# V2 two-sided fee testnet release

This release creates a new core on Robinhood testnet, chain **46630**. It reuses the eight existing synthetic
stock venues: AAPL, GME, NVDA, TSLA, MSFT, AMZN, GOOGL and META. Prices are operator-set test values, not live
equity quotes. Existing strategies keep their original factory, curve, hook and fee rules.

## Deployment boundary

`script/DeployV2FeeUpgradeTestnet.s.sol` pins operator
`0x75Cee941B0eF3A83feA0397BbF903C12c1D7e96D`, the previous factory, quote asset, market, calendar, V3 factory,
eight stock/feed/oracle/pool tuples, PoolManager and WETH. Target addresses cannot be provided by environment
variables or calldata. Offline tests alone subclass the venue getters.

The script reads the previous factory's defaults and copies them with `sweepTipBps=0`; protocol share must remain
2000 bps and supply must be 1 billion tokens. Each stock's enabled listing, open price, gates and LP share are
read from the previous deployment and registered on the new core. Recommended launch parameters are
`taxBps=300`, `creatorBps=1000`, giving 20% protocol / 10% creator / 70% treasury of the base fee. Tax and creator
share remain bounded creator choices and become frozen per launch.

New components are treasury/token/curve deployers, a newly mined `HedgeFunV2Hook`, V2 factory, trade router,
native router, two additional strategy kinds and a rebalance policy. Previously bound components cannot be
bound again. The script never creates, mints into, swaps, pokes or reconfigures a reused stock market.

Preflight checks require operator ownership, matching old factory bindings, canonical V3 pools, registered
market lines, exact oracle dependencies and 26-hour age limits, positive liquidity and observation cardinality
at least 720. This does not certify present launch readiness: independently read `tryPrice()`, V3 600-second
TWAP, deviation/slippage gates and available depth before launching. The default synthetic feeds report the
current block timestamp; do not silently enable freshness if a stale-feed test deliberately disabled it.

## Build and rehearsal

After protocol review and merge, build from the final release source commit with initialized submodules:

```sh
git submodule update --init --recursive
forge test --match-path test/DeployV2FeeUpgradeTestnet.t.sol -vv
GIT_COMMIT="$(git rev-parse HEAD)" forge script script/DeployV2FeeUpgradeTestnet.s.sol:DeployV2FeeUpgradeTestnet \
  --rpc-url https://rpc.testnet.chain.robinhood.com \
  --sender 0x75Cee941B0eF3A83feA0397BbF903C12c1D7e96D --slow
```

`HOOK_SALT_START` is an optional nonnegative search offset; the script mines the **new hook creation code** and
refuses an occupied address. `GIT_COMMIT` must be a complete lowercase 40-character Git commit. No key is read
by the script. Check the estimated gas against the operator's live ETH balance, with reserve for the journey.
The rehearsal writes `deploy/testnet-v2-fees.dryrun.json`, never a published book.

Following deployment approval, the same reviewed command can use the operator's existing encrypted testnet
keystore with `--account <testnet-keystore> --broadcast --slow`. Independently inspect confirmed receipts before
retrying; use the saved Forge broadcast log to resume an interrupted broadcast rather than deploying a second
stack from an ambiguous outcome.

## Exact top-level transaction plan

There are **38 transactions**, all sent by the pinned operator. Child creations inside constructors and
`makeChunks` do not add top-level transactions. No feed refresh, mint or poke is part of this release.

| Indices (zero based) | Operation |
| --- | --- |
| 0–2 | Create V2TreasuryDeployer, TokenDeployer, CurveDeployer |
| 3 | CREATE2 helper deploys the newly mined HedgeFunV2Hook, constructor PoolManager |
| 4–6 | Create HedgeFunV2Factory, HedgeFunV2TradeRouter, HedgeFunV2NativeRouter |
| 7–8 | treasury.makeChunks(buyback creation code), registerKind (kind 1) |
| 9–10 | Create V2RebalancePolicy, registerPolicy (gas 150000, POLICY_RETURN_BYTES) |
| 11–12 | treasury.makeChunks(engine creation code), registerEngineKind (kind 2) |
| 13–36 | AAPL, GME, NVDA, TSLA, MSFT, AMZN, GOOGL, META: list, setListingGates, setLpBps each |
| 37 | factory.setPublicLaunch(true) |

Policy dependency/audit placeholders are the existing testnet strings used by `DeployV2Testnet`; the complete
call arguments are in `_register`. Constructor factory defaults equal the old defaults at the rehearsal block
with only sweep tip zero. Registration uses the old stock listing/gate/LP values observed before broadcasting.

## Candidate and independent publication

Even a live run writes `deploy/testnet-v2-fees.candidate.json` with **`broadcast=false`**. `broadcastRequested`
records the invocation context, not execution success. Dry-run and candidate stock prices are synthetic oracle
snapshots. Large quantities are decimal strings; fees, decimals, gates and counts are JSON integers.

The candidate has schema `v2-testnet-two-sided-fee-upgrade-v1`, feature version
`v2-two-sided-stock-fees-v1`, source commit, start block, operator/owner/protocol, full flat new core addresses,
hook salt, policy address/key, engine kind, all reused venue addresses and eight stocks. Extra stock fields record
old gates and LP allocation. `plannedTransactionCount=38`, recommended tax is 300 bps and creator share 1000 bps.
`baseFactory`, `baseTreasuryDeployer` and `baseBookSha256` link the previous published venue inventory; the pinned
base book SHA-256 is `1b0d19f7e5e36ec19df5c3d8b879d95510dbe70527724602686d95411d04b67e`.

The independent verifier must check canonical live transactions/receipts, exact from/to/input/value, nonces,
CREATE initcode and constructor arguments from the compiled commit, CREATE2 salt and initcode, child chunks,
all new bindings, `hook.version()==2`, zero sweep tip, kind/policy registrations, unchanged eight markets and
new listings/gates/LP. Pin code and readback hashes to a canonical verification block. Attest the current
600-second TWAP, ring cardinality, liquidity and quote-side balance separately from the historical base proof.

Only an independently verified publisher creates the Stage resource `/testnet-v2-fees.json` with `broadcast=true`
and `verification.schema=two-sided-fee-upgrade-readback-v1`. Its `upgradeProof` binds core, eight stocks, 38
transactions, code hashes and readback. Original stock-extension proofs are bound to the **old factory** and
must not be copied into the new book as proof of the new listings. Keep old books and old strategy routes.

## Stage and journey checks

- Add an independent deployment variant, resource and launch draft key. Keep strategy identity scoped by
  factory + ID; preserve the old deployment's explicit routes. Never redirect an old ID to the new factory.
- Both fee versions support opening recipient exemptions. New curve `quoteBuyFor`'s third output is **opening
  surcharge burn only**. Show base stock fee and opening token burn separately. V4 exact-input buys accrue
  token fees awaiting conversion; V4 sells accrue stock fees. Static V4 LP fee is additional.
- To finish the curve, read remaining net principal at one block and gross up exactly:
  `net == 0 ? 0 : (net - 1) * 10000 / (10000 - taxBps) + 1`. Confirm with recipient-specific quotes at the same
  block. The refunded excess is untaxed. Existing net-cap-as-payment logic is incompatible.
- Create a new TSLA strategy with 3% base tax / 10% creator share and a fixed whitelist. Verify actual launch
  fee, creator identity and frozen terms. Exercise ordinary/whitelisted buys inside the opening window, then
  curve buy/sell; read `TradeFeesAccrued`, `totalFees` and all three claims. Only the opening surcharge burns.
- Cross the cap and verify atomic graduation, locked V4 liquidity, actual net-principal capital split and unpaid
  curve liabilities retained in the curve. A stale curve-stage quote must refuse to sign/execute after graduation.
- Trade V4 buy/sell. Audit `Taxed` denomination and ERC6909 claims. `sweep` moves token fees into
  `pendingTokenFees`; it does not yet pay stock income or burn those fees. Sell stock fees split normally.
- Claim curve stock fees for protocol, creator and treasury; compare wallet deltas to their claims and book
  treasury fees independently. Repeated claims must not pay twice.
- Owner-only `convertFees` uses the actual pool key, nonzero reviewed stock minimum, short deadline, and a
  correctly directed sqrt-price limit within 0.5% of current sqrt price. Conversion consumes only actual token
  claims; partial fills leave pending claims. It creates stock claims, not paid stock. The following `sweep`
  pays 20/10/70 with integer floors and treasury remainder. Repeated sweeps must not duplicate payments.
- Read `accrued`, `pendingTokenFees`, manager balances, owed roles and shared stock pot after every phase.
  Keep LP fee collection/buyback budget separate from base trading fees; verify routers retain no input dust.
- On a local fork, include min-out/expired/wrong-owner/wrong-pool conversion reverts, partial conversion,
  same-stock two-pool isolation, blocked-recipient ledger recovery and graduation rollback. No synthetic stress
  report from the old fee version certifies these balances. Adapt historical event reconstruction to subtract
  curve buy stock fees from principal and add both buy/sell fee liabilities.

The old `TestnetV2Journey`, `TestnetTSLAStressLaunch`, whitelist verifier/publisher and stress replay deliberately
pin old addresses, fixed transaction counts or burn semantics. Use new versioned journey/verification evidence;
changing their old book underneath them is not a compatible reuse.

## TSLA phase script

`script/TestnetV2FeesJourney.s.sol` provides the new versioned journey. It pins the new factory/router/hook
predicted by this release's production dry-run, the original TSLA pool/stock and the three existing test wallets:
creator `0xD4f69D180a9bc36F27D307E90E365d1E012816d5`, operator/protocol
`0x75Cee941B0eF3A83feA0397BbF903C12c1D7e96D`, and exempt recipient
`0xdA1AEE7018a3925AA06dEEb8631Fca09E1067614`. If any operator transaction changes the deployment nonce
before deployment, update the public core pins from a fresh dry-run and commit before using this script.

After independent core verification, save the verified broadcast address book at `deploy/testnet-v2-fees.json`.
`ADDRESS_BOOK` may select the candidate for a read-only rehearsal once the core exists; a broadcast/resume
context requires `broadcast=true`. Every phase checks chain, core bindings, hook version and zero sweep tip;
trade phases additionally check the strategy's TSLA stock, creator, frozen 300/2000/1000 bps and expected stage.
No signing material is held by the script. Supply the appropriate testnet keystore only when the reviewed phase
is actually broadcast.

| Phase | Sender | Inputs / result |
| --- | --- | --- |
| launch() | Creator | `JOURNEY_NONCE`; tax 300, creator 1000, TP1/TP2 300/600, stop off; prints new ID |
| ordinaryBuy() | Operator | `JOURNEY_ID`, `USDG_IN` (up to 500 USDG), positive `MIN_STOCK_RECEIVED`, `MIN_FINAL_OUT` |
| whitelistBuy() | Exempt recipient | Same buy inputs; execute within the 180-second opening window |
| curveBuy() | Creator | Same buy inputs, up to 500 USDG |
| curveSell() | Creator | `TOKEN_IN` up to half creator holdings, positive `MIN_FINAL_OUT` in raw USDG |
| graduate() | Creator | Buy inputs; 8000–25000 USDG; use overflow (for example 20000) to verify stock refund |
| v4Buy() | Creator | Buy inputs, up to 1000 USDG; strategy must be graduated |
| v4Sell() | Creator | `TOKEN_IN` up to a quarter of holdings, positive `MIN_FINAL_OUT` in raw USDG |
| claimCurveFees() | Creator | Three claims to fixed protocol/creator/treasury recipients; no redirected payouts |
| sweepStockFees() | Creator | Settle sell stock fees; move accrued buy-token fees to pending manager claims |
| convertTokenFees() | Operator | Positive reviewed `MIN_CONVERSION_STOCK_OUT` in raw TSLA; optional max token cap and sqrt move |
| sweepConvertedFees() | Creator | Distribute conversion's stock claims, separately from conversion execution |
| inspect() | Any | Read-only reserve, unpaid curve claims, hook token/stock claims, pending and owed ledger |

All amounts use raw integer token units. Trade and conversion deadlines are 300 seconds. Buy min-stock protects
the V3 quote route; final-output minima protect net receipt after base fee and any opening burn. The converter's
optional `CONVERSION_MAX_TOKENS` defaults to this pool's pending + accrued token claims;
`CONVERSION_SQRT_MOVE_BPS` defaults to 50 and must be 1–50. The script derives the correctly directed limit
from current sqrt price and prints limits, actual consumption and resulting stock claims for independent audit.

Before launch, confirm creator USDG covers the 25 USDG launch fee, ordinary buy, and full overflow payment;
the refund is **TSLA stock**, not USDG. Confirm test ETH for each sender. Obtain/rehearse output minima before
each broadcast. Approvals are sent only if allowance is insufficient, so the receipt verifier must inspect each
actual phase's transaction list. A fresh run normally has 24 transactions through the 12 mutating phases;
`inspect()` sends none. Archive each phase's public broadcast log immediately, as Forge reuses the latest log.
Fee claims paid to treasury become unbooked stock until `book()` succeeds; the following healthy stock sweep
also asks the treasury to book it. Verify this independently rather than counting unconverted claims as income.

Example read-only launch invocation (the real core and verified book must already exist):

```sh
JOURNEY_NONCE=2026093001 forge script script/TestnetV2FeesJourney.s.sol:TestnetV2FeesJourney \
  --sig 'launch()' --rpc-url https://rpc.testnet.chain.robinhood.com \
  --sender 0xD4f69D180a9bc36F27D307E90E365d1E012816d5 --slow
```

The independent public journey report must bind the verified new book and canonical phase receipts, parse
curve `TradeFeesAccrued` separately from `Bought` burn amounts, check the overflow `stockRefund`, and reconcile
20/10/70 claims with actual stock wallet changes. Account for all partial token conversions and retained claims;
successful contract execution alone is insufficient evidence of settled revenue.

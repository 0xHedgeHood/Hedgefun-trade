# V2 dual-engine architecture and adversarial review

This is the historical dual-engine review. The subsequent [two-sided fee change](./V2_TWO_SIDED_FEES.md)
changes ordinary buy fees from token burns into income and requires fresh curve/hook/factory deployment.
Its accounting, conversion tests and review are additional release gates; older measured capital results
do not describe the new fee model.

This review covers the change from the stock-denominated curve in PR #77 to a graduation that funds both
locked V4 liquidity and the stock-strategy treasury. It is an internal code/test review, not an independent
third-party audit and not a deployment approval.

## Model and capital accounting

Each strategy still has one listed stock as its curve and V4 quote asset. Payment in another routable ERC20
first converts to that stock. The only capital split is at graduation: from the curve's **real stock reserve**,
`floor(reserve * lpBps / 10000)` is the V4 stock budget and the remainder funds the treasury. The factory owner
sets `lpBps` per stock for future launches (10%–100%, default 50%); each launch freezes it into its treasury.
Virtual stock is a pricing term and never counts as capital. The actual V4 spend may be below budget by rounding;
that dust also goes to the treasury. `GraduationCapitalSplit` records both actual legs and whether treasury booking succeeded.

V4 opens at the curve's terminal spot price. At the default 50% share the LP uses roughly half as many FUN tokens
as the previous all-LP design; unmatched inventory is burned. The configured share changes circulating supply
and initial V4 depth. The 50/50 default is a test baseline, **not a measured optimum**. There is no
principal rebalancing after graduation and no token redemption claim against treasury assets.

## Findings and controls

| Architectural issue | Control in this branch | Residual consideration |
|---|---|---|
| Old V4 position was owned by Factory; positive LP fees would be stranded | New per-pool immutable `V2LiquidityVault` owns the position from its first mint, exposes only zero-delta fee settlement, and has no principal-withdrawal path | Prior already-graduated factory-owned positions cannot use this vault; the new pool requires a fresh deployment |
| A generic fee transfer to Treasury would become a stock-strategy cost-basis lot through `book()` | Treasury pulls stock LP fees from its registered vault into `buybackStock`; FUN-side LP fees burn directly | Stock-side fees are spent only through the Treasury's existing bounded buyback; they are not an instant market buy |
| An issuer or treasury rejects the stock-fee credit after V4 collection | The vault parks that stock fee for its next permissionless `collectFees()` retry and still burns the independent FUN-side fee | A stock transfer refused inside V4's `take` still reverts collection; retry after the issuer restores transfers |
| LP fees fund a permissionless buyback and repeatedly arm the high sell tax | Graduated V2 rates freeze `spikeBps = 0`; V1's existing hook behavior is unchanged | Buybacks no longer raise V2 sell tax; model exits at the flat rate |
| Graduation may occur while the stock oracle is unavailable | Stock remains as `unbookedStock()`; anyone may later call `book()` when the oracle is live; `stockEquivalentHeld()` includes pending stock whenever a healthy valuation is available | While health is closed, no oracle-valued total is shown; show raw pending balance and `treasuryBooked` to users |
| A smaller LP budget reduces depth and makes prices more sensitive to trades | Preflight checks the configured budget, final-buy migration is atomic, quote guards and existing TWAP/cooldown/impact limits remain, and adversarial trading fuzz now interleaves fee collection | Set the per-stock LP share using representative trade sizes and live venue depth before production |
| LP fee and hook tax both charge trades after graduation | V2 requires a nonzero static LP fee capped at 0.30%; V1 still enforces zero | Frontend quotes must include both fees. Terminal **spot** continuity does not imply identical net execution price |
| Factory and treasury deployer were near the EIP-170 code-size limit | One-time graduation runs by guarded delegatecall to the factory-bound `CurveDeployer`; per-pool fee logic lives in the vault | Current Factory and treasury deployer margins are narrow, so each later source/compiler change needs a fresh size check |
| Anyone can transfer tokens to the predictable vault address before deployment | Seed accounting and later fee collection ignore pre-existing balances | Such voluntary donations are not recoverable through the vault; the UI must not treat them as LP capital or fee yield |

The vault holds a fixed pool key, full-range ticks and salt. `collectFees()` can issue only
`liquidityDelta = 0`, checks that the returned delta equals the accrued fee delta, and rejects reentrant token
callbacks. It exact-checks token movement, approves the Treasury for only the collected stock fee and clears
that allowance. The Hook permits initialization and positive liquidity only from the registered vault for
these pools; V1 pools continue to require their factory. No actor, including the creator or Factory owner,
has a V4 principal-withdrawal entry point.

## Verification and limits

The local tests cover both token orderings, 6/18-decimal stock, the atomic final-buy boundary, partial
refunds, donation isolation, callback reentrancy, three-actor sequences, taxed round trips, half-depth V4
trades, fee-only collection, pending treasury capital and zero/excessive LP fee rejection. The fee vault's
standalone tests use a real V4 PoolManager; the integration tests use the real Factory, Hook, Treasury and
PoolManager. The public-chain fork pins Robinhood Chain block `70,786,980` and uses the live USDG/GME V3
venues and deployed V4 manager; no transaction is broadcast. It exercises curve trades, graduation, V4
buy/sell, hook tax and LP fee collection into Treasury/burn. The same suite registers the exact shipped kind-1
code, selects it for a creator, graduates it and executes its first real V4 buyback at both 5000 and 7000 LP bps.
Because the fork curve is intentionally only about 100 USDG deep to limit archive reads, that kind-1 case uses a
0.25 USDG test minimum lot; production must calibrate minimum lot, impact cap and seeded depth together.

This does not establish that the 50/50 default or 0.30% maximizes value, or that a trader cannot profit by moving the
shallower pool price, waiting for TWAP to catch up, triggering a treasury buyback and exiting. A production
decision still needs a measured adversarial slippage/volume model for the intended stock listings, review
of issuer pause/upgrade risk, deployment
rehearsal and an independent security audit of this changed code. Earlier PR #77 review results do not
substitute for those checks.

**Go/no-go:** ready for PR review and economic calibration; not approved for production deployment.

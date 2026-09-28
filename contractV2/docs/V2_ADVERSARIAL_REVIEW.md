# V2 adversarial review — 2026-09-23

Original scope: the V2 production code in PR #77 at `885123e`, including the inherited factory, hook and treasury paths.
Three agents reviewed separate concerns, exchanged evidence, and cross-checked the resulting tests. This is an
internal engineering review, not an external audit or a proof of absence of vulnerabilities. Production source
and ABI were not changed in this review round.

**Historical result: GO for code review/integration at `885123e` only.** The current PR #84 adds the fee vault,
strategy kinds and other contracts absent at that ref; this verdict does not approve its deployment. Merging
still requires green CI. Production activation retains the separate integration and
deployment conditions in [the V2 guide](./V2_BONDING_CURVE.md#deployment-and-frontend-boundaries).

## Attack and defense coverage

EVM transactions execute serially. The tests model competing users through different transaction orderings
in the same block, interleaved multi-user sequences, and nested external callbacks, rather than pretending that
two transactions execute simultaneously against shared state.

| Suite | Cases | Evidence |
|---|---:|---|
| [Callback defenses](../test/V2AdversarialCallbacks.t.sol) | 8 | Unauthorized/idle callbacks, six malformed V3 callback modes, native payout reentry and refusal, ERC20 reentry, stale-stage native calls, and cross-user fee rights. Success paths clear temporary approvals; failed paths preserve balance/state digests. |
| [Multi-user trading](../test/V2AdversarialTrading.t.sol) | 9 | Alice/Bob/Mallory random actions, same-block round trips, last-buy races, stale quotes, preinitialization/release attempts, tight/loose slippage sandwiches, and graduation rollback with multiple holders. Uses real V2 contracts and a local production V4 PoolManager. |
| [Independent accounting](../test/V2AdversarialAccounting.t.sol) | 7 | Separate cumulative payment/proceeds/fee/claim/donation/burn ledger, graduation with/without donations, actual booking before anchor assertions, and early versus mature TWAP behavior in both currency orders. |
| [Live-venue fork](../test/V2LiveVenueFork.t.sol) | 4 | Two users route USDG through the real GME V3 pool into/out of a new curve, graduate, trade through the chain's V4 PoolManager, claim fees and sweep taxes; loose/tight curve sandwiches are measured; exact kind-1 code is registered, selected, graduated and used for a real V4 buyback. All deployments and transactions exist only in the local fork. |

The original 22 local cases passed a parent-run replay with `--fuzz-runs 512 --fuzz-seed 0x77`. Three state-machine
properties schedule 48 steps per generated case: **73,728 scheduled steps** across the generated sequences,
plus 512 generated same-block round-trip cases. Cached regression inputs can add replay cases; actions with no
available balance may be no-ops, and the ledger is checked after each step. Two additional mature-TWAP cases
bring the local suite to 24 tests. The live-venue test is an additional opt-in fork test.

The accounting model independently accumulates executed cash flows and compares user, reserve, liability,
LP, donation and supply balances. Quotes still come from the production curve/router, so this is not an
independent mathematical proof of all pricing formulas. Existing tiny-unit rounding and fixed-product fuzz
tests remain part of the full suite.

## What the tests established

- With no other user's intervening trade, a taxed buy/sell round trip does not create stock profit in the
  tested ranges. Multi-user sequences preserve accounted principal, fee claims, donations and token supply.
- Another user can finish graduation first, but a stale Active-stage route then reverts without silently
  executing against V4. A failed graduation restores all users' balances, burns, fees and pool state.
- Unsolicited balances cannot fund settlement or become another trader's refund. Unauthorized release,
  pool initialization and callbacks fail. Payout/token callbacks do not produce a second payment.
- A tight minimum-output quote rejects the modeled front-run price deterioration. A permissive quote permits
  an ordinary profitable AMM sandwich. That result is expected MEV within accepted price limits, not evidence
  that fees, donations or unrelated users' reserves can be withdrawn without authorization.
- The young-pool graduation anchor rejects the modeled immediate price push. `book()` is required to return
  success and add a lot before the test claims that booking cannot overwrite that anchor.

## Mature TWAP is a market reference, not an immutable price guarantee

Both currency orders reproduce the following local scenario using the 10% curve/trading tax and 300-bps
buyback-impact configuration:

| Measurement | Result |
|---|---:|
| Participant's starting stock | 100 |
| Stock paid to move the V4 price | 50 |
| Time held before the treasury buyback | 600 seconds |
| Treasury stock spent | 1 |
| Tokens burned with no preceding market buy | 785.7777953801 |
| Tokens burned after the sustained price change | 351.0870424587 |
| Keeper token bounty in the sustained-price case | 1.7642564948 |
| Participant's stock after waiting and exiting | 92.1512111166 |

The early buyback reverts. After 600 seconds, the hook can serve a TWAP reflecting the sustained price, and the
same stock budget buys fewer tokens. The test checks the actual sqrt-price boundary derived from the stricter
TWAP/spot limit, gross output implied by that boundary, exact treasury spend, bounty, burn and remaining budget.
The old graduation anchor is not overwritten; a usable TWAP has become the reference.

`buyback()` has no separate user-supplied minimum-token-output argument. Its protection here is its pool-price
limit and spend budget. The participant's router calls separately test `minFinalOut`: one unit above the
simulated output reverts, while the exact minimum executes.

These figures were remeasured from `testMatureTwapBoundaryToken0/1` on the current PR #84 source on
2026-09-27 with the command below; both currency orders agree to the shown precision. The old table was
measured at `01e515a` and had become stale after later source changes. The participant loses stock in this
parameterized example. This
does **not** establish that every tax rate, treasury balance, liquidity level, holding period or coordinated
strategy is unprofitable. The test funds synthetic *already-realized* profit and sets its accounting slot to
isolate buyback execution; it does not suggest that a real user can change treasury storage or prove the stock
strategy's profit-generation path. Sustained-price manipulation and MEV remain economic design considerations.

## Live-venue evidence and limits

The added fork test passed at Robinhood block **70,717,634**, with **1 passed / 0 failed / 0 skipped**. It used
the repository's existing verified USDG/GME, canonical V3 pool, feeds and V4 PoolManager addresses. No swap
or pool-price call was mocked. The final capped buy returned **6.450877074340927318 GME** to the correct user.

Test users were funded by impersonating a funded pool in the local fork. Feed answer values remained real,
but their timestamps were refreshed, and an AlwaysOpen calendar removed market-hour dependence. This proves
compatibility of route execution and settlement for the sampled state; it is not a test of actual wallet
funding, unmodified oracle freshness, all market sessions, every payment asset, or issuer upgrades. No signing
or broadcasting occurred. Without `RH_FORK=1`, the test explicitly reports SKIP.

## Reproduction and CI

Current local checks on 2026-09-27: **1,422 passed / 0 failed / 52 explicitly skipped** in the offline default
profile; the 13-case low-frequency suite passed at fixed block **70,786,980**; and the four-case live-venue
suite passed at that block with both 5000 and 7000 LP bps. Python lab, bytecode-size and generated-document checks
also passed. The other required fork suites and GitHub CI remain separate gates on the PR's latest commit.

```sh
forge test --mc 'V2Adversarial(Accounting|Callbacks|Trading)Test' --fuzz-runs 512 --fuzz-seed 0x77 -vv
RH_FORK=1 RH_RPC=blockmachine RH_FORK_BLOCK=70786980 V2_LP_BPS=5000 \
  forge test --threads 1 --mc StrategyForkTestV2LiveVenue -vv
# A suitable archive RPC is required to replay the exact sampled block:
RH_FORK=1 RH_RPC=blockmachine RH_FORK_BLOCK=70786980 V2_LP_BPS=7000 \
  forge test --threads 1 --mc StrategyForkTestV2LiveVenue -vv
```

The broader required fork suite also selects the new test through its `StrategyForkTest` contract prefix.
The workflow serializes push and pull-request fork jobs for the same branch, uses one test thread, retries only
explicit 429/rate-limit failures after 65-second resets, and separately gates 5000 and 7000 live-venue runs. Any
ordinary test failure exits immediately; exhausted retries, skipped tests or a missing named proof also fail.

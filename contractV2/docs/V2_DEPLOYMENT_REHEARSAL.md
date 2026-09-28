# V2 launch rehearsal

Use `script/RehearseV2Launchpad.s.sol` to check that the V2 deployers, hook, factory, stock trade router, and kind-1 buyback code registration can be created and bound on a **local fork**. The script rejects every chain ID except `31337` and also rejects Foundry broadcast/resume contexts. It does not list a stock, open public launches, create a strategy treasury, move funds, or deploy to Robinhood Chain. Run the command below **without `--broadcast`**: Foundry simulates its deployment transactions and discards them.

```sh
anvil --fork-url https://rpc-robinhood.blockmachine.io \
  --fork-block-number 70786980 --chain-id 31337 -p 8545
```

In a second terminal, from this repository's root:

```sh
export OWNER=0x2910117dd2cB431173Ae9Fb6eAF30726321d1693
export PROTOCOL=0x2910117dd2cB431173Ae9Fb6eAF30726321d1693
export CALENDAR=0xFE9E85f0C258Fc2757eB6Acd1ca032Ec860487F5
forge build --sizes
forge script script/RehearseV2Launchpad.s.sol:RehearseV2Launchpad \
  --rpc-url http://127.0.0.1:8545 \
  --sender 0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266 \
  --unlocked -vv
```

The three role addresses above are examples from the existing V1 deployment record, **not assertions of current control**. Confirm their code, Safe threshold, owners, and intended V2 roles at the rehearsal block. The script requires `OWNER` and `PROTOCOL` to answer as Safes with at least two signatures, checks that `CALENDAR` exists, and refuses a broadcaster holding either role. It uses the known PoolManager, V3 factory and USDG addresses; all must have code on the fork. If a mined hook address is occupied, set `HOOK_SALT_START` beyond the printed salt and rerun.

The rehearsal uses fixed candidate defaults: 1 billion token supply, 0.30% V4 LP fee, 1%–15% configurable token tax, 25 USDG launch fee, a 3-second opening buy tax for launches whose creator chooses no window, and V1-sized strategy execution gates/chunks. These are **candidate settings**; confirm them with a stock-specific price, depth, and curve/LP capital analysis before launch. V2 cannot reuse V1's zero LP fee because its locked vault collects LP fees. The script prints the deployed addresses, kind-0 and kind-1 code chunks, the curve deployer's curve code chunk, `registered strategy kinds 2`, and its readback result; the readback also requires that chunk's code to equal the curve's creation code. It also prints and reads back the creator's curve choices on the curve deployer: the 1000–9000 `saleBps` range, the 4400 default, the 180-second window cap and the factory's default window, and it simulates one creator registration from a throwaway address. The kind-1 `registerKind` call is simulated with the configured owner address; it does not prove Safe signing or execution. A successful output ends with `readback passed; no stock listed or launched` and `public launch false`.

On 2026-09-27, the script completed without `--broadcast` against a local fork of Robinhood Chain block
**70,786,980** (`0xa6acfdd287dfd85edd9c6b555031d578b8f51cfdf9eb4792857249401e35b72a`). It compiled,
deployed the kind-0 and kind-1 code chunks, both other deployers, the mined hook, V2 factory and trade router in the
simulation, registered kind 1 through the simulated owner call, and passed the code-hash, binding, defaults and
closed-public-launch readbacks. Estimated total script gas was **35,150,486**. These simulated addresses are not
production addresses. That run predates the curve deployer's code chunk and the opening-tax fix of 2026-09-28; repeat
it on the release commit (item 2 below).

On 2026-09-28 it completed again at the same block with the creators' curve choices in place: `readback passed; no
stock listed or launched`, `public launch false`, the curve-choice readback (`creator saleBps range 1000 9000`,
`default saleBps (no registration) 4400`, `creator snipeSeconds max 180`, `default snipeSeconds (no registration) 3`)
and an estimated **37,452,577** gas. That is a development commit, not the release commit.

Before a real V2 launch, require all of the following:

1. V2 PR reviewed and merged after the full local suite, live venue fork suite, ABI/document checks, and runtime/initcode size checks pass. The factory has a narrow code-size margin (179 runtime bytes since the owner's per-stock `saleBps` was removed); the curve deployer has 7,846 since it took the creator's curve-choice registry.
2. Repeat this rehearsal at a recent fork block using the exact release commit and intended Safe/calendar addresses. Record the commit, fork block, default settings, readback, and gas estimates. A fork result does not prove chain RPC availability or actual account signing.
3. Verify each stock's oracle, USDG V3 pool, market-hours behavior, opening price, curve endpoint, graduation V4 pool, and trading route. Check token transfers and pool fee on a stock-specific fork launch and buy/sell/graduation replay. **The listing check (audit round 3 M-2 / M-3):** run `tools/v2_launch_check.py`. Run it before enabling a listing or opening public launches, and again immediately before each V2 launch, because pool depth here moves by tens of percent within an hour. The raise size is each creator's choice, so for a launch the check runs at that creator's `saleBps` and its verdict is shown to the creator as a warning; nothing on chain refuses the launch ([decision below](#raise-size-and-opening-window-decided-2026-09-28)):

   ```sh
   python3 tools/v2_launch_check.py --stock <T>                          # before V2 exists: deploy/v2-listings-plan.json
   python3 tools/v2_launch_check.py --stock <T> --factory <V2 factory>   # after: listing, gates, supply, default saleBps
   python3 tools/v2_launch_check.py --stock <T> --factory <V2 factory> --sale-bps <N>   # a creator's proposed raise
   ```

   It forks the chain at a pinned block and reads `Rg = ceil(supply * virtualStock / minTokenReserve) - virtualStock` off a real `HedgeFunBondingCurve` built with the listing's parameters. It then has one buyer take Rg out of the stock's V3 pool with a single swap. Rule 1(a) fails if the pool cannot deliver Rg at any price. Rule 1(b) fails if the post-trade pool fails `PoolTrader._health` at the listing's `maxDeviationBps`: spot against the oracle, and spot tick against the 600-second mean. That is the same code every V1 and V2 treasury on the pool runs. For a 0.05% pool it also applies the [chunk rule below](#deployment-parameters-decided-after-audit-round-4). Each failure prints one line naming the rule and the number that broke it, and a rule 1(b) failure also prints the largest `openPriceE18` the gate admits. The check uses the listing's current gates; a V1 treasury launched under tighter ones keeps them, so if a listing's gates were ever loosened, compare against those treasuries' `params()`. Attach the output, which carries its block and time, to the Safe proposal for the listing, run at the default `saleBps` (4400). A listing is a separate Safe decision; disabling it later stops only future launches. Direct-stock buys remain possible, so V3 inventory alone does not prove a curve can never graduate.
   Record `lpBps` explicitly for that stock in the Safe proposal and show full-raise size, V4 opening depth, and early-buyer exit scenarios at that `lpBps` across the raise sizes creators may choose. `saleBps` is no longer an owner setting. The code's permissive 90% sale / 10% LP bounds are validity bounds, not recommended settings; the experiment's 70% / 60% is a hypothesis, not a proven safe default.

### Listing check: first live run

`python3 tools/v2_launch_check.py` on the planned set, [`deploy/v2-listings-plan.json`](../deploy/v2-listings-plan.json):
the eighteen V1 listings at the rehearsal's V2 parameters (supply 1e27, the then-default `saleBps` 8000, the V1 `openPriceE18`) and
the gates and chunks those listings use today. Fork of block **75,042,450**, 2026-09-28 19:18:07 UTC
(`0x5e39b12322098c7398d82d0b30c3c05a05387a58737aca4ce7566ef83d2347bb`), official public RPC, read-only.
It exited 1: **5 PASS, 13 FAIL, every FAIL on rule 1(b)**. No pool failed rule 1(a), and no 0.05% listing failed
the chunk rule.

| stock |   fee |       Rg | deliverable | USDG for Rg | move bps | vs oracle bps | gate bps | Rg in gate | depth/1% up | depth/1% down | max chunk | chunk | verdict |
|-------|-------|----------|-------------|-------------|----------|---------------|----------|------------|-------------|---------------|-----------|-------|---------|
| NVDA  | 0.05% |   176.80 |    5,156.03 |      40,720 |     +7.7 |         -17.3 |       50 |       100% |     460,380 |       473,304 |    46,038 | 2,000 |    PASS |
| SPCX  | 0.05% |   262.40 |   10,590.33 |      38,421 |    +17.9 |          -1.5 |       50 |       100% |     213,224 |       139,451 |    13,945 | 2,000 |    PASS |
| CRCL  | 0.30% |   436.00 |   13,490.21 |      37,968 |    +61.9 |         +68.2 |       50 |        70% |      61,679 |        58,617 |       n/a | 2,000 |    FAIL |
| GOOGL | 0.05% |   112.00 |    1,440.06 |      38,410 |    +50.1 |         +66.2 |       50 |        69% |      74,747 |        68,871 |     6,887 | 2,000 |    FAIL |
| AMZN  | 0.30% |   154.00 |    2,507.68 |      38,153 |    +35.0 |         +47.0 |       50 |       100% |      98,699 |        93,878 |       n/a | 2,000 |    PASS |
| GME   | 0.05% | 1,692.00 |    3,281.23 |      40,875 |   +256.2 |        +232.3 |       50 |        34% |      22,205 |        20,759 |     2,076 | 2,000 |    FAIL |
| META  | 0.30% |    53.60 |      339.69 |      38,989 |   +142.4 |        +158.5 |       50 |        21% |      25,860 |        17,403 |       n/a | 2,000 |    FAIL |
| USAR  | 0.30% | 2,384.00 |    4,082.23 |      36,403 |  +1042.5 |       +1070.6 |       50 |         1% |       1,981 |         1,521 |       n/a | 1,000 |    FAIL |
| MSTR  | 1.00% |   244.40 |    1,359.34 |      39,858 |   +181.4 |        +230.7 |      125 |        43% |      22,106 |        21,312 |       n/a | 2,000 |    FAIL |
| AMD   | 0.30% |    65.20 |      154.35 |      40,328 |   +327.6 |        +327.2 |       50 |        16% |      12,323 |         9,898 |       n/a | 1,000 |    FAIL |
| MU    | 0.30% |    38.48 |      397.03 |      40,899 |   +106.3 |        +129.6 |       50 |        27% |      38,763 |        38,538 |       n/a | 2,000 |    FAIL |
| INTC  | 0.30% |   330.80 |    1,069.72 |      38,901 |   +299.1 |        +285.2 |       50 |        15% |      13,797 |        11,033 |       n/a | 1,000 |    FAIL |
| AAPL  | 0.05% |   119.20 |      504.09 |      40,566 |    +66.2 |         +67.3 |       50 |        75% |      59,796 |        49,908 |     4,991 | 2,000 |    FAIL |
| QQQ   | 0.05% |    54.00 |    1,146.99 |      39,893 |    +21.4 |         -20.0 |       50 |       100% |     184,601 |       106,305 |    10,631 | 2,000 |    PASS |
| MSFT  | 0.30% |    80.00 |      365.40 |      41,195 |    +79.4 |        +105.5 |       50 |        29% |      52,479 |        86,641 |       n/a | 2,000 |    FAIL |
| TSLA  | 0.30% |   106.00 |      914.70 |      38,288 |    +95.0 |        +116.3 |       50 |        29% |      40,399 |        29,958 |       n/a | 2,000 |    FAIL |
| USO   | 0.30% |   272.00 |    8,122.42 |      40,669 |    +19.5 |          +4.9 |       50 |       100% |     204,243 |       198,510 |       n/a | 2,000 |    PASS |
| GLD   | 0.05% |   101.60 |    3,427.39 |      38,582 |    +26.5 |         +52.8 |       50 |        91% |     120,021 |       104,715 |    10,471 | 2,000 |    FAIL |

These are one block's numbers; re-run the check rather than quoting them.

- **The thirteen failures are all rule 1(b).** A single buyer taking the whole raise pushes spot past the 50 bps gate
  (MSTR's is 125) and would shut `health()` for every treasury on that pool until arbitrage re-pegs it. The worst are
  USAR (+1,071 bps from the oracle, 1% of Rg inside the gate), AMD (+327), INTC (+285) and MSTR (+231). Each FAIL
  line gives the `openPriceE18` at which Rg would fit inside the gate at `saleBps` 8000. For INTC that is about
  12.4e9, 15% of today's listing price.
- **GLD and GOOGL fail on the oracle edge alone.** Their pools already sat +26 and +16 bps above Chainlink before
  the raise, so a move under 50 bps still ends outside the gate. AMZN passes at +47 bps, three inside it.
- **INTC now passes rule 1(a), which round 3 failed.** Round 3 read its pool at 262-318 INTC against the 330.80
  required. A mint of 349.88 INTC and 20,203 USDG landed in that pool at block 75,021,201 (18:42:34 UTC, 36 minutes
  before this run), and the pool now holds 1,081.88 INTC. The provider can withdraw it at any moment, which is why
  the check runs again before each launch.
- **Chunk caps on the 0.05% pools** are 10% of the thinner side's depth per 1%: NVDA 46,038, SPCX 13,945,
  QQQ 10,631, GLD 10,471, GOOGL 6,887, AAPL 4,991 and GME 2,076 USDG. Every configured chunk is 2,000 and passes.
  GME's margin is 76 USDG, about 4%, while depth here moves by 30% in half an hour. Lower GME's chunk before its
  first V2 launch; round 4's 1,300 USDG leaves a 37% margin on this read.

### Listing check at the 4400 default, and a creator's 9000

The same plan with its new default `saleBps` of 4400, fork of block **75,085,620**, 2026-09-28 20:30:24 UTC
(`0xc0a3233f7e3c57fe2cca4fcde504c661621b9d14c650e9af4c5afa4e18b9a050`), official public RPC, read-only. It exited 1:
**15 PASS, 3 FAIL, every FAIL on rule 1(b)** (META, USAR, INTC). No pool failed rule 1(a) or the chunk rule.

| stock |   fee |     Rg | deliverable | USDG for Rg | move bps | vs oracle bps | gate bps | Rg in gate | verdict |
|-------|-------|--------|-------------|-------------|----------|---------------|----------|------------|---------|
| NVDA  | 0.05% |  34.73 |    6,392.17 |       7,952 |     +0.9 |         -23.0 |       50 |       100% |    PASS |
| SPCX  | 0.05% |  51.54 |   10,893.45 |       7,517 |     +4.9 |          +4.8 |       50 |       100% |    PASS |
| CRCL  | 0.30% |  85.64 |   14,185.52 |       7,366 |    +12.9 |         +25.5 |       50 |       100% |    PASS |
| GOOGL | 0.05% |  22.00 |    1,389.16 |       7,546 |     +9.9 |         +47.7 |       50 |       100% |    PASS |
| AMZN  | 0.30% |  30.25 |    2,523.21 |       7,482 |     +6.4 |         +15.2 |       50 |       100% |    PASS |
| GME   | 0.05% | 332.36 |    3,119.65 |       7,975 |    +27.0 |         +25.0 |       50 |       100% |    PASS |
| META  | 0.30% |  10.53 |      346.07 |       7,583 |    +34.4 |         +59.5 |       50 |        72% |    FAIL |
| USAR  | 0.30% | 468.29 |    4,184.37 |       6,899 |   +297.4 |        +279.2 |       50 |        11% |    FAIL |
| MSTR  | 1.00% |  48.01 |    1,517.33 |       7,682 |    +36.5 |         +82.5 |      125 |       100% |    PASS |
| AMD   | 0.30% |  12.81 |      151.36 |       7,829 |    +56.1 |         +17.8 |       50 |       100% |    PASS |
| MU    | 0.30% |   7.56 |      396.57 |       7,997 |    +17.5 |         +37.0 |       50 |       100% |    PASS |
| INTC  | 0.30% |  64.98 |    1,046.93 |       7,573 |    +53.6 |         +62.9 |       50 |        74% |    FAIL |
| AAPL  | 0.05% |  23.41 |      515.29 |       7,941 |    +13.2 |          +6.1 |       50 |       100% |    PASS |
| QQQ   | 0.05% |  10.61 |    1,175.07 |       7,821 |     +4.2 |          +2.1 |       50 |       100% |    PASS |
| MSFT  | 0.30% |  15.71 |      387.82 |       8,049 |     +9.9 |         +19.0 |       50 |       100% |    PASS |
| TSLA  | 0.30% |  20.82 |      920.71 |       7,485 |    +20.7 |         +31.8 |       50 |       100% |    PASS |
| USO   | 0.30% |  53.43 |    7,564.89 |       8,015 |     +3.9 |         -22.3 |       50 |       100% |    PASS |
| GLD   | 0.05% |  19.96 |    3,544.88 |       7,544 |     +5.3 |          -3.6 |       50 |       100% |    PASS |

A 4400 raise is about 7,000 to 8,000 USDG per stock, a fifth of the 8000 run's 36,000 to 41,000. META fails on the
oracle edge: its pool already sat +25 bps above Chainlink. INTC fails by 13 bps. USAR's pool is too thin for any raise
of this size.

A creator's 9000 on three thin pools, `--stock USAR,GME,AMD --sale-bps 9000`, block **75,090,957**
(2026-09-28 20:39:19 UTC): **0 PASS, 3 FAIL**. GME and USAR fail **rule 1(a)**. At any price, their pools deliver
3,157.90 GME against an Rg of 3,807 (17.1% short) and 4,184.37 USAR against 5,364 (22.0% short), so those curves
could never graduate. AMD's pool can deliver its 146.70 AMD with 3% to spare, but taking it moves the pool
+1,636 bps. This is the warning the front end must show before such a launch; nothing on chain refuses it.

4. Reject a strategy whose nonzero stop setting is no greater than its slippage allowance plus V3 pool fee plus keeper bounty. For a 0.30% V3 pool and the script's 1% slippage/0.50% bounty defaults, the stop must exceed **1.80%**. The V2 deployer enforces the effective values at quote and launch.
5. Wire keepers by strategy kind. Kind 0 calls `book()` independently when stock arrives and `execute()` for trading; its individual `takeProfit`, `stopLoss`, and `buyDip` entry points revert. Kind 1 calls `book()` and the paced `buyback()`; its `execute()` reverts `UseBuyback`. Confirm stop-first behavior, partial stop completion, stale-feed halt, closed-market behavior, permissionless caller ordering, and kind-1 cooldown/cache behavior on the release commit.
6. Keep `publicLaunch` false until the owner has reviewed one complete stock-specific fork replay, the trade router and frontend use the V2 factory/ABI, and the operator has a way to pause listings and respond to an oracle fault. The V1 launch router is incompatible with V2's pre-graduation curve.

## Raise size and opening window, decided 2026-09-28

The founder's decision: "Don't restrict the gameplay, let the users decide." Each V2 creator chooses the raise size
(`saleBps`, 1000–9000) and the opening protection window (`snipeSeconds`, 0–180 seconds) for their own launch with
`CurveDeployer.setCurveConfig`. How the choice is keyed, defaulted and committed to is in the
[bonding curve doc](./V2_BONDING_CURVE.md#raise-size-and-opening-window-the-creators-choice).

- **No on-chain limit on raise size or window beyond those validity bounds.** The owner's per-stock `setSaleBps` is
  removed, and no owner limit per stock replaces it.
- **Every listed stock is available** at every raise size.
- **The launch check is shown to creators as a warning.** The front end runs `tools/v2_launch_check.py` at the
  creator's `saleBps` and shows PASS or FAIL before the launch; nothing on chain refuses a FAIL.
- **Defaults for a creator who registers nothing:** `saleBps` 4400 and the factory's `Defaults.snipeSeconds`, 3 in
  this rehearsal. The opening rate `snipeBps` stays the owner's.

**The consequence the front end must surface.** A raise larger than the stock's V3 pool can deliver can never
graduate, and its buyers can only sell back to the curve (audit round 3 M-3). The launch check's rule 1(a) is how
the front end detects this before launch: it fails when one buyer cannot take `Rg` out of the pool at any price, and
its FAIL line names the stock and the `saleBps`. The front end must show that line to the creator in plain words
before they sign. Rule 1(b) failures should be shown too: a deliverable raise can still leave the pool outside every
treasury's deviation gate until arbitrage re-pegs it.

## Deployment parameters decided after audit round 4

Two parameters were decided on 2026-09-28 in response to [audit round 4](../audit/round-4-2026-09-27/ISSUES.md). Neither
needs code; both belong in the listing and launch procedure. A third constraint, on V1, follows from the V2 opening-tax
fix of the same day.

**Trade tax: the creator chooses it within the factory's existing bounds.** The rehearsal's candidate bounds are
1%–15%, and the founder does not want a tighter cap. The front end must show a creator what the tax is likely to
cost them before they choose. The best evidence on this chain is the pons.family natural experiment in
[PONS_TAX_ELASTICITY.md](./research/PONS_TAX_ELASTICITY.md): up to 5% total tax, graduation rates and the same
creator's volume are flat; above 5%, graduation falls from about 1% of launches to 0.18% and the same creator gets
about half the volume. Pons's total is its 1% base fee plus the creator's tax; the comparable Hedgefun number is the
whole `taxBps`.

**0.05%-fee listings: `sellChunkUsdg` is sized to the pool (M4-1).** The rebalance engine's actions are predictable
and unpaid, and on a 0.05% V3 pool a sandwich inside the deviation gate pays once an action exceeds about 10% of the
pool's USDG depth per 1% move. `maxTradeUsdg` can never exceed the listing's `sellChunkUsdg`, so the chunk is the
bound. On every 0.05%-fee listing:

1. At listing time, run `tools/v2_launch_check.py`. Its `max chunk` column is 10% of the pool's USDG depth per 1%
   move, in the thinner direction. Set `sellChunkUsdg` to at most that with
   `setListingGates(stock, maxDeviationBps, maxSlippageBps, sellChunkUsdg)`. This is a Safe transaction.
2. Run it again before any V2 launch on that stock. Rule 2 fails when the configured chunk exceeds the cap; lower the
   chunk first if the depth has fallen. A launch freezes the chunk in its treasury.
3. Record the block, the measured depth and the chunk in the Safe proposal. The check prints all three.

For GME the rule gave a chunk of about 1,300 USDG on 2026-09-27, from roughly 13,500 USDG of depth per 1%. The rule
costs no contract bytes.

**V1's opening window stays at 3 seconds.** The V2 curve now decays its opening buy rate to the flat tax over the whole
window ([bonding curve](./V2_BONDING_CURVE.md#frozen-terms-and-curve-math)). The deployed V1 hook keeps the old formula,
`max(snipeBps * (snipeSeconds - elapsed) / snipeSeconds, taxBps)`, and is immutable, so the fix cannot reach it. That
formula reaches the flat tax at `snipeSeconds * (1 - taxBps / snipeBps)`, ending the window early by
`snipeSeconds * taxBps / snipeBps`. At 3 seconds it does not bite: the window's last second reads `snipeBps / 3`, 33% at
the shipped 99%, above the 15% `MAX_TAX_BPS` that bounds every launch's tax. Keep V1's `snipeSeconds` at 3. The V1
factory's `setDefaults` does not refuse a larger value, and raising it would end every later V1 launch's window early
(by about 9 seconds of a 60-second window at a 15% tax). For the same reason keep V1's `snipeBps` well above 4,500
(3 x 15%): near that value the last second already reads the flat tax.

This script is a readiness check, not a production deployment command. The production transaction plan needs its own review of immutable recipients, role addresses, factory parameters, hook salt, all expected contract addresses, the exact kind-1 chunk code hashes, and the separate Safe `registerKind` transaction before anyone signs it.

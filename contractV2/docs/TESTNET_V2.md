# Hedgefun V2 on the Robinhood Chain testnet

The whole V2 launchpad on the **public** Robinhood Chain testnet, so team members can use the front end with their own
wallets and nothing of value. The testnet has Uniswap V4 but no Uniswap V3, no USDG we can mint, no Chainlink equity
feeds, and faucet stock tokens without `oraclePaused()`. `script/DeployV2Testnet.s.sol` deploys test doubles for those
gaps and then the same V2 contracts as mainnet, from the same `src/`. Nothing here is a mainnet rehearsal: the owner is
an EOA, the prices are set by hand, and the pools are far deeper than mainnet's.

| | |
|---|---|
| chain id | **46630** (`0xb626`) |
| RPC | `https://rpc.testnet.chain.robinhood.com` |
| explorer | `https://explorer.testnet.chain.robinhood.com` |
| faucet | `https://faucet.testnet.chain.robinhood.com`: 0.01 ETH and 5 of each faucet stock, once per 24 hours |
| gas price | 0.01 gwei base fee (read 2026-09-29) |

## What exists on the testnet, and what is mocked

Read 2026-09-29 around block 125,954,885 with `cast` against the testnet RPC and, for comparison, the mainnet RPC.
"Code" is the runtime size in bytes.

| item | testnet address | code | call result | used as |
|---|---|---|---|---|
| Uniswap V4 PoolManager | `0x8366a39CC670B4001A1121B8F6A443A643e40951` | 24,009 | code hash `0xbd3881…95626`, **identical to mainnet's**; `owner()` slot `0x9701fb0a…1A52` | **real** |
| Uniswap V3 factory | mainnet's `0x1f7d7550…2EfA` | 0 | none. Uniswap's `sdk-core` has Robinhood addresses for 4663 only; mainnet's V3 NonfungiblePositionManager, SwapRouter02 and QuoterV2 addresses are empty here too. The explorer lists many `UniswapV3Factory` contracts deployed by third parties, none official | **mocked**: our own factory, see below |
| USDG | mainnet's `0x5fc5360D…1d168` | 0 | none | **mocked**: tUSDG |
| USDG, issuer's testnet copy (probable) | `0x7E955252E15c84f5768B83c41a71F9eba181802F` | 170 | "Global Dollar" / `USDG`, 6 decimals, supply 56,201,240; proxy bytecode hash identical to mainnet USDG's | not used: we cannot mint it, and the pools need tens of millions |
| faucet stocks | TSLA `0xC9f9c86933092BbbfFF3CCb4b105A4A94bf3Bd4E`, AMZN `0x5884aD2f920c162CFBbACc88C9C51AA75eC09E02`, AMD `0x71178BAc73cBeb415514eB542a8995b82669778d`, PLTR `0x1FBE1a0e43594b3455993B5dE5Fd0A7A266298d0`, NFLX `0x3b8262A63d25f0477c4DDE23F83cfe22Cb768C93` | 283 each | `BeaconProxy` to a verified `Stock` implementation `0xBd14156E05c6AF28ad39aA53a2AB8eB9CDf657DA`; `decimals()` 18, `uiMultiplier()` 1e18; **`oraclePaused()` reverts** (not in the implementation's ABI); `mint` is access-controlled | **not usable**: `PriceOracle.tryPrice` calls `oraclePaused()` inside a `try` and fails closed when it reverts, so a listing on these would never have a price. Mocked as test stocks |
| Chainlink equity feeds | mainnet's `RHNVDA / USD` `0x379EC4f7C378F34a1B47E4F3cbeBCbAC3E8E9F15` and USDG/USD `0x61B7e5650328764B076A108EFF5fa7282a1B9aD2` | 0 | none. Chainlink's docs config lists only "Robinhood Chain Mainnet" for Data Feeds, and no testnet feed directory exists. Data Streams did reach this testnet, but equity streams are an entitlement (see the repository notes) | **mocked**: operator-set feeds |
| WETH | `0x7943e237c7F95DA44E0301572D358911207852Fa` | 2,202 | `name()`/`symbol()` "WETH", 18 decimals, supply 2,158.17; listed as the testnet's L2 WETH in Robinhood's protocol-contracts page | **real** (native router) |
| CREATE2 deployer | `0x4e59b44847b379578588920cA78FbF26c0B4956C` | 69 | code hash identical to mainnet's | **real** (hook salt) |
| Permit2 | `0x000000000022D473030F116dDEE9F6B43aC78BA3` | 9,152 | `DOMAIN_SEPARATOR()` `0x385ef69f…173f`; the code hash differs from mainnet's only because Permit2 caches the chain id | real, unused by V2 |
| V4 periphery | PositionManager `0x58dAEC3116aAe6D93017BAaea7749052e8A04fA7`, StateView `0xf3334192…673b`, V4Quoter `0x8dc178ef…8f94`, UniversalRouter `0x88767899…0904`, Multicall3 `0xcA11bde0…CA11` | 23,877 / 3,531 / 6,118 / 24,546 / 3,808 | StateView, V4Quoter and Multicall3 hashes identical to mainnet's; PositionManager and UniversalRouter differ by their immutables (WETH) | available to the front end |

The test doubles, all under [`script/testnet/`](../script/testnet/), none in `src/`:

- **UniswapV3Factory**, deployed from the vendored v1.0.0 creation bytecode `lib/v4-core/test/bin/v3Factory.bytecode`.
  Deployed locally, its runtime code is **byte-identical to Robinhood Chain mainnet's factory** except for its own
  address (the `NoDelegateCall` immutable), so its pools are the mainnet pool code. One `<stock>/tUSDG` pool per stock,
  at the mainnet listing's fee tier.
- **tUSDG** (`TestUsdg`): "Test USDG (testnet, no value)", 6 decimals like USDG. The owner and its operators mint;
  anyone may call `drip()` for 10,000 tUSDG once a day.
- **Test stocks** (`TestStock`): NVDA, TSLA, GME and AAPL, 18 decimals, with `oraclePaused()` and `uiMultiplier()`
  (1e18). `delegate` is deliberately absent, as on the live token. `drip()` gives 20 NVDA, 15 TSLA, 200 GME or 15 AAPL
  once a day. They share tickers with the faucet's stocks but are different tokens: only these are listed.
- **Feeds** (`TestFeed`): 8-decimal AggregatorV3s the operator sets, one per stock and one for tUSDG at 1.00. With
  `alwaysFresh` on (the default) `updatedAt` reads as the current block, so nothing goes stale without a keeper.
  Turn it off to test a stale feed.
- **TradingCalendar**: a **fresh deployment of the production contract**, owned by the operator. It is the same code
  the mainnet oracles use, so the front end sees the real 24/5 schedule, and the engine's daily cap rolls on the same
  trading date. An always-open stub would hide weekend and holiday behaviour that the front end has to show on mainnet.
  The operator can open a weekend date for a test session with `setOverride(day, 2)`. Launching and curve trading are
  not gated by the oracle; only treasury strategy execution waits for an open market.
- **PriceOracles**: the production `PriceOracle`, 26-hour ages as on mainnet.
- **TestnetMarket**: owns one V3 position per pool, spanning about half to double the opening price, with 30 million
  tUSDG on its USDG side. That is about **510,000 tUSDG per 1% move**. It mints what a pool asks of it, and it moves a
  pool and its feed together (`setPrice`), so pool and oracle agree the way arbitrage keeps them agreeing on mainnet.

| stock | opening price | fee tier | `openPriceE18` (mainnet plan) | drip |
|---|---|---|---|---|
| NVDA | 228 tUSDG | 0.05% | 44,200,000,000 | 20 |
| TSLA | 358 tUSDG | 0.30% | 26,500,000,000 | 15 |
| GME | 24 tUSDG | 0.05% | 423,000,000,000 | 200 |
| AAPL | 339 tUSDG | 0.05% | 29,800,000,000 | 15 |

Prices are the mainnet oracles' `lastPriceAt()` on 2026-09-29, rounded.

## What the deployment script does

`script/DeployV2Testnet.s.sol` reverts unless `block.chainid == 46630`, and unless the broadcaster is `OPERATOR`
from the environment. It never holds a key. In order, all from the operator's address:

1. tUSDG, its feed, a `TradingCalendar`, the V3 factory and the `TestnetMarket`.
2. Per stock: the test stock, its feed and `PriceOracle`; its V3 pool, created and initialised at the opening price
   and grown to a 720-slot observation ring (`PoolTrader` requires 660); then the market's liquidity.
3. V2, as [`RehearseV2Launchpad`](../script/RehearseV2Launchpad.s.sol): treasury, token and curve deployers, the
   hook at a CREATE2 salt it mines, `HedgeFunV2Factory`, `HedgeFunV2TradeRouter`, and `HedgeFunV2NativeRouter` on the
   testnet WETH.
4. Registrations, as `test/V2StrategyEngine.t.sol`: strategy kind 1 (buyback), `V2RebalancePolicy` (150,000 gas,
   placeholder manifest hashes marked as testnet), and kind 2 (the spot engine, buy and sell).
5. Per stock: `list` at the mainnet plan's `openPriceE18`, `setListingGates(stock, 50, 100, 2,000 USDG)`, and
   `setLpBps(stock, 5000)`.
6. `setPublicLaunch(true)`.

The `Defaults` are the rehearsal's, with the decisions of 2026-09-28 in
[V2_DEPLOYMENT_REHEARSAL.md](./V2_DEPLOYMENT_REHEARSAL.md#raise-size-and-opening-window-decided-2026-09-28): 1 billion
supply, 0.30% V4 LP fee, creator tax 1–15%, protocol 20%, creator share up to 30%, no sell spike, opening buy rate 99%
decaying over the creator's window (default 3 seconds), creator-chosen raise size (default `saleBps` 4400), 25 tUSDG
launch fee. It reads the simulated deployment back and writes an **unverified candidate**. A broadcast request writes
`deploy/testnet-v2.candidate.json`; a dry run writes `deploy/testnet-v2.dryrun.json`. Both are git-ignored and
always say `broadcast: false`: Forge writes files during simulation, before it submits transactions. Only
`tools/verify_testnet_deployment.py` can promote a candidate to `deploy/testnet-v2.json` after checking actual
successful receipts, canonical block membership, runtime code hashes, roles, listings, defaults and policy bindings
at one pinned live block. The verifier needs Python 3.9+ and Foundry `cast`; it never signs or broadcasts.

`script/SeedTestnet.s.sol` tops team wallets up to 100,000 tUSDG and $25,000 of each test stock at the feed price
(minting only the shortfall, so a rerun is harmless), and pokes each pool. The poke matters: a pool's grown
observation ring goes live only on the first observation written in a later second, and V3 writes one only when a
swap changes the tick. The poke is a one-tick round trip that ends at the exact starting price.

## Dry run and cost

Commit `cc95fb6`, 2026-09-29, **without `--broadcast`**, against the live testnet at block 125,969,440: 72
transactions, readback passed. Forge estimated **198,170,409 gas** and **0.00396 ETH** at its 0.02 gwei estimate,
which is twice the base fee. (The same run at `711abd7`, block 125,961,262, estimated 198,243,519.) Executed on a local fork of the testnet, the same 72 transactions used **145,764,709
gas**. The largest single transaction is a pool's `increaseObservationCardinalityNext(720)`, at 22.1 million gas with
Forge's 1.3 margin. Robinhood Chain adds an L1 data charge: the RPC's `eth_estimateGas` for the largest creation
(`V2TreasuryDeployer`, 38.7 KB) was 9.33 million against 8.39 million locally, about 10%. The deployment should cost
about 0.002 ETH at the 0.01 gwei base fee, and one faucet claim (0.01 ETH) covers the Forge estimate. Seeding two
wallets and poking four pools estimated 1.7 million gas.

On that fork, after the seed and 11 minutes, `python3 tools/v2_launch_check.py --testnet --rpc http://127.0.0.1:8547`
passed every stock at the default `saleBps` 4400. Each raise was about 7,500–8,000 tUSDG and moved its pool +1.5 to
+1.6 bps, and depth was about 510,000 tUSDG per 1% each way. At a creator's 9000, raises of about 86,000–91,000 tUSDG
moved each pool about +17 to +18 bps. That also passed. `test/DeployV2Testnet.t.sol` runs the script offline and
takes a launch through the trade router with tUSDG, from the V3 pool to the curve, graduation into V4, and a sale back.

## Operator steps

The operator broadcasts; nothing in this repository signs. Use a key that holds nothing but testnet ETH.

1. **Test ETH.** Claim at the faucet for the operator address, 0.01 ETH per day.
2. **Import the key** into Foundry's encrypted keystore, never a plain-text file or an environment variable:

   ```sh
   cast wallet import hedgefun-testnet --interactive      # paste the testnet-only private key
   cast wallet address --account hedgefun-testnet          # prints the operator address
   ```

3. **Dry run**, from the repository root at the commit you mean to deploy:

   ```sh
   export OPERATOR=0x...                                    # the address printed above
   export RPC=https://rpc.testnet.chain.robinhood.com
   forge build
   forge script script/DeployV2Testnet.s.sol:DeployV2Testnet --rpc-url $RPC --sender $OPERATOR -vv
   ```

   It must end `public launch true; readback passed` and print the estimated ETH. If the mined hook address is
   occupied, set `HOOK_SALT_START` beyond the printed salt. `PROTOCOL=0x...` sends protocol fees elsewhere than the
   operator.

4. **Deploy:**

   ```sh
   GIT_COMMIT=$(git rev-parse HEAD) forge script script/DeployV2Testnet.s.sol:DeployV2Testnet \
     --rpc-url $RPC --account hedgefun-testnet --sender $OPERATOR --broadcast --slow -vv
   ```

   `--slow` sends each transaction after the previous one lands. If it stops part way, rerun the same command with
   `--resume`. The candidate file is not proof that any transaction landed. After all transactions succeed,
   verify and promote it (the default requires two block confirmations):

   ```sh
   python3 tools/verify_testnet_deployment.py --rpc "$RPC" \
     --broadcast-log broadcast/DeployV2Testnet.s.sol/46630/run-latest.json
   ```

   Only commit the promoted `deploy/testnet-v2.json`. A missing, failed or noncanonical receipt, missing code,
   wrong roles or mismatched configuration prevents promotion and leaves any existing verified book untouched.
   Seeding rejects unverified books and checks their chain and live factory/listing bindings.

   For a disposable local fork, pass its local RPC to the verifier and `--out /tmp/testnet-v2-local.json`;
   this verifies **that local fork only**, and is not a public testnet launch. Never publish a local rehearsal book.

5. **Seed**, at least one second later (the poke needs a new second). Wait **10 minutes** after the deployment before
   the first launch: a pool's 600-second mean does not exist until the pool is that old, and every treasury's health
   check reads it.

   ```sh
   TEAM=0xAAA...,0xBBB... forge script script/SeedTestnet.s.sol:SeedTestnet \
     --rpc-url $RPC --account hedgefun-testnet --sender $OPERATOR --broadcast --slow
   # one more wallet later:
   forge script script/SeedTestnet.s.sol:SeedTestnet --sig "topUp(address)" 0xCCC... \
     --rpc-url $RPC --account hedgefun-testnet --sender $OPERATOR --broadcast
   ```

   Each team member claims their own test ETH at the faucet.

6. **Check** the listings, exactly as before a mainnet launch:

   ```sh
   python3 tools/v2_launch_check.py --testnet                  # every stock, default saleBps
   python3 tools/v2_launch_check.py --testnet --sale-bps 9000  # a creator's largest raise
   ```

### Operating it

Read the addresses from `deploy/testnet-v2.json` (`jq -r .market deploy/testnet-v2.json`, `.stocks.NVDA.pool`, …).
Each command below adds `--rpc-url $RPC --account hedgefun-testnet`.

| to | command |
|---|---|
| move a stock (pool and feed together) | `cast send $MARKET "setPrice(address,uint256)" $POOL $(cast to-wei 240)` |
| realign the feed after trades moved the pool | `cast send $MARKET "syncFeed(address)" $POOL` |
| test an oracle deviation (feed only) | `cast send $FEED "set(int256)" 24000000000` (8 decimals) |
| test a stale feed | `cast send $FEED "setAlwaysFresh(bool)" false` |
| test a corporate action | `cast send $STOCK "setOraclePaused(bool)" true` |
| open a weekend date | `cast send $CALENDAR "setOverride(uint256,uint8)" $(cast call $CALENDAR "tradingDate(uint256)(uint256)" $(date +%s)) 2` |
| add depth | `cast send $MARKET "provide(address,uint128)" $POOL <liquidity>` |

`setPrice` refuses a price outside the position's range, roughly half to double the opening price.

## Add the network to MetaMask

The faucet's **Add testnet** button (`https://faucet.testnet.chain.robinhood.com/add-chain`) does it. By hand:
*Settings → Networks → Add a network manually*:

| field | value |
|---|---|
| Network name | Robinhood Chain Testnet |
| New RPC URL | `https://rpc.testnet.chain.robinhood.com` |
| Chain ID | `46630` |
| Currency symbol | `ETH` |
| Block explorer URL | `https://explorer.testnet.chain.robinhood.com` |

Then *Import tokens* with the `usdg` address and each `stocks.<T>.token` from the address book.

## The address book

`deploy/testnet-v2.json`, promoted by the receipt verifier. The candidate additionally records `codeHashes`,
per-stock runtime hashes and `expectedDefaults`; the verifier preserves these and adds a `verification` object
(schema 1, chain id, pinned block number/hash, transaction hashes, verification time). `block` remains the simulation
start block for indexing; `verification.blockNumber` is the actual readback block. Big integers are decimal strings, because JSON numbers past
2^53 do not survive a JavaScript parser. Keys:

```json
{
  "chainId": 46630, "broadcast": true, "block": 0, "commit": "<git sha>",
  "operator": "0x…", "owner": "0x…", "protocol": "0x…",
  "poolManager": "0x8366a39CC670B4001A1121B8F6A443A643e40951",
  "weth": "0x7943e237c7F95DA44E0301572D358911207852Fa",
  "usdg": "0x… tUSDG", "usdgFeed": "0x…", "calendar": "0x…", "v3Factory": "0x…", "market": "0x…",
  "factory": "0x… HedgeFunV2Factory", "tradeRouter": "0x…", "nativeRouter": "0x…",
  "hook": "0x…2844", "hookSalt": "0x…",
  "treasuryDeployer": "0x…", "tokenDeployer": "0x…", "curveDeployer": "0x…",
  "rebalancePolicy": "0x…", "rebalancePolicyKey": "0x…", "engineKind": 2,
  "stocks": {
    "NVDA": { "token": "0x…", "feed": "0x…", "oracle": "0x…", "pool": "0x…", "fee": 500, "decimals": 18,
              "priceE18": "228000000000000000000", "openPriceE18": "44200000000",
              "tickLower": 215060, "tickUpper": 228980, "liquidity": "…" },
    "TSLA": { "…": "…" }, "GME": { "…": "…" }, "AAPL": { "…": "…" }
  }
}
```

The dry run mined hook salt `0x…644a`, giving hook `0xdF96e3Ec55079a05999a8B358aA0C74Bf4422844`. That address
depends only on the CREATE2 deployer, the salt and the hook's creation code with the PoolManager, so it holds for any
operator at this commit unless someone takes it first. Every other address depends on the operator's nonce. Read them
from the file, never from this page.

## Pointing the front end at the testnet

The site (`hegefun_mainsite`) is wired to chain 4663 and to V1. This section describes the change; the site repository
is not edited from here.

- **`src/lib/contracts/chain.ts`.** Define a second chain with `defineChain`: id 46630, name "Robinhood Chain
  Testnet", RPC `https://rpc.testnet.chain.robinhood.com`, explorer `https://explorer.testnet.chain.robinhood.com`.
  Pick one at build time, for example `VITE_CHAIN=testnet`, and export it as the chain everything imports; today
  that is `robinhood`. Behind the same switch, take the addresses from `deploy/testnet-v2.json`: `USDG` becomes tUSDG,
  `WETH` becomes `0x7943…52Fa`, `POOL_MANAGER` stays the same, `listedStocks` becomes the four test stocks, and the
  deployment's factory, hook, trade router and `startBlock` come from the book. `officialHistory` uses the testnet
  RPC.
- **Chain id 4663 is also hardcoded outside `chain.ts`.** It appears in `src/app/shell.tsx` (the wrong-network
  check and two footer labels), in `src/lib/wallet-runtime.tsx` and `src/lib/localized-errors.ts` ("Select Robinhood
  Chain (4663)"), and in `indexer/ponder.config.ts`. `src/lib/wallet-config.ts` passes `chains: [robinhood]`, so it
  follows `chain.ts`.
- **RPC proxy and CSP.** `functions/api/rpc.ts` and `vite.config.ts` forward to the mainnet publicnode. Testnet
  reads must go to the testnet RPC, and `public/_headers` `connect-src` must allow it.
- **Contracts the testnet does not have.** The site's `SWAP_ROUTER` (SwapRouter02) and V1 `LAUNCH_ROUTER` and
  `TRADE_ROUTER` do not exist on 46630. Hide the zap and direct-swap paths, or route them through
  `HedgeFunV2TradeRouter`, which takes a V3 path of the testnet pools. The native router is deployed, but no
  WETH/tUSDG pool exists, so an ETH-paid buy has no route. Test ETH is scarce anyway at 0.01 per day.
- **V2 itself.** The site speaks V1: `LIVE_FACTORY` is the V1 factory, and launch goes through the V1 launch router,
  which cannot launch on V2's curve. Testing V2 from the front end needs the V2 ABIs: the factory's `predict` and
  `launch` with the curve terms, `CurveDeployer.setCurveConfig` for the creator's raise and window,
  `V2TreasuryDeployer.setEngineConfig` for kind 2, the curve's `buy`/`sell`, and `HedgeFunV2TradeRouter`. That work
  is the same for mainnet V2; the testnet only changes the chain and the addresses.

## How the testnet differs from mainnet

- **Owner and protocol are the operator's EOA**, not a Safe with two or more signatures. Registrations, listings and
  public launch happen in the deployment itself. On mainnet each is a separate Safe transaction, and
  `RehearseV2Launchpad` refuses a non-Safe owner.
- **tUSDG and the test stocks are mintable by the operator** and dripped to anyone. There are no transfer
  restrictions or corporate actions, except ones the operator simulates with `setOraclePaused`/`setUiMultiplier`.
- **Prices move only when the operator moves them.** No arbitrageur re-pegs a pool after a trade, so team trades
  drift a pool from its feed until `syncFeed` or `setPrice`. The feeds never go stale unless told to.
- **The pools are much deeper than mainnet's**: about 510,000 tUSDG per 1%, against 21,000 for GME and 460,000 for
  NVDA on mainnet (2026-09-28). A testnet launch-check PASS says nothing about a mainnet listing. It
  shows that the flow works, not that the economics do.
- **Our own V3 factory**, byte-identical code at a different address. The V4 PoolManager is the same code at the same
  address, so the hook address mined here is the one the same salt gives on mainnet.
- **Placeholder policy manifests.** `V2RebalancePolicy` is registered with manifest hashes labelled as testnet
  placeholders, not audit records.
- **No indexer, no Uniswap routing, no SwapRouter02**, and 0.01 test ETH per address per day.
- Everything else, the V2 contracts, `PriceOracle`, `TradingCalendar`, `Defaults`, gates, `lpBps` and the hook, is
  the code and configuration mainnet would run.

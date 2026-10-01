# Append four synthetic stocks to the existing V2 testnet

`script/AddV2TestnetStocks.s.sol` extends the existing opening-tax-whitelist deployment on chain **46630**.
Its production targets are fixed in source: factory `0x3E95976E2425e63cb2A8d48BBce8976F55627019`, market
`0xc1AF2f52980F8A7AA4E90A8E30D5c3FaF0375f21`, and operator `0x75Cee941B0eF3A83feA0397BbF903C12c1D7e96D`.
The script verifies the existing factory/market/tUSDG/feed/calendar/V3Factory/treasury-deployer bindings before
creating anything. The starting market inventory must contain exactly the existing four pools. A fresh rerun
after any pool is added is refused; an interrupted broadcast must resume the original Forge transaction list.

## Synthetic assets and launch sizes

These are declared test prices, **not current or historical market quotes**. New `TestStock` and `TestFeed`
contracts support the same faucet and operator controls as the original test assets.

| Symbol | Synthetic price (tUSDG/stock) | `openPriceE18` | V3 fee | Daily drip (whole stocks) |
|---|---:|---:|---:|---:|
| MSFT | 500 | 20,000,000,000 | 0.30% | 20 |
| AMZN | 200 | 50,000,000,000 | 0.30% | 50 |
| GOOGL | 200 | 50,000,000,000 | 0.05% | 50 |
| META | 600 | 16,666,666,667 | 0.30% | 20 |

For the fixed one-billion token supply, these curve opening prices produce an initial FDV of about 10,000
tUSDG. At `saleBps=4400`, the graduation stock target is about **7,857 tUSDG** at the synthetic price,
before stock-pool execution costs. A market-stock price is never used directly as the curve opening price.

Each new stock uses the existing tUSDG feed and calendar, production `PriceOracle` with 26-hour ages,
the existing V3 factory, and the existing `TestnetMarket`. A new pool receives a position around half to double
its opening price, with **30 million tUSDG** on its tUSDG side and observation capacity **720**.
Each listing receives deviation/slippage gates **50/100 bps**, sale chunk **2,000 tUSDG**, and **50% LP** allocation.
No existing stock listing, protocol defaults, factory, router, quote asset, or calendar is reconfigured.

## Prepare, broadcast and verify

The operator uses the existing encrypted testnet-only keystore. No script accepts, reads or stores its password
or private key. A dry run needs only the public sender address:

```sh
forge test --match-contract AddV2TestnetStocksTest
GIT_COMMIT=$(git rev-parse HEAD) forge script script/AddV2TestnetStocks.s.sol:AddV2TestnetStocks \
  --rpc-url https://rpc.testnet.chain.robinhood.com \
  --sender 0x75Cee941B0eF3A83feA0397BbF903C12c1D7e96D
```

Review the dry-run bindings, transactions and gas estimate. Preserve at least **0.002 test ETH** in the operator
wallet after the estimated cost. The 720-slot pool allocation is the largest gas item. An authorized broadcast
adds the encrypted `--account` selection and `--broadcast --slow`; do not begin another deployment after a
partial broadcast, use `--resume` on its saved transaction list.

There are exactly **52 deployment/configuration transactions**: 13 per stock, in MSFT, AMZN, GOOGL, META order.
Per stock: token creation, feed creation, two market-operator grants, oracle creation, canonical pool creation,
market line registration, pool initialization, observation-capacity allocation, liquidity provision, factory
listing, listing gates, and LP allocation. Liquidity provision mints only test assets through the existing
market callbacks. There are no separate team-wallet mint transactions.

The script writes `deploy/testnet-v2-stock-extension.dryrun.json` for a dry run and
`deploy/testnet-v2-stock-extension.candidate.json` for a broadcast request. **Both always have `broadcast:false`.**
`broadcastRequested` only records whether Forge was asked to broadcast; it is not a receipt. The candidate includes
the source commit, starting block, fixed venue addresses, per-stock constructors/configuration, and runtime code
hashes. It must not be published directly or substituted for the existing verified address book.

Independent verification must check successful canonical receipts, sender/to/calldata or exact creation code,
all created addresses, pinned runtime hashes and role/listing/market/oracle/pool bindings. Preserve the original
72-transaction deployment evidence and append separate stock-extension evidence to the published book.

## Activate only the new pool observations

At least one second after the pools' last write, invoke the separate script entry point with the four NEW pool
addresses from the verified transaction list (their order does not matter):

```sh
forge script script/AddV2TestnetStocks.s.sol:AddV2TestnetStocks \
  --sig 'poke(address[4])' '[<MSFT pool>,<AMZN pool>,<GOOGL pool>,<META pool>]' \
  --rpc-url https://rpc.testnet.chain.robinhood.com \
  --sender 0x75Cee941B0eF3A83feA0397BbF903C12c1D7e96D
```

The operator's authorized broadcast adds its encrypted account and `--broadcast --slow`. This produces four
`market.poke` transactions. The entry point refuses duplicates, old pools, noncanonical pools and mismatched
listing/feed/owner bindings. A poke makes the reserved observation capacity live without changing the final price.
It does not manufacture 600 seconds of price history: wait until every pool is at least **600 seconds old** and
independently check `observe([600,0])` before declaring treasury execution ready.

`tools/v2_launch_check.py --testnet --book <verified expanded book>` supports a larger inventory. Check the new
stocks at both default `--sale-bps 4400` and maximum `--sale-bps 9000`; a testnet pass says nothing about real-market
depth. The old full-deployment verifier and `SeedTestnet` assume exactly four stocks and are not extension tools.

## Offline evidence

`test/AddV2TestnetStocks.t.sol` runs the real initial deployment and the append operation against real Uniswap pool
code in an offline test environment. It checks unchanged old listings/pool prices/defaults, all new bindings and
faucets, rejection of wrong chains/callers/broken venue bindings/repeated deployment, pool observation activation,
unverified candidate output, and launch → graduation → graduated buy/sell for all four new stocks with 1% tax and
3%/6% profit targets. The test harness overrides venue addresses only to use its offline fixture; the production
script has no environment or calldata override for its fixed deployment addresses.

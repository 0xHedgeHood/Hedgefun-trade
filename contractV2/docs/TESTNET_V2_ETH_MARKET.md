# Testnet ETH payments and ETH strategies

Status: source and local fork rehearsal only. This document is not a deployment receipt. Stage must not enable ETH until the independent native-market publisher has verified all seventeen real transactions.

## Two uses of ETH

- Stock strategy purchases: native ETH → canonical WETH/tUSDG V3 pool → existing tUSDG/stock pool → strategy token. Sales follow the reverse route and unwrap the final WETH to native ETH.
- ETH strategy: WETH is the underlying asset. Native purchases wrap automatically, and native sales unwrap automatically. Users do not need a separate WETH approval to buy.
- Selling approves the strategy token to `HedgeFunV2NativeRouter`, not the underlying stock or the direct trade router.
- The existing V1 and V2 contracts, stock listings, stock calendar and source proofs stay unchanged. This release adds an independently verified testnet venue.

## Price source

ETH strategies read the canonical WETH/tUSDG V3 pool's **600 second tick TWAP** through `TestnetCryptoOracle` version 2. The pool itself determines the price; an operator feed does not set the strategy's ETH price. The initial pool is seeded at a synthetic 3,000 tUSDG per ETH; this is not a live dollar quotation. `TestFeed` remains a test helper for deliberate market moves and is not the oracle's price source.

Execution quotes come from router simulation against the current pools. Strategy decisions use the trailing TWAP. These are different values during a market move. The oracle checks canonical token bindings and 18/6 decimals, rounds negative tick averages toward negative infinity, and fails closed when the observation history or minimum active liquidity is unavailable. It does not fall back to a feed or spot quote.

The crypto calendar opens every UTC day, including weekends. Its operator halt makes oracle/strategy execution and the frontend ETH capability unavailable; it does not globally pause direct curve trading, router trading or every inherited buyback path. ETH uses band ceiling zero so it never takes the stock market weekend-close pricing fallback.

The small testnet pool is suitable for rehearsals. A sustained manipulation of the pool over the TWAP window remains possible; this is not proof of production manipulation resistance. Production pool selection needs adequate depth. Uniswap documents the [V3 oracle and observation history](https://developers.uniswap.org/docs/protocols/v3/concepts/price-oracles).

## Deployment boundaries

Use chain 46630, the configured operator, canonical WETH `0x7943e237c7F95DA44E0301572D358911207852Fa`, and fee 500. Do not mint WETH through the synthetic stock market.

Before running file-based phases, put an exact byte copy of the separately verified base book at `deploy/testnet-v2-fees.json` or `deploy/testnet-v2-creator.json`. The existing fee book's SHA256 is `ada428eb04ac0f982fd6a002e8745b4100c6db99ce620adcf254e42ac1cbc82c`. Do not replace an existing different file or reconstruct the JSON with a formatter; raw bytes bind the supplemental proof. The keeper proof archive and frontend public book remain unchanged.

The operator must have **at least 1.001 native test ETH and 3,100 tUSDG before initialization**. One ETH is deposited into the real WETH contract. The seeder receives one WETH and 3,100 tUSDG; the position uses at most 0.9 WETH, leaving a WETH budget outside the position for bounded test swaps. The 0.001 ETH reserve is a preflight minimum, not a promise that it covers every future transaction's gas cost.

`script/TestnetV2EthMarket.s.sol` has three separate phases:

1. `initialize(basePath, sha256(rawBaseBytes))`: twelve operator transactions, including four helper deployments, canonical pool creation/initialization, ring growth, real ETH deposit, two prefunding transfers and bounded liquidity provision.
2. `pokeFile(initCandidate)`: one bounded round-trip transaction, mined in a later second than initialization.
3. `activateFile(initCandidate)`: four transactions after at least 600 seconds of real observation history. Configure gates 50/100/0, LP share 5,000 and band zero before enabling the listing in the final transaction. Public creators cannot freeze incomplete listing settings between those transactions.

The compiled source uses the existing Foundry settings. The installed Foundry release ignores `FOUNDRY_FS_PERMISSIONS`, so use `tools/native_testnet_config.py` with Python 3.11 or later to create an explicit temporary config. It changes only the file permission array: two verified base books and V3 factory bytecode are read-only, and the six native candidate/dry-run files are writable. Do not edit tracked `foundry.toml` or grant general deployment-directory write access.

```sh
task_native_config=$(python3 tools/native_testnet_config.py)
forge config --config-path "$task_native_config" --root "$(dirname "$task_native_config")" --json
```

Inspect that configuration once, then pass `--config-path "$task_native_config" --root "$(dirname "$task_native_config")"` to every native `forge script` invocation. Foundry requires the filename `foundry.toml` and resolves source paths relative to that file. The generator therefore symlinks the project folders into its isolated temporary directory; source-unit names, remappings, compiler settings and the original tracked config remain unchanged. Output/cache/broadcast folders point to the real checkout so artifacts and receipts stay there. The publication source gate still requires the tracked compiler configuration to match the reviewed core. Remove only the generated temporary directory after completing the phases; removing its symlinks must not remove their targets.

Compile the release with `forge build --ast` using that config before publication. The publisher maps every numeric compiler immutable ID to its AST declaration name and checks the full reviewed position sets. Run `NATIVE_CONFIG_SMOKE=true forge test --ast --match-contract TestnetV2EthMarketCandidateTest` with the same config once: it executes the actual serializer for all three phases, reads/writes only the allowed dry-run paths and verifies every phase has its own top-level block. It refuses to overwrite an existing dry-run file and removes its new test files afterward.

Candidate files always have `broadcast: false`. `broadcastRequested` records Foundry broadcast/resume context. Candidate output, local fork funding and local test results are not public chain proof. Preserve all three candidates and all three broadcast logs; `provide` and `poke` have different deadlines.

## Publication and Stage

The frontend's `scripts/publish-testnet-native-market.mjs` takes the base book, three phase candidates, three phase logs, protocol checkout and a new `.eth.json` output. It reconstructs twelve/one/four exact transactions from reviewed artifacts, checks ETH value, signer, CREATE nonces and canonical receipt positions, and verifies the current pool, proxy implementation, helper runtimes, owner bindings, TWAP, liquidity and listing settings. It also verifies the original eight stock listings were not replaced.

Publish a separate `/testnet-v2-fees.eth.json` or `/testnet-v2-creator.eth.json`. Never add ETH to the original eight-stock base proof or overwrite a source book/log. Missing or invalid native proof leaves the original eight stock markets usable and ETH disabled.

For local fork rehearsal, set `ETH_MARKET_FORK=true`, `ETH_MARKET_FORK_BLOCK` to a current state-available stable block and `ETH_BASE_BOOK` to the verified fee/creator book. Run `TestnetV2EthMarketForkTest`. The rehearsal explicitly uses `vm.deal`; report it as fork execution, not faucet funding or live deployment. The public RPC prunes old state, so use a fresh block for a later rehearsal.

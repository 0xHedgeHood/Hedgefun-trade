# V2 native ETH launch and first buy

Status: source and local fork rehearsals only. No factory defaults, launch-router authorization, or public-chain
deployment is changed by merging this source. The existing testnet address books remain historical records.

## Creator transaction

The creator calls `HedgeFunV2LaunchNativeRouter.launchAndBuy(q, terms, info, b, path)` once with
`msg.value = factory.getDefaults().launchFeeAmount + b.amountIn`. The wallet needs ETH for that payment and
gas. The router pays the factory's native launch fee, creates the V2 curve and token with metadata, wraps the
buy portion into WETH, routes it through canonical V3 pools to the listed stock, and buys on the new curve.
The launch token goes straight to `q.creator`. No USDG or stock balance or ERC20 approval is needed from the
creator. A failure in any step rolls back the launch and fee transfer.

Use `factory.predict(q)` to pin the launch terms. Quote the complete WETH-to-stock path and the curve buy for
`q.creator`, including the base tax and creator opening-surcharge exemption. `b.minStockReceived` protects the
V3 conversion, `b.minFinalOut` protects the final launch-token output, and `b.deadline` bounds the quote's age.
Both minima must be positive. The router fills in the actual factory-assigned strategy id and Active curve
stage, so another creator launching first cannot stale a guessed global id.

If `b.allowPartialFill` is true and the first buy reaches the curve's cap, the unused stock goes to the
creator; when the listed stock is WETH, it is unwrapped and returned as ETH. If it is false, any stock refund
reverts the entire launch. The native launch fee is fixed by the factory defaults and is separate from the
curve's stock-denominated buy tax. This change does not implement a percentage-of-first-buy launch fee.

## Operator activation

`script/ActivateV2NativeLaunch.s.sol` accepts **required** environment values `OPERATOR`, `V2_FACTORY`,
`V2_TRADE_ROUTER`, `WETH`, `WETH_USDG_POOL`, and `LAUNCH_FEE_WEI`. The operator chooses the wei fee explicitly;
the script has no price conversion or default fee. It checks ownership, V2/trade-router bindings, public
launch status and a canonical, initialized, funded WETH/USDG V3 pool before its first broadcast transaction.
It then deploys the native launch router, authorizes it with `factory.setLauncher`, and changes only
`launchFeeCurrency` and `launchFeeAmount` in future factory defaults. Its in-script readback verifies the
simulation; the companion `VerifyV2NativeLaunch` checks confirmed live state afterward.
Old terms quotes become stale by design; already launched strategies keep their frozen fee terms.

Run the activation first **without** `--broadcast`, using the intended RPC and `--sender "$OPERATOR"`.
Review the logged router address, fee and expected defaults hash. Broadcast the same inputs with `--slow`
so Foundry waits for each dependent transaction to confirm before sending the next:

```sh
forge script script/ActivateV2NativeLaunch.s.sol:ActivateV2NativeLaunch \
  --rpc-url "$RPC_URL" --sender "$OPERATOR"
forge script script/ActivateV2NativeLaunch.s.sol:ActivateV2NativeLaunch \
  --rpc-url "$RPC_URL" --sender "$OPERATOR" --account <operator-keystore> --broadcast --slow
```

Check that all three receipts succeeded: router deployment, `setLauncher`, then `setDefaults`. Obtain
`LAUNCH_ROUTER` from the confirmed deployment receipt and set `EXPECTED_DEFAULTS_HASH` to the hash logged in
the reviewed simulation. Run the read-only confirmation on a current RPC state:

```sh
forge script script/ActivateV2NativeLaunch.s.sol:VerifyV2NativeLaunch --rpc-url "$RPC_URL"
```

These are three on-chain transactions, so activation can be partial if one fails. Inspect receipts and live
factory state before retrying any missing step. The verification contract requires the complete defaults
hash, launcher permission and router bindings to match the reviewed values.

Before activation, verify the WETH/USDG pool has enough depth for the intended first-buy size and that every
offered stock has a viable USDG/stock leg. An initialized pool with nonzero liquidity is only the script's
minimum safety gate, not a production depth assessment. Exercise the intended WETH → USDG → stock route in a
fork rehearsal with realistic slippage and both small and near-graduation buys. `forge test --offline
--match-path test/V2LaunchNativeRouter.t.sol` covers the contract path with real V2 factory and curve code.

The committed fork tests exercise the new launcher against genuine canonical V3 pools and check that a failed
final-output bound rolls back the native fee, wrapped ETH and pool swaps. The mainnet replay uses an archived
block where the WETH/USDG and USDG/GME pools already exist. It deploys V2 contracts only in the local fork:

```sh
RH_FORK=1 RH_RPC=blockmachine forge test --threads 1 \
  --match-path test/V2LaunchNativeRouterMainnetFork.t.sol -vv
```

The testnet replay connects to the deployed V2 factory and trade router, publishes the missing WETH/tUSDG
market only inside the fork, runs `ActivateV2NativeLaunch`, and buys through the real WETH/tUSDG and
tUSDG/TSLA pools. Copy the byte-exact verified fee book to the Foundry read-allowlisted path if it is absent;
the test asserts its documented SHA-256 hash. Use a fresh testnet block because the public RPC prunes old state:

```sh
cp -n deploy/testnet-v2-keeper-reward-proof/fee-core-book.json deploy/testnet-v2-fees.json
FORK_BLOCK=$(cast block-number --rpc-url https://rpc.testnet.chain.robinhood.com)
V2_NATIVE_LAUNCH_FORK=true V2_NATIVE_LAUNCH_FORK_BLOCK="$FORK_BLOCK" \
ETH_MARKET_FORK=true ETH_MARKET_FORK_BLOCK="$FORK_BLOCK" ETH_BASE_BOOK=deploy/testnet-v2-fees.json \
forge test --threads 1 --mc 'TestnetV2EthMarketForkTest|V2LaunchNativeRouterForkTest' -vv
```

Both suites run without a private key or public-chain broadcast. In the testnet replay, `vm.deal` gives the
fork-only operator and creator ETH for the simulated market setup and buy. The tests also run in separate CI
fork gates; a skipped test fails those gates.

The original V2 testnet venue lacks a WETH/tUSDG pool; the [separate ETH-market rollout](./TESTNET_V2_ETH_MARKET.md)
must be verified and published before enabling the ETH launch entry point there. As of the PR's read-only
testnet RPC check, the canonical fee-500 WETH/tUSDG `getPool` returned the zero address. No activation
broadcast is part of this PR.

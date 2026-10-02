# Testnet native ETH payment bridge

Status: source and local fork rehearsal only. No live bridge pool, native-launch activation, or first buy is claimed here. A candidate file from a Foundry simulation is not a deployment receipt.

## What this adds

`TestnetV2EthBridge` seeds a canonical WETH/tUSDG Uniswap V3 **0.30% (fee 3000)** pool on Robinhood Chain testnet (46630). It exists solely to route native ETH payments through WETH → tUSDG → an existing listed stock pool. The route is used by `HedgeFunV2LaunchNativeRouter.launchAndBuy` after that router is separately activated. The bridge does not list WETH as a strategy underlying, deploy an ETH oracle, or supply a 600-second TWAP. The **0.05% (fee 500)** WETH/tUSDG slot remains empty for the separate full ETH strategy-market rollout.

The initial price is a synthetic 3,000 tUSDG per ETH, not a live ETH/USD quotation. The operator wraps **0.0055 real test ETH** in the canonical WETH contract and transfers it with **20 tUSDG** to a dedicated liquidity helper. The V3 position uses at most **0.005 WETH** and roughly 14.8 tUSDG, for about 30 tUSDG of initial pool value. The operator must have at least **0.0105 native test ETH** at preflight: 0.0055 for wrapping plus a 0.005 reserve for gas and future operator actions. That reserve is a preflight floor, not a gas guarantee.

The helper owns the position and exposes owner-only `decrease`, `collect`, and `withdrawUnused`. Those return position principal, earned fees, and unused prefunding to the current owner. WETH can then be unwrapped by the owner. The recovered token mix may differ after trading; the helper cannot mint WETH or tUSDG and has no price-setting function.

## Size and route limits

Use **0.00001 ETH** for the first test buy. Keep a demo buy at or below **0.00004 ETH** while this pool has its initial depth. At this depth, 0.00004 ETH alone shifts the bridge pool's spot price by roughly 40 basis points; the bridge's 30-basis-point fee and price impact together consume about 50 basis points of execution quality before the stock pool fee and its impact. These are operational and frontend size limits: the public V3 pool and router do not enforce them on chain. Requote immediately before sending and set both `minStockReceived` and `minFinalOut` from that quote.

Native launch activation sets a **fixed** factory launch fee. A fixed fee of 0.0000001 ETH equals 1% of a 0.00001 ETH nominal buy only at that size; the deployed factory does not implement a dynamic percentage fee. The fork test uses that fixed value and does not establish a general 1% fee rule.

## Deployment and evidence boundary

`script/TestnetV2EthBridge.s.sol` binds the verified V2 base book, operator, tokens, and V3 factory; refuses any existing fee-3000 bridge pool or fee-500 ETH strategy pool; and checks that WETH is unlisted before and after seeding. Its seven planned operator transactions are: create pool, initialize it, deploy recoverable liquidity helper, deposit real ETH into WETH, transfer WETH, transfer tUSDG, and provide liquidity. It writes an unverified `deploy/testnet-v2-native-bridge.candidate.json` only in broadcast context, or `.dryrun.json` in simulation. There is no private key or signer configured by this document.

Those seven operations are separate transactions when broadcast. If one fails, earlier transactions may already be on chain. Inspect receipts and current pool/helper balances before resuming or recovering funds; rerunning `initialize` blindly will fail once the pool exists. If only the pool exists, it may still need initialization and liquidity. If the helper holds prefunding but has no position, its owner can call `withdrawUnused`. If liquidity was provided, its owner can call `decrease`, `collect`, and `withdrawUnused`, then unwrap recovered WETH. Check the helper address and owner against receipts before recovery. A candidate file is written after the script's simulated final readback; it may exist even if the later broadcast fails.

`verifyFile(candidatePath)` performs read-only current-state checks: exact base-book bytes, canonical pool and fee, vacant fee-500 slot, WETH unlisted, helper code hash and owner, position liquidity, both pool balances, and the originally required token amounts. Run it against the confirmed chain without `--broadcast` after matching each transaction and receipt to the candidate. State readback cannot establish which transactions happened, source provenance of the helper code hash, or that a candidate was broadcast. A separate receipt/source publisher would be needed before enabling a public frontend from this bridge proof.

For a no-broadcast local fork rehearsal, copy the exact verified fee base book to the allowlisted `deploy/testnet-v2-fees.json` path without changing its bytes; its SHA256 is `ada428eb04ac0f982fd6a002e8745b4100c6db99ce620adcf254e42ac1cbc82c`. Generate an isolated permission config with `python3.12 tools/native_testnet_config.py`. The opt-in `TestnetV2EthBridgeForkTest` pins a recent testnet block and simulates bridge seeding from the operator's **actual** chain balance, separate native-launch activation, a two-hop TSLA `launchAndBuy` from a wallet holding only ETH, and owner recovery. It does not broadcast. The local `TestnetV2EthBridgeLiquidityTest` uses a real V3 factory and verifies owner-only recovery. Remove only the temporary exact-copy book and generated dry-run candidate after the rehearsal.

The full ETH strategy market described in [TESTNET_V2_ETH_MARKET.md](./TESTNET_V2_ETH_MARKET.md) is independent. This fee-3000 payment bridge is not its fee-500 TWAP venue and does not satisfy its oracle, observation-history, or WETH-listing activation requirements.

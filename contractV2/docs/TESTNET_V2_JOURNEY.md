# Whitelist V2 Robinhood testnet journey

This run uses chain 46630 and the isolated `deploy/testnet-v2-whitelist.json` address book. It does not alter the earlier `deploy/testnet-v2.json` deployment. The feature source is commit `67adedfb897c20d8814295597fb8ee58162f0d86`, which has the same `src/` contract code as the reviewed whitelist PR #102 commit `3244fbf`. The only change in the deployment commit is the versioned output book and Foundry file permission. The old deployment and its strategy #0 remain separate.

## Addresses and page routes

| Item | Address |
| --- | --- |
| Factory | `0x3E95976E2425e63cb2A8d48BBce8976F55627019` |
| Curve deployer | `0xAE8028aE15A012e6C9875B4E7DCe74407E8a16cf` |
| Trade router | `0xf86Ee10C49d89a47Bd0E8Ec5c390631F7b454B65` |
| Native router | `0xC8676bFc1415D2674AC713AAD508295dEce1Abfe` |
| tUSDG | `0x539574139BB4Ac74Fd2183ba0E38ed777E30B58d` |
| GME stock | `0xDD301669340232F283b1e91a7c4571591d86bd85` |
| New strategy #0 | `0x708B9EcBbA100B0dbf9CB48c28412e8B0c9661Db` |
| New curve | `0x02D953A261306F393A07f03af1eb060b889DFF0b` |

The matching frontend routes are `/testnet/whitelist/launch` and `/testnet/whitelist/trade/0`. The old `/testnet/trade/0` stays bound to the old factory.

## What was run

The new pool passed `tools/v2_launch_check.py --testnet --book deploy/testnet-v2-whitelist.json --stock GME --sale-bps 4400`: 1 PASS, 0 FAIL. The launch fixed one additional exempt recipient, `0xdA1AEE7018a3925AA06dEEb8631Fca09E1067614`; the creator is automatically exempt. The curve sale is 44%, base buy tax is 300 bps, and opening tax fades over 180 seconds. Each trade used tUSDG → GME V3 → curve/V4, or the reverse route on sell.

Eight phases were broadcast and verified against canonical RPC transactions and receipts:

1. Launch, including `setCurveConfig`, `setOpeningTaxExemptions`, approval, and factory launch.
2. Ordinary operator buy during the opening window. The curve burned 8,726 bps of gross tokens at execution.
3. Whitelisted recipient buy during the same opening window. The curve burned 299 bps of gross tokens after integer truncation, corresponding to the configured 300 bps base tax.
4. Creator buy during the window. The same base tax applied.
5. Curve sell.
6. A curve buy crossed the cap and atomically graduated the strategy to V4.
7. V4 buy.
8. V4 sell.

The live launch-block mapping readback was `operator=false`, `whitelisted=true`, `creator=true`, `router=false`. This confirms that routing does not accidentally exempt every buy. The final curve status was 2 (graduated). The report `deploy/testnet-v2-whitelist.journey.json` contains the 18 transaction hashes, block timestamps, recipients, emitted amounts, gas cost, and stage result. Raw public broadcast receipts live under `broadcast/TestnetV2Journey.s.sol/46630/` and can be rechecked with:

```sh
python3 tools/report_testnet_v2_whitelist_journey.py
```

The deployment itself was checked independently against 72 canonical receipts, code at every address, contract roles, and book data by `tools/verify_testnet_whitelist.py`. Its output is `deploy/testnet-v2-whitelist.verification.json`.

## Rehearsal and signing

The journey script `script/TestnetV2Journey.s.sol` pins this deployment's addresses and refuses another chain or sender. It holds no key. A phase runs as a read-only simulation unless a user supplies a testnet-only encrypted keystore with `--broadcast`. The fork test below rehearses the full lifecycle without sending transactions:

```sh
RUN_TESTNET_FORK=true forge test --match-contract TestnetV2JourneyForkTest --fork-url https://rpc.testnet.chain.robinhood.com -vv
```

For a fresh run, use a new creator nonce, verify pool readiness, then call the script's `launch()`, `ordinaryBuy()`, `whitelistBuy()`, `curveBuy()`, `curveSell()`, `graduate()`, `v4Buy()`, and `v4Sell()` functions in that order. The first two noncreator buys must execute within the 180-second opening window. Use the exact `JOURNEY_ID` printed by launch and set `USDG_IN` or `TOKEN_IN`, `MIN_STOCK_RECEIVED`, and `MIN_FINAL_OUT` for each trade. The script enforces nonzero minima and a deadline. On a fresh deployment, use a separate versioned address book and re-evaluate the amounts; this script intentionally pins the addresses above.

## Boundaries

The exemption applies to a fixed recipient wallet's opening buy surcharge only; the base trade tax and sell tax remain. `setOpeningTaxExemptions` is a prelaunch creator configuration, not an admin list that can be changed after launch. An exempt wallet can buy and later transfer tokens, so this is a fee exemption for the buy recipient, not a permanent token transfer restriction. The prior chain-46630 V2 deployment is immutable and gains no exemption from this new stack. These contracts and assets are testnet only.

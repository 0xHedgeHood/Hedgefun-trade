# TSLA V2 stage exercise — 2026-09-30 UTC

## Entry points and scope

- [Stage launcher](https://stage.hedgefun-v2-testnet.pages.dev/launch?stock=TSLA)
- [Graduated TSLA strategy #1](https://stage.hedgefun-v2-testnet.pages.dev/trade/1)
- Network: Robinhood Chain Testnet, **46630**. All assets in this exercise are valueless test assets.
- Factory: `0x3E95976E2425e63cb2A8d48BBce8976F55627019`.
- Strategy token: `0xd673BB81E47b0D50561bb294Bd6E7284Ae79A3Cb` (`HFTTS1`).
- Curve: `0xD59651D48c7769aFAe8D1Cce6FBC06871b563844`.

The exercise uses **12 trading wallets and a separate creator**. The strategy
sells 44% of initial supply on the curve, has a 180-second opening window,
3% base tax, and one additional opening-tax exempt recipient. These are the
stress strategy's settings; the launcher allows other supported choices.

The deployment and protocol contracts were not changed by this exercise.
The existing verified whitelist address book remains authoritative.

## Results

| Scenario | Environment and evidence | Observed result |
| --- | --- | --- |
| Launch, funding and buys | Public 46630 receipts | Strategy #1 launched; all 12 trading wallets ultimately bought. |
| Opening sniper vs exempt recipient | Public block **126441040**, transaction indices **1 and 2** | Ordinary recipient's effective opening burn was **69.14%**; exempt recipient's was **3%**. The base tax remains payable. |
| Ten concurrent buy submissions | Public blocks **126441734–126441737** | Eight succeeded; two reverted as intervening fills changed the price. Both succeeded after requoting. Transactions execute in order on chain; this batch was not all in one block. |
| Concentrated buying | Historical block **126441940** | Three wallets held **90.405% of the 12 participants' combined balances**, equal to **32.035% of ERC-20 totalSupply**, which also includes curve/protocol reserves. |
| Whale sells before another seller | Public whale sale, failed stale-floor sale, successful requote | The later half-wallet sale quote fell from **3.0077852046** to **2.4701366400 TSLA**. The rejected trade left the seller's tokens and curve reserve unchanged. |
| Exact final buy and graduation | [Graduation receipt](https://explorer.testnet.chain.robinhood.com/tx/0xd1a268bb75e9b44323fff9dbde37082a8f090c39faa8e3c6d056a2602d2d6092) | Exact remaining input completed the raise with **zero refund** and `allowPartialFill=false`; curve status became 2. |
| Graduate, then buy and sell | Public V4 buy and sell receipts | Both completed against the graduated pool. Additional [strict buy](https://explorer.testnet.chain.robinhood.com/tx/0x7198f87f3630853c5a5075caf66e0f4eee18a931aace2d1acf594c0cd8c868bc) and [strict sell](https://explorer.testnet.chain.robinhood.com/tx/0xc8d6e58db4228921d4b17105119fe8357cb2a3b181a9d1855361f54d160c4789) transactions both succeeded with the frontend's `allowPartialFill=false` and zero refunds. A trade declaring the old Active stage was rejected. |
| Ten buys in one block, ten sells in another | Local Anvil fork, blocks **126441340** and **126441342**, indices **0–9** in each | All 20 distinct transactions succeeded. These are local fork transactions, not public-chain receipts. |
| Migration failure and over-cap input | Local Foundry fault injection | A blocked migration transfer and a one-raw-unit overfill revert atomically. |
| Huge V4 order and minimum output | Local Foundry test | An unacceptable absolute output floor rejects the trade and preserves balances. A full-range pool can consume a huge input at a very poor price; input size alone does not imply a partial fill. |

The curated public manifest contains **51 distinct receipts: 47 successful
and four reverted**. It records the selected exercise transactions; it is not
a complete ledger of every setup approval. The machine-readable report lists
each transaction's actual block, index and status. Failure reasons require
trace or historical-call evidence in addition to a status-zero receipt.

## Graduation capital and accounting

| Amount | TSLA |
| --- | ---: |
| Full curve raise target | 20.821428571428571429 |
| Reserve immediately before final buy | 8.515903508603723196 |
| Final remaining buy | 12.305525062824848233 |
| Stock seeded into V4 liquidity | 10.410714285714285713 |
| Stock transferred to and booked by the treasury | 10.410714285714285716 |

The full raise is **not** the pool's stock liquidity: approximately half is
treasury capital. The graduation emitted positive liquidity of
`35813405311419360743914` and seeded `123200000000000023019369051`
raw strategy-token units alongside the LP stock.

At the report's accounting snapshot:

- Curve stock balance equals outstanding fee liabilities and the sum of the
  protocol, creator and treasury claims: **0.197522894741888303 TSLA**.
- Curve real stock reserve, token reserve and strategy-token balance are zero
  after release; the liquidity vault is seeded.
- Initial supply equals current supply plus all recorded burns. Raw units:
  `1000000000000000000000000000 = 543920141408288739342697494 + 456079858591711260657302506`.
- The router holds **zero TSLA and zero strategy tokens**.

These checks cover accounting conservation in the tested path. They do not
guarantee low price impact, prevent concentrated ownership, or prove general
MEV resistance. The whale exercise moves the **strategy token price**, not
the TSLA oracle reference price; it does not validate oracle-triggered
take-profit, dip-buy or stop-loss execution.

## Reproduce and inspect

Run from this worktree with Foundry (`forge`, `cast`, `anvil`) and Python:

```sh
forge test --match-contract V2TSLAStressTest -vv
python3 tools/tsla_stress_replay.py audit
python3 tools/tsla_stress_replay.py local
```

- `deploy/tsla-stress-transactions.json`: public transaction manifest.
- `deploy/tsla-stress-report.json`: receipt and accounting audit; its
  `auditBlock` identifies the accounting snapshot.
- `deploy/tsla-stress-local.json`: explicitly labelled local fork evidence.
- `deploy/tsla-stress-wallets.json`: public test addresses only.
- `test/V2TSLAStress.t.sol`: deterministic economic and failure cases.
- `script/TestnetTSLAStressLaunch.s.sol`: guarded creation of a **new** test
  strategy, requiring a fresh nonce and matching testnet creator signer.

The `audit` command is read-only against the public RPC. Historical balances
may be reconstructed from canonical token/curve event logs when the RPC has
pruned old state; the report identifies that source. The `local` command
creates its own localhost Anvil fork at block **126441339** and impersonates
test wallets only there. Its buy batch permits partial fills and uses a
minimal output floor to isolate ordering; frontend trades keep partial fills
disabled and simulate their quoted minimum output. A fork rerun requires the
RPC to retain the requested historical state; the public endpoint may prune
it. The captured local report records the completed run. The two Foundry tests use
sequential EVM calls, not multiple public transactions.

Encrypted testnet keys stay outside Git. This report and the replay tool do
not contain signing credentials. Twelve wallets received **0.0012 test ETH
in total**; the operator's reserved gas was preserved.

## Release boundary

The stage frontend's launch, buy/sell and graduation flow is available for
team testing. Frontend validation passed **1,265 tests**, builds and separate
security/runtime reviews. The stage address uses its own deployment config
and the testnet-only address book.

This is a sample of adversarial test cases, not production approval. Contract
repository GitHub Actions has an account billing/spending-limit blocker; the
PR stack remains unmerged. X OAuth still requires account/app setup, and
X Money payout is not active. Neither is needed for wallet-based stage tests.

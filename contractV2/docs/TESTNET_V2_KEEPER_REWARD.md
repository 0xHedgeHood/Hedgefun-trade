# Execute keeper reward: isolated testnet proof

This appends immutable Engine kind **3** to the existing fee registry on chain **46630**. Factory `0xACEB03aAeE5494Aa54929Ec840630ae32A9ade0A`, registry `0xe874fE425e14f3CBa3aDBA2Dd10B50E153Ac6064`, their runtime hashes, and all three old kind creation hashes are pinned to core source `698e577048fde8681d9263f32a653d5176f888e5`. Old kind 2 and existing strategies cannot be upgraded. The current Stage launcher creates kind 0; these two new Engine launches are separate tests and do not change Stage launch behavior.

The old deployed registry checks the previous deadband floor. The script independently requires `500 >= 2 * (effective maximum slippage + 30 bps TSLA venue fee + 50 bps reward)` before scheduling a transaction. It reuses the existing registered policy with Engine schema/version 1, target 5000, band 500, cooldown 600 seconds, maximum action 100 tUSDG, daily turnover 500 tUSDG, and profit payout 5000. This selected configuration is compatible; it does not upgrade the old registry's general configuration validation.

## Funds and phases

At public-RPC snapshot **126884377**, creator held 92.62938 TSLA, 60,540.357707 tUSDG and 0.00047466616 ETH; operator held 69.97914 TSLA / 99,875 tUSDG / 0.01302368323 ETH; second keeper held 69.83240 TSLA / 99,800 tUSDG / 0.00019141609 ETH. These are synthetic test assets. Recheck ETH and fresh asset balances before broadcasting.

Each new curve offers at most **24 TSLA**, spends only the actual cap (about 21.46539 TSLA), and delivers 44% of the token supply to the creator. The direct stock purchase does not route through USDG. Each creation costs 25 tUSDG. The buy strategy also receives **10,000 tUSDG** from the creator. Separate sell/buy strategies allow both first executions without waiting 600 seconds.

| Order | Function | Sender | Scheduled calls |
| --- | --- | --- | --- |
| 1 | `appendEngine()` | operator `75Cee…96D` | two direct CREATEs of V2InitCodeChunk, registerEngineKind; expected new kind 3 |
| 2 | `launchSell()` | creator `D4f69…16d5` | setEngineConfig, setCurveConfig, optional finite USDG approval, launch |
| 3 | `graduateSell()` | creator | optional finite stock approval, direct curve buy |
| 4 | `executeSell()` | second keeper `dA1AE…7614` | one execute; reward paid to this other wallet in USDG |
| 5 | `launchBuy()` | creator | same launch calls, then exactly 10,000 tUSDG transfer to predicted treasury |
| 6 | `graduateBuy()` | creator | optional finite stock approval, direct curve buy |
| 7 | `executeBuy()` | operator | one execute; reward paid to our operator wallet in TSLA |

Sell identity is symbol `HFKSELL`, nonce `202609300108`, strategy id **1**. Buy identity is `HFKBUY`, nonce `202609300109`, id **2**. Calls fail if the existing strategy count, old hashes or new kind bytes differ; do not silently change IDs or register duplicate kinds to retry. If a phase partially succeeds, inspect canonical receipts and current state before deciding which remaining transaction to resume.

## Review and simulate

Use the reviewed committed checkout. No command here loads a wallet key or broadcasts.

```sh
forge test --match-contract TestnetV2KeeperRewardTest
KEEPER_REWARD_FORK=true KEEPER_REWARD_FORK_BLOCK=126884377 \
  forge test --match-contract TestnetV2KeeperRewardTest --match-test test_fork -vv
GIT_COMMIT=$(git rev-parse HEAD) forge script \
  script/TestnetV2KeeperReward.s.sol:TestnetV2KeeperReward \
  --sig 'appendEngine()' --sender 0x75Cee941B0eF3A83feA0397BbF903C12c1D7e96D \
  --rpc-url https://rpc.testnet.chain.robinhood.com --fork-block-number 126884377
```

An individual later phase needs the preceding canonical deployment state; separate simulations against the original chain do not persist earlier phase changes. The opt-in fork fixture executes all seven phases on one local fork. Choose a fresh stable block for the actual append dry run, preserve its log, and have the operator separately review the exact three transactions before broadcast. Both chunks are deployed directly by the owner EOA: a third party cannot consume that EOA's nonce, whereas the public registry's makeChunks CREATE nonce can be raced. Do not concurrently submit other owner transactions during this append. `KEEPER_REWARD_CANDIDATE` console JSON always says `broadcast:false, verified:false`, even if an operator subsequently broadcasts. Record the actual release commit, canonical receipts, blocks and deployed code in an independent extension proof; never publish this console candidate as verified or overwrite the original fee address book.

## Required receipt proof

For the three append transactions, independently verify owner CREATE sender/nonce/initcode/constructor data and registration sender/destination/calldata, CREATE chunk runtime concatenation equal to the reviewed new Engine creation bytecode, kind-3 version/schema/capabilities, and unchanged old kinds/core. For each new launch, bind creator, fixed symbol/nonce, engine config, predicted token/treasury/curve, `TreasuryCodeBound`, and the actual creation fee; verify the buy seed's canonical USDG transfer. Graduate each with same-receipt Released, capital split and Vault Seeded/transfers, excluding unpaid curve fees from principal.

For each execute, `StrategyExecuted.actualOutput` is **gross**. `KeeperRewardPaid.amount` must equal floor(gross × 50 / 10000), and the canonical ERC20 transfer must pay the actual caller. Sell treasury USDG increase plus second-keeper USDG increase equals gross; stock removed from inventory equals actual stock sold plus stock assigned to buyback. Buy treasury stock increase plus operator TSLA increase equals gross; `bookedStock` grows by retained stock, and average cost is `ceil((oldBooked * oldCost + actualUsdgInput * 1e30) / (oldBooked + retainedStock))`. Turnover uses actual USDG input on buys and oracle value of actual stock moved on sells. Both nonces become 1 and cooldown/turnover/state are committed.

After `stopBroadcast`, each execute phase locally simulates a second call and requires NotDue/Cooldown with no state or balance change. This is a **read-only guard probe**, not a canonical failed transaction and not a second reward. Preserve that distinction in the release report. The script's simulation assertions aid review; the operator must still generate independent canonical receipt and pre/post-state proof before claiming live success.

## Canonical live result

All seven phases completed on Robinhood Testnet: **18 successful protocol transactions**, plus one separately identified transfer of **0.002 test ETH** from the operator to the creator for gas. There were no mined failed protocol transactions in this reward rehearsal. A preliminary funding submission with insufficient intrinsic gas was rejected by RPC before inclusion; it is not a successful receipt. Signing password files were removed after the last execution.

The [independent extension proof](../deploy/testnet-v2-keeper-reward.json) is pinned to block **126893089**, hash `0x1edecff76b22f2e6440d676a59901bcf3709d0ecced3600d4b5c7c3e99a5e16b`. Contract/tooling release source is `ba76659c274e1dbb540c7bae4fca15b5bb43c6bb`; the reward source change is `3dbd5dd`. New kind 3 creation hash is `0x21db9a11b19dfe73eb5e372972f0dc7057595360c989d92012ba4b638e0d271f`.

| Strategy | Treasury | Gross swap output | Actual keeper reward | Retained output |
| --- | --- | --- | --- | --- |
| 1 / HFKSELL | `0x58a2d9800e1f833e007618338deb8d3d6d1ca0ac` | 99.737664 tUSDG | 0.498688 tUSDG to second keeper | 99.238976 tUSDG |
| 2 / HFKBUY | `0x30b96039fc571a9fb14fd44ce15264bdd73bb900` | 0.278386450964393703 TSLA | 0.001391932254821968 TSLA to operator | 0.276994518709571735 TSLA |

Execute receipts: sell `0xa0232148619995693ff894337b7312b3f583d76002a3ff506c47b73ca6a8540f`; buy `0x759ee926f2daf7d447a607a1b9824828a8674794486c3c3c91c91e87571d1bd5`. Buy average cost rises from `358000000000000000000` to `358078217166772666680` after including the full spend over retained shares. Each nonce is 1. Subsequent historical `eth_call` probes return `NotDue()`; these probes were not broadcast.

The verifier independently checks canonical transactions/blocks, exact call order and finite approvals, both owner CREATE initcodes, compiled source inputs/settings, raw new and old creation chunks, unchanged core and strategy-0 mapping/runtime, full TreasuryCodeBound constructor/init/runtime/config hashes, creation fees, buy seed, graduation principal/LP/capital transfers, actual venue output and reward transfers, pre/post balances, net cost, turnover, known treasury activity and final state. The original fee book is unchanged. Inputs are archived as public Forge transaction/receipt JSON in [the proof manifest](../deploy/testnet-v2-keeper-reward-proof/manifest.json).

```sh
forge build
python3 tools/audit_v2_keeper_reward.py \
  --book deploy/testnet-v2-keeper-reward-proof/fee-core-book.json \
  --manifest deploy/testnet-v2-keeper-reward-proof/manifest.json \
  --output /tmp/keeper-reward-readback.json --block 126893089
```

Use an RPC retaining these historical states when repeating the audit later; the default public endpoint can prune old state. Local gates: 1,614 unit tests passed before adding the deployment fixture; 21 focused reward/config tests passed, plus 5 deployment fixture tests and the full seven-phase live-venue fork. Size and documentation checks passed. Hosted GitHub jobs could not start because account billing/spending limits blocked runners.

These are synthetic test assets and controlled permissionless execute calls. The existing automated Go keeper still plans V1 entry points and needs separate V2 integration. Rewards earned by a protocol-operated wallet are execution income; gas, RPC, failed attempts and any stock conversion costs must be deducted to determine net revenue. Old kinds/treasuries are immutable and do not acquire this change; the ordinary Stage launcher still creates kind 0.

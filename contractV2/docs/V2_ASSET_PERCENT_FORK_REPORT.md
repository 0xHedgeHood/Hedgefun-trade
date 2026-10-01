# Percentage Engine fork and scenario rehearsal

Verified locally on **2026-10-01**. These are execution and accounting scenarios on a pinned mainnet-state fork,
not historical returns or evidence that the percentage Engine has been deployed or registered on-chain.

## Scope and inputs

- Robinhood Chain mainnet, chain ID **4663**, block **70,786,980**.
- Genuine GME and USDG contracts, the existing GME/USDG V3 listing pool, and the deployed V4 PoolManager.
- The factory, schema-2 policy/kind, fund, hook and owned LP are deployed only inside the local fork.
- Funding uses real token transfers from an impersonated deep pool. No balance, swap or asset-reader call is mocked.
- Oracle answers are the pinned chain's answers; only report timestamps are refreshed. An `AlwaysOpen` test calendar
  removes the wall-clock market-open dependency. The separate offline trading-date tests cover the real calendar.
- LP shares of **50% and 70%** are selected through the owner setter and verified as frozen at fund launch.
- The target is 70% tradable stock with a 5 percentage-point band and a 600-second interval. Limits use the
  complete external-asset NAV, including own LP stock and buyback stock. The normal action/daily caps are 10%/50%;
  the fourth case uses a 10% daily cap and a 6 USDG listing chunk to exercise depleted capacity.

This fork covers GME. Other supported stocks are not claimed to have received individual live-venue fork replays.
The offline token-ordering tests and fuzz/invariant suites cover shared arithmetic and execution behavior.
Synthetic deposits and time steps exercise rule triggers; they are not a recorded historical price path.

Production source remains the reviewed implementation from `aecfd05574888446debe0f4595e23fe9f265648d`.
The new tests, CI and this report do not change its bytecode commitments.

## Results

Both LP configurations completed **4 passed, 0 failed, 0 skipped**, for eight successful scenario executions.
USDG figures below use six decimals; the test asserts the underlying integer amounts.

| Scenario | LP 50% | LP 70% | Verified behavior |
| --- | ---: | ---: | --- |
| Initial real V3 sell | 9.999998 USDG turnover | 8.999999 USDG turnover | Full NAV includes own LP; actual sale, keeper output reward and turnover agree. Target gap can bind before the percentage cap. |
| Real V3 buy after deposit | 20.142857 USDG input | 19.285714 USDG input | Actual input stays within the live cap; keeper stock reward and retained stock agree. |
| Band and interval waits | pass | pass | Within the band, immediately after an action, and at 599 seconds, execution waits without changing inventory, nonce, cooldown or used turnover. At 600 seconds the next buy is eligible. |
| LP stock fee delivery | NAV 104.939792 before/after | NAV 104.945814 before/after | A genuine V4 stock-input swap accrues fees. Collecting them into buyback stock preserves full NAV exactly and leaves the daily ledger unchanged. |
| Hard chunk and daily capacity | 6.000000 chunk | 6.000000 chunk | Remaining capacity 3.996388 is below minLot and waits. A 30 USDG deposit raises it to 6.996388 without resetting used turnover; the next buy respects the chunk. Cumulative used becomes 11.999999. |

The original cold-cache attempt encountered the archive provider's HTTP 429 limit: one scenario passed and three
failed on RPC reads. A separate CLI attempt rejected rate-limit options without `--fork-url`. These failures were
not counted as passes. After warming Foundry's read cache and lowering the request rate, complete new runs passed.
No production assertion was changed to suppress these errors.

The offline full-suite run completed **1,667 passed, 0 failed, 62 skipped**. The four new opt-in fork cases are
explicitly skipped in that offline run; their actual success is established by the two enabled runs above.
The existing 48 percentage-engine tests include four 256-case fuzz tests and two invariants with 256 sequences
of 500 calls each. Skipped integration tests remain skipped and are not live deployment evidence.

## Reproduction and CI gates

Use the repository's pinned Foundry v1.5.0 and an archive provider that can read the pinned block. From the
repository root, run each configuration serially:

```sh
RH_FORK=1 RH_RPC=blockmachine V2_LP_BPS=5000 forge test --fork-url blockmachine --fork-block-number 70786980 --compute-units-per-second 10 --threads 1 --mc '^V2AssetPercentForkTest$' -vv
RH_FORK=1 RH_RPC=blockmachine V2_LP_BPS=7000 forge test --fork-url blockmachine --fork-block-number 70786980 --compute-units-per-second 10 --threads 1 --mc '^V2AssetPercentForkTest$' -vv
```

The compute-unit setting is a conservative Foundry pacing input, not a claimed conversion to the provider's quota.
The official RPC and the publicnode endpoint did not serve the pinned historical state in this verification.

Source CI and the organization mirror both run these four named scenarios at both LP shares. The added gates
use `pipefail`, reject any skipped test, and require every scenario's PASS result. There is one test invocation
per LP share and no automatic suite rerun that could overwrite a failed assertion; an RPC failure is a red gate.

Security and runtime review cover the new test and gates. Percentage launch remains disabled until actual human
deployment/registration and verified public proof. This rehearsal did not sign or broadcast any transaction.

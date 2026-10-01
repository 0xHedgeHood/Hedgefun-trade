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
not counted as passes. Complete new runs passed after warming Foundry's read cache.
No production assertion was changed to suppress these errors.

The first organization CI runs also failed on archive HTTP 429, with no contract assertion failure. A central
Anvil experiment did not solve this: a 65-second upstream backoff exceeded Foundry's fixed 45-second backend
read timeout. Neither result was represented as a passing remote check. The added read-only RPC proxy spaces
upstream requests before they are sent, rather than relying on the compute-unit retry setting to prevent bursts.

A later uncached SELL run through the final paced proxy passed **1 passed, 0 failed, 0 skipped** at LP 50%.
It used `--no-storage-caching` and completed 215 real upstream reads, with zero rate-limit retries, in 636.62 seconds
(627.63-second test suite). The proxy counter moved from 3 to 218; the initial three reads belonged to the excluded
empty-filter setup. It independently reproduced turnover 9.999998 USDG and keeper reward 0.049948 USDG.
This cold-read result validates the final transport on one scenario; it does not turn the earlier failed or empty
invocations into successes. Both complete LP profiles were subsequently rerun through this final proxy using
Foundry's existing read cache, each again producing four passes, zero failures and zero skips.

The offline full-suite run completed **1,667 passed, 0 failed, 62 skipped**. The four new opt-in fork cases are
explicitly skipped in that offline run; their actual success is established by the two enabled runs above.
The existing 48 percentage-engine tests include four 256-case fuzz tests and two invariants with 256 sequences
of 500 calls each. Skipped integration tests remain skipped and are not live deployment evidence.

## Reproduction and CI gates

Use the repository's pinned Foundry v1.5.0 and an archive provider that can read the pinned block. From the
repository root, start the read-only proxy and run each configuration serially:

```sh
FORK_ARCHIVE_RPC=https://rpc-robinhood.blockmachine.io python3 tools/fork_rpc_proxy.py --port 18545 --interval-seconds 3 >/tmp/percentage-fork-proxy.log 2>&1 &
proxy_pid=$!
trap 'kill "$proxy_pid" 2>/dev/null || true' EXIT
# Wait for /healthz to be ready before running the test commands, as the CI startup gate does.
RH_FORK=1 RH_RPC=http://127.0.0.1:18545 V2_LP_BPS=5000 forge test --fork-url http://127.0.0.1:18545 --fork-block-number 70786980 --threads 1 --mc '^V2AssetPercentForkTest$' -vv
RH_FORK=1 RH_RPC=http://127.0.0.1:18545 V2_LP_BPS=7000 forge test --fork-url http://127.0.0.1:18545 --fork-block-number 70786980 --threads 1 --mc '^V2AssetPercentForkTest$' -vv
```

The proxy binds only to loopback, forwards allowlisted read methods with their real arguments, and rejects writes
and signing. Batch entries receive the same spacing as single requests. Read-level rate-limit retries have a
bounded deadline; other errors retain their failure code with sanitized diagnostics and fail the test. It does not manufacture balances or cached responses.
The helper has 22 passing offline transport/security tests, including rejected writes, pacing, malformed responses,
credential redaction and limit exhaustion. An explicit client User-Agent is required by the public archive; the first
proxy setup attempt received HTTP 403 with Python's default User-Agent and did not execute a scenario.
A separate exact-name filter matched no tests; that empty invocation is also excluded from the results.
The 25-second read budget is logical; OS DNS and slow HTTP input may take longer, but late results are rejected
and runner timeouts stay failures. Foundry's compute-unit setting adjusts its retry behavior; it does not actively pace the first request burst.
The official RPC and the publicnode endpoint did not serve the pinned historical state in this verification.

Source CI and the organization mirror both run these four named scenarios at both LP shares. The added gates
use `pipefail`, reject any skipped test, and require every scenario's PASS result. There is one test invocation
per LP share and no automatic suite rerun that could overwrite a failed assertion; an RPC failure is a red gate.

Security and runtime review cover the new test and gates. Percentage launch remains disabled until actual human
deployment/registration and verified public proof. This rehearsal did not sign or broadcast any transaction.

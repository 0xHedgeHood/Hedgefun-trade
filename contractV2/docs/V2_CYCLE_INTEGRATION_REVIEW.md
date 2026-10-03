# V2 Cycle integration review

## Scope and provenance

Review date: **2026-10-03**. Target: [Hedgefun-trade PR #12](https://github.com/0xHedgeHood/Hedgefun-trade/pull/12).
Integration base: `codex/contract-v1` at `d23de8779465620447406d356a35e1aee4c1b8a4`, after target #3 merged.
Original source: [hedgefund PR #110](https://github.com/keyuyuan/hedgefund/pull/110) at
`e6a6097ca622da1d7342b48fb0f1771b08832026`. [PR_SYNC_110.json](PR_SYNC_110.json) preserves that source
provenance and records target adaptations. Tested implementation commit: `2f990efbb506475deaddd714d897b37fba764e22`.

Local validation is complete; current-head GitHub CI is checked separately. Historical
source sizes, test counts and audit conclusions remain in [V2_SIMPLE_CYCLE_AUDIT.md](V2_SIMPLE_CYCLE_AUDIT.md);
its old CI or RPC status does not establish the current target result.

## Integrated behavior

- Keep current stop/profit dust retirement, previously released-stock accounting and cleanup/booking at
  128 lots. Dust remains unbooked principal until a viable later booking; it earns no sale bounty.
- Observe a Cycle stop only after the scheduler confirms an actual stock/USDG sale. Ledger-only cleanup
  does not open recovery, update a sale reference, or renew a cooldown/report gate.
- Extend the post-dust readiness check for an eligible bounded recovery buy. Cleanup can continue to
  a real due stop/TP or a ready dip/recovery in the same call; unavailable buying capacity retains the
  cleanup and booking without consuming recovery.
- Pass the scheduler's healthy price and live-feed snapshot into the Cycle buy helper. Recovery uses
  the smaller of the cash fraction and sellChunkUsdg, preserving ordinary dip sizing and accounting.
- Preserve the default native and percentage Engine workflows and ABI/reference contract lists; add
  Cycle and regenerate publication artifacts from the integrated source. `BuyRecovery = 5`; values 0–4
  keep their meanings. Cycle remains separately registered and selected for new launches.

## Added dust/recovery fuzz coverage

[V2CycleDustFuzz.t.sol](../test/V2CycleDustFuzz.t.sol) defines six properties. Each runs in both
`V2CycleDustStock0FuzzTest` and `V2CycleDustUsdg0FuzzTest`, giving **12 property instances**.

| Property | Main assertion |
|---|---|
| cyclePureStopCleanupCannotArmRecovery | Pure stop-tail retirement changes the lot ledger without a sale, reward or recovery reference |
| cycleCleanupDoesNotRenewRecoveryCooldown | Cleanup preserves the original sale/stop observations; recovery can use the original cooldown/report gate |
| cycleDustCleanupMakesSameCallRecoveryProgress | A ready recovery follows dust cleanup in the same execute call, creates an actual bought lot and consumes recovery once |
| cycleCleanupBeforeRealStopUsesOnlyActualFill | After cleanup, a genuine due stop updates references and pays a reward only from its actual fill |
| cycleDustCleanupPreservesOriginalDipGates | Ordinary post-stop dip still needs cooldown, a newer report and its deeper price; cleanup does not reset them |
| cycleCapacity128BookingPreservesCleanupAndPending | Cleanup frees a slot, pending donation plus released stock can reuse it, and a full ledger keeps cleanup/booking and pending recovery |

The fixture deploys a real local V4 PoolManager and concentrated-liquidity pool through a V3-ABI mirror.
Tokens, feeds, calendar, balances and injected test conditions are local/synthetic. These tests do not
establish public-chain venue liquidity or a completed deployment.

## Current validation results

| Gate | Result / evidence |
|---|---|
| Compile and runtime/initcode sizes | PASS. Cycle runtime 24,514 bytes (62-byte EIP-170 margin); initcode 29,236 bytes before constructor arguments. [Sizes](fuzz/cycle-integration-2026-10-03/sizes.txt) |
| Cycle regression and kind registration/launch/graduation | PASS. Cycle plus current scheduler smoke: 163 passed, 0 failed, 0 skipped. Includes registerKind → selection/quote invalidation → launch → graduation and unchanged kind zero. [Log](fuzz/cycle-integration-2026-10-03/cycle-regression.txt) |
| 12 Cycle dust/recovery fuzz instances; runs, seeds, successful cases | PASS. 3 fixed seeds; 10 non-capacity instances × 1,024 runs and 2 capacity instances × 256 runs per seed = 32,256 samples. 36 passed results, 0 failed, 0 skipped. [Manifest and logs](fuzz/cycle-integration-2026-10-03/results.json) |
| Complete offline Solidity regression, including native and percentage Engine tests | PASS. 839 passed, 0 failed, 35 fork cases skipped; skipped cases are not live evidence. [Log](fuzz/cycle-integration-2026-10-03/full-offline.txt) |
| Python keeper/backtest/tool regression | PASS. 93 tests under tests/ plus 20 under tools/tests/ (Python 3.9.6). [Tests](fuzz/cycle-integration-2026-10-03/python-tests.txt), [tools](fuzz/cycle-integration-2026-10-03/python-tools.txt) |
| ABI/SURFACE/REFERENCE regeneration and documentation checks | PASS. All 25 ABI artifacts match compiled output; publication lists retain native and percentage contracts; 228 relative documentation links resolve. Generated at the tested implementation commit. |
| Current-head target CI | Pending publication/checks; old-head success is not current evidence. |
| External V3/public RPC Cycle integration evidence | Not run for Cycle at this integration; local mirror fills are recorded separately. Native/percentage CI forks exercise their existing paths. |

Toolchain: Foundry 1.5.0, solc 0.8.26, Cancun, optimizer runs 1, bytecode hash disabled.
Seeds: `0x2026100301`, `0x2026100302`, `0x2026100303`. Reproduce from `contractV2/`:

```sh
forge build --offline --sizes
forge test --offline --threads 4
python3 -m unittest discover -s tests -p 'test_*.py'
python3 -m unittest discover -s tools/tests -p 'test_*.py'
```

For each seed, run both groups (replace `<seed>` with the value above):

```sh
forge test --offline --threads 4 --match-contract V2CycleDust --fuzz-seed <seed> --fuzz-runs 1024 --no-match-test Capacity128 -vv
forge test --offline --threads 4 --match-contract V2CycleDust --fuzz-seed <seed> --fuzz-runs 256 --match-test Capacity128 -vv
```

The recorded runs use a fresh `FOUNDRY_FUZZ_FAILURE_PERSIST_DIR` per seed/group; saved failing examples may
add replay cases otherwise. The evidence manifest pins Solidity and log SHA-256 hashes. Fixture pricing
swaps occur before keeper balance snapshots, so their transfers do not contaminate reward assertions.
The historical one-wei test now separates the due TP2 cleanup price from the ordinary dip trigger,
retaining its independent recovery wait/report checks. Shared original TP-to-dip behavior remains covered.
RPC errors and skipped scenarios are not counted as passing. Runtime margin is narrow: rebuild the size
gate for every subsequent production change.

## Release limits

This change does not broadcast, deploy, register a kind or change an existing fund. Before an operator
selects Cycle on a network, verify the reviewed creation code, returned kind ID, exact factory/deployer
and chain, fixed Params, native router configuration where used, and normal graduation wiring. Historical
backtest returns do not establish better returns for current users or universal Cycle settings.

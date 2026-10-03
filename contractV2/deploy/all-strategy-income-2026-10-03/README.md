# All-strategy and FUN income evidence

Read `../../docs/ALL_STRATEGY_INCOME_REPORT_2026_10_03.md` for the complete Chinese interpretation and deployment boundaries.

- `spot/`: 51 local-fork annual cases; frozen source/compiler bindings, complete raw log and daily ledger, exports/charts and independent audit.
- `options/`: 20 annual contract simulations plus six risk tests and one actual-cash bridge; synthetic option quotes and mock reinvestment venue are explicit. Includes the independently reconstructed calendar comparison.
- `income/`: nine actual cash-allocation cases, external $10k stock sponsor plus $20k FUN fund, 0/50/100% staking. Closed-portfolio realized income, loss carryforward, cash and marked-capital gates are enforced by the test sponsor. This is not a production income adapter.
- `staking-independent/`: three seeded stateful fuzz campaigns against the experimental staking module, source/test hashes and review.
- `validation/`: integrated offline test/build/tool results and separately preserved attempts, including errors. Failed fragments are never assembled into successful annual logs.
- `interfaces/`: exact candidate ABIs, source identifiers and null public deployment addresses.
- `import-provenance.json`: upstream source snapshots with commits and file hashes. Historical Cycle fixture is preserved only for its upstream regression suite; annual comparisons here all use 2025.
- `validation.json`: test counts and runtime sizes. Skipped opt-in tests are separate from passes.
- `SHA256SUMS`: whole-archive checksums, excluding checksum files themselves. Per-component checksum files are also retained.

Prices are split-adjusted stock closes, not historical options or FUN prices. Daily open-option gross assets are not fair net NAV. Terminal product returns use contributed capital, not the first gross row (which already includes the first premium). FUN marginal marks are not liquidation returns or redeemable NAV. Rewards still streaming remain assets of the reward pool and are not counted as paid cash.

Replaying compressed evidence is read-only and does not require a private key. Local fork execution needs read-only RPC access. Gzip payloads are deterministic with the recorded Python runtime; different Python versions can encode a different gzip OS header while preserving identical decompressed records.

# Creator-selected ordinary V2 testnet release

## Full release: fresh core

The old fee registry has an immutable global stop friction floor. To allow creator-selected TP/dip/stop without
economic minima, use [DeployV2CreatorTestnet](../script/DeployV2CreatorTestnet.s.sol). It reuses the existing
USDG, eight stock assets, feeds, oracles, canonical V3 pools, manager and calendar. It deploys a fresh
registry/factory and their factory-bound hook, token/curve deployers and routers. No existing strategy funds move.

- Chain: Robinhood testnet 46630; operator `0x75Cee941B0eF3A83feA0397BbF903C12c1D7e96D`.
- Reviewed ordinary creation hash: `0x009bc6eaf9730c8339e40b736087e64e31ec4c2542a2ec41dcf05fc781050ac4`.
- Ordinary default kind: 0; manifest version/schema/capabilities 0/0/0.
- Compiler: Solidity 0.8.26, Cancun, optimizer runs 1, no viaIR or metadata bytecode hash.
- Feature: `v2-creator-selected-stock-fees-v1`; planned deployment transactions: 40.
- Separate candidate files: `deploy/testnet-v2-creator.candidate.json` / `deploy/testnet-v2-creator.dryrun.json`.
- Every candidate has `broadcast=false`; `broadcastRequested` never means independently verified.

Run a fresh pinned simulation with the exact reviewed `GIT_COMMIT`. After actual canonical receipts exist,
use the frontend fee-upgrade publisher's explicit `--creator` mode to verify all 40 deployment transactions,
source/compiler artifacts, roles, core runtime/bindings, manifests and all eight stock listings/depth/history.
Publish only the separate `testnet-v2-creator.json` book. Preserve earlier books and proofs.

Buyback and Engine raw chunks are created directly by the operator EOA, then registered. The registry's
public `makeChunks` helper must not be used across separate broadcast transactions: another caller can advance
its CREATE nonce between simulation and mining. The creator script's independent EOA chunk deployments avoid
that address substitution, and publication verifies each constructor, nonce, raw chunk and registration.

The opt-in local fresh-core rehearsal needs no keys:

```sh
GIT_COMMIT=<reviewed-release-commit> \
  CREATOR_CORE_FORK=true CREATOR_CORE_FORK_BLOCK=<fresh-reviewed-block> \
  forge test --match-contract TestnetV2CreatorCoreTest -vv
```

It checks creation with TP1 1 bps, TP2 2 bps, dip 1 bps and stop 1 bps, then graduates on the existing TSLA venue.
This is a local state fork, not a live deployment, user signing proof or return guarantee.

## Frontend activation

Use the independent creator address book, draft key and routes. Before admitting full creator-selected
parameters, pin reads to one block and verify the new factory/registry binding, exact default-kind wrapper raw
chunks, manifest, registry runtime template and its exact immutable creation hash. Do not infer this capability
from an unverified feature flag. Stage's default creation entry switches only after canonical proof passes.
Old deployment routes remain available for existing strategies and pending transactions.

Wallet unlock/signing stays local. Keeper inventory approval must include the new factory/kind/hash and retain
original identities and journals. Mainnet and testnet keep separate state. Keeper live signing activation remains
a human operator action under that repository's AGENTS.md.

## Optional old-core append: TP/dip only

[TestnetV2AllInFloor](../script/TestnetV2AllInFloor.s.sol) is retained as an optional compatibility tool. It is not
the full creator release because old registry stop constraints remain. It pins the old fee factory
`0xACEB03aAeE5494Aa54929Ec840630ae32A9ade0A`, registry `0xe874fE425e14f3CBa3aDBA2Dd10B50E153Ac6064`,
old code/manifests and actors, then executes three bounded phases:

1. Owner creates two raw chunks directly and appends ordinary kind4.
2. Creator selects kind4 for `HFSTEADY`/nonce `202609300310`, saves 44% sale/zero opening window, re-quotes and
   launches strategy id3 at TP1/TP2/dip 1/2/1 bps, stop disabled. Creation fee allowance is limited to 25 USDG.
3. Creator offers at most 24 synthetic TSLA for 440 million tokens; re-prediction and graduation ledgers must agree.

Archive actual public phase runs as `phase-01-appendPlain.json`, `phase-02-launchPlain.json` and
`phase-03-graduatePlain.json`, with a manifest containing the exact source commit, file SHA256s and dry-run pins.
[The read-only append auditor](../tools/audit_v2_all_in_floor.py) verifies exact canonical transaction/receipt
positions, CREATE nonces, raw chunks, constructor/runtime, fees and graduation transfers. Its scope explicitly
retains the old stop floor. Never reuse that limited proof as a full creator-core proof.

If a phase stops, preserve and reconcile public receipts before any retry. No candidate, console address,
synthetic fixture or passing local fork may overwrite the archived original fee book or Engine reward evidence.

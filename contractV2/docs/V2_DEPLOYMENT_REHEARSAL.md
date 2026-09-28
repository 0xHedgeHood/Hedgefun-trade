# V2 launch rehearsal

Use `script/RehearseV2Launchpad.s.sol` to check that the V2 deployers, hook, factory, stock trade router, and kind-1 buyback code registration can be created and bound on a **local fork**. The script rejects every chain ID except `31337` and also rejects Foundry broadcast/resume contexts. It does not list a stock, open public launches, create a strategy treasury, move funds, or deploy to Robinhood Chain. Run the command below **without `--broadcast`**: Foundry simulates its deployment transactions and discards them.

```sh
anvil --fork-url https://rpc-robinhood.blockmachine.io \
  --fork-block-number 70786980 --chain-id 31337 -p 8545
```

In a second terminal, from this repository's root:

```sh
export OWNER=0x2910117dd2cB431173Ae9Fb6eAF30726321d1693
export PROTOCOL=0x2910117dd2cB431173Ae9Fb6eAF30726321d1693
export CALENDAR=0xFE9E85f0C258Fc2757eB6Acd1ca032Ec860487F5
forge build --sizes
forge script script/RehearseV2Launchpad.s.sol:RehearseV2Launchpad \
  --rpc-url http://127.0.0.1:8545 \
  --sender 0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266 \
  --unlocked -vv
```

The three role addresses above are examples from the existing V1 deployment record, **not assertions of current control**. Confirm their code, Safe threshold, owners, and intended V2 roles at the rehearsal block. The script requires `OWNER` and `PROTOCOL` to answer as Safes with at least two signatures, checks that `CALENDAR` exists, and refuses a broadcaster holding either role. It uses the known PoolManager, V3 factory and USDG addresses; all must have code on the fork. If a mined hook address is occupied, set `HOOK_SALT_START` beyond the printed salt and rerun.

The rehearsal uses fixed candidate defaults: 1 billion token supply, 0.30% V4 LP fee, 1%–15% configurable token tax, 25 USDG launch fee, 3-second opening buy tax, and V1-sized strategy execution gates/chunks. These are **candidate settings**; confirm them with a stock-specific price, depth, and curve/LP capital analysis before launch. V2 cannot reuse V1's zero LP fee because its locked vault collects LP fees. The script prints the deployed addresses, kind-0 and kind-1 code chunks, `registered strategy kinds 2`, and its readback result. The kind-1 `registerKind` call is simulated with the configured owner address; it does not prove Safe signing or execution. A successful output ends with `readback passed; no stock listed or launched` and `public launch false`.

On 2026-09-27, the script completed without `--broadcast` against a local fork of Robinhood Chain block
**70,786,980** (`0xa6acfdd287dfd85edd9c6b555031d578b8f51cfdf9eb4792857249401e35b72a`). It compiled,
deployed the kind-0 and kind-1 code chunks, both other deployers, the mined hook, V2 factory and trade router in the
simulation, registered kind 1 through the simulated owner call, and passed the code-hash, binding, defaults and
closed-public-launch readbacks. Estimated total script gas was **35,150,486**. These simulated addresses are not
production addresses.

Before a real V2 launch, require all of the following:

1. V2 PR reviewed and merged after the full local suite, live venue fork suite, ABI/document checks, and runtime/initcode size checks pass. The hook, curve deployer, and factory have narrow code-size margins.
2. Repeat this rehearsal at a recent fork block using the exact release commit and intended Safe/calendar addresses. Record the commit, fork block, default settings, readback, and gas estimates. A fork result does not prove chain RPC availability or actual account signing.
3. Verify each stock's oracle, USDG V3 pool, market-hours behavior, opening price, curve endpoint, graduation V4 pool, and trading route. Check token transfers and pool fee on a stock-specific fork launch and buy/sell/graduation replay. Before enabling a listing or opening public launches, compute the full-raise stock target `Rg = ceil(supply * virtualStock / minTokenReserve) - virtualStock` in raw stock units. On current chain state, fork-simulate sourcing that stock amount through the intended V3 route (including a single-buyer fill), verify the route can deliver it, and check the post-swap spot against both the stock oracle and the V3 TWAP using the tightest live V1 treasury deviation gate for that stock; also replay each affected V1 treasury's `health()` after the simulated trade. Record block, stock/USDG decimals, required stock, USDG input, price deviation and a liquidity buffer. Reject the listing if the route or gate fails. While public launches are open, monitor pool depth and disable the listing if it falls below the threshold; this cannot guarantee a check immediately before a permissionless launch. Keep public launches closed if a per-launch operator check is required. Direct-stock buys remain possible, so V3 inventory alone does not prove a curve can never graduate. A listing is a separate Safe decision; keep it disabled until this check passes. Disabling it later stops only future launches.
   Record `saleBps` and `lpBps` explicitly for that stock in the Safe proposal and show full-raise size, V4 opening depth, and early-buyer exit scenarios at those values. The code's permissive 90% sale / 10% LP bounds are validity bounds, not recommended settings; the experiment's 70% / 60% is a hypothesis, not a proven safe default. Do not rely on an unset deployer default to make this decision.
4. Reject a strategy whose nonzero stop setting is no greater than its slippage allowance plus V3 pool fee plus keeper bounty. For a 0.30% V3 pool and the script's 1% slippage/0.50% bounty defaults, the stop must exceed **1.80%**. The V2 deployer enforces the effective values at quote and launch.
5. Wire keepers by strategy kind. Kind 0 calls `book()` independently when stock arrives and `execute()` for trading; its individual `takeProfit`, `stopLoss`, and `buyDip` entry points revert. Kind 1 calls `book()` and the paced `buyback()`; its `execute()` reverts `UseBuyback`. Confirm stop-first behavior, partial stop completion, stale-feed halt, closed-market behavior, permissionless caller ordering, and kind-1 cooldown/cache behavior on the release commit.
6. Keep `publicLaunch` false until the owner has reviewed one complete stock-specific fork replay, the trade router and frontend use the V2 factory/ABI, and the operator has a way to pause listings and respond to an oracle fault. The V1 launch router is incompatible with V2's pre-graduation curve.

## Deployment parameters decided after audit round 4

Two parameters were decided on 2026-09-28 in response to [audit round 4](../audit/round-4-2026-09-27/ISSUES.md). Neither
needs code; both belong in the listing and launch procedure.

**Trade tax: the creator chooses it within the factory's existing bounds.** The rehearsal's candidate bounds are
1%–15%, and the founder does not want a tighter cap. The front end must show a creator what the tax is likely to
cost them before they choose. The best evidence on this chain is the pons.family natural experiment in
[PONS_TAX_ELASTICITY.md](./research/PONS_TAX_ELASTICITY.md): up to 5% total tax, graduation rates and the same
creator's volume are flat; above 5%, graduation falls from about 1% of launches to 0.18% and the same creator gets
about half the volume. Pons's total is its 1% base fee plus the creator's tax; the comparable Hedgefun number is the
whole `taxBps`.

**0.05%-fee listings: `sellChunkUsdg` is sized to the pool (M4-1).** The rebalance engine's actions are predictable
and unpaid, and on a 0.05% V3 pool a sandwich inside the deviation gate pays once an action exceeds about 10% of the
pool's USDG depth per 1% move. `maxTradeUsdg` can never exceed the listing's `sellChunkUsdg`, so the chunk is the
bound. On every 0.05%-fee listing:

1. At listing time, measure the pool's USDG depth for a 1% price move and set `sellChunkUsdg` to at most 10% of it
   with `setListingGates(stock, maxDeviationBps, maxSlippageBps, sellChunkUsdg)`. This is a Safe transaction.
2. Measure again before any V2 launch on that stock, and lower the chunk first if the depth has fallen. A launch
   freezes the chunk in its treasury.
3. Record the block, the measured depth and the chunk in the Safe proposal.

For GME the rule gave a chunk of about 1,300 USDG on 2026-09-27, from roughly 13,500 USDG of depth per 1%. The rule
costs no contract bytes.

This script is a readiness check, not a production deployment command. The production transaction plan needs its own review of immutable recipients, role addresses, factory parameters, hook salt, all expected contract addresses, the exact kind-1 chunk code hashes, and the separate Safe `registerKind` transaction before anyone signs it.

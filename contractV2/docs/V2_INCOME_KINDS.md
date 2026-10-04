# V2 income kinds: staking dividends and buy-backs, chosen at launch

Status: source, local tests and fork rehearsals only. Nothing in this document is a deployment receipt. The
registration below is an operator action that has not been broadcast.

## What a creator can now choose

A creator names a strategy kind for their own `(symbol, nonce)` before `predict`/`launch`, with the existing
`V2TreasuryDeployer.setStrategyKind(symbol, nonce, kind)`. The kind is in the launch terms and in the treasury's
CREATE2 address, and cannot be changed after launch.

| Kind | Contract | Stock strategy | Where post-graduation income goes |
|---|---|---|---|
| 0 (default) | `HedgeFunV2AllInTreasury` | take-profit / dip / stop on the listed stock | tax share becomes a new lot; LP stock fees and realised profit fund the buy-back |
| buy-back | `HedgeFunV2BuybackTreasury` | none | everything funds the buy-back |
| dividend | `HedgeFunV2DividendTreasury` | none | 100% to stakers of the launch token |
| buy-back + dividend | `HedgeFunV2BuybackDividendTreasury` | none | 50% to stakers, 50% to the buy-back |

The last two are new. Kind ids are assigned by registration order on each registry: read them from the
registration output, do not hard-code them. The ratio is a constant of each kind's code. A different ratio is a new
kind, registered the same way.

## Income, and what is not income

Income is the stock that reaches the treasury **after** graduation:

- the treasury's share of the trade tax: claimed from the curve, and paid by the hook's sweep;
- the stock side of the locked position's LP fees, credited by `V2LiquidityVault.collectFees()`;
- any stock sent to it.

The stock the treasury holds when the factory books it at graduation is the launch's principal. An income kind
records it as `protectedGraduationStock` and never spends it: not on a dividend, not on a buy-back. It stays in the
treasury. A dividend paid out of the raise would be a return of the buyers' own capital under another name.

Stock claimed from the curve before graduation is in the treasury when the factory books it, so it is counted as
principal, not income. Claiming after graduation makes it income.

## The split

```text
stakers' share of income so far = totalIncomeStock * stakingBps / 10000   (cumulative)
of which transferred             = totalDividendStock
of which still in the treasury   = pendingDividendStock
buy-back budget                  = the rest, in buybackStock
```

The share is computed on the cumulative total, so rounding never drifts toward either side. The stakers' share
never enters `buybackStock`, so the inherited `buyback()` cannot spend it.

`book()` books arrived stock and transfers what is pending. The liquidity vault requires the treasury's balance to
rise by exactly the LP fee it credits, so `creditLiquidityFee` only records the split; `distribute()`, or the next
`book()`, transfers it. Anyone may call either. Between an LP-fee collection and that transfer, the base's
`unbookedStock()` view includes `pendingDividendStock`; `unbookedIncome()` is the figure that excludes it.

If the transfer to the staking pool fails, for a paused or blocking stock token, the call reverts and nothing is
relabelled: the stock stays unbooked in the treasury, or parked in the vault, until a later call succeeds.

## The staking pool

Each income-kind treasury deploys its own `V2StakingIncome` in its constructor and is that pool's only funding
source. `treasury.staking()` returns it.

- Stake the launch token; earn the listed stock token. The reward is the stock, not USDG and not ETH.
- Each funding streams over 7 days. A new funding restarts a 7-day stream over the new amount plus whatever had
  not yet streamed.
- A stake is locked for 7 days from the staker's most recent deposit. Adding to a stake restarts that staker's lock.
- `withdraw` returns principal and never attempts a reward transfer, so a blocked reward token cannot trap staked
  tokens. `claim` pays accrued rewards separately.
- Income funded while nothing is staked is queued and starts streaming with the first stake.
- No administrator can withdraw staked tokens, rewards or donations.

The pool is a port of `FunStakingIncome` from the realised-income experiments, with one change: its income source
is the treasury, on chain, instead of a separately funded sponsor.

## What this does not do

- It does not make the launch token a claim on the treasury's principal. Stakers receive income as it arrives.
- It does not promise income. With no trading there is no tax share and no LP fee.
- It runs no stock strategy. `execute()` reverts `UseBuyback`; tp/dip/stop parameters are accepted and ignored.
- The dividend is paid in the stock token. Its USD value moves with the stock, and a stock token that pauses or
  blocks transfers pauses the dividend with it.
- Token-side tax from V4 buys still has to be converted by the factory owner (`convertFees`) before the hook can
  pay it out. Until then it is not income.
- Staked tokens are still in `totalSupply`. A dividend kind burns nothing; only the buy-back share burns.

## Keeper actions

```text
curve.claimFees(treasury)          once after graduation, and whenever the curve still owes the treasury
hook.sweep(poolId)                 pays the treasury its stock-side tax share
treasury.book()                    books arrivals as income, pays the stakers' share
vault.collectFees()                credits LP stock fees to the treasury, burns LP token fees
treasury.distribute()              pays the stakers' share of the LP fees just credited
treasury.buyback()                 buy-back share only, when the budget and cooldown allow
```

All are permissionless.

## Enabling on an existing deployment

Three independent operator actions. Each is a script that checks its bindings before its first transaction;
simulate without `--broadcast` first.

1. **Income kinds.** `OPERATOR` must be the factory owner. Four transactions.

   ```sh
   OPERATOR=<owner> V2_FACTORY=<factory> forge script script/RegisterV2IncomeKinds.s.sol:RegisterV2IncomeKinds \
     --rpc-url "$RPC_URL" --sender "$OPERATOR"
   # review the two logged kind ids, then repeat with: --account <keystore> --broadcast --slow
   V2_FACTORY=<factory> DIVIDEND_KIND=<id> SPLIT_KIND=<id> \
     forge script script/RegisterV2IncomeKinds.s.sol:VerifyV2IncomeKinds --rpc-url "$RPC_URL"
   ```

   Existing kinds and launched treasuries are unchanged. Pending quotes stay valid: registration does not touch
   launch terms.

2. **ETH payment route.** [TESTNET_V2_ETH_BRIDGE.md](./TESTNET_V2_ETH_BRIDGE.md): seeds the WETH/tUSDG pool that
   routes native ETH to a listed stock.

3. **ETH launch fee and launch-and-buy.** [V2_NATIVE_LAUNCH.md](./V2_NATIVE_LAUNCH.md):
   `ActivateV2NativeLaunch` deploys the native launch router, authorises it, and switches the factory's launch fee
   to a fixed amount of wei. `LAUNCH_FEE_WEI` is chosen by the operator; the script has no default.

Steps 2 and 3 are existing source. Their order matters: activation refuses an empty or missing WETH/USDG pool.

## Evidence

Local, offline: `forge test --offline --match-contract V2IncomeKindsTest`. 18 tests, including a fuzz of the ledger
identity over arbitrary arrival sequences, LP-fee crediting through the real vault, a blocked staking transfer, and
the registration script run against the fixture factory.

Fork, no key and no broadcast, against the deployed testnet factory
`0xACEB03aAeE5494Aa54929Ec840630ae32A9ade0A` at block 128458046: the registration script run as the factory's
owner, then a launch of each income kind through the deployed factory, hook and vault, graduation, trades both
ways, tax and LP-fee income split at the kind's ratio, and a staker claiming the full stream.

```sh
INCOME_KINDS_FORK=true INCOME_KINDS_FORK_BLOCK=$(cast block-number --rpc-url https://rpc.testnet.chain.robinhood.com) \
  forge test --threads 1 --match-contract TestnetV2IncomeKindsForkTest -vv
```

The same day, `TestnetV2EthBridgeForkTest` passed at block 128456854: bridge seeding from the operator's actual
balance, native-launch activation, and a launch-and-buy from a wallet holding only ETH. The public RPC prunes old
state, so both commands need a fresh block.

The existing kinds' creation code is byte-identical before and after this change. `creditLiquidityFee` gained the
`virtual` keyword, which emits no code.

## Front end

- Launch form: offer the kind, show the ratio, and state that the choice is permanent and that an income kind runs
  no stock strategy.
- Token page for an income kind: `staking()`, then on the pool `balanceOf`, `unlockAt`, `earned`, `totalStaked`,
  `periodFinish`; actions `approve` + `stake`, `claim`, `withdraw`. Show that adding to a stake restarts the lock.
- Treasury figures: `totalIncomeStock`, `totalDividendStock`, `pendingDividendStock`, `buybackStock`,
  `protectedGraduationStock`. Show principal separately from income.
- ABIs: `abi/HedgeFunV2DividendTreasury.json` (both income kinds share it) and `abi/V2StakingIncome.json`.

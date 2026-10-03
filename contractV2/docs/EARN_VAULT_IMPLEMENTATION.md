# RHNVDA Earn vault: call / cash-secured put wheel implementation

**Status (2026-10-02): source-only vault, physical call desk, swap adapter and put desk; none has a production address or enabled deposits.** The legacy [CoveredCallDesk](./COVERED_CALL_DESK.md) remains separate and is not the vault's intended call desk, because its delayed backstop can net settle. This mirror does not carry a production address book; verify every mainnet address independently before deployment. A Robinhood brokerage NVDA position cannot be deposited; the vault accepts only the on-chain RHNVDA Stock Token. See [cash-secured put wheel mechanics](./CASH_SECURED_PUT_WHEEL.md) for collateral and payoff examples.

## Components

| Component | Role | Launch state |
| --- | --- | --- |
| [`EarnVault`](../src/options/EarnVault.sol) | User RHNVDA queue, hNVDA shares, one call **or** put per epoch, mixed RHNVDA/USDG redemption claims | Source only |
| [`EarnVaultV3SwapAdapter`](../src/options/EarnVaultV3SwapAdapter.sol) | Bounded USDG-to-RHNVDA reinvestment through the existing V3 pool | Source only |
| [`PhysicalCallDesk`](../src/options/PhysicalCallDesk.sol) | Allowlisted, strictly physical covered-call RFQ and RHNVDA escrow | Source only; deploy with the vault |
| [`CashSecuredPutDesk`](../src/options/CashSecuredPutDesk.sol) | Allowlisted put RFQ, full USDG strike collateral and physical stock delivery | Source only; deploy, verify and configure separately |
| [`PriceOracle`](../src/PriceOracle.sol) | Fresh RHNVDA/USDG per-token price, gated by calendar and issuer pause | Existing source; verify live address and state |

The vault is an **asynchronous, two-asset share vault**, not an ERC-4626 vault. At each settlement checkpoint, the contract prices the active RHNVDA plus USDG basket through `PriceOracle`, which gates market hours, issuer pause and both feed ages (the configured stock age is 26 hours). Pending deposits are excluded from the old shareholders' NAV and from RFQ collateral. Escrowed redeem shares remain in the supply until the checkpoint. The same pre-flow NAV fixes shares for deposits and pro-rata RHNVDA/USDG claims for redemptions. Individual deposits that round to zero share wei become claimable RHNVDA refunds. Initial seed permanently locks at least 1e12 share wei (0.000001 RHNVDA share basis) at an inaccessible address to deter tiny-supply inflation. Claims can be pulled later without affecting future option collateral. One epoch accepts at most 64 distinct depositing addresses so on-chain close gas is bounded.

The Safe must approve each `offer` or `offerPut`; an operator cannot publish an RFQ. A new option requires the prior call or put to reach a terminal state **and** an epoch close. Both directions require a specified allowlisted buyer, matching feed, fresh oracle, minimum premium **above any intrinsic value**, bounded tenor, five-minute fill window and utilization limit. A call additionally requires `Physical` mode and locks only free RHNVDA; a put locks the full `ceil(size × strike / 1e18)` USDG cost at offer, using only free USDG. Neither pending deposits nor settled redemption reserves can be used as collateral. Both new desks' 14-day owner backstops can set a price, but an in-the-money result opens a physical exercise window: the call buyer must pay the full USDG strike cost before receiving all locked RHNVDA; the put buyer must deliver RHNVDA before receiving locked USDG. If a buyer does not exercise, the collateral returns to the vault. No fixed stock-only redemption or guaranteed weekly payout is promised.

For a mixed RHNVDA/USDG redemption, the USDG leg is paid first. If the stock token refuses delivery to the chosen address, the vault records `owedRedeemStock(account)` and reserves the RHNVDA; the user can later call `claimOwedRedeemStock(to)` for a receivable address. This lets the user collect USDG despite a frozen stock recipient. If USDG itself refuses payment, `claimRedeem` reverts and both legs remain pending; the user may retry to another eligible receiving address after transfers resume. An issuer freeze of the vault itself can still prevent stock claims.

The operator may reinvest free USDG **or retain it for a future cash-secured put**. The adapter is bound to one vault, one stock, one USDG token, one V3 pool and one oracle; the vault verifies those identities and the adapter's guard settings before its one-time installation. It requires fresh oracle and pool spot/TWAP health, caps average execution slippage and refunds unused USDG. It cannot spend USDG locked for a put or reserved for prior redemptions. A one-time Safe call installs the adapter in the vault after both are deployed.

## What was reused

- The repository already uses **OpenZeppelin Contracts**. EarnVault directly inherits its `ERC20`, `Ownable2Step` and `ReentrancyGuard` and uses `SafeERC20` and `Math.mulDiv`. The swap adapter directly inherits the repository's [`PoolTrader`](../src/PoolTrader.sol), including its V3 callback, oracle/spot/TWAP gates and bounded swap math.
- [Ribbon v2](https://github.com/ribbon-finance/ribbon-v2) is a useful MIT-licensed reference for weekly rounds, pending deposits, withdrawals and share snapshots. We followed that *accounting pattern*, but did not copy its option or vault code: it is wired to Opyn oTokens and auctions rather than this desk's RFQ and physical exercise.
- [Valorem Clear](https://github.com/valorem-labs-inc/clear) is a fully collateralized physical-exercise clearing engine. It has no hNVDA deposit/share vault, and using it would change this vault's settlement integration.
- [ERC-7540 Community Contracts](https://docs.openzeppelin.com/community-contracts/erc7540) provides asynchronous request semantics, but its single-asset ERC-4626-style claims do not express a redemption that returns both RHNVDA and USDG. The Community Contracts repository identifies itself as experimental and unaudited; this vault does not claim ERC-7540 compatibility.

## Before any mainnet deposit

1. Run the full tests and an independent review of share rounding, issuer pause/blocklist behavior, RFQ timing, desk backstop, V3 reinvestment and two-asset claims. Test a Robinhood Chain fork with live token/pool/feed code. No such external review has yet been completed.
2. Verify the live RHNVDA, USDG, pool, oracle, calendar and Safe addresses and their current configuration. [`DeployEarnVault.s.sol`](../script/DeployEarnVault.s.sol) validates the expected chain and addresses, then deploys the inert physical call desk, vault, adapter **and put desk**. Both desks use the same Safe owner, USDG and calendar. Simulate before broadcast.
3. Verify all four bytecodes and record their addresses. **Before the first user deposit**, the Safe calls `vault.setPutDesk(putDesk)` and `vault.setSwapAdapter(adapter)`. `setPutDesk` enforces an empty vault and can only be called once. The Safe configures each desk's RHNVDA feed listing, `setWriter(vault, true)` and `setBuyer(marketMaker, true)`, plus `vault.setBuyer(marketMaker, true)` and `vault.setEligible(user, true)` under the approved eligibility process. [`earn_vault_setup_batch.py`](../tools/earn_vault_setup_batch.py) prepares these calls for Safe review without broadcasting. The Safe signs each `vault.offer` or `vault.offerPut` RFQ; an optional operator may only reinvest free USDG and cancel unfilled offers.
4. Test a small deposit, epoch close, hNVDA claim, call and put RFQ fill/cancel, OTM and physical ITM settlement in both directions, and both-asset redemption claim before inviting users. The Earn frontend is separate follow-up work; configure its chain 4663 and verified vault address only after these checks.
5. Publish actual share NAV and RFQ history separately from the modelled return charts. No 44% APY, fixed monthly dividend, lending spread, or leverage product exists in this contract.

Neither desk reprices an RFQ if RHNVDA moves after the vault publishes it. The vault limits that free option to five minutes, and anyone can cancel an unfilled offer after its deadline. A five-minute price race remains; an atomic signed RFQ fill would require a desk upgrade. The minimum on-chain premium is a guard against obvious underpricing, **not** a fair-value quote; the Safe needs independent price checks or competing RFQs. If the Chainlink expiry round is unavailable, this vault cannot use either desk's writer-side bilateral price proposal and may wait at least 14 days for the desk owner's backstop, followed by up to three more days of physical exercise. Both desk owners' price-setting authority, issuer controls and lack of guaranteed market-maker quotes remain material operating dependencies.

## Local verification

```sh
forge build --skip test --offline
forge test --offline --match-contract EarnVaultTest -vv
forge test --offline --match-contract EarnVaultPhysicalCallTest -vv
forge test --offline --match-contract PhysicalCallDeskTest -vv
forge test --offline --match-contract EarnWheelVaultTest -vv
forge test --offline --match-contract CashSecuredPutDeskTest -vv
forge test --offline --match-contract EarnVaultV3SwapAdapterTest -vv
```

The `--offline` flag avoids Foundry's macOS proxy detection crash in this environment; it does not change test semantics.

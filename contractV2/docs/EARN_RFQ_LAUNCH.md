# RHNVDA Earn RFQ: physical delivery and launch sequence

**State on 2026-10-02:** the new `PhysicalCallDesk`, `EarnVault` (hNVDA), swap adapter and `CashSecuredPutDesk` are implemented locally but have no verified Robinhood Chain addresses. The legacy `CoveredCallDesk` is a separate contract whose delayed backstop may net settle a Physical call; the Earn deployment script creates a new physical-only desk instead. No user deposit or hNVDA market is live.

## What Wintermute receives

| Moment | Wintermute pays | RHNVDA location / recipient |
| --- | --- | --- |
| Safe publishes the RFQ | Nothing | The vault's exact offered amount moves into `PhysicalCallDesk` escrow. |
| Wintermute fills before the five-minute deadline | Agreed premium in USDG; forwarded to the vault | Still locked in the desk. Wintermute has the option, not the stock. |
| Expiry is out of the money | Nothing more | The whole locked amount returns to the vault. |
| Expiry is in the money and Wintermute exercises in time | Full `ceil(size × strike / 1e18)` USDG; forwarded to the vault | The whole locked amount is sent directly to Wintermute's chosen receiving address. |
| In the money but no exercise by deadline | Nothing more | The whole locked amount returns to the vault; the vault keeps the premium. |

The oracle path fixes the exercise deadline at expiry + five minutes + the window bound into the RFQ (two hours by default). The desk supports bilateral price proposals, but this vault has no method to make or accept one on the writer's behalf. If the oracle round cannot be proven, this integration may therefore wait for the Safe-owned desk's unilateral price backstop after 14 days. An in-the-money backstop gives the buyer three more days to pay and take delivery. Neither fallback sends any RHNVDA without the full strike payment. This long fallback can interrupt a weekly vault cycle.

Sending the RHNVDA to Wintermute **at RFQ fill** would transfer the principal before the strike is paid. Premium alone does not cover its value or guarantee its return. If Wintermute requires upfront custody, that is a separately collateralized sale/forward or stock loan requiring a different contract and commercial terms. The current covered-call path delivers the stock directly to their wallet **upon exercise**.

## Depositor and hNVDA accounting

An eligible user deposits on-chain RHNVDA with `requestDeposit`. At `closeEpoch`, the vault excludes pending deposits from the old holders' NAV, allocates hNVDA shares, and the user calls `claimDeposit`. Shares track a claim on the vault's **combined RHNVDA plus USDG** assets. Premium and any strike proceeds raise the vault NAV; they are not automatically distributed as monthly cash dividends. A user requests redemption, then receives a pro-rata RHNVDA/USDG basket after the epoch closes. If the RHNVDA recipient is frozen, the USDG leg can still be paid and the reserved stock claimed later to a receiving address. If USDG itself refuses payment, that claim remains pending until the user chooses another receivable address or USDG transfers resume.

Starting from 400 RHNVDA and no USDG, the vault can sell a fully covered call. A cash-secured put becomes possible only after the vault has **free USDG equal to the entire put strike obligation**. Reinvesting every premium into RHNVDA leaves less cash for puts. The modeled 44% annualized premium rate is a scenario, not a guaranteed payout or hNVDA APY. hNVDA value also changes with RHNVDA price and lost upside when calls are exercised.

The current contracts do not create an hNVDA liquidity pool, lending market, leverage loop, fee split, or separately tokenized dividend stream. Each of those needs its own liquidity, collateral, risk and access design before being shown as a live Earn product.

## Live launch order

1. Confirm Wintermute's **chain-4663 buyer wallet** (the address that pays premium and strike) and RHNVDA receiving wallet, plus each RFQ's size, strike, premium, expiry, feed, stock multiplier and exercise window. The receiving wallet may differ: `exercise(id, roundId, to)` sends stock to `to`.
2. Complete independent code review and a live Robinhood Chain fork test for the token, feed, calendar, V3 pool, both settlement paths and freeze behavior. Check current token transfer eligibility before admitting depositors.
3. With working chain access and a funded deployer signing path, simulate [`DeployEarnVault.s.sol`](../script/DeployEarnVault.s.sol), then deploy the four contracts, verify source/runtime bytecode, and record addresses. The deployer retains no owner role; the existing 3-of-4 Safe owns both desks and the vault.
4. Generate [`earn_vault_setup_batch.py`](../tools/earn_vault_setup_batch.py) with the verified addresses and approved depositor(s). Safe signers decode and simulate the batch, then set both desks' RHNVDA listing, vault writer and Wintermute buyer; install the put desk and adapter in the vault; allowlist the buyer and depositors. No RFQ works before the Safe actually executes these calls.
5. On a live fork, test both out-of-money return and in-the-money full-strike exercise. For the live pilot, start with a small controlled deposit → `closeEpoch` → hNVDA claim → call `offer`/Wintermute `fill` → settlement → mixed-asset redemption. Two actual option outcomes require separate cycles; the vault requires at least a 12-hour tenor and a market-open expiry. Only then publish the verified vault address to the Earn frontend and open further eligible deposits.

No live fork of the new vault/physical desk stack, deployment broadcast, Safe setup or user deposit has been executed for this PR. A deployment signer and Wintermute buyer address have not been supplied. The setup generator prepares calldata offline; it does not verify chain state, sign or broadcast.

## Operator handoff: deploy, verify and configure

Use the pinned compiler/settings in [`foundry.toml`](../foundry.toml) and an exact reviewed commit. `EarnVault` is 24,523 runtime bytes, only 53 bytes below the EIP-170 limit; any source or compiler change requires a new size check and deployment simulation. The `robinhood` RPC alias is the archive-capable endpoint used by this repository's fork tests. The deployer signs contract-creation transactions but retains no owner role.

```sh
forge build --offline --skip test --sizes
# Existing live-token and V1 desk fork regressions; these do not replace a new-stack fork rehearsal.
RH_FORK=1 forge test --match-contract CoveredCallDeskFork -j 1

# Set EARN_DEPLOYER to the funded signer address and EARN_KEYSTORE to its local keystore name.
forge script script/DeployEarnVault.s.sol --tc DeployEarnVault \
  --rpc-url robinhood --sender "$EARN_DEPLOYER"                 # simulate only
forge script script/DeployEarnVault.s.sol --tc DeployEarnVault \
  --rpc-url robinhood --sender "$EARN_DEPLOYER" --account "$EARN_KEYSTORE" --broadcast
```

Use the **same signer and sender** for both script calls. Confirm its nonce has not changed, and trust the actual broadcast receipts over simulated addresses. Record the four deployed addresses and transaction hashes in a reviewed operator address book outside this source snapshot. This mirror contains no production address book or broadcast receipts. Keep the legacy desk separate; do not pass its address as the Earn call desk. Compare the call desk's deployed bytecode and constructor state with `PhysicalCallDesk`; the vault constructor checks desk currency but does not identify the desk implementation. Verify source from the exact deployed commit:

```sh
# Set EARN_CALL_DESK, EARN_VAULT, EARN_ADAPTER and EARN_PUT_DESK from broadcast receipts.
forge verify-contract --verifier sourcify --chain-id 4663 "$EARN_CALL_DESK" src/options/PhysicalCallDesk.sol:PhysicalCallDesk
forge verify-contract --verifier sourcify --chain-id 4663 "$EARN_VAULT" src/options/EarnVault.sol:EarnVault
forge verify-contract --verifier sourcify --chain-id 4663 "$EARN_ADAPTER" src/options/EarnVaultV3SwapAdapter.sol:EarnVaultV3SwapAdapter
forge verify-contract --verifier sourcify --chain-id 4663 "$EARN_PUT_DESK" src/options/CashSecuredPutDesk.sol:CashSecuredPutDesk
```

Sourcify verification publishes the source permanently. Confirm the intended publication time before running those four commands. The verified runtime and immutable addresses must match the receipts and deployment script.

Generate the Safe initialization file only with the verified addresses and Wintermute's buyer wallet:

```sh
# Set EARN_WINTERMUTE and EARN_PILOT to verified Chain 4663 addresses.
# EARN_ADDRESSES is a locally maintained JSON file, excluded from this source repo.
# It must contain chainId, safe, stocks.NVDA, rhNvdaFeed and legacyCallDesk.
# Set EARN_SETUP_FILE to a new JSON path under deploy/safe/.
python3 tools/earn_vault_setup_batch.py build --addresses "$EARN_ADDRESSES" \
  --call-desk "$EARN_CALL_DESK" --vault "$EARN_VAULT" --adapter "$EARN_ADAPTER" \
  --put-desk "$EARN_PUT_DESK" --buyer "$EARN_WINTERMUTE" \
  --depositor "$EARN_PILOT" --output "$EARN_SETUP_FILE"
python3 tools/earn_vault_setup_batch.py decode --addresses "$EARN_ADDRESSES" "$EARN_SETUP_FILE"
```

The Safe's 3-of-4 signers compare each target and calldata with the four verified contracts, simulate the batch in the Safe UI, then execute it. Read back `vault.owner()`, `vault.desk()`, `vault.putDesk()`, `vault.swapAdapter()`, `vault.eligible(pilot)`, `vault.allowedBuyer(Wintermute)`, and both desks' RHNVDA listings, writer and buyer allowlists. Confirm the `desk()` address is the **new** physical-only desk, not the existing V1 desk. These are on-chain checks; the offline batch generator does not perform them.

For example, read the vault bindings and pilot eligibility directly after the Safe batch executes:

```sh
cast call "$EARN_VAULT" 'owner()(address)' --rpc-url robinhood
cast call "$EARN_VAULT" 'desk()(address)' --rpc-url robinhood
cast call "$EARN_VAULT" 'putDesk()(address)' --rpc-url robinhood
cast call "$EARN_VAULT" 'swapAdapter()(address)' --rpc-url robinhood
cast call "$EARN_VAULT" 'eligible(address)(bool)' "$EARN_PILOT" --rpc-url robinhood
# Set EARN_ORACLE to the oracle read from vault.oracle(); compare it with the deployment script.
cast call "$EARN_VAULT" 'oracle()(address)' --rpc-url robinhood
cast call "$EARN_ORACLE" 'tryPrice()(bool,uint256)' --rpc-url robinhood
```

For the pilot, the eligible destination wallet approves RHNVDA and calls `requestDeposit`, then a keeper calls `closeEpoch` and the depositor calls `claimDeposit(epoch, eligibleTo)`. Check `PriceOracle.tryPrice()` immediately before closing: the market calendar must be open and the feeds healthy, otherwise deposits remain queued. After settlement, compare the recorded epoch NAV and redemption basket with actual token balances. Frontend configuration follows the verified vault and physical desk addresses only after the complete pilot and review gates.

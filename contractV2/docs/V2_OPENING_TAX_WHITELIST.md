# V2 opening-tax recipient whitelist

V2 creators can fix up to 32 additional wallet addresses that pay only the ordinary `taxBps` on curve buys during the opening window. The creator address is automatically exempt; in V2 that address is also the creator-fee recipient. The exemption never removes the ordinary buy tax, never changes sell tax, and does not carry into the graduated V4 pool.

## Launch flow

The whitelist is a creator-owned launch choice, keyed by the same `(symbol, creator, nonce)` salt as `setCurveConfig`. Before requesting a quote or submitting a launch, the creator calls:

```solidity
CurveDeployer.setOpeningTaxExemptions(symbol, nonce, recipients)
```

The list may be empty. It cannot contain zero, duplicates, or the creator (already exempt), and has a maximum of 32 addresses. A second call replaces the pending list. Another account can register only for its own salt. The launcher then calls `HedgeFunV2Factory.predict(q)` and passes the returned `terms` to `launch` or `launchWithMetadata`. `predictCurve(q)` includes the registered list in the curve's CREATE2 address. If the creator replaces the list after the quote, the old `terms` revert with `Restated` and the creator must quote again. The deployed curve copies the list, exposes `isOpeningTaxExempt(address)` and `openingTaxExemptions(index)`, and cannot be edited.

The shared V1/V2 `HedgeFunFactory.Request` ABI remains stable: the V2-only whitelist follows the existing `setCurveConfig` registration pattern. UI launch forms should gather the addresses before predict/launch, show the creator as an automatic exemption, and display the 32-address limit and recipient-only scope.

## Buy and quote flow

`buyRateBps()` and `quoteBuy(amount)` describe a normal recipient. For the amount a particular wallet receives, use `buyRateBpsFor(recipient)` and `quoteBuyFor(amount, recipient)`. The curve's `buy` uses the actual `recipient` argument for the same rate and sends tokens there. Even a whitelisted recipient pays `taxBps`; only the opening surcharge above that rate is waived. At `launchedAt + snipeSeconds`, all recipient rates are equal.

The ERC20 trade router sends an active curve buy directly to the final recipient and verifies its exact balance increase. `buy` uses its caller as recipient. `buyFor` supports a different final recipient, with payment still pulled from its caller; it cannot give a payer the recipient's whitelist rate and deliver the tokens elsewhere. The native wrapper calls `buyFor` with its real caller, so exempting the wrapper address does not exempt everyone using it. On a partial curve fill, stock refunds still return to the payer. `minFinalOut` remains an absolute minimum on the final recipient's balance increase.

## Deployment boundary

Robinhood testnet chain `46630` already has the previous immutable V2 factory, curve deployer, curve bytecode, and routers. This change cannot upgrade those addresses. A test of this policy needs a new deployment, a verified new address book, refreshed ABI/config in the frontend, and a new six-stage testnet journey. Do not relabel the existing book as whitelist-capable.

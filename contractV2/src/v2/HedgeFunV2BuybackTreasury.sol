// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {HedgeFunV2Treasury} from "./HedgeFunV2Treasury.sol";

/// @notice Strategy kind 1: a pure buy-back treasury. Opt-in: production must register its exact code chunks.
///
/// Every unit of stock this treasury ever receives -- its share of the graduation raise, the sell tax, stock-side
/// LP fees -- becomes buy-back budget. It opens no stock lot, never sells stock, and has no stop, take-profit or
/// dip. The stock leaves only through the inherited `buyback()`: one `buybackChunkUsdg` per `buybackCooldown`,
/// bounded by the pool's own TWAP/anchor and `maxBuybackImpactBps`, burning what it buys. Graduated V2 pools
/// keep the flat sell tax after each buy-back. Nothing about pacing, price limits or bounties is new.
///
/// Because it is `HedgeFunV2Treasury` with `book()` and `execute()` replaced, it takes the same constructor
/// arguments and serves the same surface the factory, hook, vault and routers call. The creator's tp/stop/dip
/// parameters are accepted and ignored; `lotCount()` is always 0 and `bookedStock` always 0.
///
/// What a keeper does: `claimFees` on the curve, `book()` here (or let graduation's own `book()` do it), then
/// `buyback()` whenever the cooldown allows. `execute()` reverts `UseBuyback`.
contract HedgeFunV2BuybackTreasury is HedgeFunV2Treasury {
    error UseBuyback();
    event BuybackBooked(uint256 amount, uint256 buybackStock);

    constructor(address usdg_, address stock_, address v3Pool_, address oracle_, address token_,
        address poolManager_, address factory_, Params memory p)
        HedgeFunV2Treasury(usdg_, stock_, v3Pool_, oracle_, token_, poolManager_, factory_, p) {}

    /// @notice Pending stock becomes buy-back budget. Needs no oracle and no open market: the budget is spent by
    ///         `buyback()`, which is priced off the token pool, not the stock. Parked until graduation wires the pool.
    /// @dev Also records `totalStockReceived`, the scorecard's denominator, which the inherited `_book` writes for a
    ///      lot and this kind never reaches: without it the published score is a division by zero for life.
    function book() public override nonReentrant returns (bool) {
        if (hook == address(0)) return false;
        uint256 pending = unbookedStock();
        if (pending == 0) return false;
        buybackStock += pending;
        totalStockReceived += pending;
        emit BuybackBooked(pending, buybackStock);
        return true;
    }

    /// @dev No lot is ever opened, so a lot can never be due.
    function _canAddLot() internal pure override returns (bool) { return false; }

    /// @notice This kind has no stock strategy to execute.
    function execute() external pure override returns (Action, uint256) { revert UseBuyback(); }
}

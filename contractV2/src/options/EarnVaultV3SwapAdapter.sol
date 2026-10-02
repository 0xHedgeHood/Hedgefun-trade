// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import {MAX_SLIPPAGE_BPS} from "../libraries/HedgeFunLimits.sol";
import {PoolTrader} from "../PoolTrader.sol";

/// @notice A single-purpose, exact-input USDG -> RHNVDA route for an Earn vault.
/// @dev The vault approves this adapter for USDG immediately before use. The adapter has no
///      keeper/admin path and cannot retain any part of an individual buy. PoolTrader binds
///      execution to one V3 pool, a fresh stock/USDG oracle and a 10-minute pool TWAP.
contract EarnVaultV3SwapAdapter is PoolTrader, ReentrancyGuard {
    using SafeERC20 for IERC20;

    address public immutable vault;
    uint16 public immutable maxDeviationBps;
    uint16 public immutable maxSlippageBps;

    error NotVault();
    error UnhealthyMarket();
    error InvalidAmount();
    error InsufficientOutput();
    error BalanceMismatch();

    event Bought(uint256 requestedUsdg, uint256 spentUsdg, uint256 receivedStock);

    constructor(
        address vault_,
        address usdg_,
        address stock_,
        address pool_,
        address oracle_,
        uint16 maxDeviationBps_,
        uint16 maxSlippageBps_
    ) PoolTrader(usdg_, stock_, pool_, oracle_) {
        if (
            vault_ == address(0) || maxDeviationBps_ == 0 || maxDeviationBps_ >= maxSlippageBps_
                || maxSlippageBps_ > MAX_SLIPPAGE_BPS || maxSlippageBps_ + poolFeeBps >= 10_000
        ) revert BadConfig();
        vault = vault_;
        maxDeviationBps = maxDeviationBps_;
        maxSlippageBps = maxSlippageBps_;
    }

    /// @param amountIn Maximum USDG the vault will spend. The pool may fill short at its oracle-derived price limit.
    /// @param minStockOut Minimum RHNVDA received across the whole swap; a short fill below this reverts.
    /// @return spent USDG actually paid to the pool.
    /// @return received RHNVDA actually sent to the vault.
    function buy(uint256 amountIn, uint256 minStockOut)
        external
        nonReentrant
        returns (uint256 spent, uint256 received)
    {
        if (msg.sender != vault) revert NotVault();
        if (amountIn == 0 || amountIn > uint256(type(int256).max) || minStockOut == 0) revert InvalidAmount();

        (bool healthy, uint256 p) = _health(maxDeviationBps);
        if (!healthy) revert UnhealthyMarket();

        uint256 vaultUsdgBefore = usdg.balanceOf(vault);
        uint256 vaultStockBefore = stock.balanceOf(vault);
        uint256 adapterUsdgBefore = usdg.balanceOf(address(this));
        uint256 adapterStockBefore = stock.balanceOf(address(this));
        usdg.safeTransferFrom(vault, address(this), amountIn);
        if (usdg.balanceOf(address(this)) != adapterUsdgBefore + amountIn) revert BalanceMismatch();

        (spent, received) = _swapBounded(true, amountIn, p, maxSlippageBps);
        if (spent > amountIn || received < minStockOut) revert InsufficientOutput();
        if (usdg.balanceOf(address(this)) != adapterUsdgBefore + amountIn - spent) revert BalanceMismatch();
        if (stock.balanceOf(address(this)) != adapterStockBefore + received) revert BalanceMismatch();

        stock.safeTransfer(vault, received);
        if (spent < amountIn) usdg.safeTransfer(vault, amountIn - spent);
        if (usdg.balanceOf(address(this)) != adapterUsdgBefore) revert BalanceMismatch();
        if (stock.balanceOf(address(this)) != adapterStockBefore) revert BalanceMismatch();
        if (usdg.balanceOf(vault) != vaultUsdgBefore - spent) revert BalanceMismatch();
        if (stock.balanceOf(vault) != vaultStockBefore + received) revert BalanceMismatch();
        emit Bought(amountIn, spent, received);
    }
}

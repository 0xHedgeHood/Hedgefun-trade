// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {HedgeFunHook} from "./HedgeFunHook.sol";
import {IPoolManager} from "v4-core/src/interfaces/IPoolManager.sol";
import {PoolKey} from "v4-core/src/types/PoolKey.sol";
import {PoolId, PoolIdLibrary} from "v4-core/src/types/PoolId.sol";
import {Currency, CurrencyLibrary} from "v4-core/src/types/Currency.sol";
import {SwapParams} from "v4-core/src/types/PoolOperation.sol";
import {BalanceDelta, BalanceDeltaLibrary} from "v4-core/src/types/BalanceDelta.sol";
import {StateLibrary} from "v4-core/src/libraries/StateLibrary.sol";
import {TickMath} from "v4-core/src/libraries/TickMath.sol";
import {FullMath} from "v4-core/src/libraries/FullMath.sol";
import {IOwned} from "../interfaces/IOwned.sol";

/// @notice V2 buy fees accrue as tokens, then convert to stock before the same split used by sell fees.
/// @dev Conversion is a separate owner-operated transaction; user swaps never depend on it succeeding.
///      The legacy hook's buy burn is unchanged. LP-fee collection remains the vault's separate responsibility.
contract HedgeFunV2Hook is HedgeFunHook {
    using PoolIdLibrary for PoolKey;
    using CurrencyLibrary for Currency;
    using StateLibrary for IPoolManager;
    using BalanceDeltaLibrary for BalanceDelta;

    /// A 0.5% sqrt-price boundary limits one conversion to approximately 1% pool-price movement.
    uint256 public constant MAX_CONVERSION_SQRT_MOVE_BPS = 50;
    mapping(PoolId => uint256) public pendingTokenFees;
    bool private _converting;
    bytes32 private _conversionHash;

    error BadConversion();
    error ConversionExpired();
    error ConversionMinimum();
    event TokenFeesPending(PoolId indexed id, uint256 amount);
    event FeesConverted(PoolId indexed id, uint256 tokensConsumed, uint256 stockAccrued, uint256 tokensRemaining);

    constructor(IPoolManager manager) HedgeFunHook(manager) {}

    function version() external pure returns (uint256) { return 2; }

    /// @dev Keep this pool's pending inventory as ERC-6909 claims, separate from parked ERC20 stock balances.
    function settleToken(PoolId id, address caller) public override {
        if (liquidityVaultOf[id] == address(0)) { super.settleToken(id, caller); return; }
        if (msg.sender != address(this)) revert NotPoolManager();
        Pool storage p = _pool(id);
        uint256 amount = p.accruedToken;
        if (amount == 0) return;
        p.accruedToken = 0;
        pendingTokenFees[id] += amount;
        emit TokenFeesPending(id, amount);
    }

    /// @notice Convert at most maxTokens of this pool's fee inventory; unsold tokens remain pending.
    /// @dev The factory owner supplies an independently reviewed minimum and deadline. Price-sensitive fee
    ///      execution is never permissionless. Anyone can still sweep already-stock-denominated fees.
    function convertFees(PoolKey calldata key, uint256 maxTokens, uint256 minStockOut,
        uint160 sqrtPriceLimitX96, uint256 deadline) external returns (uint256 consumed, uint256 stockOut)
    {
        if (msg.sender != IOwned(factory).owner()) revert NotOwner();
        if (_converting || _distributing) revert Reentered();
        if (block.timestamp > deadline) revert ConversionExpired();
        if (maxTokens == 0 || minStockOut == 0) revert ConversionMinimum();
        PoolId id = key.toId();
        Pool storage p = _pool(id);
        if (liquidityVaultOf[id] == address(0) || address(key.hooks) != address(this)) revert WrongPool();
        _converting = true;
        // This independent sweep also retries existing stock recipients; failures do not strand token fees.
        this.sweep(id);
        uint256 amount = pendingTokenFees[id];
        if (amount > maxTokens) amount = maxTokens;
        if (amount == 0 || amount > uint256(type(int256).max)) revert BadConversion();
        (uint160 current,,,) = poolManager.getSlot0(id);
        bool zeroForOne = p.tokenIsCurrency0;
        _checkLimit(current, sqrtPriceLimitX96, zeroForOne);
        bytes memory data = abi.encode(key, amount, minStockOut, sqrtPriceLimitX96);
        _conversionHash = keccak256(data);
        _distributing = true;
        (consumed, stockOut) = abi.decode(poolManager.unlock(data), (uint256, uint256));
        _distributing = false;
        _conversionHash = bytes32(0);
        _converting = false;
        emit FeesConverted(id, consumed, stockOut, pendingTokenFees[id]);
    }

    function _checkLimit(uint160 current, uint160 limit, bool zeroForOne) private pure {
        if (zeroForOne) {
            uint256 bound = FullMath.mulDiv(current, 10_000 - MAX_CONVERSION_SQRT_MOVE_BPS, 10_000);
            if (limit >= current || limit < bound || limit <= TickMath.MIN_SQRT_PRICE) revert BadConversion();
        } else {
            uint256 bound = FullMath.mulDiv(current, 10_000 + MAX_CONVERSION_SQRT_MOVE_BPS, 10_000);
            if (limit <= current || limit > bound || limit >= TickMath.MAX_SQRT_PRICE) revert BadConversion();
        }
    }

    function unlockCallback(bytes calldata data) public override returns (bytes memory) {
        if (_conversionHash == bytes32(0)) {
            if (data.length != 64) revert NotPoolManager();
            return super.unlockCallback(data);
        }
        if (msg.sender != address(poolManager) || keccak256(data) != _conversionHash) revert NotPoolManager();
        (PoolKey memory key, uint256 amount, uint256 minimum, uint160 limit) =
            abi.decode(data, (PoolKey, uint256, uint256, uint160));
        PoolId id = key.toId();
        Pool storage p = _pool(id);
        // Reconcile prior parked stock losses before any new fee income can reach the stock pot.
        _square(p);
        bool zeroForOne = p.tokenIsCurrency0;
        BalanceDelta delta = poolManager.swap(key, SwapParams({zeroForOne: zeroForOne,
            amountSpecified: -int256(amount), sqrtPriceLimitX96: limit}), "");
        int128 input = zeroForOne ? delta.amount0() : delta.amount1();
        int128 output = zeroForOne ? delta.amount1() : delta.amount0();
        if (input >= 0 || output <= 0) revert BadConversion();
        uint256 consumed = uint256(-int256(input));
        uint256 stockOut = uint256(uint128(output));
        if (consumed > amount || stockOut < minimum) revert ConversionMinimum();
        pendingTokenFees[id] -= consumed;
        // Burn only consumed input claims to settle the debt; unconverted inventory never leaves the manager.
        poolManager.burn(address(this), Currency.wrap(p.token).toId(), consumed);
        // Preserve the legacy stock settlement ledger: output remains an ERC-6909 claim until sweep.
        poolManager.mint(address(this), Currency.wrap(p.stock).toId(), stockOut);
        p.accruedStock += uint128(stockOut);
        // Core skips callbacks for a hook's own swaps, so record the conversion's price explicitly.
        _observe(id, p);
        return abi.encode(consumed, stockOut);
    }
}

// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {IPoolManager} from "v4-core/src/interfaces/IPoolManager.sol";
import {IUnlockCallback} from "v4-core/src/interfaces/callback/IUnlockCallback.sol";
import {PoolKey} from "v4-core/src/types/PoolKey.sol";
import {Currency} from "v4-core/src/types/Currency.sol";
import {ModifyLiquidityParams} from "v4-core/src/types/PoolOperation.sol";
import {BalanceDelta} from "v4-core/src/types/BalanceDelta.sol";
import {TickMath} from "v4-core/src/libraries/TickMath.sol";
import {HedgeFunToken} from "../HedgeFunToken.sol";

interface IV2LiquidityFeeSink {
    function creditLiquidityFee(uint256 amount) external;
}

/// @notice Owns one full-range V4 position. Its principal cannot be removed; anyone can realize its fees.
/// @dev One instance per graduated pool. The factory must register this address as the sole seeder in the hook.
contract V2LiquidityVault is IUnlockCallback {
    using SafeERC20 for IERC20;

    IPoolManager public immutable poolManager;
    address public immutable factory;
    address public immutable token;
    address public immutable stock;
    address public immutable treasury;

    PoolKey private _key;
    bool public seeded;
    uint8 private _mode; // 0 idle, 1 seeding, 2 collecting
    bytes32 private _unlockHash;

    event Seeded(uint128 liquidity, uint256 amount0, uint256 amount1);
    event FeesCollected(uint256 stockToTreasury, uint256 tokenBurned);

    error BadConfig();
    error NotFactory();
    error AlreadySeeded();
    error NotSeeded();
    error Busy();
    error NotPoolManager();
    error UnexpectedDelta();
    error Overspent();
    error InexactTransfer();

    constructor(address factory_, IPoolManager manager_, PoolKey memory key_, address token_, address stock_, address treasury_) {
        if (factory_ == address(0) || address(manager_).code.length == 0 || token_ == address(0)
            || stock_ == address(0) || token_ == stock_ || treasury_ == address(0)
            || address(key_.hooks).code.length == 0 || key_.tickSpacing <= 0
            || Currency.unwrap(key_.currency0) == address(0) || Currency.unwrap(key_.currency0) >= Currency.unwrap(key_.currency1)
            || !((Currency.unwrap(key_.currency0) == token_ && Currency.unwrap(key_.currency1) == stock_)
                || (Currency.unwrap(key_.currency0) == stock_ && Currency.unwrap(key_.currency1) == token_))) revert BadConfig();
        factory = factory_;
        poolManager = manager_;
        token = token_;
        stock = stock_;
        treasury = treasury_;
        _key = key_;
    }

    function poolKey() external view returns (PoolKey memory) { return _key; }

    /// @notice Factory calls after funding this vault with at most max0/max1 and after hook registration.
    /// @dev Returns unused seed budget to the factory in the same transaction. Donations already held here remain here.
    function seed(uint160 sqrtPriceX96, uint128 liquidity, uint256 max0, uint256 max1)
        external returns (uint256 used0, uint256 used1)
    {
        if (msg.sender != factory) revert NotFactory();
        if (seeded) revert AlreadySeeded();
        if (_mode != 0) revert Busy();
        if (liquidity == 0 || max0 == 0 || max1 == 0) revert BadConfig();
        seeded = true;
        _mode = 1;
        PoolKey memory key = _key;
        poolManager.initialize(key, sqrtPriceX96);
        bytes memory callData = abi.encode(uint8(1), liquidity, max0, max1);
        _unlockHash = keccak256(callData);
        (used0, used1) = abi.decode(poolManager.unlock(callData), (uint256, uint256));
        _unlockHash = bytes32(0);
        if (used0 > max0 || used1 > max1) revert Overspent();
        _sendExact(Currency.unwrap(key.currency0), factory, max0 - used0);
        _sendExact(Currency.unwrap(key.currency1), factory, max1 - used1);
        _mode = 0;
        emit Seeded(liquidity, used0, used1);
    }

    /// @notice Realize only the fees earned by this vault's position. Anyone may call; recipients are immutable.
    /// @dev No liquidity removal path exists. The zero-delta operation uses this vault as the V4 position owner.
    function collectFees() external returns (uint256 stockFee, uint256 tokenBurned) {
        if (!seeded) revert NotSeeded();
        if (_mode != 0) revert Busy();
        _mode = 2;
        bytes memory callData = abi.encode(uint8(2), uint128(0), uint256(0), uint256(0));
        _unlockHash = keccak256(callData);
        (uint256 fee0, uint256 fee1) = abi.decode(poolManager.unlock(callData), (uint256, uint256));
        _unlockHash = bytes32(0);
        PoolKey memory key = _key;
        (stockFee, tokenBurned) = Currency.unwrap(key.currency0) == stock ? (fee0, fee1) : (fee1, fee0);
        if (stockFee != 0) {
            IERC20 asset = IERC20(stock);
            uint256 beforeVault = asset.balanceOf(address(this));
            uint256 beforeTreasury = asset.balanceOf(treasury);
            asset.forceApprove(treasury, stockFee);
            IV2LiquidityFeeSink(treasury).creditLiquidityFee(stockFee);
            asset.forceApprove(treasury, 0);
            if (asset.balanceOf(address(this)) != beforeVault - stockFee
                || asset.balanceOf(treasury) != beforeTreasury + stockFee) revert InexactTransfer();
        }
        if (tokenBurned != 0) HedgeFunToken(token).burn(tokenBurned);
        _mode = 0;
        emit FeesCollected(stockFee, tokenBurned);
    }

    function unlockCallback(bytes calldata data) external override returns (bytes memory) {
        if (msg.sender != address(poolManager) || _mode == 0 || keccak256(data) != _unlockHash) revert NotPoolManager();
        (uint8 mode, uint128 liquidity, uint256 max0, uint256 max1) = abi.decode(data, (uint8, uint128, uint256, uint256));
        PoolKey memory key = _key;
        if (mode == 1 && _mode == 1 && liquidity != 0) {
            (BalanceDelta delta,) = poolManager.modifyLiquidity(key, ModifyLiquidityParams({
                tickLower: TickMath.minUsableTick(key.tickSpacing), tickUpper: TickMath.maxUsableTick(key.tickSpacing),
                liquidityDelta: int256(uint256(liquidity)), salt: bytes32(0)
            }), "");
            if (delta.amount0() > 0 || delta.amount1() > 0) revert UnexpectedDelta();
            uint256 used0 = uint256(-int256(delta.amount0()));
            uint256 used1 = uint256(-int256(delta.amount1()));
            if (used0 > max0 || used1 > max1) revert Overspent();
            _settle(key.currency0, used0);
            _settle(key.currency1, used1);
            return abi.encode(used0, used1);
        }
        if (mode == 2 && _mode == 2 && liquidity == 0 && max0 == 0 && max1 == 0) {
            (BalanceDelta delta, BalanceDelta feeDelta) = poolManager.modifyLiquidity(key, ModifyLiquidityParams({
                tickLower: TickMath.minUsableTick(key.tickSpacing), tickUpper: TickMath.maxUsableTick(key.tickSpacing),
                liquidityDelta: 0, salt: bytes32(0)
            }), "");
            if (delta.amount0() != feeDelta.amount0() || delta.amount1() != feeDelta.amount1()
                || delta.amount0() < 0 || delta.amount1() < 0) revert UnexpectedDelta();
            uint256 fee0 = uint256(uint128(delta.amount0()));
            uint256 fee1 = uint256(uint128(delta.amount1()));
            _take(key.currency0, fee0);
            _take(key.currency1, fee1);
            return abi.encode(fee0, fee1);
        }
        revert NotPoolManager();
    }

    function _settle(Currency currency, uint256 amount) private {
        if (amount == 0) return;
        poolManager.sync(currency);
        _sendExact(Currency.unwrap(currency), address(poolManager), amount);
        if (poolManager.settle() != amount) revert InexactTransfer();
    }

    function _take(Currency currency, uint256 amount) private {
        if (amount == 0) return;
        IERC20 asset = IERC20(Currency.unwrap(currency));
        uint256 beforeBalance = asset.balanceOf(address(this));
        poolManager.take(currency, address(this), amount);
        if (asset.balanceOf(address(this)) != beforeBalance + amount) revert InexactTransfer();
    }

    function _sendExact(address currency, address recipient, uint256 amount) private {
        if (amount == 0) return;
        IERC20 asset = IERC20(currency);
        uint256 beforeSender = asset.balanceOf(address(this));
        uint256 beforeRecipient = asset.balanceOf(recipient);
        asset.safeTransfer(recipient, amount);
        if (asset.balanceOf(address(this)) != beforeSender - amount
            || asset.balanceOf(recipient) != beforeRecipient + amount) revert InexactTransfer();
    }
}

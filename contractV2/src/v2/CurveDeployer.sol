// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;
import {BoundDeployer} from "../HedgeFunDeployers.sol";
import {HedgeFunBondingCurve} from "./HedgeFunBondingCurve.sol";
import {V2LiquidityVault} from "./V2LiquidityVault.sol";
import {Math} from "@openzeppelin/contracts/utils/math/Math.sol";
import {TickMath} from "v4-core/src/libraries/TickMath.sol";
import {Pool} from "v4-core/src/libraries/Pool.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {IPoolManager} from "v4-core/src/interfaces/IPoolManager.sol";
import {PoolKey} from "v4-core/src/types/PoolKey.sol";
import {Currency} from "v4-core/src/types/Currency.sol";
import {HedgeFunHook} from "../hooks/HedgeFunHook.sol";
import {HedgeFunV2Treasury} from "./HedgeFunV2Treasury.sol";

interface IV2LpShare { function lpBpsOfTreasury(address treasury) external view returns (uint16); }

interface IV2GraduationView {
    function strategies(uint256 id) external view returns (address token, address treasury, address hook, address stock, address creator);
    function treasuryDeployer() external view returns (address);
    function graduationConfig(uint256 id) external view returns (PoolKey memory key, HedgeFunHook.Rates memory rates);
    function poolManager() external view returns (IPoolManager);
    function protocol() external view returns (address);
}

contract CurveDeployer is BoundDeployer {
    using SafeERC20 for IERC20;
    address private immutable SELF = address(this);
    error CurveDeployFailed();
    error VaultDeployFailed();
    error Unseedable();
    error InexactTransfer();
    struct GraduationCtx {
        PoolKey key;
        HedgeFunHook.Rates rates;
        address token;
        address treasury;
        address hook;
        address stock;
        address creator;
        address vault;
        uint256 max0;
        uint256 max1;
    }
    /// @dev Graduation quotes live here so the factory stays under EIP-170's runtime limit.
    function sqrtPrice(uint256 effectiveStock, uint256 tokens, bool tokenIs0) external pure returns (uint160) {
        uint256 value = Math.sqrt(tokenIs0
            ? Math.mulDiv(effectiveStock, 1 << 192, tokens)
            : Math.mulDiv(tokens, 1 << 192, effectiveStock));
        if (value <= TickMath.MIN_SQRT_PRICE || value >= TickMath.MAX_SQRT_PRICE) revert Unseedable();
        return uint160(value);
    }
    function liquidity(int24 spacing, uint160 price, uint256 amount0, uint256 amount1) external pure returns (uint128) {
        return _liquidity(spacing, price, amount0, amount1);
    }
    function _liquidity(int24 spacing, uint160 price, uint256 amount0, uint256 amount1) private pure returns (uint128) {
        uint160 a = TickMath.getSqrtPriceAtTick(TickMath.minUsableTick(spacing));
        uint160 b = TickMath.getSqrtPriceAtTick(TickMath.maxUsableTick(spacing));
        if (price <= a || price >= b || amount0 < 2 || amount1 < 2
            || amount0 > uint256(uint128(type(int128).max)) || amount1 > uint256(uint128(type(int128).max))) revert Unseedable();
        uint256 l0 = Math.mulDiv(amount0 - 1, Math.mulDiv(price, b, 1 << 96), b - price);
        uint256 l1 = Math.mulDiv(amount1 - 1, 1 << 96, price - a);
        uint256 l = Math.min(l0, l1);
        if (l == 0 || l > Pool.tickSpacingToMaxLiquidityPerTick(spacing) || l > uint256(uint128(type(int128).max))) revert Unseedable();
        return uint128(l);
    }

    /// @dev Called only through the bound factory's delegatecall. The factory is the caller of the
    ///      hook, treasury and deployVault; this module holds no launch funds or mutable authority.
    function executeGraduation(uint256 id, uint160 price, uint256 stockAmount, uint256 tokenAmount)
        external returns (uint128 liquidity_, uint256 stockUsed, uint256 tokenUsed)
    {
        if (address(this) != CurveDeployer(SELF).factory()) revert NotFactory();
        IV2GraduationView v = IV2GraduationView(address(this));
        GraduationCtx memory g;
        (g.token, g.treasury, g.hook, g.stock, g.creator) = v.strategies(id);
        // the LP share was frozen into the treasury's record when the launch deployed it
        uint256 lpStock = stockAmount * IV2LpShare(v.treasuryDeployer()).lpBpsOfTreasury(g.treasury) / 10000;
        (g.key, g.rates) = v.graduationConfig(id);
        bool tokenIs0 = g.token < g.stock;
        (g.max0, g.max1) = tokenIs0 ? (tokenAmount, lpStock) : (lpStock, tokenAmount);
        liquidity_ = _liquidity(g.key.tickSpacing, price, g.max0, g.max1);
        g.vault = CurveDeployer(SELF).deployVault(bytes32(id),
            abi.encode(address(this), v.poolManager(), g.key, g.token, g.stock, g.treasury));
        _seedGraduation(v, g);
        (uint256 used0, uint256 used1) = V2LiquidityVault(g.vault).seed(price, liquidity_, g.max0, g.max1);
        HedgeFunV2Treasury(g.treasury).wire(g.key);
        (stockUsed, tokenUsed) = tokenIs0 ? (used1, used0) : (used0, used1);
    }

    function _seedGraduation(IV2GraduationView v, GraduationCtx memory g) private {
        HedgeFunV2Treasury(g.treasury).setLiquidityVault(g.vault);
        HedgeFunHook(g.hook).registerGraduatedWithVault(g.key, g.token, g.stock, g.treasury, v.protocol(), g.creator, g.rates, g.vault);
        _sendExact(Currency.unwrap(g.key.currency0), g.vault, g.max0);
        _sendExact(Currency.unwrap(g.key.currency1), g.vault, g.max1);
    }

    function _sendExact(address asset, address to, uint256 amount) private {
        IERC20 erc20 = IERC20(asset);
        uint256 senderBefore = erc20.balanceOf(address(this));
        uint256 recipientBefore = erc20.balanceOf(to);
        erc20.safeTransfer(to, amount);
        if (erc20.balanceOf(address(this)) != senderBefore - amount
            || erc20.balanceOf(to) != recipientBefore + amount) revert InexactTransfer();
    }
    function deploy(bytes32 salt, bytes calldata args) external returns (address a) {
        _onlyFactory();
        bytes memory code = abi.encodePacked(type(HedgeFunBondingCurve).creationCode, args);
        assembly { a := create2(0, add(code, 0x20), mload(code), salt) }
        if (a == address(0)) revert CurveDeployFailed();
    }
    function predict(bytes32 salt, bytes calldata args) external view returns (address) {
        return _at(salt, keccak256(abi.encodePacked(type(HedgeFunBondingCurve).creationCode, args)));
    }
    function deployVault(bytes32 salt, bytes calldata args) external returns (address a) {
        _onlyFactory();
        bytes memory code = abi.encodePacked(type(V2LiquidityVault).creationCode, args);
        assembly ("memory-safe") { a := create2(0, add(code, 0x20), mload(code), salt) }
        if (a == address(0)) revert VaultDeployFailed();
    }
    function predictVault(bytes32 salt, bytes calldata args) external view returns (address) {
        return _at(salt, keccak256(abi.encodePacked(type(V2LiquidityVault).creationCode, args)));
    }
}

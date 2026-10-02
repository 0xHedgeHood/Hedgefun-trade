// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {IPoolManager} from "v4-core/src/interfaces/IPoolManager.sol";
import {PoolSwapTest} from "v4-core/src/test/PoolSwapTest.sol";
import {PoolKey} from "v4-core/src/types/PoolKey.sol";
import {PoolId, PoolIdLibrary} from "v4-core/src/types/PoolId.sol";
import {Currency} from "v4-core/src/types/Currency.sol";
import {BalanceDelta} from "v4-core/src/types/BalanceDelta.sol";
import {SwapParams} from "v4-core/src/types/PoolOperation.sol";
import {StateLibrary} from "v4-core/src/libraries/StateLibrary.sol";
import {TickMath} from "v4-core/src/libraries/TickMath.sol";
import {HedgeFunFactory} from "../src/HedgeFunFactory.sol";
import {HedgeFunToken} from "../src/HedgeFunToken.sol";
import {HedgeFunHook} from "../src/hooks/HedgeFunHook.sol";
import {HedgeFunBondingCurve} from "../src/v2/HedgeFunBondingCurve.sol";
import {V2FactoryFixture} from "./utils/V2FactoryFixture.sol";

/// Production factory, curve, hook and PoolManager, in both currency orderings. Buy-token claims are
/// deliberately distinguished from converted stock claims and paid stock throughout these tests.
abstract contract V2TwoSidedFeesBase is V2FactoryFixture {
    using PoolIdLibrary for PoolKey;
    using StateLibrary for IPoolManager;

    PoolSwapTest internal swapRouter;
    HedgeFunBondingCurve internal curve;
    HedgeFunToken internal token;
    PoolKey internal key;
    PoolId internal pid;
    uint96 internal nextNonce;

    function tokenIsCurrency0() internal pure virtual returns (bool);

    function _request() internal view override returns (HedgeFunFactory.Request memory q) {
        q = super._request();
        q.taxBps = 300;
        q.nonce = nextNonce;
    }

    function setUp() public {
        _setUpV2(18);
        (, curve, key) = _launchV2(tokenIsCurrency0());
        _graduateV2(curve);
        token = HedgeFunToken(curve.token());
        pid = key.toId();
        swapRouter = new PoolSwapTest(pm);
        stock.approve(address(swapRouter), type(uint256).max);
        token.approve(address(swapRouter), type(uint256).max);
    }

    function _swap(bool selling, int256 amount) internal returns (BalanceDelta delta) {
        bool zeroForOne = selling == tokenIsCurrency0();
        delta = swapRouter.swap(key, SwapParams({zeroForOne: zeroForOne, amountSpecified: amount,
            sqrtPriceLimitX96: zeroForOne ? TickMath.MIN_SQRT_PRICE + 1 : TickMath.MAX_SQRT_PRICE - 1}),
            PoolSwapTest.TestSettings({takeClaims: false, settleUsingBurn: false}), "");
    }

    function _limit(uint256 sqrtMoveBps) internal view returns (uint160) {
        (uint160 spot,,,) = pm.getSlot0(pid);
        return tokenIsCurrency0()
            ? uint160(uint256(spot) * (10_000 - sqrtMoveBps) / 10_000)
            : uint160(uint256(spot) * (10_000 + sqrtMoveBps) / 10_000);
    }

    function _convert(uint256 amount, uint256 minOut, uint160 limit)
        internal returns (uint256 spent, uint256 received)
    {
        vm.prank(owner);
        return hook.convertFees(key, amount, minOut, limit, block.timestamp);
    }

    function _tokenClaim() internal view returns (uint256) {
        return pm.balanceOf(address(hook), uint256(uint160(address(token))));
    }

    function _stockClaim() internal view returns (uint256) {
        return pm.balanceOf(address(hook), uint256(uint160(address(stock))));
    }

    function _assertSplit(uint256 grossStock, uint256 protocolBefore, uint256 creatorBefore, uint256 treasuryBefore)
        internal view
    {
        uint256 protocolCut = grossStock * 2000 / 10_000;
        uint256 creatorCut = grossStock * 1000 / 10_000;
        assertEq(stock.balanceOf(protocol) - protocolBefore, protocolCut);
        assertEq(stock.balanceOf(address(this)) - creatorBefore, creatorCut);
        assertEq(stock.balanceOf(curve.treasury()) - treasuryBefore, grossStock - protocolCut - creatorCut);
        assertEq(hook.rates(pid).sweepTipBps, 0);
    }

    function testCurveBuyAccruesAndPaysStockToAllThreeRoles() public {
        uint256 gross = curve.terminalStock() - curve.virtualStock();
        uint256 stockFee = curve.totalFees();
        assertGt(stockFee, 0);
        assertEq(curve.claimable(protocol), stockFee * 2000 / 10_000);
        assertEq(curve.claimable(address(this)), stockFee * 1000 / 10_000);
        assertEq(curve.claimable(curve.treasury()), stockFee - stockFee * 2000 / 10_000 - stockFee * 1000 / 10_000);
        assertEq(curve.realStockReserve(), 0, "graduation released principal, not fees");
        assertApproxEqAbs(stockFee * 9700, gross * 300, 10_000);
        uint256 p = stock.balanceOf(protocol);
        uint256 c = stock.balanceOf(address(this));
        uint256 t = stock.balanceOf(curve.treasury());
        curve.claimFees(protocol);
        curve.claimFees(address(this));
        curve.claimFees(curve.treasury());
        _assertSplit(stockFee, p, c, t);
        assertEq(curve.totalFees(), 0);
    }

    function testV4BuyInventoryIsNotBurnedOrReportedAsStockIncome() public {
        uint256 supply = token.totalSupply();
        _swap(false, -int256(1e18));
        (uint256 accruedToken, uint256 accruedStock) = hook.accrued(pid);
        assertGt(accruedToken, 0);
        assertEq(accruedToken, _tokenClaim());
        assertEq(accruedStock, 0);
        assertEq(hook.pendingTokenFees(pid), 0, "claims are not yet physical token inventory");
        uint256 protocolBefore = stock.balanceOf(protocol);
        hook.sweep(pid);
        assertEq(hook.pendingTokenFees(pid), accruedToken);
        assertEq(token.balanceOf(address(hook)), 0);
        assertEq(_tokenClaim(), accruedToken);
        assertEq(_stockClaim(), 0);
        assertEq(stock.balanceOf(protocol), protocolBefore);
        assertEq(token.totalSupply(), supply, "the 3% basic buy fee must survive for conversion");
        hook.sweep(pid);
        assertEq(hook.pendingTokenFees(pid), accruedToken, "a repeated sweep cannot duplicate inventory");
    }

    function testV4BuyConvertsOnceThenPaysExactTwentyTenSeventy() public {
        _swap(false, -int256(1e18));
        (uint256 taxed,) = hook.accrued(pid);
        uint256 supply = token.totalSupply();
        uint256 p = stock.balanceOf(protocol);
        uint256 c = stock.balanceOf(address(this));
        uint256 t = stock.balanceOf(curve.treasury());
        (uint256 actualIn, uint256 stockOut) = _convert(taxed, 1, _limit(50));
        assertEq(actualIn, taxed);
        assertGt(stockOut, 0);
        assertEq(token.balanceOf(address(hook)), 0);
        assertEq(hook.pendingTokenFees(pid), 0);
        assertEq(_stockClaim(), stockOut);
        (, uint256 accruedStock) = hook.accrued(pid);
        assertEq(accruedStock, stockOut);
        assertEq(stock.balanceOf(protocol), p, "conversion has produced claims, not yet paid cash");
        assertEq(token.totalSupply(), supply);
        hook.sweep(pid);
        _assertSplit(stockOut, p, c, t);
        assertEq(_stockClaim(), 0);
        (uint256 at, uint256 ast) = hook.accrued(pid);
        assertEq(at + ast, 0);
        assertEq(hook.owedProtocol(pid) + hook.owedCreator(pid) + hook.owedTreasury(pid), 0);
    }

    function testV4SellPaysExactTwentyTenSeventyWithoutConversion() public {
        _swap(true, -int256(100e18));
        (uint256 at, uint256 stockFee) = hook.accrued(pid);
        assertEq(at, 0);
        assertGt(stockFee, 0);
        uint256 p = stock.balanceOf(protocol);
        uint256 c = stock.balanceOf(address(this));
        uint256 t = stock.balanceOf(curve.treasury());
        hook.sweep(pid);
        _assertSplit(stockFee, p, c, t);
        assertEq(hook.pendingTokenFees(pid), 0);
        assertEq(_stockClaim(), 0);
    }

    function testExactOutputBuyStillPaysInStock() public {
        _swap(false, int256(100e18));
        (uint256 at, uint256 stockFee) = hook.accrued(pid);
        assertEq(at, 0);
        assertGt(stockFee, 0);
        uint256 p = stock.balanceOf(protocol);
        uint256 c = stock.balanceOf(address(this));
        uint256 t = stock.balanceOf(curve.treasury());
        hook.sweep(pid);
        _assertSplit(stockFee, p, c, t);
    }

    function testOnlyFactoryOwnerCanConvertAndCallbackCannotBeSpoofed() public {
        _swap(false, -int256(1e18));
        (uint256 taxed,) = hook.accrued(pid);
        uint160 limit = _limit(50);
        vm.expectRevert();
        hook.convertFees(key, taxed, 1, limit, block.timestamp);
        vm.expectRevert(HedgeFunHook.NotPoolManager.selector);
        hook.unlockCallback(abi.encode(key, taxed));
        vm.prank(address(pm));
        vm.expectRevert();
        hook.unlockCallback(abi.encode(key, taxed));
        (uint256 remaining,) = hook.accrued(pid);
        assertEq(remaining, taxed);
        assertEq(hook.pendingTokenFees(pid), 0);
    }

    function testDeadlineMinimumOutputAndPoolIdentityFailClosed() public {
        _swap(false, -int256(1e18));
        (uint256 taxed,) = hook.accrued(pid);
        uint160 limit = _limit(50);
        vm.startPrank(owner);
        vm.expectRevert();
        hook.convertFees(key, taxed, 1, limit, block.timestamp - 1);
        vm.expectRevert();
        hook.convertFees(key, taxed, 0, limit, block.timestamp);
        vm.expectRevert();
        hook.convertFees(key, taxed, type(uint256).max, limit, block.timestamp);
        PoolKey memory wrong = key;
        wrong.fee += 1;
        vm.expectRevert();
        hook.convertFees(wrong, taxed, 1, limit, block.timestamp);
        vm.stopPrank();
        (uint256 remaining, uint256 cash) = hook.accrued(pid);
        assertEq(remaining, taxed);
        assertEq(cash, 0);
        assertEq(hook.pendingTokenFees(pid), 0, "failed conversion also rolls back materialization");
    }

    function testConversionRejectsLimitsBeyondHalfPercentSqrtMovement() public {
        _swap(false, -int256(1e18));
        (uint256 taxed,) = hook.accrued(pid);
        uint160 tooFar = _limit(51);
        (uint160 spot,,,) = pm.getSlot0(pid);
        uint160 wrongDirection = tokenIsCurrency0() ? spot + 1 : spot - 1;
        vm.startPrank(owner);
        vm.expectRevert();
        hook.convertFees(key, taxed, 1, tooFar, block.timestamp);
        vm.expectRevert();
        hook.convertFees(key, taxed, 1, wrongDirection, block.timestamp);
        vm.stopPrank();
        (uint256 remaining,) = hook.accrued(pid);
        assertEq(remaining, taxed);
        _convert(taxed, 1, _limit(50));
    }

    function testPartialFillLeavesInventoryAndCanBeRetried() public {
        _swap(false, -int256(40e18));
        (uint256 taxed,) = hook.accrued(pid);
        uint256 supply = token.totalSupply();
        uint256 p = stock.balanceOf(protocol);
        uint256 c = stock.balanceOf(address(this));
        uint256 t = stock.balanceOf(curve.treasury());
        (uint256 actualIn, uint256 stockOut) = _convert(taxed, 1, _limit(1));
        assertGt(actualIn, 0);
        assertLt(actualIn, taxed);
        assertGt(stockOut, 0);
        assertEq(hook.pendingTokenFees(pid), taxed - actualIn);
        assertEq(token.balanceOf(address(hook)), 0);
        assertEq(_tokenClaim(), taxed - actualIn);
        uint256 converted = stockOut;
        for (uint256 i; i < 10 && hook.pendingTokenFees(pid) != 0; ++i) {
            uint256 remaining = hook.pendingTokenFees(pid);
            (uint256 used, uint256 out) = _convert(remaining, 1, _limit(50));
            assertGt(used, 0);
            assertEq(hook.pendingTokenFees(pid), remaining - used);
            converted += out;
        }
        assertEq(hook.pendingTokenFees(pid), 0);
        assertEq(stock.balanceOf(protocol) - p + stock.balanceOf(address(this)) - c
            + stock.balanceOf(curve.treasury()) - t + _stockClaim(), converted,
            "each retry first distributes the previous batch, preserving the total");
        assertEq(token.balanceOf(address(hook)), 0);
        assertEq(token.totalSupply(), supply);
        hook.sweep(pid);
        // Floors apply independently to each converted batch, within one raw wei per batch.
        assertApproxEqAbs(stock.balanceOf(protocol) - p, converted * 2000 / 10_000, 11);
        assertApproxEqAbs(stock.balanceOf(address(this)) - c, converted * 1000 / 10_000, 11);
        assertEq(stock.balanceOf(protocol) - p + stock.balanceOf(address(this)) - c
            + stock.balanceOf(curve.treasury()) - t, converted);
    }

    function testRejectedStockPayoutPreservesRoleCreditAndRetryDoesNotDoubleSplit() public {
        _swap(false, -int256(1e18));
        (uint256 taxed,) = hook.accrued(pid);
        (, uint256 stockOut) = _convert(taxed, 1, _limit(50));
        stock.blockRecipient(protocol);
        uint256 c = stock.balanceOf(address(this));
        uint256 t = stock.balanceOf(curve.treasury());
        hook.sweep(pid);
        uint256 cut = stockOut * 2000 / 10_000;
        assertEq(hook.owedProtocol(pid), cut);
        assertEq(stock.balanceOf(protocol), 0);
        assertEq(stock.balanceOf(address(this)) - c, stockOut * 1000 / 10_000);
        assertEq(stock.balanceOf(curve.treasury()) - t, stockOut - cut - stockOut * 1000 / 10_000);
        hook.sweep(pid);
        assertEq(hook.owedProtocol(pid), cut);
        assertEq(stock.balanceOf(address(this)) - c, stockOut * 1000 / 10_000);
        stock.blockRecipient(address(0));
        hook.sweep(pid);
        assertEq(stock.balanceOf(protocol), cut);
        assertEq(hook.owedProtocol(pid), 0);
    }

    function testConverterIsObservedForPoolHistoryButNotTaxedAgain() public {
        _swap(false, -int256(1e18));
        (uint256 taxed,) = hook.accrued(pid);
        uint256 before = hook.observationCountOf(pid);
        vm.warp(block.timestamp + 1);
        (, uint256 stockOut) = _convert(taxed, 1, _limit(50));
        assertEq(hook.observationCountOf(pid), before + 1);
        (, uint256 cash) = hook.accrued(pid);
        assertEq(cash, stockOut, "conversion produces stock, without adding another 3% tax");
        assertEq(hook.pendingTokenFees(pid), 0);
    }

    function testConversionCannotSpendDonatedTokens() public {
        _swap(false, -int256(1e18));
        (uint256 taxed,) = hook.accrued(pid);
        token.transfer(address(hook), 123e18);
        (uint256 used,) = _convert(type(uint256).max, 1, _limit(50));
        assertEq(used, taxed);
        assertEq(token.balanceOf(address(hook)), 123e18);
        assertEq(hook.pendingTokenFees(pid), 0);
        assertEq(_tokenClaim(), 0);
    }

    function testTwoPoolsOnSameStockKeepTheirFeesSeparate() public {
        _swap(false, -int256(1e18));
        (uint256 firstTax,) = hook.accrued(pid);
        hook.sweep(pid);
        nextNonce = lastNonce + 1;
        (, HedgeFunBondingCurve secondCurve, PoolKey memory secondKey) = _launchV2(tokenIsCurrency0());
        _graduateV2(secondCurve);
        PoolId secondId = secondKey.toId();
        swapRouter.swap(secondKey, SwapParams({zeroForOne: !tokenIsCurrency0(), amountSpecified: -int256(1e18),
            sqrtPriceLimitX96: tokenIsCurrency0() ? TickMath.MAX_SQRT_PRICE - 1 : TickMath.MIN_SQRT_PRICE + 1}),
            PoolSwapTest.TestSettings({takeClaims: false, settleUsingBurn: false}), "");
        (uint256 secondTax,) = hook.accrued(secondId);
        hook.sweep(secondId);
        assertGt(secondTax, 0);
        assertEq(hook.pendingTokenFees(pid), firstTax);
        assertEq(hook.pendingTokenFees(secondId), secondTax);
        (, uint256 stockOut) = _convert(firstTax, 1, _limit(50));
        assertEq(hook.pendingTokenFees(secondId), secondTax);
        assertEq(pm.balanceOf(address(hook), uint256(uint160(secondCurve.token()))), secondTax);
        uint256 p = stock.balanceOf(protocol);
        hook.sweep(secondId);
        assertEq(stock.balanceOf(protocol), p, "pool 2 cannot redeem pool 1's shared-stock claims");
        assertEq(_stockClaim(), stockOut);
        (, uint256 secondCash) = hook.accrued(secondId);
        assertEq(secondCash, 0);
    }

    function testStockRedemptionFailureDoesNotStrandPendingBuyClaims() public {
        _swap(false, -int256(1e18));
        _swap(true, -int256(100e18));
        (uint256 taxed, uint256 oldStockFees) = hook.accrued(pid);
        assertGt(taxed, 0);
        assertGt(oldStockFees, 0);
        stock.blockRecipient(address(hook));
        hook.sweep(pid);
        (uint256 tokenAccrued, uint256 stockAccrued) = hook.accrued(pid);
        assertEq(tokenAccrued, 0);
        assertEq(stockAccrued, oldStockFees);
        assertEq(hook.pendingTokenFees(pid), taxed);
        assertEq(_tokenClaim(), taxed);
        (, uint256 stockOut) = _convert(taxed, 1, _limit(50));
        assertEq(hook.pendingTokenFees(pid), 0);
        assertEq(_stockClaim(), oldStockFees + stockOut);
        stock.blockRecipient(address(0));
        uint256 p = stock.balanceOf(protocol);
        uint256 c = stock.balanceOf(address(this));
        uint256 t = stock.balanceOf(curve.treasury());
        hook.sweep(pid);
        _assertSplit(oldStockFees + stockOut, p, c, t);
        assertEq(_stockClaim(), 0);
    }

    function testFactoryOwnershipTransferChangesConverterAuthorization() public {
        _swap(false, -int256(1e18));
        (uint256 taxed,) = hook.accrued(pid);
        address successor = address(0xA0B0C0);
        vm.prank(owner);
        factory.transferOwnership(successor);
        vm.prank(successor);
        factory.acceptOwnership();
        uint160 limit = _limit(50);
        vm.prank(owner);
        vm.expectRevert(HedgeFunHook.NotOwner.selector);
        hook.convertFees(key, taxed, 1, limit, block.timestamp);
        vm.prank(successor);
        (uint256 used,) = hook.convertFees(key, taxed, 1, limit, block.timestamp);
        assertEq(used, taxed);
    }
}

contract V2TwoSidedFeesToken0Test is V2TwoSidedFeesBase {
    function tokenIsCurrency0() internal pure override returns (bool) { return true; }
}

contract V2TwoSidedFeesToken1Test is V2TwoSidedFeesBase {
    function tokenIsCurrency0() internal pure override returns (bool) { return false; }
}

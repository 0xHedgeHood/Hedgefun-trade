// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {stdStorage, StdStorage} from "forge-std/StdStorage.sol";
import {Math} from "@openzeppelin/contracts/utils/math/Math.sol";
import {IPoolManager} from "v4-core/src/interfaces/IPoolManager.sol";
import {IUnlockCallback} from "v4-core/src/interfaces/callback/IUnlockCallback.sol";
import {PoolKey} from "v4-core/src/types/PoolKey.sol";
import {PoolId, PoolIdLibrary} from "v4-core/src/types/PoolId.sol";
import {Currency} from "v4-core/src/types/Currency.sol";
import {ModifyLiquidityParams, SwapParams} from "v4-core/src/types/PoolOperation.sol";
import {BalanceDelta} from "v4-core/src/types/BalanceDelta.sol";
import {StateLibrary} from "v4-core/src/libraries/StateLibrary.sol";
import {TickMath} from "v4-core/src/libraries/TickMath.sol";
import {HedgeFunFactory} from "../src/HedgeFunFactory.sol";
import {HedgeFunV2Factory} from "../src/v2/HedgeFunV2Factory.sol";
import {HedgeFunBondingCurve} from "../src/v2/HedgeFunBondingCurve.sol";
import {V2LiquidityVault} from "../src/v2/V2LiquidityVault.sol";
import {HedgeFunHook} from "../src/hooks/HedgeFunHook.sol";
import {HedgeFunTreasuryBase} from "../src/HedgeFunTreasuryBase.sol";
import {HedgeFunV2Treasury} from "../src/v2/HedgeFunV2Treasury.sol";
import {V2TreasuryDeployer} from "../src/v2/V2TreasuryDeployer.sol";
import {V2FactoryFixture} from "./utils/V2FactoryFixture.sol";

contract V2FactoryTest is V2FactoryFixture, IUnlockCallback {
    using stdStorage for StdStorage;
    using PoolIdLibrary for PoolKey;
    using StateLibrary for IPoolManager;

    function setUp() public { _setUpV2(18); }

    function _checkGraduation(bool tokenIs0) internal {
        (, HedgeFunBondingCurve curve, PoolKey memory key) = _launchV2(tokenIs0);
        uint256 targetStock = curve.terminalStock() - curve.virtualStock();
        uint256 remaining = curve.minTokenReserve();
        uint256 supplyBefore = IERC20(curve.token()).totalSupply();
        uint160 expectedPrice = uint160(Math.sqrt(tokenIs0
            ? Math.mulDiv(curve.terminalStock(), 1 << 192, remaining)
            : Math.mulDiv(remaining, 1 << 192, curve.terminalStock())));
        _graduateV2(curve);
        (uint160 sqrtPrice,,,) = pm.getSlot0(key.toId());
        assertEq(sqrtPrice, expectedPrice, "migration must retain terminal spot price");
        assertGt(pm.getLiquidity(key.toId()), 0);
        assertEq(HedgeFunTreasuryBase(curve.treasury()).hook(), address(hook));
        uint256 pmStock = stock.balanceOf(address(pm));
        uint256 pmToken = IERC20(curve.token()).balanceOf(address(pm));
        assertApproxEqAbs(pmStock, targetStock / 2, 2, "half real stock principal seeds V4 except rounding dust");
        assertApproxEqAbs(pmToken, Math.mulDiv(remaining, targetStock / 2, curve.terminalStock()),
            Math.max(100_000_000, 2 * remaining / curve.terminalStock()),
            "excess virtual-liquidity tokens must not enter LP");
        assertApproxEqAbs(stock.balanceOf(curve.treasury()), targetStock - pmStock, 2,
            "other half becomes strategy capital");
        address vault = hook.liquidityVaultOf(key.toId());
        assertEq(HedgeFunTreasuryBase(curve.treasury()).hook(), address(hook));
        assertGt(vault.code.length, 0);
        assertLt(IERC20(curve.token()).totalSupply(), supplyBefore);
        assertEq(stock.balanceOf(address(factory)), 0);
        assertEq(IERC20(curve.token()).balanceOf(address(factory)), 0);
        assertEq(stock.balanceOf(address(curve)), 0);
        assertEq(IERC20(curve.token()).balanceOf(address(curve)), 0);
        assertEq(hook.protocolOf(key.toId()), protocol);
        vm.expectRevert(HedgeFunV2Factory.NotReady.selector);
        factory.graduateCurve();
        vm.expectRevert(HedgeFunV2Factory.NotReady.selector);
        factory.graduate(0);
    }

    function test_graduatesRealV4Token0Stock18Decimals() public { _checkGraduation(true); }
    function test_graduatesRealV4Token1Stock18Decimals() public { _checkGraduation(false); }
    function test_graduatesRealV4Token0Stock6Decimals() public { _setUpV2(6); _checkGraduation(true); }
    function test_graduatesRealV4Token1Stock6Decimals() public { _setUpV2(6); _checkGraduation(false); }

    function test_launchAndGraduationUseFrozenTerms() public {
        (uint256 id, HedgeFunBondingCurve curve, PoolKey memory key) = _launchV2(true);
        bytes32 frozen;
        { (PoolKey memory k, HedgeFunHook.Rates memory r) = factory.graduationConfig(id); frozen = keccak256(abi.encode(k, r)); }
        HedgeFunFactory.Defaults memory d = _defaults();
        d.tickSpacing = 10;
        d.protocolBps = 3000;
        d.spikeBps = 8000;
        d.supply = 2_000_000e18;
        vm.startPrank(owner);
        factory.setDefaults(d);
        factory.setSaleBps(address(stock), 9000);
        factory.list(address(stock), address(oracle), address(stockPool), openPrice * 2, false);
        vm.stopPrank();
        _graduateV2(curve);
        (PoolKey memory actualKey, HedgeFunHook.Rates memory actualRates) = factory.graduationConfig(id);
        assertEq(keccak256(abi.encode(actualKey, actualRates)), frozen);
        assertEq(hook.rates(key.toId()).protocolBps, 2000);
        assertEq(curve.initialSupply(), 1_000_000e18);
        assertEq(curve.minTokenReserve(), 200_000e18);
        assertEq(curve.snipeBps(), 9900);
        assertEq(curve.snipeSeconds(), 3);
    }

    function test_curveOpeningBuyTaxDecaysAndAppliesToCreator() public {
        (, HedgeFunBondingCurve curve,) = _launchV2(true);
        uint256 start = block.timestamp;
        assertEq(curve.launchedAt(), start);
        assertEq(curve.buyRateBps(), 9900);
        (uint256 spent, uint256 out, uint256 burned) = curve.quoteBuy(10e18);
        assertEq(burned, (out + burned) * 9900 / 10000);
        uint256 supply = IERC20(curve.token()).totalSupply();
        // The fixture's creator buys in the launch timestamp. V2 has no privileged recipient.
        (uint256 actualSpent, uint256 actualOut) = curve.buy(10e18, out, address(this), block.timestamp);
        assertEq(actualSpent, spent);
        assertEq(actualOut, out);
        assertEq(supply - IERC20(curve.token()).totalSupply(), burned);

        HedgeFunFactory.Defaults memory d = _defaults();
        d.snipeBps = 5000;
        d.snipeSeconds = 10;
        vm.prank(owner);
        factory.setDefaults(d);
        assertEq(curve.snipeBps(), 9900, "existing launch retains frozen rate");
        assertEq(curve.snipeSeconds(), 3, "existing launch retains frozen window");
        vm.warp(start + 1); assertEq(curve.buyRateBps(), 6600);
        vm.warp(start + 2); assertEq(curve.buyRateBps(), 3300);
        vm.warp(start + 3); assertEq(curve.buyRateBps(), curve.taxBps());
    }

    function test_quoteBecomesStaleWhenCurveTermsChange() public {
        HedgeFunFactory.Request memory q = _request();
        (,, bytes32 terms) = factory.predict(q);
        vm.prank(owner);
        factory.setSaleBps(address(stock), 9000);
        vm.expectRevert(HedgeFunFactory.Restated.selector);
        factory.launch(q, terms);
    }

    function test_tinyStopIsRejectedAtQuoteAndLaunch() public {
        HedgeFunFactory.Request memory q = _request();
        q.stopBps = 1;
        vm.expectRevert(abi.encodeWithSelector(V2TreasuryDeployer.StopInsideExecutionFriction.selector, 1, 180));
        factory.predict(q);
        vm.expectRevert(abi.encodeWithSelector(V2TreasuryDeployer.StopInsideExecutionFriction.selector, 1, 180));
        factory.launch(q, bytes32(0));
        q.stopBps = 180;
        vm.expectRevert(abi.encodeWithSelector(V2TreasuryDeployer.StopInsideExecutionFriction.selector, 180, 180));
        factory.predict(q);
        q.stopBps = 181;
        (,, bytes32 terms) = factory.predict(q);
        factory.launch(q, terms);
    }

    function test_graduationDoesNotRestartLaunchTaxButBuybackSpikeStillWorks() public {
        (, HedgeFunBondingCurve curve, PoolKey memory key) = _launchV2(true);
        _graduateV2(curve);
        assertEq(hook.buyRateBps(key.toId()), curve.taxBps());
        assertEq(hook.sellRateBps(key.toId()), curve.taxBps());
        assertEq(hook.lastEventAt(key.toId()), 0);
        HedgeFunHook.Rates memory rates = hook.rates(key.toId());
        assertEq(rates.snipeBps, 0);
        assertEq(rates.snipeSeconds, 0);
        assertEq(rates.spikeBps, 9000);
        vm.prank(curve.treasury());
        hook.noteEvent();
        assertEq(hook.sellRateBps(key.toId()), 9000);
        assertEq(hook.lastEventAt(key.toId()), block.timestamp);
        vm.warp(block.timestamp + 120);
        assertEq(hook.sellRateBps(key.toId()), curve.taxBps());
    }

    function test_graduatedRegistrationIsFactoryOnly() public {
        (, HedgeFunBondingCurve curve, PoolKey memory key) = _launchV2(true);
        (, HedgeFunHook.Rates memory rates) = factory.graduationConfig(0);
        address token = curve.token();
        address treasury = curve.treasury();
        vm.expectRevert(HedgeFunHook.NotFactory.selector);
        hook.registerGraduated(key, token, address(stock), treasury, protocol, address(this), rates);
    }

    function test_graduationFeesAndDonationsNeverSeedThePool() public {
        (, HedgeFunBondingCurve curve, PoolKey memory key) = _launchV2(true);
        (, uint256 bought) = curve.buy(10e18, 1, address(this), block.timestamp);
        curve.sell(bought / 2, 1, address(this), block.timestamp);
        uint256 fees = curve.totalFees();
        uint256 treasuryFee = curve.claimable(curve.treasury());
        assertGt(fees, 0);
        stock.transfer(address(curve), 7e18);
        stock.transfer(address(factory), 11e18);
        uint256 principal = curve.terminalStock() - curve.virtualStock();
        _graduateV2(curve);
        assertApproxEqAbs(stock.balanceOf(address(pm)), principal / 2, 2);
        assertEq(stock.balanceOf(address(factory)), 11e18);
        assertEq(stock.balanceOf(address(curve)), fees + 7e18);
        assertEq(curve.totalFees(), fees);
        assertGt(pm.getLiquidity(key.toId()), 0);
        uint256 beforeBalance = stock.balanceOf(curve.treasury());
        curve.claimFees(curve.treasury());
        assertEq(stock.balanceOf(curve.treasury()), beforeBalance + treasuryFee);
        assertEq(stock.balanceOf(address(curve)), fees + 7e18 - treasuryFee);
    }

    function test_failedSeedRollsBackFinalBuyAndCurveRemainsSellable() public {
        (, HedgeFunBondingCurve curve, PoolKey memory key) = _launchV2(true);
        (, uint256 bought) = curve.buy(10e18, 1, address(this), block.timestamp);
        uint256 reserve = curve.realStockReserve();
        uint256 tokens = curve.tokenReserve();
        uint256 wallet = stock.balanceOf(address(this));
        uint256 supply = IERC20(curve.token()).totalSupply();
        stock.blockRecipient(address(pm));
        vm.expectRevert(bytes("blocked recipient"));
        curve.buy(type(uint256).max, 1, address(this), block.timestamp);
        assertEq(uint256(curve.status()), uint256(HedgeFunBondingCurve.Status.Active));
        assertEq(curve.realStockReserve(), reserve);
        assertEq(curve.tokenReserve(), tokens);
        assertEq(stock.balanceOf(address(this)), wallet);
        assertEq(IERC20(curve.token()).totalSupply(), supply);
        assertEq(HedgeFunTreasuryBase(curve.treasury()).hook(), address(0));
        assertFalse(hook.isRegistered(key.toId()));
        (uint160 price,,,) = pm.getSlot0(key.toId());
        assertEq(price, 0);
        assertGt(curve.sell(bought / 2, 1, address(this), block.timestamp), 0);
        stock.blockRecipient(address(0));
        _graduateV2(curve);
    }

    function test_factorySenderFeeCannotConsumeDonationsDuringGraduation() public {
        (, HedgeFunBondingCurve curve, PoolKey memory key) = _launchV2(true);
        stock.transfer(address(factory), 1e18);
        stock.taxSender(address(factory));
        vm.expectRevert(bytes4(keccak256("InexactTransfer()")));
        curve.buy(type(uint256).max, 1, address(this), block.timestamp);
        assertEq(stock.balanceOf(address(factory)), 1e18);
        assertEq(uint256(curve.status()), uint256(HedgeFunBondingCurve.Status.Active));
        assertFalse(hook.isRegistered(key.toId()));
    }

    function test_runtimeAndInitcodeFitEvmLimits() public view {
        assertLe(address(factory).code.length, 24_576);
        assertLe(address(hook).code.length, 24_576);
        bytes memory args = abi.encode(owner, address(pm), address(v3f), address(usdg), protocol,
            address(factory.treasuryDeployer()), address(factory.tokenDeployer()), address(hook),
            address(factory.curveDeployer()), _defaults());
        assertLe(type(HedgeFunV2Factory).creationCode.length + args.length, 49_152);
    }

    function test_seededPositionCannotBeRemovedByCreatorOrCallbackReplay() public {
        (, HedgeFunBondingCurve curve, PoolKey memory key) = _launchV2(true);
        _graduateV2(curve);
        address vault = hook.liquidityVaultOf(key.toId());
        (uint128 liquidity,,) = pm.getPositionInfo(key.toId(), vault,
            TickMath.minUsableTick(key.tickSpacing), TickMath.maxUsableTick(key.tickSpacing), bytes32(0));
        assertGt(liquidity, 0);
        vm.expectRevert();
        pm.unlock(abi.encode(key, uint256(0)));
        vm.prank(address(pm));
        vm.expectRevert(V2LiquidityVault.NotPoolManager.selector);
        V2LiquidityVault(vault).unlockCallback(abi.encode(key, liquidity, 0, 0));
        (uint128 afterLiquidity,,) = pm.getPositionInfo(key.toId(), vault,
            TickMath.minUsableTick(key.tickSpacing), TickMath.maxUsableTick(key.tickSpacing), bytes32(0));
        assertEq(afterLiquidity, liquidity);
    }

    function test_treasuryWaitsForGraduationBeforeBookingOrTrading() public {
        (, HedgeFunBondingCurve curve,) = _launchV2(true);
        HedgeFunTreasuryBase treasury = HedgeFunTreasuryBase(curve.treasury());
        stock.transfer(address(treasury), 2e18);
        usdg.mint(address(treasury), 100e6);
        assertFalse(treasury.book());
        assertEq(treasury.lotCount(), 0);
        vm.expectRevert(HedgeFunTreasuryBase.Unhealthy.selector);
        HedgeFunV2Treasury(address(treasury)).execute();
        vm.expectRevert(HedgeFunV2Treasury.UseExecute.selector);
        treasury.takeProfit(0);
        vm.expectRevert(HedgeFunV2Treasury.UseExecute.selector);
        treasury.stopLoss(0);
        vm.expectRevert(HedgeFunV2Treasury.UseExecute.selector);
        treasury.buyDip();
        _graduateV2(curve);
        // Graduation may already book dust plus donations. Either way the first lot now exists.
        treasury.book();
        assertEq(treasury.lotCount(), 1);
        assertEq(usdg.balanceOf(address(treasury)), 100e6);
    }

    function _checkGraduationAnchor(bool tokenIs0) internal {
        (, HedgeFunBondingCurve curve, PoolKey memory key) = _launchV2(tokenIs0);
        _graduateV2(curve);
        HedgeFunTreasuryBase treasury = HedgeFunTreasuryBase(curve.treasury());
        (uint160 graduationPrice,,,) = pm.getSlot0(key.toId());
        assertEq(treasury.buybackAnchorSqrtP(), graduationPrice);
        assertEq(treasury.buybackAnchorAt(), block.timestamp);
        // Give it realised-profit accounting so this checks price protection, not an empty pot.
        stdstore.target(address(treasury)).sig("buybackStock()").checked_write(1e18);
        stock.transfer(address(treasury), 1e18);
        uint256 snapshot = vm.snapshotState();
        // A first-block price shove cannot replace the trusted graduation anchor before TWAP exists.
        pm.unlock(abi.encode(key, uint256(50e18)));
        (bool ready,) = hook.meanTickOf(key.toId(), 600);
        assertFalse(ready);
        vm.expectRevert(HedgeFunTreasuryBase.NotDue.selector);
        treasury.buyback();
        assertEq(treasury.buybackAnchorSqrtP(), graduationPrice);
        assertEq(treasury.buybackStock(), 1e18);
        vm.revertToState(snapshot);
        // At the actual graduation price a first buyback can execute safely without a warm-up wait.
        (uint256 spent, uint256 burned) = treasury.buyback();
        assertGt(spent, 0);
        assertGt(burned, 0);
    }

    function test_token0GraduationAnchorProtectsFirstBuyback() public { _checkGraduationAnchor(true); }
    function test_token1GraduationAnchorProtectsFirstBuyback() public { _checkGraduationAnchor(false); }

    function unlockCallback(bytes calldata data) external returns (bytes memory) {
        require(msg.sender == address(pm));
        (PoolKey memory key, uint256 amount) = abi.decode(data, (PoolKey, uint256));
        if (amount == 0) {
            pm.modifyLiquidity(key, ModifyLiquidityParams({tickLower: TickMath.minUsableTick(key.tickSpacing),
                tickUpper: TickMath.maxUsableTick(key.tickSpacing), liquidityDelta: -1, salt: bytes32(0)}), "");
        } else {
            bool zeroForOne = Currency.unwrap(key.currency0) == address(stock);
            BalanceDelta delta = pm.swap(key, SwapParams({zeroForOne: zeroForOne, amountSpecified: -int256(amount),
                sqrtPriceLimitX96: zeroForOne ? TickMath.MIN_SQRT_PRICE + 1 : TickMath.MAX_SQRT_PRICE - 1}), "");
            uint256 spent = uint256(-int256(zeroForOne ? delta.amount0() : delta.amount1()));
            uint256 received = uint256(int256(zeroForOne ? delta.amount1() : delta.amount0()));
            pm.sync(Currency.wrap(address(stock)));
            stock.transfer(address(pm), spent);
            pm.settle();
            pm.take(zeroForOne ? key.currency1 : key.currency0, address(this), received);
        }
        return "";
    }
}

// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {PoolKey} from "v4-core/src/types/PoolKey.sol";
import {PoolIdLibrary} from "v4-core/src/types/PoolId.sol";
import {HedgeFunFactory} from "../src/HedgeFunFactory.sol";
import {HedgeFunTreasuryBase} from "../src/HedgeFunTreasuryBase.sol";
import {HedgeFunBondingCurve} from "../src/v2/HedgeFunBondingCurve.sol";
import {HedgeFunV2Treasury} from "../src/v2/HedgeFunV2Treasury.sol";
import {HedgeFunV2TradeRouter as Router} from "../src/v2/HedgeFunV2TradeRouter.sol";
import {HedgeFunV2IncomeTreasury} from "../src/v2/HedgeFunV2IncomeTreasury.sol";
import {V2StakingIncome} from "../src/v2/V2StakingIncome.sol";
import {V2LiquidityVault} from "../src/v2/V2LiquidityVault.sol";
import {V2TreasuryDeployer} from "../src/v2/V2TreasuryDeployer.sol";
import {RegisterV2IncomeKinds} from "../script/RegisterV2IncomeKinds.s.sol";
import {V2FactoryFixture} from "./utils/V2FactoryFixture.sol";

/// The income kinds through the whole lifecycle: registered by the owner, chosen by the creator, launched,
/// graduated, and then paying their post-graduation income to stakers and the buy-back in a fixed ratio.
contract V2IncomeKindsTest is V2FactoryFixture {
    using PoolIdLibrary for PoolKey;

    V2TreasuryDeployer internal deployer;
    Router internal router;
    RegisterV2IncomeKinds internal script;
    uint8 internal dividendKind;
    uint8 internal splitKind;
    uint96 internal nonce;
    address internal alice = address(0xA71CE);

    struct Launch {
        uint256 id;
        HedgeFunBondingCurve curve;
        HedgeFunV2IncomeTreasury treasury;
        V2StakingIncome staking;
        IERC20 token;
        PoolKey key;
    }

    function setUp() public {
        _setUpV2(18);
        deployer = V2TreasuryDeployer(address(factory.treasuryDeployer()));
        router = new Router(factory);
        // the operator script is how an existing factory gets these kinds; the fixture is that factory
        script = new RegisterV2IncomeKinds();
        (dividendKind, splitKind) = script.register(owner, factory);
        assertEq(dividendKind, 1);
        assertEq(splitKind, 2);
        stock.approve(address(router), type(uint256).max);
    }

    function _launch(uint8 kind) internal returns (Launch memory l) {
        HedgeFunFactory.Request memory q = _request();
        q.nonce = ++nonce;
        deployer.setStrategyKind(q.symbol, q.nonce, kind);
        (,, bytes32 terms) = factory.predict(q);
        l.id = factory.launch(q, terms);
        l.curve = HedgeFunBondingCurve(factory.curves(l.id));
        (, address t,,,) = factory.strategies(l.id);
        l.treasury = HedgeFunV2IncomeTreasury(t);
        l.staking = l.treasury.staking();
        l.token = IERC20(l.curve.token());
        (l.key,) = factory.graduationConfig(l.id);
        stock.approve(address(l.curve), type(uint256).max);
        l.token.approve(address(router), type(uint256).max);
        vm.warp(l.curve.launchedAt() + l.curve.snipeSeconds());
    }

    function _graduated(uint8 kind) internal returns (Launch memory l) {
        l = _launch(kind);
        _graduateV2(l.curve);
    }

    function test_creatorChoosesAKindAndGetsItsOwnStakingPool() public {
        Launch memory l = _launch(dividendKind);
        assertEq(l.treasury.stakingBps(), 10000);
        assertEq(address(l.staking.stakeToken()), address(l.token));
        assertEq(address(l.staking.rewardToken()), address(stock));
        assertEq(l.staking.incomeSource(), address(l.treasury));
        assertEq(l.staking.duration(), 7 days);
        assertEq(l.staking.minimumStakeTime(), 7 days);
        Launch memory s = _launch(splitKind);
        assertEq(s.treasury.stakingBps(), 5000);
        assertTrue(address(s.staking) != address(l.staking), "one pool per launch");
        assertEq(s.staking.incomeSource(), address(s.treasury));
    }

    function test_graduationPrincipalIsProtectedNeverIncome() public {
        Launch memory l = _launch(dividendKind);
        assertFalse(l.treasury.book(), "nothing to book before graduation");
        _graduateV2(l.curve);
        uint256 share = stock.balanceOf(address(l.treasury));
        assertGt(share, 0);
        assertEq(l.treasury.protectedGraduationStock(), share);
        assertEq(l.treasury.bookedStock(), share);
        assertEq(l.treasury.totalStockReceived(), share);
        assertEq(l.treasury.buybackStock(), 0, "principal is not a buy-back budget");
        assertEq(l.treasury.totalIncomeStock(), 0);
        assertEq(l.treasury.totalDividendStock(), 0);
        assertEq(l.treasury.pendingDividendStock(), 0);
        assertEq(stock.balanceOf(address(l.staking)), 0, "principal is not a dividend");
        assertEq(l.treasury.lotCount(), 0);
        assertEq(l.treasury.unbookedStock(), 0);
        vm.expectRevert(HedgeFunTreasuryBase.NotDue.selector);
        l.treasury.buyback();
        vm.expectRevert(HedgeFunV2IncomeTreasury.UseBuyback.selector);
        l.treasury.execute();
        vm.expectRevert(HedgeFunV2Treasury.UseExecute.selector);
        l.treasury.buyDip();
    }

    function test_dividendKindSendsEveryLaterArrivalToStakers() public {
        Launch memory l = _graduated(dividendKind);
        uint256 principal = l.treasury.protectedGraduationStock();
        stock.transfer(address(l.treasury), 3e18); // a sell-tax payout or a donation land the same way
        assertTrue(l.treasury.book());
        assertEq(l.treasury.totalIncomeStock(), 3e18);
        assertEq(l.treasury.totalDividendStock(), 3e18);
        assertEq(l.staking.totalFunded(), 3e18);
        assertEq(stock.balanceOf(address(l.staking)), 3e18);
        assertEq(l.treasury.buybackStock(), 0);
        assertEq(l.treasury.pendingDividendStock(), 0);
        assertEq(stock.balanceOf(address(l.treasury)), principal, "only the principal remains");
        assertEq(l.treasury.protectedGraduationStock(), principal);
        assertEq(l.treasury.totalStockReceived(), principal + 3e18);
        assertFalse(l.treasury.book(), "nothing new to book");
    }

    function test_splitKindHalvesIncomeAndKeepsTheLedgerAcrossBuybacks() public {
        Launch memory l = _graduated(splitKind);
        stock.transfer(address(l.treasury), 10e18);
        assertTrue(l.treasury.book());
        assertEq(l.treasury.totalDividendStock(), 5e18);
        assertEq(l.treasury.buybackStock(), 5e18);
        assertEq(stock.balanceOf(address(l.staking)), 5e18);

        uint256 supplyBefore = l.token.totalSupply();
        (uint256 spent, uint256 burned) = l.treasury.buyback();
        assertGt(spent, 0); assertGt(burned, 0);
        assertEq(l.token.totalSupply(), supplyBefore - burned);
        assertEq(l.treasury.buybackStock(), 5e18 - spent);
        assertEq(l.treasury.totalIncomeStock(), 10e18, "spending is not income");
        assertEq(l.treasury.pendingDividendStock(), 0, "a buy-back never creates a dividend");
        assertEq(l.treasury.protectedGraduationStock(), l.treasury.bookedStock(), "the buy-back did not touch principal");

        stock.transfer(address(l.treasury), 4e18);
        assertTrue(l.treasury.book());
        assertEq(l.treasury.totalIncomeStock(), 14e18);
        assertEq(l.treasury.totalDividendStock(), 7e18);
        assertEq(l.treasury.buybackStock(), 7e18 - spent);
    }

    function test_oddIncomeRoundsTowardTheBuyback() public {
        Launch memory l = _graduated(splitKind);
        stock.transfer(address(l.treasury), 3);
        assertTrue(l.treasury.book());
        assertEq(l.treasury.totalDividendStock(), 1);
        assertEq(l.treasury.buybackStock(), 2);
        stock.transfer(address(l.treasury), 1);
        assertTrue(l.treasury.book());
        assertEq(l.treasury.totalDividendStock(), 2, "the cumulative share catches up; nothing is lost to rounding");
        assertEq(l.treasury.buybackStock(), 2);
    }

    function test_lpFeesAreSplitWhenCreditedAndPaidByDistribute() public {
        Launch memory l = _graduated(splitKind);
        router.buy(Router.TradeParams(l.id, address(stock), 20e18, 20e18, 1, block.timestamp, router.GRADUATED(), false),
            new Router.Hop[](0));
        V2LiquidityVault vault = V2LiquidityVault(l.treasury.liquidityVault());
        (uint256 stockFee,) = vault.collectFees();
        assertGt(stockFee, 0, "the vault delivered: this treasury's balance rose by exactly the fee");
        assertEq(l.treasury.totalIncomeStock(), stockFee);
        assertEq(l.treasury.pendingDividendStock(), stockFee / 2, "held outside the budget, not yet transferred");
        assertEq(l.treasury.buybackStock(), stockFee - stockFee / 2);
        assertEq(l.treasury.unbookedIncome(), 0, "a pending dividend is not new income");
        assertFalse(l.treasury.book(), "nothing new arrived");
        assertEq(l.treasury.totalIncomeStock(), stockFee, "book() does not count the pending dividend twice");
        assertEq(l.treasury.pendingDividendStock(), 0, "but it does pay it");
        assertEq(l.treasury.totalDividendStock(), stockFee / 2);
        assertEq(stock.balanceOf(address(l.staking)), stockFee / 2);
    }

    function test_dividendKindLeavesNoLpFeeForABuybackToTake() public {
        Launch memory l = _graduated(dividendKind);
        router.buy(Router.TradeParams(l.id, address(stock), 20e18, 20e18, 1, block.timestamp, router.GRADUATED(), false),
            new Router.Hop[](0));
        (uint256 stockFee,) = V2LiquidityVault(l.treasury.liquidityVault()).collectFees();
        assertGt(stockFee, 0);
        assertEq(l.treasury.pendingDividendStock(), stockFee);
        assertEq(l.treasury.buybackStock(), 0);
        vm.expectRevert(HedgeFunTreasuryBase.NotDue.selector); // before anyone has called distribute()
        l.treasury.buyback();
        assertEq(l.treasury.distribute(), stockFee);
        assertEq(stock.balanceOf(address(l.staking)), stockFee);
        assertEq(l.treasury.distribute(), 0);
    }

    function test_onlyTheVaultCreditsLpFees() public {
        Launch memory l = _graduated(dividendKind);
        stock.approve(address(l.treasury), 1e18);
        vm.expectRevert(HedgeFunTreasuryBase.NotFactory.selector);
        l.treasury.creditLiquidityFee(1e18);
    }

    function test_sellTaxSweptByTheHookBecomesIncome() public {
        Launch memory l = _graduated(dividendKind);
        uint256 principal = l.treasury.protectedGraduationStock();
        router.sell(Router.TradeParams(l.id, address(stock), 10_000e18, 0, 1, block.timestamp, router.GRADUATED(), false),
            new Router.Hop[](0));
        hook.sweep(l.key.toId());
        uint256 arrived = l.treasury.unbookedStock();
        assertGt(arrived, 0, "the treasury's share of the stock-side sell tax");
        assertTrue(l.treasury.book());
        assertEq(l.treasury.totalDividendStock(), arrived);
        assertEq(stock.balanceOf(address(l.treasury)), principal);
    }

    function test_curveFeesClaimedAfterGraduationAreIncome() public {
        Launch memory l = _graduated(dividendKind);
        uint256 owed = l.curve.claimable(address(l.treasury));
        assertGt(owed, 0);
        l.curve.claimFees(address(l.treasury));
        assertTrue(l.treasury.book());
        assertEq(l.treasury.totalDividendStock(), owed);
    }

    function test_stakersEarnTheStreamAndLeaveAfterTheLock() public {
        Launch memory l = _graduated(dividendKind);
        l.token.transfer(alice, 1_000e18);
        vm.startPrank(alice);
        l.token.approve(address(l.staking), type(uint256).max);
        l.staking.stake(1_000e18);
        vm.stopPrank();
        stock.transfer(address(l.treasury), 7e18);
        assertTrue(l.treasury.book());
        vm.warp(block.timestamp + 3.5 days);
        assertApproxEqAbs(l.staking.earned(alice), 3.5e18, 1e6, "half the stream at half the duration");
        vm.prank(alice);
        vm.expectRevert(V2StakingIncome.StakeLocked.selector);
        l.staking.withdraw(1_000e18, alice);
        vm.warp(block.timestamp + 3.5 days);
        vm.startPrank(alice);
        uint256 paid = l.staking.claim(alice);
        l.staking.withdraw(1_000e18, alice);
        vm.stopPrank();
        assertApproxEqAbs(paid, 7e18, 1e6);
        assertEq(stock.balanceOf(alice), paid);
        assertEq(l.token.balanceOf(alice), 1_000e18);
    }

    function test_incomeFundedBeforeAnyoneStakesWaitsForTheFirstStaker() public {
        Launch memory l = _graduated(dividendKind);
        stock.transfer(address(l.treasury), 7e18);
        assertTrue(l.treasury.book());
        vm.warp(block.timestamp + 30 days);
        l.token.transfer(alice, 1e18);
        vm.startPrank(alice);
        l.token.approve(address(l.staking), 1e18);
        l.staking.stake(1e18);
        vm.warp(block.timestamp + 7 days);
        assertApproxEqAbs(l.staking.claim(alice), 7e18, 1e6, "queued income streams from the first stake");
        vm.stopPrank();
    }

    function test_aBlockedStakingTransferLeavesIncomeUnbookedUntilItClears() public {
        Launch memory l = _graduated(splitKind);
        stock.transfer(address(l.treasury), 10e18);
        stock.blockRecipient(address(l.staking));
        vm.expectRevert();
        l.treasury.book();
        assertEq(l.treasury.unbookedIncome(), 10e18);
        assertEq(l.treasury.buybackStock(), 0);
        assertEq(l.treasury.totalIncomeStock(), 0);
        stock.blockRecipient(address(0));
        assertTrue(l.treasury.book());
        assertEq(l.treasury.totalDividendStock(), 5e18);
        assertEq(l.treasury.buybackStock(), 5e18);
    }

    function test_onlyTheTreasuryFundsItsPool() public {
        Launch memory l = _graduated(dividendKind);
        stock.approve(address(l.staking), 1e18);
        vm.expectRevert(V2StakingIncome.NotIncomeSource.selector);
        l.staking.fund(1e18);
    }

    function testFuzz_ledgerIdentityHoldsForAnyArrivalSequence(uint96[6] memory arrivals, bool split) public {
        Launch memory l = _graduated(split ? splitKind : dividendKind);
        uint256 bps = l.treasury.stakingBps();
        uint256 total;
        for (uint256 i; i < arrivals.length; ++i) {
            uint256 amount = bound(uint256(arrivals[i]), 0, 50e18);
            if (amount == 0) continue;
            stock.transfer(address(l.treasury), amount);
            assertTrue(l.treasury.book());
            total += amount;
            assertEq(l.treasury.totalIncomeStock(), total);
            assertEq(l.treasury.totalDividendStock(), total * bps / 10000);
            assertEq(l.treasury.pendingDividendStock(), 0);
            assertEq(stock.balanceOf(address(l.staking)), l.treasury.totalDividendStock());
            assertEq(stock.balanceOf(address(l.treasury)),
                l.treasury.protectedGraduationStock() + l.treasury.buybackStock(), "every unit is accounted for");
        }
    }

    function test_registrationScriptRefusesAnyoneButTheFactoryOwner() public {
        vm.expectRevert(abi.encodeWithSelector(RegisterV2IncomeKinds.BadBinding.selector, "factory owner"));
        script.register(alice, factory);
    }

    function test_registrationReadbackRejectsOtherKinds() public {
        script.check(deployer, dividendKind, splitKind);
        vm.expectRevert(abi.encodeWithSelector(RegisterV2IncomeKinds.ReadbackFailed.selector, "kind ids"));
        script.check(deployer, 0, splitKind);
        vm.expectRevert(abi.encodeWithSelector(RegisterV2IncomeKinds.ReadbackFailed.selector, "dividend kind code"));
        script.check(deployer, splitKind, dividendKind);
    }

    function test_kindZeroIsUnaffected() public {
        HedgeFunFactory.Request memory q = _request();
        q.nonce = 99; // no kind chosen for this salt
        (,, bytes32 terms) = factory.predict(q);
        uint256 id = factory.launch(q, terms);
        (, address t,,,) = factory.strategies(id);
        assertEq(deployer.strategyKindOf(keccak256(abi.encode(q.symbol, address(this), q.nonce))), 0);
        vm.expectRevert(); // a kind-0 treasury has no staking pool
        HedgeFunV2IncomeTreasury(t).staking();
    }
}

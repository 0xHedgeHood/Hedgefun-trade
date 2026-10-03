// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Test} from "forge-std/Test.sol";
import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import {FunStakingIncome} from "../src/experimental/FunStakingIncome.sol";

contract IncomeTestToken is ERC20 {
    bool public blocked;
    bool public tax;
    constructor(string memory name_) ERC20(name_, name_) {}
    function decimals() public pure override returns (uint8) { return 6; }
    function mint(address to, uint256 amount) external { _mint(to, amount); }
    function setBlocked(bool value) external { blocked = value; }
    function setTax(bool value) external { tax = value; }
    function _update(address from, address to, uint256 amount) internal override {
        require(!blocked, "blocked");
        if (tax && from != address(0) && to != address(0) && amount > 100) {
            super._update(from, address(0), amount / 100);
            amount -= amount / 100;
        }
        super._update(from, to, amount);
    }
}

contract FunStakingIncomeTest is Test {
    uint256 constant WEEK = 7 days;
    address constant ALICE = address(0xA11CE);
    address constant BOB = address(0xB0B);
    IncomeTestToken fun;
    IncomeTestToken usd;
    FunStakingIncome income;

    function setUp() public {
        vm.warp(1_800_000_000);
        fun = new IncomeTestToken("FUN");
        usd = new IncomeTestToken("tUSDG");
        income = new FunStakingIncome(fun, usd, address(this), WEEK, WEEK);
        fun.mint(ALICE, 1000e6);
        fun.mint(BOB, 1000e6);
        usd.mint(address(this), 100_000e6);
        usd.approve(address(income), type(uint256).max);
        vm.prank(ALICE); fun.approve(address(income), type(uint256).max);
        vm.prank(BOB); fun.approve(address(income), type(uint256).max);
    }

    function _stake(address who, uint256 amount) private {
        vm.prank(who); income.stake(amount);
    }

    function _claim(address who) private returns (uint256) {
        vm.prank(who); return income.claim(who);
    }

    function testCashRewardsRequireActualFundingAndCannotSpendPrincipal() public {
        _stake(ALICE, 100e6);
        income.fund(100e6);
        assertEq(income.totalFunded(), 100e6);
        assertEq(usd.balanceOf(address(income)), 100e6);
        assertEq(fun.balanceOf(address(income)), 100e6);
        vm.warp(block.timestamp + WEEK);
        assertApproxEqAbs(_claim(ALICE), 100e6, 1);
        assertEq(fun.balanceOf(address(income)), 100e6);
        assertEq(usd.balanceOf(address(income)) + income.totalClaimed(), income.totalFunded());
    }

    function testLateStakerHasNoClaimOnAlreadyAccruedIncome() public {
        _stake(ALICE, 100e6);
        income.fund(100e6);
        vm.warp(block.timestamp + WEEK / 2);
        _stake(BOB, 100e6);
        assertEq(income.earned(BOB), 0);
        vm.warp(block.timestamp + WEEK / 2);
        assertApproxEqAbs(_claim(ALICE), 75e6, 2);
        assertApproxEqAbs(_claim(BOB), 25e6, 1);
    }

    function testNoInstantRewardAndPrincipalCannotFlashExit() public {
        _stake(ALICE, 100e6);
        income.fund(100e6);
        assertEq(_claim(ALICE), 0);
        vm.prank(ALICE);
        vm.expectRevert(FunStakingIncome.StakeLocked.selector);
        income.withdraw(100e6, ALICE);
    }

    function testFundingWithZeroStakesIsQueuedAndStartsAfterDeposit() public {
        income.fund(100e6);
        vm.warp(block.timestamp + 60 days);
        assertEq(income.totalClaimed(), 0);
        _stake(ALICE, 100e6);
        assertEq(income.earned(ALICE), 0);
        vm.warp(block.timestamp + WEEK);
        assertApproxEqAbs(_claim(ALICE), 100e6, 1);
    }

    function testOverlappingFundingPreservesVestedAndStreamsOnlyRemainder() public {
        _stake(ALICE, 100e6);
        income.fund(100e6);
        vm.warp(block.timestamp + WEEK / 2);
        uint256 vested = income.earned(ALICE);
        income.fund(200e6);
        assertEq(income.earned(ALICE), vested);
        vm.warp(block.timestamp + WEEK);
        assertApproxEqAbs(_claim(ALICE), 300e6, 2);
        assertEq(_claim(ALICE), 0);
        assertEq(usd.balanceOf(address(income)) + income.totalClaimed(), 300e6);
    }

    function testTinyUsdIncomeStreamsInsteadOfWaitingForWholeRawUnitPerSecond() public {
        _stake(ALICE, 100e6);
        income.fund(10_000); // one cent in a 6-decimal reward asset
        assertGt(income.rewardRateScaled(), 0);
        vm.warp(block.timestamp + WEEK);
        assertApproxEqAbs(_claim(ALICE), 10_000, 1);
    }

    function testRewardFailureDoesNotTrapUnlockedStakeAndClaimRetriesElsewhere() public {
        _stake(ALICE, 100e6);
        income.fund(100e6);
        vm.warp(block.timestamp + WEEK);
        usd.setBlocked(true);
        vm.prank(ALICE); vm.expectRevert(); income.claim(ALICE);
        assertEq(income.totalClaimed(), 0);
        vm.prank(ALICE); income.withdraw(100e6, ALICE);
        assertEq(fun.balanceOf(ALICE), 1000e6);
        usd.setBlocked(false);
        vm.prank(ALICE);
        uint256 paid = income.claim(BOB);
        assertApproxEqAbs(paid, 100e6, 1);
        assertEq(usd.balanceOf(BOB), paid);
    }

    function testExitThenNewCohortCannotTakeFormerCohortAccrual() public {
        _stake(ALICE, 100e6);
        vm.warp(block.timestamp + WEEK);
        income.fund(100e6);
        vm.warp(block.timestamp + WEEK / 2);
        uint256 oldEarned = income.earned(ALICE);
        vm.prank(ALICE); income.withdraw(100e6, ALICE);
        vm.warp(block.timestamp + 30 days);
        _stake(BOB, 100e6);
        assertEq(income.earned(BOB), 0);
        vm.warp(block.timestamp + WEEK);
        assertEq(_claim(ALICE), oldEarned);
        assertApproxEqAbs(_claim(BOB), 50e6, 2);
    }

    function testOnlyImmutableSourceCanFundAndTaxedTransfersRollback() public {
        vm.prank(BOB); vm.expectRevert(FunStakingIncome.NotIncomeSource.selector); income.fund(1);
        usd.setTax(true);
        vm.expectRevert(FunStakingIncome.InexactTransfer.selector); income.fund(100e6);
        assertEq(income.totalFunded(), 0);
        assertEq(usd.balanceOf(address(income)), 0);
        fun.setTax(true);
        vm.prank(ALICE); vm.expectRevert(FunStakingIncome.InexactTransfer.selector); income.stake(100e6);
        assertEq(income.totalStaked(), 0);
    }

    function testPartialExitKeepsEarnedRewardsAndOnlyRemainingStakeEarnsFuture() public {
        _stake(ALICE, 100e6);
        _stake(BOB, 100e6);
        vm.warp(block.timestamp + WEEK);
        income.fund(120e6);
        vm.warp(block.timestamp + WEEK / 2);
        vm.prank(ALICE); income.withdraw(50e6, ALICE);
        vm.warp(block.timestamp + WEEK / 2);
        assertApproxEqAbs(_claim(ALICE), 50e6, 2);
        assertApproxEqAbs(_claim(BOB), 70e6, 2);
        assertEq(fun.balanceOf(address(income)), income.totalStaked());
    }

    function testFuzzProRataFundingConservation(uint96 a, uint96 b, uint96 rawIncome) public {
        uint256 sa = bound(uint256(a), 1, 1000e6);
        uint256 sb = bound(uint256(b), 1, 1000e6);
        uint256 amount = bound(uint256(rawIncome), 1, 100_000e6);
        _stake(ALICE, sa); _stake(BOB, sb);
        income.fund(amount);
        vm.warp(block.timestamp + WEEK);
        uint256 paidA = _claim(ALICE);
        uint256 paidB = _claim(BOB);
        assertLe(paidA + paidB, amount);
        assertApproxEqAbs(paidA, amount * sa / (sa + sb), 1);
        assertApproxEqAbs(paidB, amount * sb / (sa + sb), 1);
        assertEq(usd.balanceOf(address(income)) + paidA + paidB, amount);
        vm.prank(ALICE); income.withdraw(sa, ALICE);
        vm.prank(BOB); income.withdraw(sb, BOB);
        assertEq(income.totalStaked(), 0);
        assertEq(fun.balanceOf(ALICE), 1000e6);
        assertEq(fun.balanceOf(BOB), 1000e6);
    }
}

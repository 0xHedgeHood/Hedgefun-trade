// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Test} from "forge-std/Test.sol";
import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import {FunStakingIncome} from "../src/experimental/FunStakingIncome.sol";

/// @dev Independent review fixture: transfer failures, inexact assets and callback attempts are observable.
contract IndependentIncomeReviewToken is ERC20 {
    uint8 private immutable unitDecimals;
    bool public blocked;
    bool public taxed;
    address public callbackTarget;
    bytes public callbackData;
    bool public callbackSucceeded;
    bytes4 public callbackFailure;

    constructor(string memory symbol_, uint8 decimals_) ERC20(symbol_, symbol_) { unitDecimals = decimals_; }
    function decimals() public view override returns (uint8) { return unitDecimals; }
    function mint(address account, uint256 value) external { _mint(account, value); }
    function setBlocked(bool value) external { blocked = value; }
    function setTaxed(bool value) external { taxed = value; }
    function setCallback(address target, bytes calldata data) external {
        callbackTarget = target;
        callbackData = data;
    }
    function _update(address from, address to, uint256 value) internal override {
        if (from != address(0)) require(!blocked, "issuer blocked");
        if (taxed && from != address(0) && to != address(0) && value != 0) {
            super._update(from, to, value - 1);
            super._update(from, address(0), 1);
        } else {
            super._update(from, to, value);
        }
        if (callbackTarget != address(0) && from != address(0)) {
            (bool ok, bytes memory result) = callbackTarget.call(callbackData);
            callbackSucceeded = ok;
            if (!ok && result.length >= 4) callbackFailure = bytes4(result);
        }
    }
}

contract FunStakingIncomeIndependentReviewTest is Test {
    uint256 private constant D = 7 days;
    uint256 private constant SCALE = 1e27;
    uint256 private constant RATE_SCALE = 1e18;
    IndependentIncomeReviewToken private fun;
    IndependentIncomeReviewToken private reward;
    FunStakingIncome private pool;
    address private constant SOURCE = address(0x510);
    address private constant A = address(0xA11CE);
    address private constant B = address(0xB0B);
    address private constant C = address(0xCA401);
    uint256 private start;

    function setUp() public {
        vm.warp(1_800_000_000);
        start = block.timestamp;
        fun = new IndependentIncomeReviewToken("FUN", 18);
        reward = new IndependentIncomeReviewToken("USDG", 6);
        pool = new FunStakingIncome(IERC20(address(fun)), IERC20(address(reward)), SOURCE, D, D);
        reward.mint(SOURCE, 1e24);
        vm.prank(SOURCE);
        reward.approve(address(pool), type(uint256).max);
        address[3] memory users = [A, B, C];
        for (uint256 i; i < users.length; ++i) {
            fun.mint(users[i], 1e26);
            vm.prank(users[i]);
            fun.approve(address(pool), type(uint256).max);
        }
    }

    function _stake(address user, uint256 amount) private { vm.prank(user); pool.stake(amount); }
    function _fund(uint256 amount) private { vm.prank(SOURCE); pool.fund(amount); }
    function _claim(address user) private { vm.prank(user); pool.claim(user); }

    function test_independentOverlapAndLateEntryHaveExactTimeWeightedPayouts() public {
        _stake(A, 100e18);
        _fund(D * 10);
        vm.warp(start + 2 days);
        _stake(B, 100e18);
        assertEq(pool.earned(B), 0, "new stake cannot earn elapsed time");
        vm.warp(start + 3 days);
        // Four remaining days at 10 plus fresh income produce exactly seven days at 20.
        _fund(D * 20 - 4 days * 10);
        assertEq(pool.earned(A), 2 days * 10 + 1 days * 5);
        assertEq(pool.earned(B), 1 days * 5);
        vm.warp(start + 10 days);
        _claim(A);
        _claim(B);
        assertEq(reward.balanceOf(A), 2 days * 10 + 1 days * 5 + D * 10);
        assertEq(reward.balanceOf(B), 1 days * 5 + D * 10);
        assertEq(pool.totalClaimed(), pool.totalFunded());
        assertEq(reward.balanceOf(address(pool)), 0);
    }

    function test_independentZeroTVLQueuesAndCannotPayAbsentUsers() public {
        _fund(D * 10);
        vm.warp(start + 20 days);
        assertEq(pool.earned(A), 0);
        assertEq(pool.queuedRewardsScaled(), D * 10 * RATE_SCALE);
        _stake(A, 100e18);
        assertEq(pool.periodFinish(), block.timestamp + D);
        vm.warp(start + 27 days);
        _claim(A);
        assertEq(reward.balanceOf(A), D * 10);
        vm.prank(A);
        pool.withdraw(100e18, A);
        _fund(D * 20);
        vm.warp(start + 40 days);
        _stake(B, 100e18);
        assertEq(pool.earned(B), 0);
        vm.warp(start + 47 days);
        _claim(B);
        assertEq(reward.balanceOf(B), D * 20);
        assertEq(pool.totalClaimed(), pool.totalFunded());
    }

    function test_independentLastExitQueuesOnlyFutureOverlapIncome() public {
        _stake(A, 100e18);
        _fund(D * 10);
        vm.warp(start + D);
        _fund(D * 20);
        vm.warp(start + D + 2 days);
        vm.prank(A);
        pool.withdraw(100e18, A);
        assertEq(pool.earned(A), D * 10 + 2 days * 20);
        assertEq(pool.queuedRewardsScaled(), 5 days * 20 * RATE_SCALE);
        vm.warp(start + 30 days);
        _stake(B, 100e18);
        assertEq(pool.earned(B), 0);
        vm.warp(start + 37 days);
        _claim(A);
        _claim(B);
        assertEq(reward.balanceOf(A), D * 10 + 2 days * 20);
        assertLe(5 days * 20 - reward.balanceOf(B), 2, "fractional rate dust only");
        assertLe(pool.totalFunded() - pool.totalClaimed(), 2);
    }

    function test_independentSubCentUSDGIncomeStreamsWithoutRawUnitRateFloor() public {
        _stake(A, 100e18);
        _fund(10_000); // 0.01 USDG for a 6-decimal reward asset.
        assertGt(pool.rewardRateScaled(), 0);
        vm.warp(start + D);
        _claim(A);
        assertGe(reward.balanceOf(A), 9_998);
        assertLe(reward.balanceOf(A), 10_000);
        assertEq(pool.totalClaimed() + reward.balanceOf(address(pool)), 10_000);
    }

    function test_independentBlockedRewardCannotTrapMaturedPrincipalOrLoseClaim() public {
        _stake(A, 100e18);
        _fund(D * 10);
        vm.warp(start + D);
        reward.setBlocked(true);
        uint256 due = pool.earned(A);
        vm.expectRevert("issuer blocked");
        vm.prank(A);
        pool.claim(A);
        assertEq(pool.totalClaimed(), 0);
        assertEq(pool.earned(A), due);
        vm.prank(A);
        pool.withdraw(100e18, A);
        assertEq(pool.totalStaked(), 0);
        assertEq(pool.earned(A), due);
        reward.setBlocked(false);
        _claim(A);
        assertEq(reward.balanceOf(A), due);
    }

    function test_independentInexactTransfersRollbackAccountingAndTransfers() public {
        fun.setTaxed(true);
        vm.expectRevert(FunStakingIncome.InexactTransfer.selector);
        vm.prank(A);
        pool.stake(100e18);
        assertEq(pool.totalStaked(), 0);
        assertEq(fun.balanceOf(A), 1e26);
        fun.setTaxed(false);
        _stake(A, 100e18);
        reward.setTaxed(true);
        vm.expectRevert(FunStakingIncome.InexactTransfer.selector);
        vm.prank(SOURCE);
        pool.fund(D * 10);
        assertEq(pool.totalFunded(), 0);
        assertEq(reward.balanceOf(address(pool)), 0);
        reward.setTaxed(false);
        _fund(D * 10);
        vm.warp(start + D);
        reward.setTaxed(true);
        vm.expectRevert(FunStakingIncome.InexactTransfer.selector);
        vm.prank(A);
        pool.claim(A);
        assertEq(pool.totalClaimed(), 0);
        assertEq(pool.earned(A), D * 10);
        fun.setTaxed(true);
        vm.expectRevert(FunStakingIncome.InexactTransfer.selector);
        vm.prank(A);
        pool.withdraw(100e18, A);
        assertEq(pool.totalStaked(), 100e18);
    }

    function test_independentTransferCallbacksCannotReenterStakeFundClaimOrWithdraw() public {
        fun.setCallback(address(pool), abi.encodeCall(FunStakingIncome.stake, (1)));
        _stake(A, 100e18);
        assertFalse(fun.callbackSucceeded());
        assertEq(fun.callbackFailure(), ReentrancyGuard.ReentrancyGuardReentrantCall.selector);
        reward.setCallback(address(pool), abi.encodeCall(FunStakingIncome.fund, (1)));
        _fund(D * 10);
        assertFalse(reward.callbackSucceeded());
        assertEq(reward.callbackFailure(), ReentrancyGuard.ReentrancyGuardReentrantCall.selector);
        vm.warp(start + D);
        reward.setCallback(address(pool), abi.encodeCall(FunStakingIncome.claim, (A)));
        _claim(A);
        assertFalse(reward.callbackSucceeded());
        assertEq(reward.callbackFailure(), ReentrancyGuard.ReentrancyGuardReentrantCall.selector);
        fun.setCallback(address(pool), abi.encodeCall(FunStakingIncome.withdraw, (1, A)));
        vm.prank(A);
        pool.withdraw(100e18, A);
        assertFalse(fun.callbackSucceeded());
        assertEq(fun.callbackFailure(), ReentrancyGuard.ReentrancyGuardReentrantCall.selector);
        assertEq(pool.totalClaimed(), pool.totalFunded());
        assertEq(pool.totalStaked(), 0);
    }

    function test_independentUnauthorizedFundingAndRecipientLockBoundaries() public {
        vm.expectRevert(FunStakingIncome.NotIncomeSource.selector);
        vm.prank(A);
        pool.fund(1);
        _stake(A, 100e18);
        vm.warp(start + D - 1);
        vm.expectRevert(FunStakingIncome.StakeLocked.selector);
        vm.prank(A);
        pool.withdraw(1, A);
        vm.warp(start + D);
        vm.expectRevert(FunStakingIncome.InvalidRecipient.selector);
        vm.prank(A);
        pool.withdraw(1, address(pool));
        vm.prank(A);
        pool.withdraw(1, B);
        assertEq(pool.balanceOf(A), 100e18 - 1);
        assertEq(pool.balanceOf(B), 0);
    }

    /// @notice Stateful randomized ledger checks use token conservation and a terminal dust bound,
    /// not a copy of the contract's reward-per-token calculation. Three accounts may interleave every action.
    function testFuzz_independentStatefulLedgerNeverOverallocates(uint256 seed) public {
        address[3] memory users = [A, B, C];
        uint256 funded;
        uint256 successfulUpdates;
        for (uint256 i; i < 96; ++i) {
            seed = uint256(keccak256(abi.encode(seed, i)));
            address user = users[(seed >> 8) % 3];
            uint256 op = seed % 5;
            if (op == 0) {
                _stake(user, 1 + (seed >> 32) % 1e23);
                ++successfulUpdates;
            } else if (op == 1) {
                uint256 amount = 1 + (seed >> 32) % (D * 1_000);
                _fund(amount);
                funded += amount;
                ++successfulUpdates;
            } else if (op == 2) {
                _claim(user);
                ++successfulUpdates;
            } else if (op == 3) {
                uint256 held = pool.balanceOf(user);
                if (held != 0 && block.timestamp >= pool.unlockAt(user)) {
                    uint256 amount = 1 + (seed >> 32) % held;
                    vm.prank(user);
                    pool.withdraw(amount, user);
                    ++successfulUpdates;
                }
            } else {
                vm.warp(block.timestamp + (seed >> 32) % (D * 2));
            }
            _assertLedger(users, funded);
        }
        // Settle every current promise, leave TVL zero, and compare only cash paid + explicitly queued cash.
        vm.warp(block.timestamp + 2 * D);
        for (uint256 i; i < users.length; ++i) {
            uint256 held = pool.balanceOf(users[i]);
            if (held != 0) { vm.prank(users[i]); pool.withdraw(held, users[i]); ++successfulUpdates; }
            _claim(users[i]);
            ++successfulUpdates;
        }
        _assertLedger(users, funded);
        assertEq(pool.totalStaked(), 0);
        uint256 dust = funded - pool.totalClaimed() - pool.queuedRewardsScaled() / RATE_SCALE;
        // Total stake is below SCALE; each global or account integer floor loses <1 raw reward.
        assertLe(dust, 2 * successfulUpdates + 3, "unexplained terminal reward loss");
    }

    function _assertLedger(address[3] memory users, uint256 funded) private view {
        uint256 staked;
        uint256 earnedTotal;
        uint256 walletRewards;
        for (uint256 i; i < users.length; ++i) {
            staked += pool.balanceOf(users[i]);
            earnedTotal += pool.earned(users[i]);
            walletRewards += reward.balanceOf(users[i]);
            assertEq(fun.balanceOf(users[i]) + pool.balanceOf(users[i]), 1e26);
        }
        assertLt(staked, SCALE);
        assertEq(pool.totalStaked(), staked);
        assertEq(fun.balanceOf(address(pool)), staked);
        assertEq(pool.totalFunded(), funded);
        assertEq(pool.totalClaimed(), walletRewards);
        uint256 cash = reward.balanceOf(address(pool));
        assertEq(walletRewards + cash, funded);
        uint256 remaining = block.timestamp < pool.periodFinish()
            ? (pool.periodFinish() - block.timestamp) * pool.rewardRateScaled() : 0;
        assertLe(earnedTotal * RATE_SCALE + remaining + pool.queuedRewardsScaled(), cash * RATE_SCALE, "promises exceed funded cash");
    }
}

// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {CoveredCallDesk} from "../src/options/CoveredCallDesk.sol";
import {CashSecuredPutDesk} from "../src/options/CashSecuredPutDesk.sol";
import {EarnVault} from "../src/options/EarnVault.sol";
import {PriceOracle} from "../src/PriceOracle.sol";
import {FreezableToken, RoundFeed, Calendar} from "./CoveredCallDesk.t.sol";

/// @notice Exercises the same share supply through a physical call, cash-secured put, and another call.
contract EarnWheelVaultTest is Test {
    uint64 internal constant T0 = 1_790_000_000;
    address internal alice = makeAddr("alice");
    address internal mm = makeAddr("wintermute");

    FreezableToken internal stock;
    FreezableToken internal usdg;
    RoundFeed internal stockFeed;
    RoundFeed internal usdgFeed;
    Calendar internal calendar;
    CoveredCallDesk internal callDesk;
    CashSecuredPutDesk internal putDesk;
    EarnVault internal vault;
    uint64 internal stockRound = 2;
    uint64 internal usdgRound = 2;

    function setUp() public {
        vm.warp(T0);
        stock = new FreezableToken("RHNVDA", 18);
        usdg = new FreezableToken("USDG", 6);
        stockFeed = new RoundFeed();
        usdgFeed = new RoundFeed();
        calendar = new Calendar();
        stockFeed.push(1, 1, 227e8, T0 - 1 minutes);
        usdgFeed.push(1, 1, 1e8, T0 - 1 minutes);
        callDesk = new CoveredCallDesk(address(this), IERC20(address(usdg)), calendar);
        putDesk = new CashSecuredPutDesk(address(this), IERC20(address(usdg)), calendar);
        PriceOracle oracle = new PriceOracle(
            address(stock), address(stockFeed), address(usdgFeed), address(calendar), 26 hours, 26 hours
        );
        vault = new EarnVault(
            address(this), IERC20(address(stock)), IERC20(address(usdg)), callDesk, oracle, address(0xBEEF)
        );
        vault.setPutDesk(putDesk);

        callDesk.list(address(stock), address(stockFeed), true);
        callDesk.setWriter(address(vault), true);
        callDesk.setBuyer(mm, true);
        putDesk.list(address(stock), address(stockFeed), true);
        putDesk.setWriter(address(vault), true);
        putDesk.setBuyer(mm, true);
        vault.setEligible(alice, true);
        vault.setBuyer(mm, true);

        stock.mint(alice, 400 ether);
        usdg.mint(mm, 1_000_000e6);
        vm.prank(alice);
        stock.approve(address(vault), type(uint256).max);
        vm.prank(mm);
        usdg.approve(address(callDesk), type(uint256).max);
        vm.prank(mm);
        usdg.approve(address(putDesk), type(uint256).max);

        vm.prank(alice);
        vault.requestDeposit(400 ether);
        vault.closeEpoch();
        vm.prank(alice);
        vault.claimDeposit(1, alice);
    }

    function _callTerms() internal view returns (CoveredCallDesk.Terms memory t) {
        t = CoveredCallDesk.Terms({
            buyer: mm,
            underlying: address(stock),
            size: 400 ether,
            strike: 235e6,
            premium: 500e6,
            expiry: uint64(block.timestamp + 7 days),
            fillDeadline: uint64(block.timestamp + 2 minutes),
            mode: CoveredCallDesk.Settlement.Physical,
            feed: address(stockFeed),
            exerciseWindow: callDesk.exerciseWindow(),
            stockMultiplier: stock.uiMultiplier()
        });
    }

    function _putTerms(uint128 size) internal view returns (CashSecuredPutDesk.Terms memory t) {
        t = CashSecuredPutDesk.Terms({
            buyer: mm,
            underlying: address(stock),
            size: size,
            strike: 225e6,
            premium: 1_000e6,
            expiry: uint64(block.timestamp + 7 days),
            fillDeadline: uint64(block.timestamp + 2 minutes),
            feed: address(stockFeed),
            exerciseWindow: putDesk.exerciseWindow(),
            stockMultiplier: stock.uiMultiplier()
        });
    }

    function _roundAtExpiry(uint64 expiry, int256 price) internal returns (uint80 id) {
        id = stockFeed.push(1, stockRound++, price, expiry - 4 minutes);
        stockFeed.push(1, stockRound++, price, expiry + 1 minutes);
        usdgFeed.push(1, usdgRound++, 1e8, expiry + 1 minutes);
        vm.warp(uint256(expiry) + 5 minutes + 1);
    }

    function _refresh(int256 price) internal {
        stockFeed.push(1, stockRound++, price, block.timestamp);
        usdgFeed.push(1, usdgRound++, 1e8, block.timestamp);
    }

    function _callAssignedAndClosed() internal returns (uint256 id) {
        id = vault.offer(_callTerms());
        vm.prank(mm);
        callDesk.fill(id);
        uint80 roundId = _roundAtExpiry(callDesk.getOption(id).expiry, 250e8);
        vm.prank(mm);
        callDesk.exercise(id, roundId, mm);
        _refresh(250e8);
        vault.closeEpoch();
        assertEq(uint8(vault.epochOptionKind(2)), uint8(EarnVault.OptionKind.Call));
        assertEq(usdg.balanceOf(address(vault)), 94_500e6);
    }

    function test_physicalCallPutCallWheelUsesOnePrincipal() public {
        _callAssignedAndClosed();
        CashSecuredPutDesk.Terms memory p = _putTerms(400 ether);
        uint256 putId = vault.offerPut(p);
        assertEq(uint8(vault.activeOptionKind()), uint8(EarnVault.OptionKind.Put));
        assertEq(putDesk.exerciseCost(putId), 90_000e6);
        assertEq(usdg.balanceOf(address(putDesk)), 90_000e6);
        assertEq(usdg.balanceOf(address(vault)), 4_500e6);
        CoveredCallDesk.Terms memory blockedCall = _callTerms();
        vm.expectRevert(EarnVault.Busy.selector);
        vault.offer(blockedCall);
        vm.expectRevert(EarnVault.Busy.selector);
        vault.closeEpoch();

        vm.prank(mm);
        putDesk.fill(putId);
        assertEq(usdg.balanceOf(address(vault)), 5_500e6);
        uint80 roundId = _roundAtExpiry(p.expiry, 200e8);
        vm.prank(mm);
        stock.approve(address(putDesk), 400 ether);
        vm.prank(mm);
        putDesk.exercise(putId, roundId, mm);
        assertEq(stock.balanceOf(address(vault)), 400 ether);
        assertEq(usdg.balanceOf(address(vault)), 5_500e6);
        _refresh(200e8);
        vault.closeEpoch();
        assertEq(uint8(vault.epochOptionKind(3)), uint8(EarnVault.OptionKind.Put));
        assertEq(vault.activeOptionId(), 0);

        CoveredCallDesk.Terms memory nextCall = _callTerms();
        nextCall.strike = 210e6;
        uint256 nextId = vault.offer(nextCall);
        assertEq(uint8(vault.activeOptionKind()), uint8(EarnVault.OptionKind.Call));
        assertEq(stock.balanceOf(address(callDesk)), 400 ether);
        assertEq(nextId, 2);
    }

    function test_redeemReservationCannotBeUsedAsPutCollateral() public {
        uint256 callId = vault.offer(_callTerms());
        vm.prank(mm);
        callDesk.fill(callId);
        vm.prank(alice);
        vault.requestRedeem(100 ether);
        uint80 roundId = _roundAtExpiry(callDesk.getOption(callId).expiry, 250e8);
        vm.prank(mm);
        callDesk.exercise(callId, roundId, mm);
        _refresh(250e8);
        vault.closeEpoch();
        (, uint256 freeUSDG) = vault.freeBalances();
        assertEq(freeUSDG, 70_875e6);
        CashSecuredPutDesk.Terms memory oversizedPut = _putTerms(400 ether);
        vm.expectRevert(EarnVault.BadTerms.selector);
        vault.offerPut(oversizedPut);
        uint256 id = vault.offerPut(_putTerms(300 ether));
        assertEq(putDesk.exerciseCost(id), 67_500e6);
        assertEq(usdg.balanceOf(address(vault)), 27_000e6);
    }

    function test_stockOnlyVaultNeedsCashBeforeOfferingEvenSmallPut() public {
        CashSecuredPutDesk.Terms memory smallPut = _putTerms(2 ether);
        smallPut.premium = 5e6;
        vm.expectRevert(EarnVault.BadTerms.selector);
        vault.offerPut(smallPut);

        CoveredCallDesk.Terms memory c = _callTerms();
        uint256 callId = vault.offer(c);
        vm.prank(mm);
        callDesk.fill(callId);
        uint80 roundId = _roundAtExpiry(c.expiry, 230e8);
        callDesk.settle(callId, roundId);
        _refresh(230e8);
        vault.closeEpoch();
        assertEq(stock.balanceOf(address(vault)), 400 ether);
        assertEq(usdg.balanceOf(address(vault)), 500e6);

        smallPut = _putTerms(2 ether);
        smallPut.premium = 5e6;
        uint256 putId = vault.offerPut(smallPut);
        assertEq(putDesk.exerciseCost(putId), 450e6);
        assertEq(usdg.balanceOf(address(vault)), 50e6);
        assertEq(uint8(vault.activeOptionKind()), uint8(EarnVault.OptionKind.Put));
    }

    function test_putCancellationOwedMustBeClaimedBeforeEpochCloses() public {
        _callAssignedAndClosed();
        uint256 id = vault.offerPut(_putTerms(400 ether));
        usdg.freeze(address(vault), true);
        vault.cancelOffered();
        assertEq(uint8(putDesk.getOption(id).state), uint8(CashSecuredPutDesk.State.Cancelled));
        assertEq(putDesk.owed(address(usdg), address(vault)), 90_000e6);
        vm.expectRevert(EarnVault.UnsafeAssets.selector);
        vault.closeEpoch();
        usdg.freeze(address(vault), false);
        vault.claimPutDeskOwed(address(usdg));
        assertEq(putDesk.owed(address(usdg), address(vault)), 0);
        vault.closeEpoch();
        assertEq(uint8(vault.epochOptionKind(3)), uint8(EarnVault.OptionKind.Put));
    }

    function test_putDeskMustBeInstalledBeforeAnyDeposit() public {
        EarnVault freshVault = new EarnVault(
            address(this), IERC20(address(stock)), IERC20(address(usdg)), callDesk, vault.oracle(), address(0xBEEF)
        );
        freshVault.setEligible(alice, true);
        stock.mint(alice, 1 ether);
        vm.prank(alice);
        stock.approve(address(freshVault), 1 ether);
        vm.prank(alice);
        freshVault.requestDeposit(1 ether);
        vm.expectRevert(EarnVault.Busy.selector);
        freshVault.setPutDesk(putDesk);
        freshVault.closeEpoch();
        vm.expectRevert(EarnVault.Busy.selector);
        freshVault.setPutDesk(putDesk);
    }

    function test_frozenVaultStockCreditBlocksCloseUntilRecovered() public {
        _callAssignedAndClosed();
        CashSecuredPutDesk.Terms memory t = _putTerms(400 ether);
        uint256 id = vault.offerPut(t);
        vm.prank(mm);
        putDesk.fill(id);
        uint80 roundId = _roundAtExpiry(t.expiry, 200e8);
        stock.freeze(address(vault), true);
        vm.prank(mm);
        stock.approve(address(putDesk), 400 ether);
        vm.prank(mm);
        putDesk.exercise(id, roundId, mm);
        assertEq(putDesk.owed(address(stock), address(vault)), 400 ether);
        vm.expectRevert(EarnVault.UnsafeAssets.selector);
        vault.closeEpoch();
        stock.freeze(address(vault), false);
        vault.claimPutDeskOwed(address(stock));
        _refresh(200e8);
        vault.closeEpoch();
        assertEq(stock.balanceOf(address(vault)), 400 ether);
    }
}

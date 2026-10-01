// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {CoveredCallDesk} from "../src/options/CoveredCallDesk.sol";
import {FreezableToken, RoundFeed, Calendar} from "./CoveredCallDesk.t.sol";

/// Second-round review: regressions for N1 / N2 and re-checks of the first-round fixes.
contract CoveredCallDeskAudit2Test is Test {
    CoveredCallDesk desk;
    FreezableToken usdg;
    FreezableToken stock;
    RoundFeed feed;
    Calendar cal;

    address owner = makeAddr("owner");
    address writer = makeAddr("treasury");
    address mm = makeAddr("wintermute");

    uint64 constant T0 = 1_790_000_000;
    uint64 expiry;
    uint128 constant SIZE = 440e18;
    uint128 constant STRIKE = 235e6;
    uint128 constant PREMIUM = 250e6;

    function setUp() public {
        vm.warp(T0);
        usdg = new FreezableToken("USDG", 6);
        stock = new FreezableToken("NVDA", 18);
        feed = new RoundFeed();
        cal = new Calendar();
        desk = new CoveredCallDesk(owner, IERC20(address(usdg)), cal);

        vm.startPrank(owner);
        desk.list(address(stock), address(feed), true);
        desk.setWriter(writer, true);
        desk.setBuyer(mm, true);
        vm.stopPrank();

        stock.mint(writer, 10_000e18);
        usdg.mint(mm, 10_000_000e6);
        vm.prank(writer);
        stock.approve(address(desk), type(uint256).max);
        vm.prank(mm);
        usdg.approve(address(desk), type(uint256).max);

        expiry = T0 + 7 days;
        feed.push(1, 1, 227e8, T0 - 1 hours);
    }

    function _terms(address buyer, CoveredCallDesk.Settlement mode) internal view returns (CoveredCallDesk.Terms memory) {
        return CoveredCallDesk.Terms({
            buyer: buyer,
            underlying: address(stock),
            size: SIZE,
            strike: STRIKE,
            premium: PREMIUM,
            expiry: expiry,
            fillDeadline: T0 + 1 hours,
            mode: mode,
            feed: address(feed),
            exerciseWindow: 2 hours,
            stockMultiplier: 1e18
        });
    }

    // ------------------------------------------------------------------------------------------------ N-1
    /// fixed: the writer names the feed in its Terms; an owner re-list racing the offer makes the offer revert
    function test_N1_relistBeforeOffer_offerReverts() public {
        RoundFeed evil = new RoundFeed();
        evil.push(1, 1, 227e8, T0 - 1 hours);
        vm.prank(owner);
        desk.list(address(stock), address(evil), true);          // lands just before the writer's offer

        vm.prank(writer);
        vm.expectRevert(CoveredCallDesk.BadTerms.selector);
        desk.offer(_terms(address(0), CoveredCallDesk.Settlement.NetShare));   // names the feed it agreed to
        assertEq(stock.balanceOf(writer), 10_000e18);
    }

    /// fixed: same for the exercise window the RFQ priced
    function test_N1b_windowChangedBeforeOffer_offerReverts() public {
        vm.prank(owner);
        desk.setExerciseWindow(3 days);
        vm.prank(writer);
        vm.expectRevert(CoveredCallDesk.BadTerms.selector);
        desk.offer(_terms(mm, CoveredCallDesk.Settlement.Physical));
    }

    // ------------------------------------------------------------------------------------------------ N-2
    /// fixed: a backstop price settles a Physical option like NetShare -- stalling to the backstop buys no tail
    function test_N2_backstopNetsPhysical_noTail() public {
        vm.prank(writer);
        uint256 id = desk.offer(_terms(mm, CoveredCallDesk.Settlement.Physical));
        vm.prank(mm);
        desk.fill(id);
        feed.push(1, 2, 250e8, expiry - 27 hours);               // oracle cannot price this expiry
        vm.warp(uint256(expiry) + 14 days + 1);
        vm.prank(owner);
        desk.backstopSettle(id, 250e6);
        assertEq(uint8(desk.getOption(id).state), uint8(CoveredCallDesk.State.NetSettled));
        uint256 intrinsic = uint256(SIZE) * (250e6 - STRIKE) / 250e6;
        assertEq(stock.balanceOf(mm), intrinsic);
        assertEq(stock.balanceOf(writer), 10_000e18 - intrinsic);
    }

    /// fixed: when the BUYER accepts, it chose the moment, so it gets only the option's own window
    function test_N2b_buyerAccepts_optionWindowOnly() public {
        vm.prank(writer);
        uint256 id = desk.offer(_terms(mm, CoveredCallDesk.Settlement.Physical));
        vm.prank(mm);
        desk.fill(id);
        feed.push(1, 2, 250e8, expiry - 27 hours);
        vm.warp(expiry + 10 minutes);
        vm.prank(writer);
        desk.proposePrice(id, 250e6);
        vm.warp(block.timestamp + 20 hours);
        vm.prank(mm);
        desk.acceptPrice(id, 250e6);
        assertEq(desk.getOption(id).exerciseDeadline, block.timestamp + 2 hours);
    }

    // ------------------------------------------------------------------------------------------------ fix checks
    /// L3: a frozen `to` credits the BUYER, and claim can't target the desk
    function test_fix_L3_frozenTo_creditsBuyer_claimNotToDesk() public {
        vm.prank(writer);
        uint256 id = desk.offer(_terms(mm, CoveredCallDesk.Settlement.Physical));
        vm.prank(mm);
        desk.fill(id);
        uint80 r = feed.push(1, 2, 250e8, expiry - 4 minutes);
        vm.warp(expiry + 10 minutes);
        address cold = makeAddr("cold");
        stock.freeze(cold, true);
        vm.prank(mm);
        desk.exercise(id, r, cold);
        assertEq(desk.owed(address(stock), mm), SIZE);
        assertEq(desk.owed(address(stock), cold), 0);
        vm.prank(mm);
        vm.expectRevert(CoveredCallDesk.BadRecipient.selector);
        desk.claim(address(stock), address(desk));
        vm.prank(mm);
        desk.claim(address(stock), makeAddr("hot"));
        assertEq(stock.balanceOf(makeAddr("hot")), SIZE);
        assertEq(stock.balanceOf(address(desk)), desk.reserved(address(stock)));
    }

    /// H1: a re-list between offer and fill does not reach the offered option either
    function test_fix_H1_relistBetweenOfferAndFill_keepsFeed() public {
        vm.prank(writer);
        uint256 id = desk.offer(_terms(mm, CoveredCallDesk.Settlement.NetShare));
        RoundFeed evil = new RoundFeed();
        vm.prank(owner);
        desk.list(address(stock), address(evil), true);
        vm.prank(mm);
        desk.fill(id);
        assertEq(desk.getOption(id).feed, address(feed));
    }
}

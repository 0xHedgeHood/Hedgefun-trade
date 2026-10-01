// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {CoveredCallDesk, IDeskStockControls} from "../src/options/CoveredCallDesk.sol";
import {ITradingCalendar} from "../src/interfaces/ITradingCalendar.sol";

/// The desk against the live chain: the real NVDA stock token, USDG, the RHNVDA / USD feed and the production
/// trading calendar. `RH_FORK=1 forge test --mc CoveredCallDeskFork -vv`
///
/// Pinned facts, read on 2026-09-29: the feed's last print before Friday 2026-09-25 16:00 ET (20:00 UTC) is round
/// (1 << 64) | 1106, 19:56:05 UTC, 225.66018707; the next is round 1107 on Monday 00:00:23 UTC (the Sunday 20:00 ET
/// reopen). Those rounds are history, so the test holds at any later block.
contract CoveredCallDeskFork is Test {
    address constant NVDA = 0xd0601CE157Db5bdC3162BbaC2a2C8aF5320D9EEC;
    address constant USDG = 0x5fc5360D0400a0Fd4f2af552ADD042D716F1d168;
    address constant FEED = 0x379EC4f7C378F34a1B47E4F3cbeBCbAC3E8E9F15;   // RHNVDA / USD
    address constant CALENDAR = 0xFE9E85f0C258Fc2757eB6Acd1ca032Ec860487F5;
    uint64 constant FRI_CLOSE = 1_790_366_400;                             // 2026-09-25 20:00 UTC = 16:00 ET
    uint80 constant R1105 = uint80((uint256(1) << 64) | 1105);
    uint80 constant R1106 = uint80((uint256(1) << 64) | 1106);
    uint80 constant R1107 = uint80((uint256(1) << 64) | 1107);

    CoveredCallDesk desk;
    address owner = makeAddr("safe");
    address writer = makeAddr("treasury");
    address mm = makeAddr("mm");

    function setUp() public {
        vm.skip(vm.envOr("RH_FORK", uint256(0)) == 0, "fork test: set RH_FORK=1");
        vm.createSelectFork(vm.envOr("RH_RPC", string("robinhood")));
        vm.rollFork(block.number - 600);   // ~1 min back: the public RPC sometimes refuses state at its own head
        desk = new CoveredCallDesk(owner, IERC20(USDG), ITradingCalendar(CALENDAR));
        vm.startPrank(owner);
        desk.list(NVDA, FEED, true);
        desk.setWriter(writer, true);
        desk.setBuyer(mm, true);
        vm.stopPrank();
    }

    function test_fork_settlementPrice_isLastPrintBeforeFridayClose() public view {
        assertEq(desk.listingPriceAt(NVDA, FRI_CLOSE, R1106), 225_660_187);   // $225.660187 in USDG base units
    }

    function test_fork_earlierRound_refused() public {
        vm.expectRevert(CoveredCallDesk.NotLastRoundBeforeExpiry.selector);
        desk.listingPriceAt(NVDA, FRI_CLOSE, R1105);
    }

    function test_fork_laterRound_refused() public {
        vm.expectRevert(CoveredCallDesk.BadRound.selector);
        desk.listingPriceAt(NVDA, FRI_CLOSE, R1107);
    }

    function test_fork_calendar_refusesSaturdayExpiry() public {
        CoveredCallDesk.Terms memory t = _terms(uint64(block.timestamp + 7 days));
        // move the expiry to the next Saturday 12:00 UTC: the market is shut
        uint256 sat = _nextWeekday(block.timestamp, 6) + 12 hours;
        t.expiry = uint64(sat);
        deal(NVDA, writer, t.size);
        vm.startPrank(writer);
        IERC20(NVDA).approve(address(desk), t.size);
        vm.expectRevert(CoveredCallDesk.ExpiryMarketClosed.selector);
        desk.offer(t);
        vm.stopPrank();
    }

    /// a whole week with the real tokens: offer, fill, then settle against a feed round mocked at the pinned address
    /// (the future cannot be forked), physical exercise
    function test_fork_fullCycle_realTokens() public {
        uint64 expiry = uint64(_nextWeekday(block.timestamp, 5) + 20 hours);   // next Friday 16:00 ET (EDT)
        CoveredCallDesk.Terms memory t = _terms(expiry);
        deal(NVDA, writer, t.size);
        deal(USDG, mm, 1_000_000e6);

        vm.startPrank(writer);
        IERC20(NVDA).approve(address(desk), t.size);
        uint256 id = desk.offer(t);
        vm.stopPrank();

        vm.startPrank(mm);
        IERC20(USDG).approve(address(desk), type(uint256).max);
        desk.fill(id);
        vm.stopPrank();
        assertEq(IERC20(USDG).balanceOf(writer), t.premium);
        assertEq(IERC20(NVDA).balanceOf(address(desk)), t.size);

        // the future round: NVDA at $250 four minutes before the close, nothing since
        uint80 rid = uint80((uint256(1) << 64) | 5000);
        vm.mockCall(FEED, abi.encodeWithSignature("getRoundData(uint80)", rid), abi.encode(rid, int256(250e8), expiry - 240, expiry - 240, rid));
        vm.mockCall(FEED, abi.encodeWithSignature("latestRoundData()"), abi.encode(rid, int256(250e8), expiry - 240, expiry - 240, rid));
        vm.warp(expiry + 10 minutes);

        vm.prank(mm);
        desk.exercise(id, rid, mm);
        assertEq(IERC20(NVDA).balanceOf(mm), t.size);
        assertEq(IERC20(USDG).balanceOf(writer), t.premium + desk.exerciseCost(id));
        assertEq(IERC20(NVDA).balanceOf(address(desk)), 0);
        assertEq(IERC20(USDG).balanceOf(address(desk)), 0);
    }

    function _terms(uint64 expiry) internal view returns (CoveredCallDesk.Terms memory) {
        return CoveredCallDesk.Terms({
            buyer: mm,
            underlying: NVDA,
            size: 440e18,
            strike: 235e6,
            premium: 350e6,
            expiry: expiry,
            fillDeadline: uint64(block.timestamp + 1 hours),
            mode: CoveredCallDesk.Settlement.Physical,
            feed: FEED,
            exerciseWindow: 2 hours,
            stockMultiplier: IDeskStockControls(NVDA).uiMultiplier()
        });
    }

    /// midnight UTC of the next `weekday` (0 = Sunday ... 6 = Saturday) strictly after `ts`'s day
    function _nextWeekday(uint256 ts, uint256 weekday) internal pure returns (uint256) {
        uint256 day = ts / 1 days;
        uint256 dow = (day + 4) % 7;   // 1970-01-01 was a Thursday
        uint256 ahead = (weekday + 7 - dow) % 7;
        if (ahead == 0) ahead = 7;
        return (day + ahead) * 1 days;
    }
}

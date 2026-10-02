// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {CashSecuredPutDesk} from "../src/options/CashSecuredPutDesk.sol";
import {ITradingCalendar} from "../src/interfaces/ITradingCalendar.sol";

contract PutTestToken is ERC20 {
    uint8 internal immutable _dec;
    uint256 public feeBps;
    uint256 public uiMultiplier = 1e18;
    bool public oraclePaused;
    mapping(address => bool) public frozen;

    constructor(string memory name_, uint8 dec_) ERC20(name_, name_) {
        _dec = dec_;
    }

    function decimals() public view override returns (uint8) {
        return _dec;
    }

    function mint(address to, uint256 amount) external {
        _mint(to, amount);
    }

    function freeze(address who, bool value) external {
        frozen[who] = value;
    }

    function setFee(uint256 value) external {
        feeBps = value;
    }

    function setUiMultiplier(uint256 value) external {
        uiMultiplier = value;
    }

    function setOraclePaused(bool value) external {
        oraclePaused = value;
    }

    function _update(address from, address to, uint256 value) internal override {
        require(!frozen[from] && !frozen[to], "frozen");
        if (feeBps != 0 && from != address(0) && to != address(0)) {
            uint256 fee = value * feeBps / 10_000;
            super._update(from, address(0xFEE), fee);
            value -= fee;
        }
        super._update(from, to, value);
    }
}

contract PutTestFeed {
    struct Round {
        int256 answer;
        uint256 updatedAt;
        uint80 answeredInRound;
    }
    uint8 public decimals = 8;
    mapping(uint80 => Round) internal rounds;
    uint80 public latest;
    uint256 public maxPhase;

    function push(uint256 phase, uint64 aggRound, int256 answer, uint256 updatedAt) external returns (uint80 id) {
        id = uint80((phase << 64) | aggRound);
        rounds[id] = Round(answer, updatedAt, id);
        latest = id;
        if (phase > maxPhase) maxPhase = phase;
    }

    function getRoundData(uint80 id) public view returns (uint80, int256, uint256, uint256, uint80) {
        uint256 phase = uint256(id) >> 64;
        if (phase == 0 || phase > maxPhase) revert("no phase");
        Round memory r = rounds[id];
        if (r.updatedAt == 0) return (uint80(phase << 64), 0, 0, 0, uint80(phase << 64));
        return (id, r.answer, r.updatedAt, r.updatedAt, r.answeredInRound);
    }

    function latestRoundData() external view returns (uint80, int256, uint256, uint256, uint80) {
        return getRoundData(latest);
    }
}

contract PutTestCalendar is ITradingCalendar {
    bool public closed;

    function setClosed(bool value) external {
        closed = value;
    }

    function isClosed(uint256) external view returns (bool) {
        return closed;
    }

    function isScheduledClosure(uint256) external view returns (bool) {
        return closed;
    }

    function tradingDate(uint256 ts) external pure returns (uint256) {
        return ts / 1 days;
    }
}

contract CashSecuredPutDeskTest is Test {
    CashSecuredPutDesk desk;
    PutTestToken usdg;
    PutTestToken stock;
    PutTestFeed feed;
    PutTestCalendar calendar;

    address owner = makeAddr("owner");
    address writer = makeAddr("treasury");
    address buyer = makeAddr("wintermute");
    address other = makeAddr("other");
    uint64 constant T0 = 1_790_000_000;
    uint64 expiry;
    uint128 constant SIZE = 400e18;
    uint128 constant STRIKE = 225e6;
    uint128 constant PREMIUM = 1_135e6;
    uint256 constant COST = 90_000e6;

    function setUp() public {
        vm.warp(T0);
        usdg = new PutTestToken("USDG", 6);
        stock = new PutTestToken("RHNVDA", 18);
        feed = new PutTestFeed();
        calendar = new PutTestCalendar();
        desk = new CashSecuredPutDesk(owner, IERC20(address(usdg)), calendar);
        vm.startPrank(owner);
        desk.list(address(stock), address(feed), true);
        desk.setWriter(writer, true);
        desk.setBuyer(buyer, true);
        vm.stopPrank();

        usdg.mint(writer, 1_000_000e6);
        usdg.mint(buyer, 10_000e6);
        stock.mint(buyer, 2_000e18);
        vm.prank(writer);
        usdg.approve(address(desk), type(uint256).max);
        vm.prank(buyer);
        usdg.approve(address(desk), type(uint256).max);
        vm.prank(buyer);
        stock.approve(address(desk), type(uint256).max);
        expiry = T0 + 7 days;
        feed.push(1, 1, 231e8, T0 - 1 hours);
    }

    function _terms() internal view returns (CashSecuredPutDesk.Terms memory) {
        return CashSecuredPutDesk.Terms({
            buyer: buyer,
            underlying: address(stock),
            size: SIZE,
            strike: STRIKE,
            premium: PREMIUM,
            expiry: expiry,
            fillDeadline: T0 + 1 hours,
            feed: address(feed),
            exerciseWindow: 2 hours,
            stockMultiplier: 1e18
        });
    }

    function _offer() internal returns (uint256 id) {
        vm.prank(writer);
        id = desk.offer(_terms());
    }

    function _offerAndFill() internal returns (uint256 id) {
        id = _offer();
        vm.prank(buyer);
        desk.fill(id);
    }

    function _roundsAtExpiry(int256 price) internal returns (uint80 roundId) {
        roundId = feed.push(1, 2, price, expiry - 4 minutes);
        feed.push(1, 3, price * 2, expiry + 1 minutes);
        vm.warp(expiry + 5 minutes + 1);
    }

    function _assertReserved() internal view {
        assertEq(usdg.balanceOf(address(desk)), desk.reserved(address(usdg)), "USDG reserve");
        assertEq(stock.balanceOf(address(desk)), desk.reserved(address(stock)), "stock reserve");
    }

    function test_offerLocksFullStrikeCost_fillForwardsPremium() public {
        uint256 id = _offerAndFill();
        assertEq(desk.exerciseCost(id), COST);
        assertEq(usdg.balanceOf(address(desk)), COST);
        assertEq(desk.reserved(address(usdg)), COST);
        assertEq(usdg.balanceOf(writer), 1_000_000e6 - COST + PREMIUM);
        assertEq(usdg.balanceOf(buyer), 10_000e6 - PREMIUM);
        assertEq(uint8(desk.getOption(id).state), uint8(CashSecuredPutDesk.State.Active));
        _assertReserved();
    }

    function test_putITM_exercisePhysicallyDeliversStockAndUSDG() public {
        uint256 id = _offerAndFill();
        uint80 roundId = _roundsAtExpiry(200e8);
        desk.settle(id, roundId);
        assertEq(uint8(desk.getOption(id).state), uint8(CashSecuredPutDesk.State.Exercisable));
        vm.prank(buyer);
        desk.exercise(id, 0, address(0));
        assertEq(uint8(desk.getOption(id).state), uint8(CashSecuredPutDesk.State.Exercised));
        assertEq(stock.balanceOf(writer), SIZE);
        assertEq(stock.balanceOf(buyer), 2_000e18 - SIZE);
        assertEq(usdg.balanceOf(buyer), 10_000e6 - PREMIUM + COST);
        assertEq(usdg.balanceOf(address(desk)), 0);
        _assertReserved();
    }

    function test_buyerCanExerciseWithExpiryRoundWithoutPriorKeeperSettlement() public {
        uint256 id = _offerAndFill();
        uint80 roundId = _roundsAtExpiry(200e8);
        vm.prank(buyer);
        desk.exercise(id, roundId, address(0));
        assertEq(uint8(desk.getOption(id).state), uint8(CashSecuredPutDesk.State.Exercised));
        assertEq(desk.getOption(id).settlementPrice, 200e6);
        assertEq(stock.balanceOf(writer), SIZE);
        _assertReserved();
    }

    function test_putOTM_returnsUSDGToWriter() public {
        uint256 id = _offerAndFill();
        uint80 roundId = _roundsAtExpiry(230e8);
        desk.settle(id, roundId);
        assertEq(uint8(desk.getOption(id).state), uint8(CashSecuredPutDesk.State.ExpiredOTM));
        assertEq(usdg.balanceOf(writer), 1_000_000e6 + PREMIUM);
        assertEq(stock.balanceOf(writer), 0);
        _assertReserved();
    }

    function test_atStrikeIsOTM() public {
        uint256 id = _offerAndFill();
        uint80 roundId = _roundsAtExpiry(int256(uint256(STRIKE)) * 100);
        desk.settle(id, roundId);
        assertEq(uint8(desk.getOption(id).state), uint8(CashSecuredPutDesk.State.ExpiredOTM));
    }

    function test_exerciseCannotBeNakedOrEarly_andLapseReturnsCollateral() public {
        uint256 id = _offerAndFill();
        vm.prank(buyer);
        vm.expectRevert(CashSecuredPutDesk.TooEarly.selector);
        desk.exercise(id, uint80((uint256(1) << 64) | 1), address(0));

        uint80 roundId = _roundsAtExpiry(200e8);
        desk.settle(id, roundId);
        // Buyer has no stock approval: no way to take USDG without delivering the exact stock.
        vm.prank(buyer);
        stock.approve(address(desk), 0);
        vm.prank(buyer);
        vm.expectRevert();
        desk.exercise(id, 0, address(0));
        assertEq(desk.reserved(address(usdg)), COST);
        assertEq(uint8(desk.getOption(id).state), uint8(CashSecuredPutDesk.State.Exercisable));

        vm.warp(uint256(desk.getOption(id).exerciseDeadline) + 1);
        desk.lapse(id);
        assertEq(uint8(desk.getOption(id).state), uint8(CashSecuredPutDesk.State.Lapsed));
        assertEq(usdg.balanceOf(writer), 1_000_000e6 + PREMIUM);
        _assertReserved();
    }

    function test_cancelAndFillGuards() public {
        CashSecuredPutDesk.Terms memory t = _terms();
        vm.prank(other);
        vm.expectRevert(CashSecuredPutDesk.NotWriter.selector);
        desk.offer(t);
        uint256 id = _offer();

        vm.prank(other);
        vm.expectRevert(CashSecuredPutDesk.NotBuyer.selector);
        desk.fill(id);
        vm.prank(other);
        vm.expectRevert(CashSecuredPutDesk.NotWriter.selector);
        desk.cancel(id);

        vm.warp(T0 + 1 hours + 1);
        vm.prank(buyer);
        vm.expectRevert(CashSecuredPutDesk.TooLate.selector);
        desk.fill(id);
        vm.prank(writer);
        desk.cancel(id);
        assertEq(usdg.balanceOf(writer), 1_000_000e6);
        _assertReserved();
    }

    function test_offerBindsFeedWindowAndMultiplier() public {
        CashSecuredPutDesk.Terms memory t = _terms();
        t.feed = other;
        vm.prank(writer);
        vm.expectRevert(CashSecuredPutDesk.BadTerms.selector);
        desk.offer(t);

        t = _terms();
        t.exerciseWindow = 3 days;
        vm.prank(writer);
        vm.expectRevert(CashSecuredPutDesk.BadTerms.selector);
        desk.offer(t);

        t = _terms();
        stock.setUiMultiplier(2e18);
        vm.prank(writer);
        vm.expectRevert(CashSecuredPutDesk.StockMultiplierChanged.selector);
        desk.offer(t);
        stock.setUiMultiplier(1e18);

        t = _terms();
        stock.setOraclePaused(true);
        vm.prank(writer);
        vm.expectRevert(CashSecuredPutDesk.StockControlsPaused.selector);
        desk.offer(t);
        stock.setOraclePaused(false);

        calendar.setClosed(true);
        vm.prank(writer);
        vm.expectRevert(CashSecuredPutDesk.ExpiryMarketClosed.selector);
        desk.offer(t);
    }

    function test_pauseOnlyStopsNewTradesNotSettlementAndClaims() public {
        uint256 id = _offerAndFill();
        vm.prank(owner);
        desk.setPaused(true);
        vm.prank(writer);
        vm.expectRevert(CashSecuredPutDesk.IsPaused.selector);
        desk.offer(_terms());
        uint80 roundId = _roundsAtExpiry(230e8);
        desk.settle(id, roundId);
        assertEq(uint8(desk.getOption(id).state), uint8(CashSecuredPutDesk.State.ExpiredOTM));
    }

    function test_feeOnTransferCannotUndercollateralizeOrUnderDeliver() public {
        usdg.setFee(10);
        vm.prank(writer);
        vm.expectRevert(CashSecuredPutDesk.Unexpected.selector);
        desk.offer(_terms());
        usdg.setFee(0);
        uint256 id = _offerAndFill();
        uint80 roundId = _roundsAtExpiry(200e8);
        desk.settle(id, roundId);
        stock.setFee(10);
        vm.prank(buyer);
        vm.expectRevert(CashSecuredPutDesk.Unexpected.selector);
        desk.exercise(id, 0, address(0));
        assertEq(desk.reserved(address(usdg)), COST);
        _assertReserved();
    }

    function test_frozenWriterStockPayoutIsOwed_withoutBlockingBuyerUSDG() public {
        uint256 id = _offerAndFill();
        uint80 roundId = _roundsAtExpiry(200e8);
        desk.settle(id, roundId);
        stock.freeze(writer, true);
        vm.prank(buyer);
        desk.exercise(id, 0, address(0));
        assertEq(stock.balanceOf(writer), 0);
        assertEq(desk.owed(address(stock), writer), SIZE);
        assertEq(usdg.balanceOf(buyer), 10_000e6 - PREMIUM + COST);
        _assertReserved();
        vm.prank(writer);
        desk.claim(address(stock), other);
        assertEq(stock.balanceOf(other), SIZE);
        _assertReserved();
    }

    function test_frozenBuyerUSDGPayoutIsOwedAndClaimableElsewhere() public {
        uint256 id = _offerAndFill();
        uint80 roundId = _roundsAtExpiry(200e8);
        desk.settle(id, roundId);
        usdg.freeze(buyer, true);
        vm.prank(buyer);
        desk.exercise(id, 0, address(0));
        assertEq(desk.owed(address(usdg), buyer), COST);
        assertEq(stock.balanceOf(writer), SIZE);
        _assertReserved();
        vm.prank(buyer);
        desk.claim(address(usdg), other);
        assertEq(usdg.balanceOf(other), COST);
        _assertReserved();
    }

    function test_oracleMustBeLastRoundAtExpiry() public {
        uint256 id = _offerAndFill();
        feed.push(1, 1, 205e8, expiry - 1 hours);
        uint80 roundId = _roundsAtExpiry(200e8);
        uint80 oldRound = uint80((uint256(1) << 64) | 1);
        vm.expectRevert(CashSecuredPutDesk.NotLastRoundBeforeExpiry.selector);
        desk.settle(id, oldRound);
        desk.settle(id, roundId);
        assertEq(desk.getOption(id).settlementPrice, 200e6);
    }

    function test_oracleRejectsStaleRound() public {
        uint256 id = _offerAndFill();
        vm.warp(expiry + 5 minutes + 1);
        uint80 roundId = uint80((uint256(1) << 64) | 1);
        vm.expectRevert(CashSecuredPutDesk.BadRound.selector);
        desk.settle(id, roundId);
    }

    function test_backstopITMStillRequiresPhysicalStockDelivery() public {
        uint256 id = _offerAndFill();
        vm.warp(expiry + 14 days + 1);
        vm.prank(owner);
        desk.backstopSettle(id, 200e6);
        assertEq(uint8(desk.getOption(id).state), uint8(CashSecuredPutDesk.State.Exercisable));
        assertEq(desk.getOption(id).exerciseDeadline, block.timestamp + 3 days);
        assertEq(desk.reserved(address(usdg)), COST);
        vm.prank(buyer);
        desk.exercise(id, 0, address(0));
        assertEq(stock.balanceOf(writer), SIZE);
        assertEq(usdg.balanceOf(buyer), 10_000e6 - PREMIUM + COST);
        _assertReserved();
    }

    function test_backstopOTMReleasesWholeCollateral() public {
        uint256 id = _offerAndFill();
        vm.warp(expiry + 14 days + 1);
        vm.prank(owner);
        desk.backstopSettle(id, 230e6);
        assertEq(uint8(desk.getOption(id).state), uint8(CashSecuredPutDesk.State.ExpiredOTM));
        assertEq(usdg.balanceOf(writer), 1_000_000e6 + PREMIUM);
        _assertReserved();
    }

    function test_frozenWriterUSDGReturnIsOwedAfterOTMSettlement() public {
        uint256 id = _offerAndFill();
        uint80 roundId = _roundsAtExpiry(230e8);
        usdg.freeze(writer, true);
        desk.settle(id, roundId);
        assertEq(desk.owed(address(usdg), writer), COST);
        _assertReserved();
        vm.prank(writer);
        desk.claim(address(usdg), other);
        assertEq(usdg.balanceOf(other), COST);
        _assertReserved();
    }

    function test_agreedPriceFallback_opensPhysicalExerciseWindow() public {
        uint256 id = _offerAndFill();
        vm.warp(expiry + 1);
        vm.prank(writer);
        desk.proposePrice(id, 200e6);
        vm.prank(buyer);
        desk.acceptPrice(id, 200e6);
        assertEq(desk.getOption(id).exerciseDeadline, block.timestamp + 2 hours);
        vm.prank(buyer);
        desk.exercise(id, 0, other);
        assertEq(usdg.balanceOf(other), COST);
        assertEq(stock.balanceOf(writer), SIZE);
    }

    function test_sweepCannotReachReservedCollateral() public {
        uint256 id = _offerAndFill();
        usdg.mint(address(desk), 123e6);
        vm.prank(owner);
        desk.sweep(address(usdg), other);
        assertEq(usdg.balanceOf(other), 123e6);
        assertEq(usdg.balanceOf(address(desk)), COST);
        assertEq(desk.reserved(address(usdg)), COST);
        uint80 roundId = _roundsAtExpiry(230e8);
        desk.settle(id, roundId);
        _assertReserved();
    }
}

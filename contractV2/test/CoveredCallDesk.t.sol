// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";
import {CoveredCallDesk} from "../src/options/CoveredCallDesk.sol";
import {ITradingCalendar} from "../src/interfaces/ITradingCalendar.sol";

/// ERC-20 whose issuer can freeze an address (both directions), like a stock token or USDG can.
contract FreezableToken is ERC20 {
    uint8 internal immutable _dec;
    uint256 public feeBps;
    uint256 public uiMultiplier = 1e18;
    bool public oraclePaused;
    mapping(address => bool) public frozen;

    constructor(string memory n, uint8 d) ERC20(n, n) { _dec = d; }

    function decimals() public view override returns (uint8) { return _dec; }
    function mint(address to, uint256 a) external { _mint(to, a); }
    function freeze(address who, bool f) external { frozen[who] = f; }
    function setFee(uint256 bps) external { feeBps = bps; }
    function setUiMultiplier(uint256 multiplier) external { uiMultiplier = multiplier; }
    function setOraclePaused(bool p) external { oraclePaused = p; }

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

/// Chainlink proxy with history. Round id = (phase << 64) | aggregatorRound. A round not yet written in an existing
/// phase answers (phase << 64, 0, 0, 0, phase << 64) -- what RHNVDA / USD returns on chain -- and a phase that
/// does not exist reverts.
contract RoundFeed {
    struct R { int256 a; uint256 t; uint80 answeredInRound; }
    uint8 public decimals = 8;
    mapping(uint80 => R) internal _r;
    uint80 public latest;
    uint256 public maxPhase;

    function push(uint256 phase, uint64 aggRound, int256 a, uint256 t) external returns (uint80 id) {
        id = uint80((phase << 64) | aggRound);
        _r[id] = R(a, t, id);
        latest = id;
        if (phase > maxPhase) maxPhase = phase;
    }

    function setAnsweredInRound(uint80 id, uint80 answeredInRound) external {
        _r[id].answeredInRound = answeredInRound;
    }

    function getRoundData(uint80 id) public view returns (uint80, int256, uint256, uint256, uint80) {
        uint256 phase = uint256(id) >> 64;
        if (phase == 0 || phase > maxPhase) revert("no phase");
        R memory r = _r[id];
        if (r.t == 0) return (uint80(phase << 64), 0, 0, 0, uint80(phase << 64));
        return (id, r.a, r.t, r.t, r.answeredInRound);
    }

    function latestRoundData() external view returns (uint80, int256, uint256, uint256, uint80) {
        return getRoundData(latest);
    }
}

contract Calendar is ITradingCalendar {
    bool public closed;
    function setClosed(bool c) external { closed = c; }
    function isClosed(uint256) external view returns (bool) { return closed; }
    function isScheduledClosure(uint256) external view returns (bool) { return closed; }
    function tradingDate(uint256 ts) external pure returns (uint256) { return ts / 1 days; }
}

contract CoveredCallDeskTest is Test {
    CoveredCallDesk desk;
    FreezableToken usdg;
    FreezableToken stock;
    RoundFeed feed;
    Calendar cal;

    address owner = makeAddr("owner");
    address writer = makeAddr("treasury");
    address mm = makeAddr("wintermute");
    address other = makeAddr("other");

    uint64 constant T0 = 1_790_000_000;
    uint64 expiry;
    uint128 constant SIZE = 440e18;         // ~$100k of a $227 stock
    uint128 constant STRIKE = 235e6;        // $235 per token, USDG 6 decimals
    uint128 constant PREMIUM = 250e6;       // $250

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

    // ---------------------------------------------------------------------------------------------- helpers

    function _terms(CoveredCallDesk.Settlement mode) internal view returns (CoveredCallDesk.Terms memory t) {
        t = CoveredCallDesk.Terms({
            buyer: mm,
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

    function _offerAndFill(CoveredCallDesk.Settlement mode) internal returns (uint256 id) {
        vm.prank(writer);
        id = desk.offer(_terms(mode));
        vm.prank(mm);
        desk.fill(id);
    }

    /// publish rounds around expiry: one just before it at `price`, one after it at a different price
    function _roundsAtExpiry(int256 price) internal returns (uint80 atExpiry) {
        atExpiry = feed.push(1, 2, price, expiry - 4 minutes);
        feed.push(1, 3, price * 2, expiry + 1 minutes);   // a later print must never be the settlement price
        vm.warp(expiry + 5 minutes + 1);
    }

    function _assertReservedMatches() internal view {
        assertEq(stock.balanceOf(address(desk)), desk.reserved(address(stock)), "stock reserved");
        assertEq(usdg.balanceOf(address(desk)), desk.reserved(address(usdg)), "usdg reserved");
    }

    // ---------------------------------------------------------------------------------------------- offer / fill

    function test_offerLocksStock_fillForwardsPremium() public {
        uint256 id = _offerAndFill(CoveredCallDesk.Settlement.Physical);
        assertEq(stock.balanceOf(address(desk)), SIZE);
        assertEq(usdg.balanceOf(writer), PREMIUM);
        assertEq(usdg.balanceOf(address(desk)), 0);
        assertEq(uint8(desk.getOption(id).state), uint8(CoveredCallDesk.State.Active));
        _assertReservedMatches();
    }

    function test_offer_rejectsStockOraclePause() public {
        stock.setOraclePaused(true);
        vm.prank(writer);
        vm.expectRevert(CoveredCallDesk.StockControlsPaused.selector);
        desk.offer(_terms(CoveredCallDesk.Settlement.Physical));
        assertEq(stock.balanceOf(writer), 10_000e18);
    }

    function test_offer_rejectsUnavailableStockControls() public {
        vm.mockCallRevert(address(stock), abi.encodeWithSignature("oraclePaused()"), "unavailable");
        vm.prank(writer);
        vm.expectRevert(CoveredCallDesk.StockControlsUnavailable.selector);
        desk.offer(_terms(CoveredCallDesk.Settlement.Physical));
        assertEq(stock.balanceOf(writer), 10_000e18);
    }

    function test_fill_rejectsStockOraclePause_butCancelRemainsOpen() public {
        vm.prank(writer);
        uint256 id = desk.offer(_terms(CoveredCallDesk.Settlement.Physical));
        stock.setOraclePaused(true);

        vm.prank(mm);
        vm.expectRevert(CoveredCallDesk.StockControlsPaused.selector);
        desk.fill(id);
        assertEq(uint8(desk.getOption(id).state), uint8(CoveredCallDesk.State.Offered));
        assertEq(usdg.balanceOf(writer), 0);

        vm.prank(writer);
        desk.cancel(id);
        assertEq(stock.balanceOf(writer), 10_000e18);
    }

    function test_offer_rejectsStaleStockMultiplier() public {
        CoveredCallDesk.Terms memory t = _terms(CoveredCallDesk.Settlement.Physical);
        stock.setUiMultiplier(2e18);
        vm.prank(writer);
        vm.expectRevert(CoveredCallDesk.StockMultiplierChanged.selector);
        desk.offer(t);
    }

    function test_fill_rejectsStockMultiplierDrift_butCancelRemainsOpen() public {
        vm.prank(writer);
        uint256 id = desk.offer(_terms(CoveredCallDesk.Settlement.Physical));
        stock.setUiMultiplier(2e18);

        vm.prank(mm);
        vm.expectRevert(CoveredCallDesk.StockMultiplierChanged.selector);
        desk.fill(id);
        assertEq(uint8(desk.getOption(id).state), uint8(CoveredCallDesk.State.Offered));
        assertEq(usdg.balanceOf(writer), 0);

        vm.prank(writer);
        desk.cancel(id);
        assertEq(stock.balanceOf(writer), 10_000e18);
    }

    function test_cancelBeforeFill_returnsStock() public {
        vm.prank(writer);
        uint256 id = desk.offer(_terms(CoveredCallDesk.Settlement.Physical));
        vm.prank(other);
        vm.expectRevert(CoveredCallDesk.NotWriter.selector);
        desk.cancel(id);
        vm.prank(writer);
        desk.cancel(id);
        assertEq(stock.balanceOf(writer), 10_000e18);
        vm.prank(mm);
        vm.expectRevert(CoveredCallDesk.WrongState.selector);
        desk.fill(id);
    }

    function test_cannotCancelAfterFill() public {
        uint256 id = _offerAndFill(CoveredCallDesk.Settlement.Physical);
        vm.prank(writer);
        vm.expectRevert(CoveredCallDesk.WrongState.selector);
        desk.cancel(id);
    }

    function test_fillGuards() public {
        vm.prank(writer);
        uint256 id = desk.offer(_terms(CoveredCallDesk.Settlement.Physical));

        // not allowlisted
        usdg.mint(other, 1e12);
        vm.prank(other);
        vm.expectRevert(CoveredCallDesk.NotBuyer.selector);
        desk.fill(id);

        // allowlisted, but not the RFQ winner
        vm.prank(owner);
        desk.setBuyer(other, true);
        vm.prank(other);
        vm.expectRevert(CoveredCallDesk.NotBuyer.selector);
        desk.fill(id);

        // paused
        vm.prank(owner);
        desk.setPaused(true);
        vm.prank(mm);
        vm.expectRevert(CoveredCallDesk.IsPaused.selector);
        desk.fill(id);
        vm.prank(owner);
        desk.setPaused(false);

        // late
        vm.warp(T0 + 1 hours + 1);
        vm.prank(mm);
        vm.expectRevert(CoveredCallDesk.TooLate.selector);
        desk.fill(id);

        // the writer can still take it back after the deadline
        vm.prank(writer);
        desk.cancel(id);
        assertEq(stock.balanceOf(address(desk)), 0);
    }

    function test_openOffer_anyAllowlistedBuyerFills() public {
        CoveredCallDesk.Terms memory t = _terms(CoveredCallDesk.Settlement.Physical);
        t.buyer = address(0);
        vm.prank(writer);
        uint256 id = desk.offer(t);
        vm.prank(mm);
        desk.fill(id);
        assertEq(desk.getOption(id).buyer, mm);
    }

    function test_offerGuards() public {
        CoveredCallDesk.Terms memory t = _terms(CoveredCallDesk.Settlement.Physical);

        vm.prank(other);
        vm.expectRevert(CoveredCallDesk.NotWriter.selector);
        desk.offer(t);

        t.buyer = other;   // not allowlisted
        vm.prank(writer);
        vm.expectRevert(CoveredCallDesk.NotBuyer.selector);
        desk.offer(t);

        t = _terms(CoveredCallDesk.Settlement.Physical);
        t.fillDeadline = expiry + 1;
        vm.prank(writer);
        vm.expectRevert(CoveredCallDesk.BadTerms.selector);
        desk.offer(t);

        t = _terms(CoveredCallDesk.Settlement.Physical);
        t.expiry = T0;
        vm.prank(writer);
        vm.expectRevert(CoveredCallDesk.BadTerms.selector);
        desk.offer(t);

        t = _terms(CoveredCallDesk.Settlement.Physical);
        t.premium = 0;
        vm.prank(writer);
        vm.expectRevert(CoveredCallDesk.BadTerms.selector);
        desk.offer(t);

        t = _terms(CoveredCallDesk.Settlement.Physical);
        cal.setClosed(true);
        vm.prank(writer);
        vm.expectRevert(CoveredCallDesk.ExpiryMarketClosed.selector);
        desk.offer(t);
        cal.setClosed(false);

        t = _terms(CoveredCallDesk.Settlement.Physical);
        t.feed = address(0xBEEF);          // not the listing's feed
        vm.prank(writer);
        vm.expectRevert(CoveredCallDesk.BadTerms.selector);
        desk.offer(t);

        t = _terms(CoveredCallDesk.Settlement.Physical);
        t.exerciseWindow = 3 days;         // not the desk's current window
        vm.prank(writer);
        vm.expectRevert(CoveredCallDesk.BadTerms.selector);
        desk.offer(t);

        t = _terms(CoveredCallDesk.Settlement.Physical);
        t.stockMultiplier = 0;
        vm.prank(writer);
        vm.expectRevert(CoveredCallDesk.BadTerms.selector);
        desk.offer(t);

        vm.prank(owner);
        desk.list(address(stock), address(feed), false);
        vm.prank(writer);
        vm.expectRevert(CoveredCallDesk.NotListed.selector);
        desk.offer(t);
    }

    function test_feeOnTransferRefused() public {
        stock.setFee(10);
        vm.prank(writer);
        vm.expectRevert(CoveredCallDesk.Unexpected.selector);
        desk.offer(_terms(CoveredCallDesk.Settlement.Physical));
    }

    // ---------------------------------------------------------------------------------------------- settlement

    function test_otm_returnsStockToWriter() public {
        uint256 id = _offerAndFill(CoveredCallDesk.Settlement.Physical);
        uint80 r = _roundsAtExpiry(230e8);
        desk.settle(id, r);   // anyone
        assertEq(uint8(desk.getOption(id).state), uint8(CoveredCallDesk.State.ExpiredOTM));
        assertEq(stock.balanceOf(writer), 10_000e18);
        assertEq(desk.getOption(id).settlementPrice, 230e6);
        _assertReservedMatches();
    }

    function test_atStrike_isOTM() public {
        uint256 id = _offerAndFill(CoveredCallDesk.Settlement.NetShare);
        uint80 r = _roundsAtExpiry(235e8);
        desk.settle(id, r);
        assertEq(uint8(desk.getOption(id).state), uint8(CoveredCallDesk.State.ExpiredOTM));
    }

    function test_settleBeforeExpiry_reverts() public {
        uint256 id = _offerAndFill(CoveredCallDesk.Settlement.Physical);
        uint80 r = feed.push(1, 2, 240e8, expiry - 4 minutes);
        vm.warp(expiry);
        vm.expectRevert(CoveredCallDesk.TooEarly.selector);
        desk.settle(id, r);
        vm.warp(expiry + 5 minutes);   // SETTLE_DELAY: every round up to expiry must be visible first
        vm.expectRevert(CoveredCallDesk.TooEarly.selector);
        desk.settle(id, r);
        vm.warp(expiry + 5 minutes + 1);
        desk.settle(id, r);
    }

    function test_physical_itm_settleThenExercise() public {
        uint256 id = _offerAndFill(CoveredCallDesk.Settlement.Physical);
        uint80 r = _roundsAtExpiry(250e8);
        desk.settle(id, r);
        CoveredCallDesk.Option memory o = desk.getOption(id);
        assertEq(uint8(o.state), uint8(CoveredCallDesk.State.Exercisable));
        assertEq(o.exerciseDeadline, uint256(expiry) + desk.SETTLE_DELAY() + 2 hours);

        uint256 cost = desk.exerciseCost(id);
        assertEq(cost, 440 * 235e6);
        uint256 w0 = usdg.balanceOf(writer);
        vm.prank(mm);
        desk.exercise(id, 0, address(0));
        assertEq(stock.balanceOf(mm), SIZE);
        assertEq(usdg.balanceOf(writer) - w0, cost);
        assertEq(uint8(desk.getOption(id).state), uint8(CoveredCallDesk.State.Exercised));
        _assertReservedMatches();
    }

    function test_physical_itm_exerciseDirectly_toOtherWallet() public {
        uint256 id = _offerAndFill(CoveredCallDesk.Settlement.Physical);
        uint80 r = _roundsAtExpiry(250e8);
        vm.prank(mm);
        desk.exercise(id, r, other);
        assertEq(stock.balanceOf(other), SIZE);
    }

    function test_exercise_otm_reverts() public {
        uint256 id = _offerAndFill(CoveredCallDesk.Settlement.Physical);
        uint80 r = _roundsAtExpiry(200e8);
        vm.prank(mm);
        vm.expectRevert(CoveredCallDesk.NotInTheMoney.selector);
        desk.exercise(id, r, mm);
        assertEq(uint8(desk.getOption(id).state), uint8(CoveredCallDesk.State.Active));   // rolled back
    }

    function test_exercise_onlyBuyer() public {
        uint256 id = _offerAndFill(CoveredCallDesk.Settlement.Physical);
        uint80 r = _roundsAtExpiry(250e8);
        vm.prank(writer);
        vm.expectRevert(CoveredCallDesk.NotBuyer.selector);
        desk.exercise(id, r, writer);
    }

    function test_physical_lapse() public {
        uint256 id = _offerAndFill(CoveredCallDesk.Settlement.Physical);
        uint80 r = _roundsAtExpiry(250e8);
        desk.settle(id, r);
        vm.expectRevert(CoveredCallDesk.TooEarly.selector);
        desk.lapse(id);
        vm.warp(block.timestamp + 2 hours + 1);
        vm.prank(mm);
        vm.expectRevert(CoveredCallDesk.TooLate.selector);
        desk.exercise(id, 0, mm);
        desk.lapse(id);
        assertEq(stock.balanceOf(writer), 10_000e18);
        assertEq(uint8(desk.getOption(id).state), uint8(CoveredCallDesk.State.Lapsed));
        _assertReservedMatches();
    }

    function test_exerciseWindow_isSnapshotAtOffer() public {
        vm.prank(writer);
        uint256 id = desk.offer(_terms(CoveredCallDesk.Settlement.Physical));
        vm.prank(owner);
        desk.setExerciseWindow(30 minutes);
        vm.prank(mm);
        desk.fill(id);
        uint80 r = _roundsAtExpiry(250e8);
        desk.settle(id, r);
        assertEq(desk.getOption(id).exerciseDeadline, uint256(expiry) + desk.SETTLE_DELAY() + 2 hours);
    }

    function test_delayedOracleSettle_doesNotExtendPhysicalExerciseDeadline() public {
        uint256 id = _offerAndFill(CoveredCallDesk.Settlement.Physical);
        uint80 r = feed.push(1, 2, 250e8, expiry - 4 minutes);
        vm.warp(uint256(expiry) + 1 days);

        desk.settle(id, r);
        CoveredCallDesk.Option memory o = desk.getOption(id);
        assertEq(uint8(o.state), uint8(CoveredCallDesk.State.Exercisable));
        assertEq(o.exerciseDeadline, uint256(expiry) + desk.SETTLE_DELAY() + 2 hours);
        assertLt(o.exerciseDeadline, block.timestamp);

        vm.prank(mm);
        vm.expectRevert(CoveredCallDesk.TooLate.selector);
        desk.exercise(id, 0, mm);
        desk.lapse(id);
        assertEq(uint8(desk.getOption(id).state), uint8(CoveredCallDesk.State.Lapsed));
        assertEq(stock.balanceOf(writer), 10_000e18);
    }

    function test_netShare_itm_split() public {
        uint256 id = _offerAndFill(CoveredCallDesk.Settlement.NetShare);
        uint80 r = _roundsAtExpiry(250e8);
        desk.settle(id, r);
        uint256 toBuyer = uint256(SIZE) * (250e6 - STRIKE) / 250e6;
        assertEq(stock.balanceOf(mm), toBuyer);
        assertEq(stock.balanceOf(writer), 10_000e18 - toBuyer);
        assertEq(stock.balanceOf(address(desk)), 0);
        _assertReservedMatches();
    }

    function test_delisting_doesNotStrandOpenOption() public {
        uint256 id = _offerAndFill(CoveredCallDesk.Settlement.NetShare);
        vm.prank(owner);
        desk.list(address(stock), address(feed), false);
        uint80 r = _roundsAtExpiry(250e8);
        desk.settle(id, r);
        assertEq(uint8(desk.getOption(id).state), uint8(CoveredCallDesk.State.NetSettled));
    }

    function test_pause_doesNotBlockSettlement() public {
        uint256 id = _offerAndFill(CoveredCallDesk.Settlement.Physical);
        vm.prank(owner);
        desk.setPaused(true);
        uint80 r = _roundsAtExpiry(250e8);
        vm.prank(mm);
        desk.exercise(id, r, mm);
        assertEq(stock.balanceOf(mm), SIZE);
    }

    // ---------------------------------------------------------------------------------------------- round proof

    function test_round_mustBeLastBeforeExpiry() public {
        uint256 id = _offerAndFill(CoveredCallDesk.Settlement.NetShare);
        uint80 early = feed.push(1, 2, 300e8, expiry - 2 hours);   // a favourable earlier print
        uint80 atExpiry = feed.push(1, 3, 230e8, expiry - 1 minutes);
        uint80 after_ = feed.push(1, 4, 300e8, expiry + 1 minutes);
        vm.warp(expiry + 1 hours);

        vm.expectRevert(CoveredCallDesk.NotLastRoundBeforeExpiry.selector);
        desk.settle(id, early);
        vm.expectRevert(CoveredCallDesk.BadRound.selector);
        desk.settle(id, after_);
        vm.expectRevert(CoveredCallDesk.BadRound.selector);
        desk.settle(id, uint80((uint256(1) << 64) | 99));   // does not exist

        desk.settle(id, atExpiry);
        assertEq(uint8(desk.getOption(id).state), uint8(CoveredCallDesk.State.ExpiredOTM));
    }

    function test_round_latestIsAccepted() public {
        uint256 id = _offerAndFill(CoveredCallDesk.Settlement.NetShare);
        uint80 r = feed.push(1, 2, 250e8, expiry - 10 minutes);   // nothing published since
        vm.warp(expiry + 6 minutes);
        desk.settle(id, r);
        assertEq(desk.getOption(id).settlementPrice, 250e6);
    }

    function test_round_answeredInEarlierRound_refused() public {
        uint256 id = _offerAndFill(CoveredCallDesk.Settlement.NetShare);
        uint80 r = feed.push(1, 2, 250e8, expiry - 4 minutes);
        feed.setAnsweredInRound(r, r - 1);
        vm.warp(expiry + 6 minutes);

        vm.expectRevert(CoveredCallDesk.BadRound.selector);
        desk.settle(id, r);
        assertEq(uint8(desk.getOption(id).state), uint8(CoveredCallDesk.State.Active));
    }

    function test_round_staleAtExpiry_refused_thenAgreed() public {
        uint256 id = _offerAndFill(CoveredCallDesk.Settlement.NetShare);
        uint80 r = feed.push(1, 2, 250e8, expiry - 27 hours);   // e.g. an expiry inside a closure
        vm.warp(expiry + 10 minutes);
        vm.expectRevert(CoveredCallDesk.BadRound.selector);
        desk.settle(id, r);

        vm.prank(writer);
        desk.proposePrice(id, 249e6);
        vm.prank(writer);
        vm.expectRevert(CoveredCallDesk.NotParty.selector);   // cannot accept its own proposal
        desk.acceptPrice(id, 249e6);
        vm.prank(mm);
        vm.expectRevert(CoveredCallDesk.NoProposal.selector); // must accept exactly what stands
        desk.acceptPrice(id, 250e6);
        vm.prank(mm);
        desk.acceptPrice(id, 249e6);
        assertEq(uint8(desk.getOption(id).state), uint8(CoveredCallDesk.State.NetSettled));
        assertEq(desk.getOption(id).settlementPrice, 249e6);
        (address by,,) = desk.proposals(id);
        assertEq(by, address(0));
    }

    function test_round_phaseBoundary() public {
        uint256 id = _offerAndFill(CoveredCallDesk.Settlement.NetShare);
        uint80 lastOfPhase1 = feed.push(1, 2, 250e8, expiry - 10 minutes);
        feed.push(2, 1, 240e8, expiry + 30 minutes);   // new aggregator, first print after expiry
        vm.warp(expiry + 1 hours);
        desk.settle(id, lastOfPhase1);
        assertEq(desk.getOption(id).settlementPrice, 250e6);
    }

    function test_round_phaseBoundary_newPhaseLiveBeforeExpiry_refused() public {
        uint256 id = _offerAndFill(CoveredCallDesk.Settlement.NetShare);
        uint80 lastOfPhase1 = feed.push(1, 2, 250e8, expiry - 10 minutes);
        uint80 firstOfPhase2 = feed.push(2, 1, 240e8, expiry - 5 minutes);
        vm.warp(expiry + 1 hours);
        vm.expectRevert(CoveredCallDesk.NotLastRoundBeforeExpiry.selector);
        desk.settle(id, lastOfPhase1);
        desk.settle(id, firstOfPhase2);
        assertEq(desk.getOption(id).settlementPrice, 240e6);
    }

    // ---------------------------------------------------------------------------------------------- fallbacks

    function test_proposeGuards() public {
        uint256 id = _offerAndFill(CoveredCallDesk.Settlement.NetShare);
        vm.prank(writer);
        vm.expectRevert(CoveredCallDesk.TooEarly.selector);
        desk.proposePrice(id, 1);
        vm.warp(expiry + 1);
        vm.prank(other);
        vm.expectRevert(CoveredCallDesk.NotParty.selector);
        desk.proposePrice(id, 1);
        vm.prank(mm);
        desk.proposePrice(id, 200e6);
        vm.prank(other);
        vm.expectRevert(CoveredCallDesk.NotParty.selector);
        desk.acceptPrice(id, 200e6);
    }

    function test_backstop() public {
        uint256 id = _offerAndFill(CoveredCallDesk.Settlement.Physical);
        vm.warp(uint256(expiry) + 14 days);
        vm.prank(owner);
        vm.expectRevert(CoveredCallDesk.TooEarly.selector);
        desk.backstopSettle(id, 250e6);
        vm.warp(uint256(expiry) + 14 days + 1);
        vm.prank(other);
        vm.expectRevert(abi.encodeWithSelector(Ownable.OwnableUnauthorizedAccount.selector, other));
        desk.backstopSettle(id, 250e6);
        vm.prank(owner);
        desk.backstopSettle(id, 250e6);
        // a backstop price nets a Physical option: the buyer gets the intrinsic value in stock, and no free tail
        assertEq(uint8(desk.getOption(id).state), uint8(CoveredCallDesk.State.NetSettled));
        assertEq(stock.balanceOf(mm), uint256(SIZE) * (250e6 - STRIKE) / 250e6);
        _assertReservedMatches();
    }

    function test_renounceDisabled() public {
        vm.prank(owner);
        vm.expectRevert(CoveredCallDesk.RenounceDisabled.selector);
        desk.renounceOwnership();
    }

    // ---------------------------------------------------------------------------------------------- frozen parties

    function test_frozenWriter_premiumOwed_claimableElsewhere() public {
        vm.prank(writer);
        uint256 id = desk.offer(_terms(CoveredCallDesk.Settlement.Physical));
        usdg.freeze(writer, true);
        vm.prank(mm);
        desk.fill(id);   // a frozen writer cannot block the buyer
        assertEq(desk.owed(address(usdg), writer), PREMIUM);
        assertEq(usdg.balanceOf(address(desk)), PREMIUM);
        _assertReservedMatches();

        // the owner cannot sweep what is owed
        vm.prank(owner);
        desk.sweep(address(usdg), owner);
        assertEq(usdg.balanceOf(owner), 0);

        address safe2 = makeAddr("safe2");
        vm.prank(writer);
        desk.claim(address(usdg), safe2);
        assertEq(usdg.balanceOf(safe2), PREMIUM);
        _assertReservedMatches();
    }

    function test_frozenBuyer_netShareOwed() public {
        uint256 id = _offerAndFill(CoveredCallDesk.Settlement.NetShare);
        uint80 r = _roundsAtExpiry(250e8);
        stock.freeze(mm, true);
        desk.settle(id, r);   // the writer still gets its part
        uint256 toBuyer = uint256(SIZE) * (250e6 - STRIKE) / 250e6;
        assertEq(stock.balanceOf(writer), 10_000e18 - toBuyer);
        assertEq(desk.owed(address(stock), mm), toBuyer);
        _assertReservedMatches();
        vm.prank(mm);
        desk.claim(address(stock), other);
        assertEq(stock.balanceOf(other), toBuyer);
        _assertReservedMatches();
    }

    function test_sweep_onlyExcess() public {
        _offerAndFill(CoveredCallDesk.Settlement.Physical);
        stock.mint(address(desk), 5e18);   // sent by mistake
        vm.prank(owner);
        desk.sweep(address(stock), owner);
        assertEq(stock.balanceOf(owner), 5e18);
        assertEq(stock.balanceOf(address(desk)), SIZE);
        _assertReservedMatches();
    }

    // ---------------------------------------------------------------------------------------------- fuzz

    function testFuzz_netShare_conserves(uint128 size, uint128 strike, uint64 price) public {
        size = uint128(bound(size, 1, 10_000e18));
        strike = uint128(bound(strike, 1, 1e12));
        price = uint64(bound(price, 1, 1e12));

        CoveredCallDesk.Terms memory t = _terms(CoveredCallDesk.Settlement.NetShare);
        t.size = size;
        t.strike = strike;
        vm.prank(writer);
        uint256 id = desk.offer(t);
        vm.prank(mm);
        desk.fill(id);

        uint80 r = feed.push(1, 2, int256(uint256(price)) * 100, expiry - 1 minutes);   // 8-decimal feed
        vm.warp(expiry + 1 hours);
        desk.settle(id, r);

        uint256 got = stock.balanceOf(mm);
        assertEq(got + stock.balanceOf(writer), 10_000e18, "conservation");
        if (price <= strike) assertEq(got, 0);
        else assertLe(got * price, uint256(size) * (price - strike));   // never more than intrinsic
        assertEq(stock.balanceOf(address(desk)), 0);
        _assertReservedMatches();
    }

    function testFuzz_physical_cost_roundsUp(uint128 size, uint128 strike) public {
        size = uint128(bound(size, 1, 10_000e18));
        strike = uint128(bound(strike, 1, 1e12));
        CoveredCallDesk.Terms memory t = _terms(CoveredCallDesk.Settlement.Physical);
        t.size = size;
        t.strike = strike;
        vm.prank(writer);
        uint256 id = desk.offer(t);
        uint256 cost = desk.exerciseCost(id);
        assertGe(cost * 1e18, uint256(size) * strike);   // never short-changes the writer
        assertLt((cost - 1) * 1e18, uint256(size) * strike); // by at most one base unit
    }

    // ---------------------------------------------------------------------------------------------- review fixes

    function test_proposal_expires_andCanBeWithdrawn() public {
        uint256 id = _offerAndFill(CoveredCallDesk.Settlement.Physical);
        feed.push(1, 2, 250e8, expiry - 27 hours);   // oracle cannot price it
        vm.warp(expiry + 1 hours);
        vm.prank(mm);
        desk.proposePrice(id, 250e6);

        // the writer cannot sit on it and accept at a moment of its choosing
        vm.warp(block.timestamp + 1 days + 1);
        vm.prank(writer);
        vm.expectRevert(CoveredCallDesk.NoProposal.selector);
        desk.acceptPrice(id, 250e6);

        vm.prank(mm);
        desk.proposePrice(id, 251e6);
        vm.prank(writer);
        vm.expectRevert(CoveredCallDesk.NoProposal.selector);
        desk.withdrawProposal(id);                    // only the proposer
        vm.prank(mm);
        desk.withdrawProposal(id);
        vm.prank(writer);
        vm.expectRevert(CoveredCallDesk.NoProposal.selector);
        desk.acceptPrice(id, 251e6);
    }

    function test_agreedPrice_givesBuyerLongWindow() public {
        uint256 id = _offerAndFill(CoveredCallDesk.Settlement.Physical);
        feed.push(1, 2, 250e8, expiry - 27 hours);
        vm.warp(expiry + 1 hours);
        vm.prank(mm);
        desk.proposePrice(id, 250e6);
        vm.warp(block.timestamp + 20 hours);
        vm.prank(writer);
        desk.acceptPrice(id, 250e6);
        assertEq(desk.getOption(id).exerciseDeadline, block.timestamp + 3 days);
        vm.warp(block.timestamp + 2 hours + 1);       // past the option's own 2h window: still exercisable
        vm.prank(mm);
        desk.exercise(id, 0, mm);
        assertEq(stock.balanceOf(mm), SIZE);
    }

    function test_cannotPayOutToDesk() public {
        uint256 id = _offerAndFill(CoveredCallDesk.Settlement.Physical);
        uint80 r = _roundsAtExpiry(250e8);
        vm.prank(mm);
        vm.expectRevert(CoveredCallDesk.BadRecipient.selector);
        desk.exercise(id, r, address(desk));
        vm.prank(mm);
        vm.expectRevert(CoveredCallDesk.BadRecipient.selector);
        desk.claim(address(stock), address(desk));
    }

    function test_exerciseToFrozenWallet_creditsBuyer() public {
        uint256 id = _offerAndFill(CoveredCallDesk.Settlement.Physical);
        uint80 r = _roundsAtExpiry(250e8);
        stock.freeze(other, true);
        vm.prank(mm);
        desk.exercise(id, r, other);
        assertEq(desk.owed(address(stock), mm), SIZE);
        assertEq(desk.owed(address(stock), other), 0);
        vm.prank(mm);
        desk.claim(address(stock), mm);
        assertEq(stock.balanceOf(mm), SIZE);
        _assertReservedMatches();
    }

    function test_relistingNeverReachesAWrittenOption() public {
        uint256 id = _offerAndFill(CoveredCallDesk.Settlement.NetShare);
        uint80 honest = feed.push(1, 2, 230e8, expiry - 4 minutes);
        RoundFeed evil = new RoundFeed();
        evil.push(1, 1, 1_000_000e8, expiry - 1);
        vm.prank(owner);
        desk.list(address(stock), address(evil), false);
        vm.warp(expiry + 1 hours);
        desk.settle(id, honest);
        assertEq(uint8(desk.getOption(id).state), uint8(CoveredCallDesk.State.ExpiredOTM));
        assertEq(stock.balanceOf(writer), 10_000e18);
    }
}

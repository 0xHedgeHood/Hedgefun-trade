// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {CoveredCallDesk} from "../src/options/CoveredCallDesk.sol";
import {FreezableToken, RoundFeed, Calendar} from "./CoveredCallDesk.t.sol";

/// A feed whose getRoundData always reverts (a deprecated / access-revoked aggregator, or a hostile listing).
contract RevertingFeed {
    uint8 public decimals = 8;
    function getRoundData(uint80) external pure returns (uint80, int256, uint256, uint256, uint80) { revert("nope"); }
    function latestRoundData() external pure returns (uint80, int256, uint256, uint256, uint80) { revert("nope"); }
}

/// Models the view a Chainlink DualAggregator gives its SECONDARY (SVR) proxy once the latest secondary round is
/// stale: `_getSyncPrimaryRound` only exposes rounds with recordedTimestamp + cutoffTime < block.timestamp. Rounds are
/// recorded at the block time they are pushed (as on chain). Phase 1 only.
contract LaggedFeed {
    uint8 public decimals = 8;
    uint256 public cutoff;
    int256[] internal _a;
    uint256[] internal _t;

    constructor(uint256 cutoff_) { cutoff = cutoff_; _a.push(); _t.push(); }

    function push(int256 a) external returns (uint80) {
        _a.push(a);
        _t.push(block.timestamp);
        return uint80((uint256(1) << 64) | (_a.length - 1));
    }

    function _visible() internal view returns (uint256 r) {
        for (r = _a.length - 1; r > 0; --r) if (_t[r] + cutoff < block.timestamp) return r;
    }

    function getRoundData(uint80 id) public view returns (uint80, int256, uint256, uint256, uint80) {
        require(uint256(id) >> 64 == 1, "no phase");
        uint256 r = uint64(id);
        if (r > _visible()) return (uint80(1 << 64), 0, 0, 0, uint80(1 << 64));
        return (id, _a[r], _t[r], _t[r], id);
    }

    function latestRoundData() external view returns (uint80, int256, uint256, uint256, uint80) {
        return getRoundData(uint80((uint256(1) << 64) | _visible()));
    }
}

/// Regression tests for the independent review of 2026-09-29. Each finding's PoC is kept, turned around to assert
/// the fixed behaviour (H1, M1, L1, L3) or, where the behaviour is by design and now documented, to pin it (L2, I1).
contract CoveredCallDeskAuditTest is Test {
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

    function _offerAndFill(CoveredCallDesk.Settlement mode) internal returns (uint256 id) {
        (address listedFeed,,) = desk.listings(address(stock));   // the writer names the feed it checked
        CoveredCallDesk.Terms memory t = CoveredCallDesk.Terms({
            buyer: mm,
            underlying: address(stock),
            size: SIZE,
            strike: STRIKE,
            premium: PREMIUM,
            expiry: expiry,
            fillDeadline: T0 + 1 hours,
            mode: mode,
            feed: listedFeed,
            exerciseWindow: 2 hours,
            stockMultiplier: 1e18
        });
        vm.prank(writer);
        id = desk.offer(t);
        vm.prank(mm);
        desk.fill(id);
    }

    function _state(uint256 id) internal view returns (CoveredCallDesk.State) {
        return desk.getOption(id).state;
    }

    // ------------------------------------------------------------------------------------------------ H-1
    /// fixed: the feed is copied into the option at offer, so re-listing the stock cannot re-price a written option
    function test_H1_relistCannotRepriceLiveOption() public {
        uint256 id = _offerAndFill(CoveredCallDesk.Settlement.NetShare);
        uint80 honest = feed.push(1, 2, 230e8, expiry - 4 minutes);   // real market: OTM, writer keeps everything
        feed.push(1, 3, 231e8, expiry + 1 hours);
        vm.warp(expiry + 2 hours);

        RoundFeed evil = new RoundFeed();
        uint80 rid = evil.push(1, 1, 1_000_000e8, expiry - 1);
        vm.prank(owner);
        desk.list(address(stock), address(evil), false);

        vm.expectRevert(CoveredCallDesk.BadRound.selector);          // the evil round id means nothing to the option's feed
        desk.settle(id, rid);
        desk.settle(id, honest);
        assertEq(uint8(_state(id)), uint8(CoveredCallDesk.State.ExpiredOTM));
        assertEq(stock.balanceOf(mm), 0);
    }

    /// fixed: nor block oracle settlement by listing a dead feed
    function test_H1b_relistToDeadFeed_doesNotBlockSettlement() public {
        uint256 id = _offerAndFill(CoveredCallDesk.Settlement.Physical);
        uint80 r = feed.push(1, 2, 250e8, expiry - 4 minutes);
        vm.warp(expiry + 10 minutes);
        RevertingFeed dead = new RevertingFeed();
        vm.prank(owner);
        desk.list(address(stock), address(dead), false);
        vm.prank(mm);
        desk.exercise(id, r, mm);
        assertEq(stock.balanceOf(mm), SIZE);
    }

    // ------------------------------------------------------------------------------------------------ M-1
    /// fixed: a proposal expires after PROPOSAL_TTL, and a non-oracle price opens MAX_EXERCISE_WINDOW
    function test_M1_writerCannotChooseTheBuyersClock() public {
        uint256 id = _offerAndFill(CoveredCallDesk.Settlement.Physical);
        feed.push(1, 2, 250e8, expiry - 27 hours);   // oracle cannot price the expiry
        vm.warp(expiry + 1);
        vm.prank(mm);
        desk.proposePrice(id, 250e6);

        vm.warp(expiry + 5 days + 7 hours);           // sitting on it no longer works
        vm.prank(writer);
        vm.expectRevert(CoveredCallDesk.NoProposal.selector);
        desk.acceptPrice(id, 250e6);

        vm.prank(mm);
        desk.proposePrice(id, 250e6);
        vm.warp(block.timestamp + 23 hours);          // accepted at the latest moment the TTL allows
        vm.prank(writer);
        desk.acceptPrice(id, 250e6);
        vm.warp(block.timestamp + 2 hours + 1);       // the option's own window has passed...
        vm.expectRevert(CoveredCallDesk.TooEarly.selector);
        desk.lapse(id);                               // ...but the buyer still has MAX_EXERCISE_WINDOW
        vm.prank(mm);
        desk.exercise(id, 0, mm);
        assertEq(stock.balanceOf(mm), SIZE);
    }

    // ------------------------------------------------------------------------------------------------ L-1
    /// fixed: nothing may be priced until SETTLE_DELAY after expiry, by when a lagged view has caught up
    function test_L1_settleDelayOutlastsFeedLag() public {
        LaggedFeed lf = new LaggedFeed(60);
        vm.prank(owner);
        desk.list(address(stock), address(lf), true);   // listed BEFORE the offer: the option copies it
        uint256 id = _offerAndFill(CoveredCallDesk.Settlement.NetShare);

        vm.warp(expiry - 2 hours);
        uint80 older = lf.push(230e8);                // OTM print
        vm.warp(expiry - 5);
        uint80 atExpiry = lf.push(250e8);             // the real last print before expiry: ITM

        vm.warp(expiry + 1);
        vm.expectRevert(CoveredCallDesk.TooEarly.selector);
        desk.settle(id, older);                       // the stale-view window is closed

        vm.warp(expiry + 5 minutes + 1);
        vm.expectRevert(CoveredCallDesk.NotLastRoundBeforeExpiry.selector);
        desk.settle(id, older);
        desk.settle(id, atExpiry);
        assertEq(uint8(_state(id)), uint8(CoveredCallDesk.State.NetSettled));
    }

    // ------------------------------------------------------------------------------------------------ L-2
    /// by design, now documented: the backstop is trusted. 14 days after an expiry nobody settled or agreed, the
    /// owner's price stands whether or not the oracle could have priced it.
    function test_L2_backstopIsTrusted() public {
        uint256 id = _offerAndFill(CoveredCallDesk.Settlement.NetShare);
        uint80 r = feed.push(1, 2, 230e8, expiry - 4 minutes);
        vm.warp(uint256(expiry) + 14 days + 1);
        assertEq(desk.settlementPriceOf(id, r), 230e6);   // still provable: OTM
        vm.prank(owner);
        desk.backstopSettle(id, 1_000e6);
        assertEq(uint8(_state(id)), uint8(CoveredCallDesk.State.NetSettled));
    }

    // ------------------------------------------------------------------------------------------------ L-3
    /// fixed: a payout cannot be sent to the desk itself, where it would become sweepable
    function test_L3_payoutToDeskRefused() public {
        uint256 id = _offerAndFill(CoveredCallDesk.Settlement.Physical);
        uint80 r = feed.push(1, 2, 250e8, expiry - 4 minutes);
        vm.warp(expiry + 10 minutes);
        vm.prank(mm);
        vm.expectRevert(CoveredCallDesk.BadRecipient.selector);
        desk.exercise(id, r, address(desk));
    }

    // ------------------------------------------------------------------------------------------------ I-1
    /// by design, now documented: the proxy's switch to a new aggregator is not visible on chain, so when the new
    /// aggregator was already reporting before expiry its round is the one accepted
    function test_I1_aggregatorSwitchNearExpiry() public {
        uint256 id = _offerAndFill(CoveredCallDesk.Settlement.NetShare);
        uint80 served = feed.push(1, 2, 250e8, expiry - 10 minutes);
        feed.push(2, 1, 249e8, expiry - 3 days);
        uint80 p2 = feed.push(2, 2, 251e8, expiry - 5 minutes);
        feed.push(2, 3, 252e8, expiry + 1 hours);
        vm.warp(expiry + 2 hours);
        vm.expectRevert(CoveredCallDesk.NotLastRoundBeforeExpiry.selector);
        desk.settle(id, served);
        desk.settle(id, p2);
        assertEq(desk.getOption(id).settlementPrice, 251e6);
    }
}

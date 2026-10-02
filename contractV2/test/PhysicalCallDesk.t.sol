// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {PhysicalCallDesk} from "../src/options/PhysicalCallDesk.sol";
import {CoveredCallDesk} from "../src/options/CoveredCallDesk.sol";
import {FreezableToken, RoundFeed, Calendar} from "./CoveredCallDesk.t.sol";

/// @dev Transfer modes expose non-standard ERC-20 behavior that could corrupt escrow accounting.
contract HostileStockToken is ERC20 {
    uint256 public uiMultiplier = 1e18;
    bool public oraclePaused;
    uint8 public mode; // 0 normal, 1 sender fee on top, 2 transfer then return false, 3 revert
    address public feeRecipient = address(0xFEE);

    constructor() ERC20("Hostile RHNVDA", "hRHNVDA") {}

    function mint(address to, uint256 amount) external {
        _mint(to, amount);
    }

    function setMode(uint8 newMode) external {
        mode = newMode;
    }

    function transfer(address to, uint256 amount) public override returns (bool) {
        if (mode == 3) revert("blocked");
        if (mode == 1) _transfer(msg.sender, feeRecipient, 1e18);
        _transfer(msg.sender, to, amount);
        return mode != 2;
    }
}

contract PhysicalCallDeskTest is Test {
    PhysicalCallDesk desk;
    FreezableToken usdg;
    FreezableToken stock;
    RoundFeed feed;
    Calendar cal;

    address owner = makeAddr("owner");
    address writer = makeAddr("writer");
    address buyer = makeAddr("wintermute");
    address recipient = makeAddr("stock recipient");
    address other = makeAddr("other");

    uint64 constant T0 = 1_790_000_000;
    uint64 expiry;
    uint128 constant SIZE = 400e18;
    uint128 constant STRIKE = 250e6;
    uint128 constant PREMIUM = 500e6;
    uint256 constant COST = 100_000e6;

    function setUp() public {
        vm.warp(T0);
        usdg = new FreezableToken("USDG", 6);
        stock = new FreezableToken("RHNVDA", 18);
        feed = new RoundFeed();
        cal = new Calendar();
        desk = new PhysicalCallDesk(owner, IERC20(address(usdg)), cal);

        vm.startPrank(owner);
        desk.list(address(stock), address(feed), true);
        desk.setWriter(writer, true);
        desk.setBuyer(buyer, true);
        vm.stopPrank();

        stock.mint(writer, SIZE);
        usdg.mint(buyer, COST + PREMIUM);
        vm.prank(writer);
        stock.approve(address(desk), type(uint256).max);
        vm.prank(buyer);
        usdg.approve(address(desk), type(uint256).max);

        expiry = T0 + 7 days;
        feed.push(1, 1, 240e8, T0 - 1 hours);
    }

    function _terms(PhysicalCallDesk.Settlement mode) internal view returns (PhysicalCallDesk.Terms memory) {
        return PhysicalCallDesk.Terms({
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

    function _offerAndFill() internal returns (uint256 id) {
        vm.prank(writer);
        id = desk.offer(_terms(PhysicalCallDesk.Settlement.Physical));
        vm.prank(buyer);
        desk.fill(id);
    }

    function _settleAtExpiry(uint256 id, uint256 price) internal {
        uint80 round = feed.push(1, 2, int256(price * 100), expiry - 1 minutes);
        feed.push(1, 3, int256(price * 100), expiry + 1 minutes);
        vm.warp(expiry + desk.SETTLE_DELAY() + 1);
        desk.settle(id, round);
    }

    function _backstopITM(uint256 id) internal {
        vm.warp(uint256(expiry) + desk.BACKSTOP_DELAY() + 1);
        vm.prank(owner);
        desk.backstopSettle(id, STRIKE + 20e6);
    }

    function _offerHostileStock() internal returns (HostileStockToken hostile, uint256 id) {
        hostile = new HostileStockToken();
        vm.prank(owner);
        desk.list(address(hostile), address(feed), true);
        hostile.mint(writer, SIZE);
        vm.prank(writer);
        hostile.approve(address(desk), type(uint256).max);
        PhysicalCallDesk.Terms memory t = _terms(PhysicalCallDesk.Settlement.Physical);
        t.underlying = address(hostile);
        vm.prank(writer);
        id = desk.offer(t);
        vm.prank(buyer);
        desk.fill(id);
    }

    function test_onlyPhysicalOffersAccepted() public {
        vm.prank(writer);
        vm.expectRevert(PhysicalCallDesk.BadTerms.selector);
        desk.offer(_terms(PhysicalCallDesk.Settlement.NetShare));
        assertEq(stock.balanceOf(address(desk)), 0);
    }

    function test_v1AbiCanOperatePhysicalDeskForVaultCompatibility() public {
        CoveredCallDesk v1Abi = CoveredCallDesk(address(desk));
        CoveredCallDesk.Terms memory t = CoveredCallDesk.Terms({
            buyer: buyer,
            underlying: address(stock),
            size: SIZE,
            strike: STRIKE,
            premium: PREMIUM,
            expiry: expiry,
            fillDeadline: T0 + 1 hours,
            mode: CoveredCallDesk.Settlement.Physical,
            feed: address(feed),
            exerciseWindow: 2 hours,
            stockMultiplier: 1e18
        });
        vm.prank(writer);
        uint256 id = v1Abi.offer(t);
        CoveredCallDesk.Option memory o = v1Abi.getOption(id);
        assertEq(o.writer, writer);
        assertEq(o.size, SIZE);
        assertEq(uint8(o.mode), uint8(CoveredCallDesk.Settlement.Physical));
        vm.prank(buyer);
        v1Abi.fill(id);
        assertEq(uint8(v1Abi.getOption(id).state), uint8(CoveredCallDesk.State.Active));
    }

    function test_normalExercisePaysFullStrikeThenDeliversAllStockToRecipient() public {
        uint256 id = _offerAndFill();
        assertEq(stock.balanceOf(address(desk)), SIZE);
        assertEq(usdg.balanceOf(writer), PREMIUM);

        _settleAtExpiry(id, STRIKE + 20e6);
        assertEq(uint8(desk.getOption(id).state), uint8(PhysicalCallDesk.State.Exercisable));
        assertEq(stock.balanceOf(buyer), 0);
        assertEq(stock.balanceOf(recipient), 0);
        assertEq(desk.exerciseCost(id), COST);

        vm.prank(buyer);
        desk.exercise(id, 0, recipient);
        assertEq(stock.balanceOf(recipient), SIZE);
        assertEq(stock.balanceOf(address(desk)), 0);
        assertEq(usdg.balanceOf(writer), PREMIUM + COST);
        assertEq(uint8(desk.getOption(id).state), uint8(PhysicalCallDesk.State.Exercised));
        assertEq(desk.reserved(address(stock)), 0);
        assertEq(desk.reserved(address(usdg)), 0);
    }

    function test_writerPriceAcceptedByBuyerOpensQuotedPhysicalWindow() public {
        uint256 id = _offerAndFill();
        vm.warp(uint256(expiry) + 1);
        vm.prank(writer);
        desk.proposePrice(id, STRIKE + 20e6);
        vm.prank(buyer);
        desk.acceptPrice(id, STRIKE + 20e6);
        PhysicalCallDesk.Option memory o = desk.getOption(id);
        assertEq(uint8(o.state), uint8(PhysicalCallDesk.State.Exercisable));
        assertEq(o.exerciseDeadline, uint64(block.timestamp + 2 hours));
        assertEq(stock.balanceOf(address(desk)), SIZE);
        assertEq(stock.balanceOf(buyer), 0);
    }

    function test_buyerPriceAcceptedByWriterOpensFullPhysicalWindow() public {
        uint256 id = _offerAndFill();
        vm.warp(uint256(expiry) + 1);
        vm.prank(buyer);
        desk.proposePrice(id, STRIKE + 20e6);
        vm.prank(writer);
        desk.acceptPrice(id, STRIKE + 20e6);
        PhysicalCallDesk.Option memory o = desk.getOption(id);
        assertEq(uint8(o.state), uint8(PhysicalCallDesk.State.Exercisable));
        assertEq(o.exerciseDeadline, uint64(block.timestamp + desk.MAX_EXERCISE_WINDOW()));
        assertEq(stock.balanceOf(address(desk)), SIZE);
        assertEq(stock.balanceOf(buyer), 0);
    }

    function test_backstopITMRequiresFullStrikeAndNeverNetShare() public {
        uint256 id = _offerAndFill();
        vm.warp(uint256(expiry) + desk.BACKSTOP_DELAY());
        vm.prank(owner);
        vm.expectRevert(PhysicalCallDesk.TooEarly.selector);
        desk.backstopSettle(id, STRIKE + 20e6);

        _backstopITM(id);
        PhysicalCallDesk.Option memory o = desk.getOption(id);
        assertEq(uint8(o.state), uint8(PhysicalCallDesk.State.Exercisable));
        assertEq(o.exerciseDeadline, uint64(block.timestamp + desk.MAX_EXERCISE_WINDOW()));
        assertEq(stock.balanceOf(address(desk)), SIZE);
        assertEq(stock.balanceOf(buyer), 0);
        assertEq(stock.balanceOf(writer), 0);
        assertEq(desk.reserved(address(stock)), SIZE);
        assertEq(usdg.balanceOf(writer), PREMIUM);

        vm.prank(buyer);
        usdg.transfer(other, COST);
        vm.prank(buyer);
        vm.expectRevert();
        desk.exercise(id, 0, buyer);
        assertEq(uint8(desk.getOption(id).state), uint8(PhysicalCallDesk.State.Exercisable));
        assertEq(stock.balanceOf(address(desk)), SIZE);
        assertEq(stock.balanceOf(buyer), 0);

        vm.prank(other);
        usdg.transfer(buyer, COST);
        vm.prank(buyer);
        desk.exercise(id, 0, recipient);
        assertEq(stock.balanceOf(recipient), SIZE);
        assertEq(usdg.balanceOf(writer), PREMIUM + COST);
        assertEq(uint8(desk.getOption(id).state), uint8(PhysicalCallDesk.State.Exercised));
    }

    function test_backstopITMLapseReturnsAllStockToWriter() public {
        uint256 id = _offerAndFill();
        _backstopITM(id);
        uint256 deadline = desk.getOption(id).exerciseDeadline;
        vm.warp(deadline);
        vm.expectRevert(PhysicalCallDesk.TooEarly.selector);
        desk.lapse(id);
        vm.warp(deadline + 1);
        desk.lapse(id);
        assertEq(stock.balanceOf(writer), SIZE);
        assertEq(stock.balanceOf(buyer), 0);
        assertEq(stock.balanceOf(address(desk)), 0);
        assertEq(desk.reserved(address(stock)), 0);
        assertEq(uint8(desk.getOption(id).state), uint8(PhysicalCallDesk.State.Lapsed));
        vm.prank(buyer);
        vm.expectRevert(PhysicalCallDesk.NotInTheMoney.selector);
        desk.exercise(id, 0, buyer);
    }

    function test_backstopOTMReturnsAllStockImmediately() public {
        uint256 id = _offerAndFill();
        vm.warp(uint256(expiry) + desk.BACKSTOP_DELAY() + 1);
        vm.prank(owner);
        desk.backstopSettle(id, STRIKE);
        assertEq(stock.balanceOf(writer), SIZE);
        assertEq(stock.balanceOf(buyer), 0);
        assertEq(uint8(desk.getOption(id).state), uint8(PhysicalCallDesk.State.ExpiredOTM));
    }

    function test_backstopOwnerOnlyAndCannotRepriceExercise() public {
        uint256 id = _offerAndFill();
        vm.warp(uint256(expiry) + desk.BACKSTOP_DELAY() + 1);
        vm.prank(other);
        vm.expectRevert();
        desk.backstopSettle(id, STRIKE + 20e6);
        vm.prank(owner);
        desk.backstopSettle(id, STRIKE + 20e6);
        vm.prank(owner);
        vm.expectRevert(PhysicalCallDesk.WrongState.selector);
        desk.backstopSettle(id, STRIKE + 100e6);
    }

    function test_frozenRecipientGetsOwedStockButStrikeIsPaid() public {
        uint256 id = _offerAndFill();
        _backstopITM(id);
        stock.freeze(recipient, true);
        vm.prank(buyer);
        desk.exercise(id, 0, recipient);
        assertEq(stock.balanceOf(address(desk)), SIZE);
        assertEq(stock.balanceOf(recipient), 0);
        assertEq(desk.owed(address(stock), buyer), SIZE);
        assertEq(desk.reserved(address(stock)), SIZE);
        assertEq(usdg.balanceOf(writer), PREMIUM + COST);

        vm.prank(buyer);
        desk.claim(address(stock), other);
        assertEq(stock.balanceOf(other), SIZE);
        assertEq(desk.owed(address(stock), buyer), 0);
        assertEq(desk.reserved(address(stock)), 0);
    }

    function test_transferFeeCannotTurnExerciseIntoPartialStockDelivery() public {
        uint256 id = _offerAndFill();
        _backstopITM(id);
        stock.setFee(100);

        vm.prank(buyer);
        vm.expectRevert(PhysicalCallDesk.Unexpected.selector);
        desk.exercise(id, 0, recipient);
        assertEq(stock.balanceOf(recipient), 0);
        assertEq(stock.balanceOf(address(desk)), SIZE);
        assertEq(usdg.balanceOf(writer), PREMIUM);
        assertEq(usdg.balanceOf(buyer), COST);
        assertEq(uint8(desk.getOption(id).state), uint8(PhysicalCallDesk.State.Exercisable));

        stock.setFee(0);
        vm.prank(buyer);
        desk.exercise(id, 0, recipient);
        assertEq(stock.balanceOf(recipient), SIZE);
        assertEq(usdg.balanceOf(writer), PREMIUM + COST);
    }

    function test_senderFeeOnTopCannotErodeOtherEscrow() public {
        (HostileStockToken hostile, uint256 id) = _offerHostileStock();
        _backstopITM(id);
        hostile.mint(address(desk), 1e18); // surplus makes a fee-on-top transfer otherwise possible
        hostile.setMode(1);

        vm.prank(buyer);
        vm.expectRevert(PhysicalCallDesk.Unexpected.selector);
        desk.exercise(id, 0, recipient);
        assertEq(hostile.balanceOf(address(desk)), SIZE + 1e18);
        assertEq(hostile.balanceOf(recipient), 0);
        assertEq(hostile.balanceOf(hostile.feeRecipient()), 0);
        assertEq(desk.reserved(address(hostile)), SIZE);
        assertEq(usdg.balanceOf(writer), PREMIUM);
        assertEq(uint8(desk.getOption(id).state), uint8(PhysicalCallDesk.State.Exercisable));

        hostile.setMode(0);
        vm.prank(buyer);
        desk.exercise(id, 0, recipient);
        assertEq(hostile.balanceOf(recipient), SIZE);
        assertEq(hostile.balanceOf(address(desk)), 1e18);
    }

    function test_falseReturnAfterMovementCannotCreatePhantomOwedStock() public {
        (HostileStockToken hostile, uint256 id) = _offerHostileStock();
        _backstopITM(id);
        hostile.setMode(2);

        vm.prank(buyer);
        vm.expectRevert(PhysicalCallDesk.Unexpected.selector);
        desk.exercise(id, 0, recipient);
        assertEq(hostile.balanceOf(address(desk)), SIZE);
        assertEq(hostile.balanceOf(recipient), 0);
        assertEq(desk.owed(address(hostile), buyer), 0);
        assertEq(desk.reserved(address(hostile)), SIZE);
        assertEq(usdg.balanceOf(writer), PREMIUM);

        hostile.setMode(0);
        vm.prank(buyer);
        desk.exercise(id, 0, recipient);
        assertEq(hostile.balanceOf(recipient), SIZE);
    }

    function test_claimVerifiesSenderDebitAndPreservesOwedOnFailure() public {
        (HostileStockToken hostile, uint256 id) = _offerHostileStock();
        _backstopITM(id);
        hostile.setMode(3);
        vm.prank(buyer);
        desk.exercise(id, 0, recipient);
        assertEq(desk.owed(address(hostile), buyer), SIZE);
        assertEq(hostile.balanceOf(address(desk)), SIZE);

        hostile.mint(address(desk), 1e18);
        hostile.setMode(1);
        vm.prank(buyer);
        vm.expectRevert(PhysicalCallDesk.Unexpected.selector);
        desk.claim(address(hostile), recipient);
        assertEq(desk.owed(address(hostile), buyer), SIZE);
        assertEq(desk.reserved(address(hostile)), SIZE);
        assertEq(hostile.balanceOf(address(desk)), SIZE + 1e18);
        assertEq(hostile.balanceOf(recipient), 0);

        hostile.setMode(0);
        vm.prank(buyer);
        desk.claim(address(hostile), recipient);
        assertEq(desk.owed(address(hostile), buyer), 0);
        assertEq(hostile.balanceOf(recipient), SIZE);
    }

    function test_pauseDoesNotBlockBackstopOrExercise() public {
        uint256 id = _offerAndFill();
        vm.prank(owner);
        desk.setPaused(true);
        _backstopITM(id);
        vm.prank(buyer);
        desk.exercise(id, 0, buyer);
        assertEq(stock.balanceOf(buyer), SIZE);
    }
}

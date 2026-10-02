// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {CoveredCallDesk} from "../src/options/CoveredCallDesk.sol";
import {EarnVault} from "../src/options/EarnVault.sol";
import {PhysicalCallDesk} from "../src/options/PhysicalCallDesk.sol";
import {PriceOracle} from "../src/PriceOracle.sol";
import {FreezableToken, RoundFeed, Calendar} from "./CoveredCallDesk.t.sol";

/// @dev Simulates an ERC-20 that moves stock but reports false to the caller.
contract FalseReturnAfterTransferStock is FreezableToken {
    bool public falseReturn;

    constructor() FreezableToken("RHNVDA", 18) {}

    function setFalseReturn(bool enabled) external {
        falseReturn = enabled;
    }

    function transfer(address to, uint256 amount) public override returns (bool) {
        super.transfer(to, amount);
        return !falseReturn;
    }
}

contract EarnVaultPhysicalCallTest is Test {
    uint64 internal constant T0 = 1_790_000_000;
    uint256 internal constant SEED = 440 ether;
    uint256 internal constant CALL_SIZE = 400 ether;
    uint256 internal constant STRIKE_COST = 94_000e6;
    uint256 internal constant PREMIUM = 500e6;

    FalseReturnAfterTransferStock internal stock;
    FreezableToken internal usdg;
    RoundFeed internal stockFeed;
    RoundFeed internal usdgFeed;
    Calendar internal calendar;
    PhysicalCallDesk internal callDesk;
    PriceOracle internal oracle;
    EarnVault internal vault;

    address internal alice;
    address internal wintermute;

    function setUp() public {
        vm.warp(T0);
        alice = makeAddr("alice");
        wintermute = makeAddr("wintermute");
        stock = new FalseReturnAfterTransferStock();
        usdg = new FreezableToken("USDG", 6);
        stockFeed = new RoundFeed();
        usdgFeed = new RoundFeed();
        calendar = new Calendar();
        stockFeed.push(1, 1, 227e8, T0 - 1 minutes);
        usdgFeed.push(1, 1, 1e8, T0 - 1 hours);

        callDesk = new PhysicalCallDesk(address(this), IERC20(address(usdg)), calendar);
        oracle = new PriceOracle(
            address(stock), address(stockFeed), address(usdgFeed), address(calendar), 26 hours, 26 hours
        );
        // PhysicalCallDesk deliberately shares CoveredCallDesk's external ABI and Option layout.
        vault = new EarnVault(
            address(this),
            IERC20(address(stock)),
            IERC20(address(usdg)),
            CoveredCallDesk(address(callDesk)),
            oracle,
            address(0xBEEF)
        );
        callDesk.list(address(stock), address(stockFeed), true);
        callDesk.setWriter(address(vault), true);
        callDesk.setBuyer(wintermute, true);
        vault.setEligible(alice, true);
        vault.setBuyer(wintermute, true);

        stock.mint(alice, SEED);
        usdg.mint(wintermute, 100_000e6);
        vm.prank(alice);
        stock.approve(address(vault), type(uint256).max);
        vm.prank(wintermute);
        usdg.approve(address(callDesk), type(uint256).max);
    }

    function _seed() internal {
        vm.prank(alice);
        vault.requestDeposit(SEED);
        vault.closeEpoch();
        vm.prank(alice);
        uint256 shares = vault.claimDeposit(1, alice);
        assertEq(shares, SEED - vault.MIN_DEAD_SHARES());
        assertEq(vault.totalSupply(), SEED);
    }

    function test_physicalCallThroughVaultPaysStrikeAndRedeemsActualBasket() public {
        _seed();
        uint64 expiry = uint64(block.timestamp + 7 days);
        CoveredCallDesk.Terms memory terms = CoveredCallDesk.Terms({
            buyer: wintermute,
            underlying: address(stock),
            size: uint128(CALL_SIZE),
            strike: 235e6,
            premium: uint128(PREMIUM),
            expiry: expiry,
            fillDeadline: uint64(block.timestamp + 2 minutes),
            mode: CoveredCallDesk.Settlement.Physical,
            feed: address(stockFeed),
            exerciseWindow: callDesk.exerciseWindow(),
            stockMultiplier: stock.uiMultiplier()
        });

        uint256 id = vault.offer(terms);
        assertEq(stock.balanceOf(address(callDesk)), CALL_SIZE);
        assertEq(stock.balanceOf(address(vault)), 40 ether);
        vm.prank(wintermute);
        callDesk.fill(id);
        assertEq(usdg.balanceOf(address(vault)), PREMIUM);

        vm.prank(alice);
        vault.requestRedeem(110 ether);
        uint80 expiryRound = stockFeed.push(1, 2, 250e8, expiry - 4 minutes);
        stockFeed.push(1, 3, 250e8, expiry + 1 minutes);
        usdgFeed.push(1, 2, 1e8, expiry + 1 minutes);
        vm.warp(uint256(expiry) + callDesk.SETTLE_DELAY() + 1);
        callDesk.settle(id, expiryRound);
        assertEq(uint8(callDesk.getOption(id).state), uint8(PhysicalCallDesk.State.Exercisable));

        vm.prank(wintermute);
        callDesk.exercise(id, 0, wintermute);
        assertEq(uint8(callDesk.getOption(id).state), uint8(PhysicalCallDesk.State.Exercised));
        assertEq(stock.balanceOf(wintermute), CALL_SIZE);
        assertEq(stock.balanceOf(address(callDesk)), 0);
        assertEq(usdg.balanceOf(wintermute), 100_000e6 - PREMIUM - STRIKE_COST);
        assertEq(stock.balanceOf(address(vault)), 40 ether);
        assertEq(usdg.balanceOf(address(vault)), PREMIUM + STRIKE_COST);

        vault.closeEpoch();
        (bool closed, uint256 optionId, uint256 price, uint256 nav, uint256 supply,,,,,) = vault.epochs(2);
        assertTrue(closed);
        assertEq(optionId, id);
        assertEq(price, 250e18);
        assertEq(nav, 104_500e6);
        assertEq(supply, SEED);
        assertEq(vault.totalSupply(), 330 ether);
        assertEq(vault.claimableStock(), 10 ether);
        assertEq(vault.claimableUSDG(), 23_625e6);

        vm.prank(alice);
        (uint256 stockOut, uint256 usdgOut) = vault.claimRedeem(2, alice);
        assertEq(stockOut, 10 ether);
        assertEq(usdgOut, 23_625e6);
        assertEq(stock.balanceOf(alice), 10 ether);
        assertEq(usdg.balanceOf(alice), 23_625e6);
        assertEq(stock.balanceOf(address(vault)), 30 ether);
        assertEq(usdg.balanceOf(address(vault)), 70_875e6);
        assertEq(vault.claimableStock(), 0);
        assertEq(vault.claimableUSDG(), 0);
        assertEq(vault.balanceOf(alice), 330 ether - vault.MIN_DEAD_SHARES());
    }

    function test_falseReturningStockCannotCreatePhantomRedeemDebt() public {
        _seed();
        vm.prank(alice);
        vault.requestRedeem(110 ether);
        vault.closeEpoch();
        stock.setFalseReturn(true);

        vm.prank(alice);
        vm.expectRevert(EarnVault.UnsafeAssets.selector);
        vault.claimRedeem(2, alice);
        assertEq(stock.balanceOf(address(vault)), SEED);
        assertEq(stock.balanceOf(alice), 0);
        assertEq(vault.owedRedeemStock(alice), 0);
        assertEq(vault.claimableStock(), 110 ether);
        assertEq(vault.redeemRequests(2, alice), 110 ether);
    }
}

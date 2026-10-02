// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {Math} from "@openzeppelin/contracts/utils/math/Math.sol";
import {CoveredCallDesk} from "../src/options/CoveredCallDesk.sol";
import {EarnVault} from "../src/options/EarnVault.sol";
import {PriceOracle} from "../src/PriceOracle.sol";
import {FreezableToken, RoundFeed, Calendar} from "./CoveredCallDesk.t.sol";

/// @notice Integrated vault/desk tests with the real option and oracle accounting paths.
contract EarnVaultTest is Test {
    uint64 internal constant T0 = 1_790_000_000;
    uint256 internal constant P0 = 227e18;
    uint256 internal constant SEED = 440 ether;
    uint256 internal constant PREMIUM = 500e6;

    FreezableToken internal usdg;
    FreezableToken internal stock;
    RoundFeed internal stockFeed;
    RoundFeed internal usdgFeed;
    Calendar internal cal;
    CoveredCallDesk internal desk;
    PriceOracle internal oracle;
    EarnVault internal vault;

    address internal alice = makeAddr("alice");
    address internal bob = makeAddr("bob");
    address internal mm = makeAddr("wintermute");
    address internal outsider = makeAddr("outsider");
    address internal operator = makeAddr("operator");
    uint64 internal nextStockRound = 2;
    uint64 internal nextUsdgRound = 2;

    function setUp() public {
        vm.warp(T0);
        usdg = new FreezableToken("USDG", 6);
        stock = new FreezableToken("RHNVDA", 18);
        stockFeed = new RoundFeed();
        usdgFeed = new RoundFeed();
        cal = new Calendar();
        stockFeed.push(1, 1, 227e8, T0 - 1 minutes);
        usdgFeed.push(1, 1, 1e8, T0 - 1 hours);
        desk = new CoveredCallDesk(address(this), IERC20(address(usdg)), cal);
        oracle =
            new PriceOracle(address(stock), address(stockFeed), address(usdgFeed), address(cal), 26 hours, 26 hours);
        vault =
            new EarnVault(address(this), IERC20(address(stock)), IERC20(address(usdg)), desk, oracle, address(0xBEEF));

        desk.list(address(stock), address(stockFeed), true);
        desk.setWriter(address(vault), true);
        desk.setBuyer(mm, true);
        vault.setEligible(alice, true);
        vault.setEligible(bob, true);
        vault.setBuyer(mm, true);
        vault.setOperator(operator);

        stock.mint(alice, 1_000 ether);
        stock.mint(bob, 1_000 ether);
        usdg.mint(mm, 1_000_000e6);
        vm.prank(alice);
        stock.approve(address(vault), type(uint256).max);
        vm.prank(bob);
        stock.approve(address(vault), type(uint256).max);
        vm.prank(mm);
        usdg.approve(address(desk), type(uint256).max);
    }

    function _seed() internal {
        vm.prank(alice);
        vault.requestDeposit(SEED);
        vault.closeEpoch();
        vm.prank(alice);
        assertEq(vault.claimDeposit(1, alice), SEED - vault.MIN_DEAD_SHARES());
        assertEq(vault.totalSupply(), SEED);
        assertEq(vault.balanceOf(alice), SEED - vault.MIN_DEAD_SHARES());
        assertEq(vault.currentEpoch(), 2);
    }

    function _terms() internal view returns (CoveredCallDesk.Terms memory t) {
        return CoveredCallDesk.Terms({
            buyer: mm,
            underlying: address(stock),
            size: 400 ether,
            strike: 235e6,
            premium: uint128(PREMIUM),
            expiry: uint64(block.timestamp + 7 days),
            fillDeadline: uint64(block.timestamp + 2 minutes),
            mode: CoveredCallDesk.Settlement.Physical,
            feed: address(stockFeed),
            exerciseWindow: desk.exerciseWindow(),
            stockMultiplier: stock.uiMultiplier()
        });
    }

    function _offerAndFill() internal returns (uint256 id) {
        id = vault.offer(_terms());
        vm.prank(mm);
        desk.fill(id);
    }

    function _settle(uint256 id, int256 price) internal {
        uint64 expiry = desk.getOption(id).expiry;
        uint80 r = stockFeed.push(1, nextStockRound++, price, expiry - 4 minutes);
        stockFeed.push(1, nextStockRound++, price, expiry + 1 minutes);
        usdgFeed.push(1, nextUsdgRound++, 1e8, expiry + 1 minutes);
        vm.warp(uint256(expiry) + desk.SETTLE_DELAY() + 1);
        desk.settle(id, r);
    }

    function test_initialSeedExcludesPendingDepositAndClaimsAtOneToOne() public {
        vm.prank(alice);
        vault.requestDeposit(400 ether);
        vm.prank(bob);
        vault.requestDeposit(40 ether);
        assertEq(vault.pendingDepositStock(), SEED);
        (uint256 freeStock, uint256 freeUsdg) = vault.freeBalances();
        assertEq(freeStock, 0);
        assertEq(freeUsdg, 0);
        assertEq(vault.totalSupply(), 0);
        CoveredCallDesk.Terms memory t = _terms();
        vm.expectRevert(EarnVault.UnsafeAssets.selector);
        vault.offer(t);

        vault.closeEpoch();
        assertEq(vault.totalSupply(), SEED);
        uint256 firstPool = SEED - vault.MIN_DEAD_SHARES();
        uint256 expectedAlice = Math.mulDiv(400 ether, firstPool, SEED);
        uint256 expectedBob = Math.mulDiv(40 ether, firstPool, SEED);
        vm.prank(alice);
        assertEq(vault.claimDeposit(1, alice), expectedAlice);
        vm.prank(bob);
        assertEq(vault.claimDeposit(1, bob), expectedBob);
        assertEq(vault.balanceOf(address(vault)), 0);
        assertEq(vault.balanceOf(alice) + vault.balanceOf(bob) + vault.balanceOf(vault.DEAD_SHARES_HOLDER()), SEED);
    }

    function test_pendingDepositExcludedFromRfqAndCannotBeDilutedByPremium() public {
        _seed();
        vm.prank(bob);
        vault.requestDeposit(100 ether);
        (uint256 freeStock,) = vault.freeBalances();
        assertEq(freeStock, SEED);
        CoveredCallDesk.Terms memory t = _terms();
        t.size = 441 ether;
        vm.expectRevert(EarnVault.BadTerms.selector);
        vault.offer(t);

        uint256 id = _offerAndFill();
        assertEq(stock.balanceOf(address(desk)), 400 ether);
        assertEq(stock.balanceOf(address(vault)), 140 ether); // 40 vault stock + 100 pending
        assertEq(usdg.balanceOf(address(vault)), PREMIUM);
        _settle(id, 230e8);
        vault.closeEpoch();

        uint256 nav = SEED * 230e6 / 1 ether + PREMIUM;
        uint256 expectedBob = Math.mulDiv(100 * 230e6, SEED, nav);
        vm.prank(bob);
        assertEq(vault.claimDeposit(2, bob), expectedBob);
        assertEq(vault.totalSupply(), SEED + expectedBob);
        assertEq(stock.balanceOf(address(vault)), 540 ether);
        assertEq(usdg.balanceOf(address(vault)), PREMIUM);
    }

    function test_otmPhysicalReturnsStockAndPremiumBoostsExistingNav() public {
        _seed();
        uint256 id = _offerAndFill();
        assertEq(vault.activeOptionId(), id);
        assertEq(usdg.balanceOf(address(vault)), PREMIUM);
        _settle(id, 230e8);
        assertEq(uint8(desk.getOption(id).state), uint8(CoveredCallDesk.State.ExpiredOTM));
        vault.closeEpoch();
        (bool closed, uint256 optionId, uint256 price, uint256 nav, uint256 supply,,,,,) = vault.epochs(2);
        assertTrue(closed);
        assertEq(optionId, id);
        assertEq(price, 230e18);
        assertEq(nav, 440 * 230e6 + PREMIUM);
        assertEq(supply, SEED);
        assertEq(vault.activeOptionId(), 0);
    }

    function test_itmPhysicalExerciseRedeemsStockAndUsdgBasket() public {
        _seed();
        uint256 id = _offerAndFill();
        vm.prank(alice);
        vault.requestRedeem(110 ether);
        _settle(id, 250e8);
        assertEq(uint8(desk.getOption(id).state), uint8(CoveredCallDesk.State.Exercisable));
        vm.prank(mm);
        desk.exercise(id, 0, mm);
        assertEq(uint8(desk.getOption(id).state), uint8(CoveredCallDesk.State.Exercised));
        assertEq(stock.balanceOf(address(vault)), 40 ether);
        assertEq(usdg.balanceOf(address(vault)), PREMIUM + 400 * 235e6);

        vault.closeEpoch();
        uint256 stockBefore = stock.balanceOf(alice);
        uint256 usdgBefore = usdg.balanceOf(alice);
        vm.prank(alice);
        (uint256 s, uint256 u) = vault.claimRedeem(2, alice);
        assertEq(s, 10 ether);
        assertEq(u, (PREMIUM + 400 * 235e6) / 4);
        assertEq(stock.balanceOf(alice), stockBefore + s);
        assertEq(usdg.balanceOf(alice), usdgBefore + u);
        assertEq(vault.totalSupply(), 330 ether);
    }

    function test_frozenStockRecipientCanCollectUsdgThenClaimOwedStock() public {
        _seed();
        uint256 id = _offerAndFill();
        vm.prank(alice);
        vault.requestRedeem(110 ether);
        _settle(id, 250e8);
        vm.prank(mm);
        desk.exercise(id, 0, mm);
        vault.closeEpoch();

        uint256 usdBefore = usdg.balanceOf(alice);
        uint256 stockBefore = stock.balanceOf(alice);
        uint256 bobStockBefore = stock.balanceOf(bob);
        uint256 expectedUsdg = (PREMIUM + 400 * 235e6) / 4;
        stock.freeze(alice, true);

        vm.prank(alice);
        (uint256 stockDelivered, uint256 usdgDelivered) = vault.claimRedeem(2, alice);
        assertEq(stockDelivered, 0);
        assertEq(usdgDelivered, expectedUsdg);
        assertEq(usdg.balanceOf(alice), usdBefore + expectedUsdg);
        assertEq(stock.balanceOf(alice), stockBefore);
        assertEq(vault.owedRedeemStock(alice), 10 ether);
        assertEq(vault.claimableStock(), 10 ether);
        (uint256 freeStock,) = vault.freeBalances();
        assertEq(freeStock, 30 ether);

        stock.freeze(alice, false);
        vm.prank(alice);
        vault.claimOwedRedeemStock(bob);
        assertEq(stock.balanceOf(bob), bobStockBefore + 10 ether);
        assertEq(vault.owedRedeemStock(alice), 0);
        assertEq(vault.claimableStock(), 0);
    }

    function test_cannotCloseWhileOfferedActiveOrExercisable() public {
        _seed();
        uint256 id = vault.offer(_terms());
        vm.expectRevert(EarnVault.Busy.selector);
        vault.closeEpoch();
        vm.prank(mm);
        desk.fill(id);
        vm.expectRevert(EarnVault.Busy.selector);
        vault.closeEpoch();
        _settle(id, 250e8);
        vm.expectRevert(EarnVault.Busy.selector);
        vault.closeEpoch();
        vm.warp(block.timestamp + 2 hours + 1);
        desk.lapse(id);
        // Refresh the stock and USDG feeds before share pricing.
        stockFeed.push(1, nextStockRound++, 250e8, block.timestamp);
        usdgFeed.push(1, nextUsdgRound++, 1e8, block.timestamp);
        vault.closeEpoch();
        assertEq(vault.currentEpoch(), 3);
    }

    function test_cancelledOfferReturnsCollateralAndCanClose() public {
        _seed();
        uint256 id = vault.offer(_terms());
        assertEq(stock.balanceOf(address(desk)), 400 ether);
        vault.cancelOffered();
        assertEq(uint8(desk.getOption(id).state), uint8(CoveredCallDesk.State.Cancelled));
        assertEq(stock.balanceOf(address(vault)), SEED);
        CoveredCallDesk.Terms memory t = _terms();
        vm.expectRevert(EarnVault.Busy.selector);
        vault.offer(t); // must wait until closeEpoch clears activeOptionId
        vault.closeEpoch();
        assertEq(vault.activeOptionId(), 0);
        (, uint256 optionId,,,,,,,,) = vault.epochs(2);
        assertEq(optionId, id);
    }

    function test_claimableBasketRemainsReservedAcrossNextRfq() public {
        _seed();
        uint256 id = _offerAndFill();
        vm.prank(alice);
        vault.requestRedeem(110 ether);
        _settle(id, 230e8);
        vault.closeEpoch();
        assertEq(vault.claimableStock(), 110 ether);
        assertEq(vault.claimableUSDG(), PREMIUM / 4);
        (uint256 freeStock, uint256 freeUSDG) = vault.freeBalances();
        assertEq(freeStock, 330 ether);
        assertEq(freeUSDG, PREMIUM * 3 / 4);

        CoveredCallDesk.Terms memory t = _terms();
        t.size = 331 ether;
        vm.expectRevert(EarnVault.BadTerms.selector);
        vault.offer(t);
        t.size = 330 ether;
        id = vault.offer(t);
        assertEq(stock.balanceOf(address(vault)), 110 ether);
        uint256 stockBefore = stock.balanceOf(alice);
        uint256 usdgBefore = usdg.balanceOf(alice);
        vm.prank(alice);
        (uint256 stockOut, uint256 usdgOut) = vault.claimRedeem(2, alice);
        assertEq(stockOut, 110 ether);
        assertEq(usdgOut, PREMIUM / 4);
        assertEq(stock.balanceOf(alice), stockBefore + stockOut);
        assertEq(usdg.balanceOf(alice), usdgBefore + usdgOut);
        assertEq(stock.balanceOf(address(desk)), 330 ether);
        assertEq(stock.balanceOf(address(vault)), 0);
        assertEq(vault.activeOptionId(), id);
    }

    function test_rfqGuardsPhysicalBuyerStrikePremiumTenorAndUtilization() public {
        _seed();
        CoveredCallDesk.Terms memory t = _terms();
        vm.prank(alice);
        vm.expectRevert();
        vault.offer(t);

        t.mode = CoveredCallDesk.Settlement.NetShare;
        vm.expectRevert(EarnVault.BadTerms.selector);
        vault.offer(t);
        t = _terms();
        t.buyer = outsider;
        vm.expectRevert(EarnVault.BadTerms.selector);
        vault.offer(t);
        t = _terms();
        t.strike = 220e6;
        vm.expectRevert(EarnVault.BadTerms.selector);
        vault.offer(t);
        t = _terms();
        t.premium = 1;
        vm.expectRevert(EarnVault.BadTerms.selector);
        vault.offer(t);
        t = _terms();
        t.expiry = uint64(block.timestamp + 11 hours);
        vm.expectRevert(EarnVault.BadTerms.selector);
        vault.offer(t);
        t = _terms();
        t.size = 441 ether;
        vm.expectRevert(EarnVault.BadTerms.selector);
        vault.offer(t);
        vault.setQuotePolicy(10_000, 25, 5_000);
        t = _terms();
        vm.expectRevert(EarnVault.BadTerms.selector);
        vault.offer(t);
        t.size = 220 ether;
        uint256 id = vault.offer(t);
        assertEq(desk.getOption(id).writer, address(vault));
        assertEq(stock.allowance(address(vault), address(desk)), 0);
    }

    function test_pauseEligibilityAndExitRemainAvailable() public {
        _seed();
        vm.prank(alice);
        vm.expectRevert(EarnVault.NotEligible.selector);
        vault.transfer(outsider, 1 ether);
        vault.setEligible(alice, false);
        vm.prank(alice);
        vm.expectRevert(EarnVault.NotEligible.selector);
        vault.transfer(bob, 1 ether);
        vault.setPaused(true);
        vm.prank(bob);
        vm.expectRevert(EarnVault.IsPaused.selector);
        vault.requestDeposit(1 ether);
        CoveredCallDesk.Terms memory t = _terms();
        vm.expectRevert(EarnVault.IsPaused.selector);
        vault.offer(t);
        uint256 shares = vault.balanceOf(alice);
        vm.prank(alice);
        vault.requestRedeem(shares);
        vault.closeEpoch();
        vm.prank(alice);
        (uint256 s, uint256 u) = vault.claimRedeem(2, alice);
        assertEq(s, Math.mulDiv(SEED, shares, SEED));
        assertEq(u, 0);
    }

    function test_staleOracleBlocksPricingAndNewRfq() public {
        vm.prank(alice);
        vault.requestDeposit(SEED);
        vm.warp(block.timestamp + 27 hours);
        vm.expectRevert(PriceOracle.Unhealthy.selector);
        vault.closeEpoch();
        stockFeed.push(1, nextStockRound++, 227e8, block.timestamp);
        usdgFeed.push(1, nextUsdgRound++, 1e8, block.timestamp);
        vault.closeEpoch();
        vm.prank(alice);
        vault.claimDeposit(1, alice);
        vm.warp(block.timestamp + 27 hours);
        CoveredCallDesk.Terms memory t = _terms();
        vm.expectRevert(PriceOracle.Unhealthy.selector);
        vault.offer(t);
    }

    function test_exactTransferRejectsFeeOnTransferStock() public {
        stock.setFee(10);
        vm.prank(alice);
        vm.expectRevert(EarnVault.UnsafeAssets.selector);
        vault.requestDeposit(SEED);
        assertEq(vault.pendingDepositStock(), 0);
        assertEq(stock.balanceOf(address(vault)), 0);
    }

    function test_directShareTransferToVaultRejected() public {
        _seed();
        vm.prank(alice);
        vm.expectRevert(EarnVault.BadTerms.selector);
        vault.transfer(address(vault), 1 ether);
        assertEq(vault.balanceOf(alice), SEED - vault.MIN_DEAD_SHARES());
    }

    function test_owedDeskPayoutMustBePulledBeforeClose() public {
        _seed();
        uint256 id = _offerAndFill();
        stock.freeze(address(vault), true);
        _settle(id, 230e8);
        assertEq(uint8(desk.getOption(id).state), uint8(CoveredCallDesk.State.ExpiredOTM));
        assertEq(desk.owed(address(stock), address(vault)), 400 ether);
        vm.expectRevert(EarnVault.UnsafeAssets.selector);
        vault.closeEpoch();
        stock.freeze(address(vault), false);
        vm.prank(outsider);
        vault.claimDeskOwed(address(stock));
        assertEq(desk.owed(address(stock), address(vault)), 0);
        assertEq(stock.balanceOf(address(vault)), SEED);
        vault.closeEpoch();
    }

    function test_twoDustDepositsRefundWhenEachWouldMintZeroShares() public {
        _seed();
        // Make price per hNVDA high enough that each minimum deposit rounds to zero shares.
        // The deposits must be refunded individually instead of locking the entire epoch.
        usdg.mint(address(vault), 1e30);
        uint256 dust = vault.MIN_DEPOSIT();
        vm.prank(alice);
        vault.requestDeposit(dust);
        vm.prank(bob);
        vault.requestDeposit(dust);
        uint256 aliceBefore = stock.balanceOf(alice);
        uint256 bobBefore = stock.balanceOf(bob);

        vault.closeEpoch();
        assertEq(vault.depositClaimShares(2, alice), 0);
        assertEq(vault.depositClaimShares(2, bob), 0);
        assertEq(vault.refundableDepositStock(2, alice), dust);
        assertEq(vault.refundableDepositStock(2, bob), dust);
        assertEq(vault.claimableStock(), 2 * dust);
        vm.prank(alice);
        assertEq(vault.claimDeposit(2, alice), 0);
        vm.prank(bob);
        assertEq(vault.claimDeposit(2, bob), 0);
        assertEq(stock.balanceOf(alice), aliceBefore + dust);
        assertEq(stock.balanceOf(bob), bobBefore + dust);
        assertEq(vault.claimableStock(), 0);
        assertEq(vault.pendingDepositStock(), 0);
        assertEq(vault.totalSupply(), SEED);
        vault.closeEpoch(); // no residual request or claim blocks another checkpoint
        assertEq(vault.currentEpoch(), 4);
    }

    function test_preSeedDonationCanBeRecoveredWithoutTakingQueuedStock() public {
        vm.prank(alice);
        vault.requestDeposit(SEED);
        usdg.mint(address(vault), 123e6);
        (uint256 freeStock, uint256 freeUsdg) = vault.freeBalances();
        assertEq(freeStock, 0);
        assertEq(freeUsdg, 123e6);
        vault.recoverOrphaned(outsider);
        assertEq(usdg.balanceOf(outsider), 123e6);
        assertEq(stock.balanceOf(outsider), 0);
        assertEq(stock.balanceOf(address(vault)), SEED);
        assertEq(vault.pendingDepositStock(), SEED);
        assertEq(vault.depositRequests(1, alice), SEED);
        vault.closeEpoch();
        vm.prank(alice);
        assertEq(vault.claimDeposit(1, alice), SEED - vault.MIN_DEAD_SHARES());
    }

    function test_allUserSharesRedeemedLeavesDeadSharesAndNextDepositUsesResidualNav() public {
        _seed();
        uint256 dead = vault.MIN_DEAD_SHARES();
        uint256 aliceShares = vault.balanceOf(alice);
        vm.prank(alice);
        vault.requestRedeem(aliceShares);
        vault.closeEpoch();
        vm.prank(alice);
        (uint256 stockOut, uint256 usdgOut) = vault.claimRedeem(2, alice);
        assertEq(stockOut, SEED - dead);
        assertEq(usdgOut, 0);
        assertEq(stock.balanceOf(address(vault)), dead);
        assertEq(vault.balanceOf(vault.DEAD_SHARES_HOLDER()), dead);
        assertEq(vault.totalSupply(), dead);

        vm.prank(bob);
        vault.requestDeposit(10 ether);
        uint256 residualNav = Math.mulDiv(dead, P0, vault.VALUE_SCALE());
        uint256 newDepositValue = Math.mulDiv(10 ether, P0, vault.VALUE_SCALE());
        uint256 expectedShares = Math.mulDiv(newDepositValue, dead, residualNav);
        vault.closeEpoch();
        (,,, uint256 navBefore, uint256 supplyBefore,,,,,) = vault.epochs(3);
        assertEq(navBefore, residualNav);
        assertEq(supplyBefore, dead);
        vm.prank(bob);
        assertEq(vault.claimDeposit(3, bob), expectedShares);
        assertEq(vault.balanceOf(bob), expectedShares);
        assertEq(vault.totalSupply(), dead + expectedShares);
    }

    function test_navAndRfqUseOracleHeartbeatWindow() public {
        _seed();
        vm.warp(T0 + 30 minutes); // initial stock round is one minute before T0
        assertEq(oracle.price(), P0); // PriceOracle's 26-hour freshness window still passes
        vault.closeEpoch();
        assertEq(vault.currentEpoch(), 3);
        CoveredCallDesk.Terms memory t = _terms();
        uint256 id = vault.offer(t);
        assertEq(vault.activeOptionId(), id);
    }

    function test_rfqFillDeadlineBeyondFiveMinutesRejected() public {
        _seed();
        CoveredCallDesk.Terms memory t = _terms();
        t.fillDeadline = uint64(block.timestamp + vault.MAX_FILL_WINDOW() + 1);
        vm.expectRevert(EarnVault.BadTerms.selector);
        vault.offer(t);
        t.fillDeadline = uint64(block.timestamp + vault.MAX_FILL_WINDOW());
        uint256 id = vault.offer(t);
        assertEq(desk.getOption(id).fillDeadline, t.fillDeadline);
    }

    function test_itmRfqRequiresIntrinsicPlusMinimumTimePremium() public {
        _seed();
        vault.setQuotePolicy(9_800, 25, 10_000);
        CoveredCallDesk.Terms memory t = _terms();
        t.strike = 223e6; // 98.24% of $227 spot, permitted by the 98% strike floor
        uint256 spot = oracle.price();
        uint256 strike = uint256(t.strike) * 1e12;
        uint256 intrinsic = Math.mulDiv(uint256(t.size), spot - strike, vault.VALUE_SCALE());
        uint256 notional = Math.mulDiv(uint256(t.size), spot, vault.VALUE_SCALE());
        uint256 minTimePremium = Math.mulDiv(notional, vault.minPremiumBps(), 10_000, Math.Rounding.Ceil);
        assertGt(intrinsic, 0);
        t.premium = uint128(intrinsic + minTimePremium - 1);
        vm.expectRevert(EarnVault.BadTerms.selector);
        vault.offer(t);
        t.premium += 1;
        uint256 id = vault.offer(t);
        assertEq(desk.getOption(id).premium, intrinsic + minTimePremium);
    }
}

// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {Math} from "@openzeppelin/contracts/utils/math/Math.sol";
import {stdStorage, StdStorage} from "forge-std/StdStorage.sol";
import {IPoolManager} from "v4-core/src/interfaces/IPoolManager.sol";
import {PoolKey} from "v4-core/src/types/PoolKey.sol";
import {PoolIdLibrary} from "v4-core/src/types/PoolId.sol";
import {StateLibrary} from "v4-core/src/libraries/StateLibrary.sol";
import {Currency} from "v4-core/src/types/Currency.sol";
import {SwapParams} from "v4-core/src/types/PoolOperation.sol";
import {BalanceDelta} from "v4-core/src/types/BalanceDelta.sol";
import {TickMath} from "v4-core/src/libraries/TickMath.sol";
import {HedgeFunBondingCurve as Curve} from "../src/v2/HedgeFunBondingCurve.sol";
import {HedgeFunV2TradeRouter as Router} from "../src/v2/HedgeFunV2TradeRouter.sol";
import {HedgeFunTreasuryBase} from "../src/HedgeFunTreasuryBase.sol";
import {V2FactoryFixture} from "./utils/V2FactoryFixture.sol";

/// Blue-team accounting oracle: the expected balances are accumulated from individual executed trades,
/// not reconstructed from the curve's own reserve variables. The final seed uses the real V4 manager.
contract V2AdversarialAccountingTest is V2FactoryFixture {
    using stdStorage for StdStorage;
    using PoolIdLibrary for PoolKey;
    using StateLibrary for IPoolManager;

    address private constant ALICE = address(0xA100);
    address private constant BOB = address(0xB100);
    struct Ledger {
        uint256 paid;
        uint256 proceeds;
        uint256 fees;
        uint256 claimed;
        uint256 donatedStock;
        uint256 donatedTokens;
        uint256 burned;
        uint256[2] paidBy;
        uint256[2] proceedsTo;
    }
    struct Boundary {
        uint160 anchor;
        uint160 beforePrice;
        uint160 limit;
        uint256 participantStart;
        uint256 heldTokens;
        uint256 stockBefore;
        uint256 poolStockBefore;
        uint256 poolTokensBefore;
        uint256 supplyBefore;
        uint256 keeperBefore;
        uint256 controlBurned;
    }

    function _fundActors(Curve curve, uint256 unit) private {
        for (uint256 i; i < 2; ++i) {
            address actor = i == 0 ? ALICE : BOB;
            stock.mint(actor, 1000 * unit);
            vm.startPrank(actor);
            stock.approve(address(curve), type(uint256).max);
            IERC20(curve.token()).approve(address(curve), type(uint256).max);
            vm.stopPrank();
        }
    }

    function _checkActive(Curve curve, Ledger memory g, uint256 unit) private view {
        IERC20 token = IERC20(curve.token());
        assertEq(uint256(curve.status()), 0);
        assertEq(curve.realStockReserve(), g.paid - g.proceeds - g.fees, "independent principal ledger");
        assertEq(curve.totalFees(), g.fees - g.claimed, "independent fee ledger");
        assertEq(stock.balanceOf(address(curve)), g.paid - g.proceeds - g.claimed + g.donatedStock);
        assertEq(curve.claimable(protocol) + curve.claimable(address(this)) + curve.claimable(curve.treasury()), curve.totalFees());
        assertEq(token.balanceOf(address(curve)), curve.tokenReserve() + g.donatedTokens);
        assertEq(token.totalSupply(), curve.initialSupply() - g.burned);
        assertEq(token.totalSupply(), token.balanceOf(address(curve)) + token.balanceOf(ALICE) + token.balanceOf(BOB));
        assertEq(curve.virtualStock() + curve.realStockReserve(), Math.ceilDiv(curve.invariant(), curve.tokenReserve()));
        assertEq(stock.balanceOf(ALICE), 1000 * unit - g.paidBy[0] + g.proceedsTo[0]);
        assertEq(stock.balanceOf(BOB), 1000 * unit - g.paidBy[1] + g.proceedsTo[1]);
        assertEq(HedgeFunTreasuryBase(curve.treasury()).lotCount(), 0, "claims must not activate treasury");
    }

    function _step(Curve curve, Ledger memory g, uint256 entropy, uint256 unit) private {
        uint256 who = (entropy >> 16) & 1;
        address actor = who == 0 ? ALICE : BOB;
        uint256 action = entropy % 5;
        if (action == 0) {
            uint256 remaining = curve.terminalStock() - curve.virtualStock() - curve.realStockReserve();
            if (remaining < 2) return;
            uint256 budget = 1 + ((entropy >> 32) % Math.min(5 * unit, remaining - 1));
            (uint256 spent, uint256 got, uint256 burned) = curve.quoteBuy(budget);
            if (spent == 0 || got == 0) return;
            vm.prank(actor);
            (uint256 actualSpent, uint256 actualGot) = curve.buy(budget, got, actor, block.timestamp);
            assertEq(actualSpent, spent); assertEq(actualGot, got);
            g.paid += spent; g.paidBy[who] += spent; g.burned += burned;
        } else if (action == 1) {
            uint256 balance = IERC20(curve.token()).balanceOf(actor);
            if (balance == 0) return;
            uint256 amount = 1 + ((entropy >> 32) % balance);
            (uint256 proceeds, uint256 fee) = curve.quoteSell(amount);
            if (proceeds == 0) return;
            vm.prank(actor);
            assertEq(curve.sell(amount, proceeds, actor, block.timestamp), proceeds);
            g.proceeds += proceeds; g.proceedsTo[who] += proceeds; g.fees += fee;
        } else if (action == 2) {
            uint256 which = (entropy >> 32) % 3;
            address recipient = which == 0 ? protocol : which == 1 ? address(this) : curve.treasury();
            uint256 amount = curve.claimable(recipient);
            uint256 beforeBalance = stock.balanceOf(recipient);
            vm.prank(actor); curve.claimFees(recipient);
            assertEq(stock.balanceOf(recipient) - beforeBalance, amount);
            g.claimed += amount;
        } else if (action == 3) {
            uint256 amount = 1 + ((entropy >> 32) % unit);
            stock.mint(address(curve), amount);
            g.donatedStock += amount;
        } else {
            IERC20 token = IERC20(curve.token());
            uint256 balance = token.balanceOf(actor);
            if (balance == 0) return;
            uint256 amount = 1 + ((entropy >> 32) % balance);
            vm.prank(actor); token.transfer(address(curve), amount);
            g.donatedTokens += amount;
        }
    }

    function _exercise(uint256 seed, uint8 decimals_, bool tokenIs0) private {
        _setUpV2(decimals_);
        (, Curve curve, PoolKey memory key) = _launchV2(tokenIs0);
        uint256 unit = 10 ** decimals_;
        _fundActors(curve, unit);
        Ledger memory g;
        for (uint256 i; i < 48; ++i) {
            seed = uint256(keccak256(abi.encode(seed, i)));
            _step(curve, g, seed, unit);
            _checkActive(curve, g, unit);
        }
        // Principal entering LP must be independent of which trader created or claimed fees.
        uint256 feeBefore = curve.totalFees();
        uint256 treasuryBefore = stock.balanceOf(curve.treasury());
        uint256 principalBefore = curve.realStockReserve();
        (uint256 finalSpent,,) = curve.quoteBuy(type(uint256).max);
        _graduateV2(curve);
        uint256 lpStock = stock.balanceOf(address(pm));
        uint256 treasuryDust = stock.balanceOf(curve.treasury()) - treasuryBefore;
        assertEq(lpStock + treasuryDust, principalBefore + finalSpent, "only accounted principal migrates");
        assertGt(pm.getLiquidity(key.toId()), 0);
        assertEq(stock.balanceOf(address(curve)), feeBefore + g.donatedStock);
        assertEq(IERC20(curve.token()).balanceOf(address(curve)), g.donatedTokens);
        assertEq(curve.totalFees(), feeBefore);
        assertEq(stock.balanceOf(address(factory)), 0);
        assertEq(IERC20(curve.token()).balanceOf(address(factory)), 0);
        curve.claimFees(protocol); curve.claimFees(address(this)); curve.claimFees(curve.treasury());
        assertEq(curve.totalFees(), 0);
        assertEq(stock.balanceOf(address(curve)), g.donatedStock);
        assertEq(IERC20(curve.token()).totalSupply(),
            IERC20(curve.token()).balanceOf(ALICE) + IERC20(curve.token()).balanceOf(BOB)
            + IERC20(curve.token()).balanceOf(address(this)) + IERC20(curve.token()).balanceOf(address(pm))
            + g.donatedTokens, "all surviving strategy tokens accounted after burns and LP migration");
    }

    function testFuzzTwoTraderLedger18DecimalsToken0(uint256 seed) public { _exercise(seed, 18, true); }
    function testFuzzTwoTraderLedger6DecimalsToken1(uint256 seed) public { _exercise(seed, 6, false); }

    function testGraduationStockAndTokenDonationsDoNotChangeLpOrAnchor() public {
        _setUpV2(18);
        (, Curve curve, PoolKey memory key) = _launchV2(true);
        curve.buy(30 ether, 1, address(this), block.timestamp);
        uint256 snapshot = vm.snapshotState();
        _graduateV2(curve);
        uint256 stockBaseline = stock.balanceOf(address(pm));
        uint256 tokenBaseline = IERC20(curve.token()).balanceOf(address(pm));
        uint128 liquidityBaseline = pm.getLiquidity(key.toId());
        uint160 anchorBaseline = HedgeFunTreasuryBase(curve.treasury()).buybackAnchorSqrtP();
        vm.revertToState(snapshot);
        uint256 tokenDonation = IERC20(curve.token()).balanceOf(address(this)) / 3;
        IERC20(curve.token()).transfer(address(curve), tokenDonation);
        IERC20(curve.token()).transfer(address(factory), tokenDonation);
        stock.mint(address(curve), 17 ether);
        stock.mint(address(factory), 19 ether);
        _graduateV2(curve);
        assertEq(stock.balanceOf(address(pm)), stockBaseline);
        assertEq(IERC20(curve.token()).balanceOf(address(pm)), tokenBaseline);
        assertEq(pm.getLiquidity(key.toId()), liquidityBaseline);
        assertEq(HedgeFunTreasuryBase(curve.treasury()).buybackAnchorSqrtP(), anchorBaseline);
        assertEq(stock.balanceOf(address(curve)), 17 ether);
        assertEq(stock.balanceOf(address(factory)), 19 ether);
        assertEq(IERC20(curve.token()).balanceOf(address(curve)), tokenDonation);
        assertEq(IERC20(curve.token()).balanceOf(address(factory)), tokenDonation);
    }

    function _checkCrossTransactionAnchor(bool tokenIs0) private {
        _setUpV2(18);
        (, Curve curve, PoolKey memory key) = _launchV2(tokenIs0);
        _graduateV2(curve);
        HedgeFunTreasuryBase treasury = HedgeFunTreasuryBase(curve.treasury());
        uint160 originalAnchor = treasury.buybackAnchorSqrtP();
        uint256 originalTime = treasury.buybackAnchorAt();
        // Isolate price protection from the stock strategy's profit generation: this is synthetic profit,
        // not an assertion that an attacker can modify the treasury's accounting in production.
        stdstore.target(address(treasury)).sig("buybackStock()").checked_write(1 ether);
        stock.transfer(address(treasury), 1 ether);
        vm.roll(block.number + 1); vm.warp(block.timestamp + 1);
        pm.unlock(abi.encode(key, 50 ether));
        vm.roll(block.number + 1); vm.warp(block.timestamp + 1);
        (bool ready,) = hook.meanTickOf(key.toId(), 600);
        assertFalse(ready, "the attack precedes a usable TWAP");
        // Booking donated stock on the next block must not teach the anchor the expensive token price.
        stock.transfer(address(treasury), 1 ether);
        uint256 lotsBefore = treasury.lotCount();
        assertTrue(treasury.book(), "booking must execute for the anchor test to be meaningful");
        assertEq(treasury.lotCount(), lotsBefore + 1);
        assertEq(treasury.buybackAnchorSqrtP(), originalAnchor);
        assertEq(treasury.buybackAnchorAt(), originalTime);
        vm.expectRevert(HedgeFunTreasuryBase.NotDue.selector);
        treasury.buyback();
        assertEq(treasury.buybackStock(), 1 ether);
        assertEq(treasury.lastBuybackAt(), 0);
        assertEq(hook.lastEventAt(key.toId()), 0, "rejected buyback does not create a sell spike");
    }

    function testCrossTransactionShoveAndBookingCannotReplaceToken0GraduationAnchor() public { _checkCrossTransactionAnchor(true); }
    function testCrossTransactionShoveAndBookingCannotReplaceToken1GraduationAnchor() public { _checkCrossTransactionAnchor(false); }

    /// Replay a local quote without any intervening state change. The minimum one unit ABOVE that quote
    /// must fail; the exact quoted minimum must execute. This tests the user-facing output bound, not
    /// independent price discovery. The snapshot is a local simulation and is not available on chain.
    function _tradeAtQuotedMinimum(Router router, bool buy, uint256 amount) private returns (uint256 quoted) {
        Router.TradeParams memory p = Router.TradeParams(0, address(stock), amount, buy ? amount : 0,
            1, block.timestamp, 2, false);
        Router.Hop[] memory empty = new Router.Hop[](0);
        uint256 snapshot = vm.snapshotState();
        vm.prank(ALICE);
        if (buy) (quoted,) = router.buy(p, empty);
        else (quoted,) = router.sell(p, empty);
        vm.revertToState(snapshot);
        p.minFinalOut = quoted + 1;
        vm.prank(ALICE); vm.expectRevert(abi.encodeWithSelector(Router.TooLittle.selector, quoted));
        if (buy) router.buy(p, empty);
        else router.sell(p, empty);
        p.minFinalOut = quoted;
        uint256 actual;
        vm.prank(ALICE);
        if (buy) (actual,) = router.buy(p, empty);
        else (actual,) = router.sell(p, empty);
        assertEq(actual, quoted);
    }

    function _checkMatureTwapBoundary(bool tokenIs0) private {
        _setUpV2(18);
        (, Curve curve, PoolKey memory key) = _launchV2(tokenIs0);
        _graduateV2(curve);
        HedgeFunTreasuryBase treasury = HedgeFunTreasuryBase(curve.treasury());
        IERC20 token = IERC20(curve.token());
        Router router = new Router(factory);
        Boundary memory b;
        b.anchor = treasury.buybackAnchorSqrtP();
        stock.mint(ALICE, 100 ether);
        vm.startPrank(ALICE);
        stock.approve(address(router), type(uint256).max);
        token.approve(address(router), type(uint256).max);
        vm.stopPrank();
        b.participantStart = stock.balanceOf(ALICE);

        // Synthetic *already realised* profit isolates price execution from the stock strategy.
        // It is funded with real mock stock. No production caller can perform this storage write.
        stdstore.target(address(treasury)).sig("buybackStock()").checked_write(1 ether);
        stock.transfer(address(treasury), 1 ether);
        {
            uint256 snapshot = vm.snapshotState();
            vm.prank(BOB);
            (uint256 controlSpent, uint256 controlBurned) = treasury.buyback();
            assertEq(controlSpent, 1 ether);
            b.controlBurned = controlBurned;
            vm.revertToState(snapshot);
        }
        vm.roll(block.number + 1); vm.warp(block.timestamp + 1);
        b.heldTokens = _tradeAtQuotedMinimum(router, true, 50 ether);
        assertEq(stock.balanceOf(ALICE), b.participantStart - 50 ether);
        assertEq(token.balanceOf(ALICE), b.heldTokens);
        (b.beforePrice,,,) = pm.getSlot0(key.toId());
        assertEq(treasury.buybackAnchorSqrtP(), b.anchor);
        (bool ready,) = hook.meanTickOf(key.toId(), 600);
        assertFalse(ready);
        vm.expectRevert(HedgeFunTreasuryBase.NotDue.selector);
        treasury.buyback();

        // Price is held with 50 stock of actual market exposure for the full observation window.
        vm.roll(block.number + 600); vm.warp(block.timestamp + 600);
        {
            int24 mean;
            (ready, mean) = hook.meanTickOf(key.toId(), 600);
            assertTrue(ready);
            uint256 halfImpact = treasury.params().maxBuybackImpactBps / 2;
            uint256 meanLimit = Math.mulDiv(TickMath.getSqrtPriceAtTick(mean),
                tokenIs0 ? 10000 + halfImpact : 10000 - halfImpact, 10000);
            uint256 spotLimit = Math.mulDiv(b.beforePrice,
                tokenIs0 ? 10000 + halfImpact : 10000 - halfImpact, 10000);
            b.limit = uint160(tokenIs0 ? Math.min(meanLimit, spotLimit) : Math.max(meanLimit, spotLimit));
        }
        b.stockBefore = stock.balanceOf(address(treasury));
        b.poolStockBefore = stock.balanceOf(address(pm));
        b.poolTokensBefore = token.balanceOf(address(pm));
        b.supplyBefore = token.totalSupply();
        b.keeperBefore = token.balanceOf(BOB);
        vm.prank(BOB);
        (uint256 spent, uint256 burned) = treasury.buyback();
        uint256 bounty = token.balanceOf(BOB) - b.keeperBefore;
        assertGt(spent, 0); assertLe(spent, 1 ether);
        assertEq(treasury.buybackStock(), 1 ether - spent);
        assertEq(b.stockBefore - stock.balanceOf(address(treasury)), spent);
        assertEq(stock.balanceOf(address(pm)) - b.poolStockBefore, spent);
        assertEq(b.poolTokensBefore - token.balanceOf(address(pm)), burned + bounty);
        assertEq(b.supplyBefore - token.totalSupply(), burned);
        assertEq(treasury.totalStockSpentOnBuybacks(), spent);
        assertEq(treasury.totalBurned(), burned);
        assertEq(treasury.buybackAnchorSqrtP(), b.anchor, "a mature TWAP can serve without replacing the old anchor");
        {
            (uint160 afterPrice,,,) = pm.getSlot0(key.toId());
            if (tokenIs0) { assertGe(afterPrice, b.beforePrice); assertLe(afterPrice, b.limit); }
            else { assertLe(afterPrice, b.beforePrice); assertGe(afterPrice, b.limit); }
            // All executed marginal prices are inside this limit, giving a conservative gross-token floor.
            uint256 square = uint256(b.limit) * b.limit;
            uint256 minimumGross = tokenIs0
                ? Math.mulDiv(spent, 1 << 192, square)
                : Math.mulDiv(spent, square, 1 << 192);
            assertGe(burned + bounty, minimumGross);
            assertEq(token.balanceOf(ALICE), b.heldTokens);
            assertEq(stock.balanceOf(ALICE), b.participantStart - 50 ether);
        }
        // Compare the economic result only after the 120-second buyback sell spike has expired.
        vm.roll(block.number + 120); vm.warp(block.timestamp + 120);
        assertEq(hook.sellRateBps(key.toId()), curve.taxBps());
        uint256 exitStock = _tradeAtQuotedMinimum(router, false, b.heldTokens);
        assertEq(token.balanceOf(ALICE), 0);
        assertEq(stock.balanceOf(ALICE), b.participantStart - 50 ether + exitStock);
        assertLt(burned, b.controlBurned, "same stock budget buys fewer tokens after a sustained market repricing");
        emit log_named_decimal_uint("participant stock before", b.participantStart, 18);
        emit log_named_decimal_uint("participant stock after waiting and selling", stock.balanceOf(ALICE), 18);
        emit log_named_decimal_uint("participant tokens held during window", b.heldTokens, 18);
        emit log_named_decimal_uint("treasury stock spent", spent, 18);
        emit log_named_decimal_uint("treasury tokens burned", burned, 18);
        emit log_named_decimal_uint("keeper token bounty", bounty, 18);
        emit log_named_decimal_uint("control tokens burned without earlier market buy", b.controlBurned, 18);
    }

    function testMatureTwapBoundaryToken0() public { _checkMatureTwapBoundary(true); }
    function testMatureTwapBoundaryToken1() public { _checkMatureTwapBoundary(false); }

    function unlockCallback(bytes calldata data) external returns (bytes memory) {
        require(msg.sender == address(pm));
        (PoolKey memory key, uint256 amount) = abi.decode(data, (PoolKey, uint256));
        bool z = Currency.unwrap(key.currency0) == address(stock);
        BalanceDelta d = pm.swap(key, SwapParams(z, -int256(amount), z ? TickMath.MIN_SQRT_PRICE + 1 : TickMath.MAX_SQRT_PRICE - 1), "");
        uint256 spent = uint256(-int256(z ? d.amount0() : d.amount1()));
        uint256 received = uint256(int256(z ? d.amount1() : d.amount0()));
        pm.sync(Currency.wrap(address(stock)));
        stock.transfer(address(pm), spent);
        pm.settle();
        pm.take(z ? key.currency1 : key.currency0, address(this), received);
        return "";
    }
}

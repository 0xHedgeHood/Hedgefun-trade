// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {PoolKey} from "v4-core/src/types/PoolKey.sol";
import {PoolIdLibrary} from "v4-core/src/types/PoolId.sol";
import {HedgeFunFactory} from "../src/HedgeFunFactory.sol";
import {HedgeFunTreasuryBase} from "../src/HedgeFunTreasuryBase.sol";
import {HedgeFunBondingCurve} from "../src/v2/HedgeFunBondingCurve.sol";
import {HedgeFunV2Treasury} from "../src/v2/HedgeFunV2Treasury.sol";
import {HedgeFunV2BuybackTreasury} from "../src/v2/HedgeFunV2BuybackTreasury.sol";
import {V2TreasuryDeployer} from "../src/v2/V2TreasuryDeployer.sol";
import {V2FactoryFixture} from "./utils/V2FactoryFixture.sol";

/// Kind 1 through the whole lifecycle: registered by the owner, chosen by the creator, launched, graduated, and
/// then spending its stock only through the paced, TWAP-bounded buy-back. It never holds a lot.
contract V2BuybackKindTest is V2FactoryFixture {
    using PoolIdLibrary for PoolKey;

    V2TreasuryDeployer internal deployer;
    HedgeFunV2BuybackTreasury internal treasury;
    HedgeFunBondingCurve internal curve;
    PoolKey internal key;

    function setUp() public {
        _setUpV2(18);
        deployer = V2TreasuryDeployer(address(factory.treasuryDeployer()));
        (address a, address b) = deployer.makeChunks(type(HedgeFunV2BuybackTreasury).creationCode);
        vm.prank(owner);
        assertEq(deployer.registerKind(a, b), 1);
        HedgeFunFactory.Request memory q = _request();
        deployer.setStrategyKind(q.symbol, q.nonce, 1);
        (,, bytes32 terms) = factory.predict(q);
        uint256 id = factory.launch(q, terms);
        curve = HedgeFunBondingCurve(factory.curves(id));
        (, address t,,,) = factory.strategies(id);
        treasury = HedgeFunV2BuybackTreasury(t);
        (key,) = factory.graduationConfig(id);
        stock.approve(address(curve), type(uint256).max);
        vm.warp(curve.launchedAt() + curve.snipeSeconds());
    }

    function test_graduationShareIsBuybackBudgetNotALot() public {
        assertFalse(treasury.book(), "nothing to book before graduation");
        _graduateV2(curve);
        uint256 share = stock.balanceOf(address(treasury));
        assertGt(share, 0);
        assertEq(treasury.buybackStock(), share, "graduation's own book() swept the share into the budget");
        assertEq(treasury.bookedStock(), 0);
        assertEq(treasury.lotCount(), 0);
        assertEq(treasury.unbookedStock(), 0);
        vm.expectRevert(HedgeFunV2BuybackTreasury.UseBuyback.selector);
        treasury.execute();
        vm.expectRevert(HedgeFunV2Treasury.UseExecute.selector);
        treasury.buyDip();
    }

    function test_buybackPacesSpendsBurnsWithoutRearmingSellSpike() public {
        _graduateV2(curve);
        uint256 budget = treasury.buybackStock();
        IERC20 token = IERC20(curve.token());
        uint256 supplyBefore = token.totalSupply();
        (uint256 spent, uint256 burned) = treasury.buyback();
        assertGt(spent, 0); assertGt(burned, 0);
        assertGt(treasury.lastGoodPrice(), 0, "a live buy-back must seed the stale-price fallback");
        assertEq(treasury.lastGoodPriceAt(), block.timestamp);
        assertLt(spent, budget, "one chunk, not the whole budget");
        assertEq(treasury.buybackStock(), budget - spent);
        assertEq(token.totalSupply(), supplyBefore - burned);
        assertEq(hook.sellRateBps(key.toId()), 1000, "LP-funded buy-backs cannot arm the sell spike");
        uint256 cachedAt = treasury.lastGoodPriceAt();
        vm.expectRevert(HedgeFunTreasuryBase.Cooldown.selector);
        treasury.buyback();
        assertEq(treasury.lastGoodPriceAt(), cachedAt, "a cooldown rejection cannot refresh the cache");
        vm.warp(block.timestamp + 61);
        (uint256 spent2,) = treasury.buyback();
        assertGt(spent2, 0);
        assertEq(treasury.lotCount(), 0, "still no stock position after two buy-backs");
    }

    function test_liveBuybackSeedsFallbackForAStaleOracle() public {
        _graduateV2(curve);
        (uint256 spent,) = treasury.buyback();
        uint256 left = treasury.buybackStock();
        assertGt(spent, 0);
        assertGt(left, 0);

        // The fixture oracle accepts prices for 26 hours. At 27 hours the second buy-back can only be sized from
        // the live price cached by the first one; this is still well inside MAX_SIZING_AGE (five days).
        vm.warp(block.timestamp + 27 hours);
        (uint256 spentFromCache,) = treasury.buyback();
        assertGt(spentFromCache, 0);
        assertEq(treasury.buybackStock(), left - spentFromCache);
    }

    function test_staleOracleWithoutACacheStillFailsClosed() public {
        _graduateV2(curve);
        assertEq(treasury.lastGoodPrice(), 0);
        assertEq(treasury.lastGoodPriceAt(), 0);
        vm.warp(block.timestamp + 27 hours);
        vm.expectRevert(HedgeFunTreasuryBase.Unhealthy.selector);
        treasury.buyback();
    }

    function test_cachedSizingPriceExpiresAfterFiveDays() public {
        _graduateV2(curve);
        treasury.buyback();
        uint256 left = treasury.buybackStock();
        vm.warp(block.timestamp + 5 days);
        (uint256 spentAtBoundary,) = treasury.buyback();
        assertGt(spentAtBoundary, 0, "the five-day boundary is inclusive");
        left -= spentAtBoundary;

        vm.warp(block.timestamp + 61);
        vm.expectRevert(HedgeFunTreasuryBase.Unhealthy.selector);
        treasury.buyback();
        assertEq(treasury.buybackStock(), left, "an expired sizing cache cannot spend the budget");
    }

    /// The published score is `(stockEquivalentHeld + totalStockSpentOnBuybacks) / totalStockReceived`. Kind 1
    /// books straight into the budget, never through `_book`, so it must write the denominator itself.
    function test_kindOneBookRecordsReceivedStockSoTheScorecardHasADenominator() public {
        _graduateV2(curve);
        uint256 share = treasury.buybackStock();
        assertGt(share, 0);
        assertEq(treasury.totalStockReceived(), share, "the graduation share is stock received");
        treasury.buyback();
        assertGt(treasury.totalStockSpentOnBuybacks(), 0);
        assertEq(treasury.totalStockReceived(), share, "spending is not receiving");
        stock.mint(address(treasury), 7e18);
        assertTrue(treasury.book());
        assertEq(treasury.totalStockReceived(), share + 7e18, "every later arrival counts once");
        assertFalse(treasury.book(), "nothing new to book");
        assertEq(treasury.totalStockReceived(), share + 7e18, "a second book() counts nothing twice");
        (bool ok, uint256 held) = treasury.stockEquivalentHeld();
        assertTrue(ok);
        assertGt((held + treasury.totalStockSpentOnBuybacks()) * 1e18 / treasury.totalStockReceived(), 0);
    }

    function test_everyLaterStockArrivalIsBudgetToo() public {
        _graduateV2(curve);
        uint256 before = treasury.buybackStock();
        stock.transfer(address(treasury), 3e18); // a sell-tax claim or a donation land the same way
        assertTrue(treasury.book());
        assertEq(treasury.buybackStock(), before + 3e18);
        assertEq(treasury.lotCount(), 0);
    }

    function test_kindZeroLaunchIsUnaffected() public {
        HedgeFunFactory.Request memory q = _request();
        q.nonce = 7; // no kind chosen for this salt
        (,, bytes32 terms) = factory.predict(q);
        uint256 id = factory.launch(q, terms);
        (, address t,,,) = factory.strategies(id);
        assertEq(deployer.strategyKindOf(keccak256(abi.encode(q.symbol, address(this), q.nonce))), 0);
        vm.expectRevert(); // a kind-0 treasury has no UseBuyback selector: execute reverts for its own reasons
        HedgeFunV2BuybackTreasury(t).execute();
    }
}

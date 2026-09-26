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

    function test_buybackPacesSpendsBurnsAndStartsTheSpike() public {
        _graduateV2(curve);
        uint256 budget = treasury.buybackStock();
        IERC20 token = IERC20(curve.token());
        uint256 supplyBefore = token.totalSupply();
        (uint256 spent, uint256 burned) = treasury.buyback();
        assertGt(spent, 0); assertGt(burned, 0);
        assertLt(spent, budget, "one chunk, not the whole budget");
        assertEq(treasury.buybackStock(), budget - spent);
        assertEq(token.totalSupply(), supplyBefore - burned);
        assertEq(hook.sellRateBps(key.toId()), 9000, "the buy-back armed the sell spike");
        vm.expectRevert(HedgeFunTreasuryBase.Cooldown.selector);
        treasury.buyback();
        vm.warp(block.timestamp + 61);
        (uint256 spent2,) = treasury.buyback();
        assertGt(spent2, 0);
        assertEq(treasury.lotCount(), 0, "still no stock position after two buy-backs");
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

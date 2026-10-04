// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {PoolKey} from "v4-core/src/types/PoolKey.sol";
import {PoolIdLibrary} from "v4-core/src/types/PoolId.sol";
import {HedgeFunFactory} from "../src/HedgeFunFactory.sol";
import {HedgeFunHook} from "../src/hooks/HedgeFunHook.sol";
import {HedgeFunBondingCurve} from "../src/v2/HedgeFunBondingCurve.sol";
import {HedgeFunV2Factory} from "../src/v2/HedgeFunV2Factory.sol";
import {HedgeFunV2TradeRouter as Router} from "../src/v2/HedgeFunV2TradeRouter.sol";
import {HedgeFunV2IncomeTreasury} from "../src/v2/HedgeFunV2IncomeTreasury.sol";
import {V2StakingIncome} from "../src/v2/V2StakingIncome.sol";
import {V2LiquidityVault} from "../src/v2/V2LiquidityVault.sol";
import {V2TreasuryDeployer} from "../src/v2/V2TreasuryDeployer.sol";
import {RegisterV2IncomeKinds} from "../script/RegisterV2IncomeKinds.s.sol";

/// Opt-in, in-memory fork only: no key, no broadcast. The registration script runs as the DEPLOYED testnet factory's
/// owner against the deployed registry, then a creator launches with each income kind through the deployed factory,
/// hook and vault code. This is the compatibility check a local fixture cannot give.
///
///   INCOME_KINDS_FORK=true INCOME_KINDS_FORK_BLOCK=<fresh block> forge test --mc TestnetV2IncomeKindsForkTest -vv
///
/// The public RPC prunes old state: use a fresh block.
contract TestnetV2IncomeKindsForkTest is Test {
    using PoolIdLibrary for PoolKey;

    HedgeFunV2Factory factory;
    V2TreasuryDeployer registry;
    Router router;
    IERC20 stock;
    IERC20 usdg;
    uint8 dividendKind;
    uint8 splitKind;
    address creator = makeAddr("income kinds fork creator");
    address staker = makeAddr("income kinds fork staker");

    function setUp() public {
        vm.skip(!vm.envOr("INCOME_KINDS_FORK", false), "set INCOME_KINDS_FORK=true");
        vm.createSelectFork(
            vm.envOr("INCOME_KINDS_FORK_RPC", string("https://rpc.testnet.chain.robinhood.com")),
            vm.envUint("INCOME_KINDS_FORK_BLOCK")
        );
        factory = HedgeFunV2Factory(vm.envOr("V2_FACTORY", address(0xACEB03aAeE5494Aa54929Ec840630ae32A9ade0A)));
        registry = V2TreasuryDeployer(address(factory.treasuryDeployer()));
        usdg = IERC20(factory.usdg());
        (,,, address listed,) = factory.strategies(0); // a stock this factory has already launched on
        stock = IERC20(listed);
        uint256 before = registry.kindCount();
        (dividendKind, splitKind) = new RegisterV2IncomeKinds().register(factory.owner(), factory);
        assertEq(registry.kindCount(), before + 2, "appended, nothing replaced");
        router = new Router(factory);
    }

    function test_dividendKindOnDeployedFactory() public { _lifecycle(dividendKind, 10000); }
    function test_splitKindOnDeployedFactory() public { _lifecycle(splitKind, 5000); }

    uint256 id;
    HedgeFunBondingCurve curve;
    HedgeFunV2IncomeTreasury treasury;
    V2StakingIncome staking;
    IERC20 token;

    function _lifecycle(uint8 kind, uint256 bps) private {
        _launch(kind);
        assertEq(treasury.stakingBps(), bps);
        assertEq(staking.incomeSource(), address(treasury));
        uint256 principal = _graduateAndTrade();
        _settle(bps, principal);
    }

    function _launch(uint8 kind) private {
        HedgeFunFactory.Defaults memory d = factory.getDefaults();
        HedgeFunFactory.Request memory q;
        q.name = "Income kind fork rehearsal";
        q.symbol = "INCOME";
        q.nonce = kind;
        q.stock = address(stock);
        q.creator = creator;
        q.taxBps = d.minTaxBps;
        q.creatorBps = 1000;
        q.tp1Bps = 500; q.tp2Bps = 1000; q.dipBps = 500; q.lotBps = 2000;
        q.maxFee = d.launchFeeAmount;
        (,, q.expectedOpenPriceE18,) = factory.listings(address(stock));
        bool native = d.launchFeeCurrency == HedgeFunFactory.FeeCurrency.Native;
        if (native) vm.deal(creator, d.launchFeeAmount); else deal(address(usdg), creator, d.launchFeeAmount);
        deal(address(stock), creator, 10_000e18);
        vm.startPrank(creator);
        registry.setStrategyKind(q.symbol, q.nonce, kind);
        usdg.approve(address(factory), d.launchFeeAmount);
        (,, bytes32 terms) = factory.predict(q);
        id = native ? factory.launch{value: d.launchFeeAmount}(q, terms) : factory.launch(q, terms);
        vm.stopPrank();
        curve = HedgeFunBondingCurve(factory.curves(id));
        (address token_, address treasury_,,,) = factory.strategies(id);
        treasury = HedgeFunV2IncomeTreasury(treasury_);
        staking = treasury.staking();
        token = IERC20(token_);
    }

    function _graduateAndTrade() private returns (uint256 principal) {
        // the creator is exempt from the opening surcharge: one buy graduates the curve
        vm.startPrank(creator);
        stock.approve(address(curve), type(uint256).max);
        curve.buy(type(uint256).max, 1, creator, block.timestamp);
        assertEq(uint256(curve.status()), uint256(HedgeFunBondingCurve.Status.Graduated));
        principal = treasury.protectedGraduationStock();
        assertGt(principal, 0);
        assertEq(stock.balanceOf(address(treasury)), principal);
        assertEq(treasury.buybackStock(), 0);
        assertEq(treasury.totalIncomeStock(), 0);

        // stake, then trade both ways in the graduated pool
        token.transfer(staker, 1_000_000e18);
        stock.approve(address(router), type(uint256).max);
        token.approve(address(router), type(uint256).max);
        uint8 stage = router.GRADUATED();
        router.buy(Router.TradeParams(id, address(stock), 1e18, 1e18, 1, block.timestamp, stage, false), new Router.Hop[](0));
        router.sell(Router.TradeParams(id, address(stock), 5_000_000e18, 0, 1, block.timestamp, stage, false),
            new Router.Hop[](0));
        vm.stopPrank();
        vm.startPrank(staker);
        token.approve(address(staking), type(uint256).max);
        staking.stake(1_000_000e18);
        vm.stopPrank();
    }

    /// tax share (curve claim + hook sweep) and LP fees all become income, split at `bps`
    function _settle(uint256 bps, uint256 principal) private {
        (PoolKey memory key,) = factory.graduationConfig(id);
        curve.claimFees(address(treasury));
        HedgeFunHook(address(factory.hook())).sweep(key.toId());
        assertTrue(treasury.book());
        (uint256 lpStock,) = V2LiquidityVault(treasury.liquidityVault()).collectFees();
        assertGt(lpStock, 0, "the deployed vault accepted this kind's fee credit");
        treasury.distribute();
        uint256 income = treasury.totalIncomeStock();
        assertGt(income, lpStock, "tax share plus LP fees");
        assertEq(treasury.totalDividendStock(), income * bps / 10000);
        assertEq(treasury.pendingDividendStock(), 0);
        assertEq(stock.balanceOf(address(staking)), treasury.totalDividendStock());
        assertEq(treasury.buybackStock(), income - treasury.totalDividendStock());
        assertEq(stock.balanceOf(address(treasury)), principal + treasury.buybackStock(), "principal untouched");

        vm.warp(block.timestamp + 7 days);
        vm.prank(staker);
        uint256 paid = staking.claim(staker);
        assertApproxEqAbs(paid, treasury.totalDividendStock(), 1e6, "the only staker earns the whole stream");
        assertEq(stock.balanceOf(staker), paid);
    }
}

// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Math} from "@openzeppelin/contracts/utils/math/Math.sol";
import {HedgeFunFactory} from "../src/HedgeFunFactory.sol";
import {HedgeFunV2Factory} from "../src/v2/HedgeFunV2Factory.sol";
import {CurveDeployer} from "../src/v2/CurveDeployer.sol";
import {HedgeFunBondingCurve} from "../src/v2/HedgeFunBondingCurve.sol";
import {V2FactoryFixture} from "./utils/V2FactoryFixture.sol";

/// Each V2 creator chooses the raise size (`saleBps`) and the opening window (`snipeSeconds`) of their own launch,
/// registered on the curve deployer for the factory salt (symbol, creator, nonce). These pin: the bounds are the
/// curve's own and nothing tighter; the curve is built from the registration; nobody registers for anyone else;
/// both choices are in the launch terms; and a launch that registered nothing gets 7931 and the factory's window.
contract V2CreatorCurveConfigTest is V2FactoryFixture {
    address internal stranger = address(uint160(uint256(keccak256("creator curve config stranger"))));
    CurveDeployer internal registry;
    uint256 internal constant S = 1_000_000e18;   // the fixture's supply
    uint256 internal constant V = 50e18;          // ceil(openPrice * S / 1e18) for the fixture's 18-decimal stock

    event CurveConfigSet(address indexed creator, string symbol, uint96 nonce, uint16 saleBps, uint8 snipeSeconds);
    event CurveLaunched(uint256 indexed id, address indexed curve, uint16 saleBps, uint256 virtualStock);

    function setUp() public {
        _setUpV2(18);
        registry = factory.curveDeployer();
    }

    function _launchWith(uint96 nonce, uint16 sale, uint8 window, bool register)
        internal returns (HedgeFunFactory.Request memory q, HedgeFunBondingCurve curve)
    {
        q = _request();
        q.nonce = nonce;
        if (register) registry.setCurveConfig(q.symbol, q.nonce, sale, window);
        address predicted = factory.predictCurve(q);
        (,, bytes32 terms) = factory.predict(q);
        uint256 id = factory.launch(q, terms);
        curve = HedgeFunBondingCurve(factory.curves(id));
        assertEq(address(curve), predicted, "predictCurve");
        stock.approve(address(curve), type(uint256).max);
    }

    // ------------------------------------------------------------------------------------------------ raise size

    /// Rg = ceil(S * V / Tmin) - V with Tmin = floor(S * (10000 - sale) / 10000), which for this supply is
    /// ceil(V * sale / (10000 - sale)): 4400 raises 0.79 V, 6000 1.5 V, 8000 4 V, 1000 V / 9 and 9000 9 V.
    function test_creatorSaleBpsBuildsTheCurveAndItsRaise() public {
        uint16[5] memory sale = [uint16(4400), 6000, 8000, 1000, 9000];
        uint256[5] memory rgLiteral = [uint256(39_285_714_285_714_285_715), 75e18, 200e18, 5_555_555_555_555_555_556, 450e18];
        for (uint256 i; i < sale.length; ++i) {
            (, HedgeFunBondingCurve curve) = _launchWith(uint96(i + 1), sale[i], 3, true);
            uint256 tMin = S * (10_000 - sale[i]) / 10_000;
            assertEq(curve.virtualStock(), V);
            assertEq(curve.minTokenReserve(), tMin, "minTokenReserve");
            assertEq(curve.terminalStock(), Math.ceilDiv(S * V, tMin), "terminalStock");
            uint256 rg = curve.terminalStock() - curve.virtualStock();
            assertEq(rg, Math.ceilDiv(V * sale[i], 10_000 - sale[i]), "Rg = V * sale / (10000 - sale)");
            assertEq(rg, rgLiteral[i], "Rg literal");
            // The immutable raise is net principal. The buyer additionally funds the base stock
            // fee, and only that fee remains claimable after the net raise seeds the V4 pool.
            uint256 beforeBalance = stock.balanceOf(address(this));
            (uint256 spent,) = curve.buy(type(uint256).max, 1, address(this), block.timestamp);
            uint256 fee = spent * _request().taxBps / 10_000;
            assertEq(beforeBalance - stock.balanceOf(address(this)), spent, "actual gross payment");
            assertEq(spent - fee, rg, "gross payment less base fee funds the whole net raise");
            assertLt((spent - 1) - (spent - 1) * _request().taxBps / 10_000, rg,
                "no smaller gross payment funds the cap");
            assertEq(curve.totalFees(), fee);
            assertEq(stock.balanceOf(address(curve)), fee, "only fee liabilities remain after graduation");
            assertEq(uint256(curve.status()), uint256(HedgeFunBondingCurve.Status.Graduated));
        }
    }

    function test_saleBpsOutsideTheCurvesBoundsIsRefusedAtRegistration() public {
        assertEq(registry.MIN_SALE_BPS(), 1000);
        assertEq(registry.MAX_SALE_BPS(), 9000);
        uint16[4] memory bad = [uint16(999), 9001, 0, 10_000];
        for (uint256 i; i < bad.length; ++i) {
            vm.expectRevert(CurveDeployer.BadCurveConfig.selector);
            registry.setCurveConfig("V2", 0, bad[i], 3);
        }
        registry.setCurveConfig("V2", 0, 1000, 3);
        registry.setCurveConfig("V2", 0, 9000, 3);
    }

    /// `_preflight` runs on the creator's choice, not the default: on a listing where a 10% sale leaves the V4 seed
    /// under two raw units, that choice is refused at quote and launch while the default quotes and launches.
    function test_preflightRefusesAnUnseedableCreatorChoice() public {
        HedgeFunFactory.Defaults memory d = _defaults();
        d.supply = 1e18;
        vm.startPrank(owner);
        factory.setDefaults(d);
        factory.list(address(stock), address(oracle), address(stockPool), 9, true);
        vm.stopPrank();
        HedgeFunFactory.Request memory q = _request();
        q.expectedOpenPriceE18 = 9;
        registry.setCurveConfig(q.symbol, q.nonce, 1000, 3);   // Rg = 10 - 9 = 1 wei: an LP stock of 0
        vm.expectRevert(HedgeFunV2Factory.Unseedable.selector);
        factory.predict(q);
        vm.expectRevert(HedgeFunV2Factory.Unseedable.selector);
        factory.launch(q, bytes32(0));
        registry.setCurveConfig(q.symbol, q.nonce, 4400, 3);   // Rg = 17 - 9 = 8 wei: seedable
        (,, bytes32 terms) = factory.predict(q);
        HedgeFunBondingCurve curve = HedgeFunBondingCurve(factory.curves(factory.launch(q, terms)));
        assertEq(curve.terminalStock() - curve.virtualStock(), 8);
    }

    // ------------------------------------------------------------------------------------------------ opening window

    /// The #95 schedule: taxBps + ceil((snipeBps - taxBps) * (W - t) / W) for t < W, then the flat tax. W = 0 is off.
    function test_creatorSnipeSecondsSetsTheOpeningWindow() public {
        uint8[4] memory window = [uint8(0), 3, 60, 180];
        for (uint256 i; i < window.length; ++i) {
            uint256 snap = vm.snapshotState();
            (, HedgeFunBondingCurve curve) = _launchWith(uint96(i + 1), 8000, window[i], true);
            uint256 w = window[i];
            uint256 tax = curve.taxBps();
            assertEq(tax, 1000);
            assertEq(curve.snipeSeconds(), w, "the creator's window");
            assertEq(curve.snipeBps(), _defaults().snipeBps, "the opening rate stays the owner's");
            uint256 t0 = curve.launchedAt();
            for (uint256 t; t <= w + 1; ++t) {
                vm.warp(t0 + t);
                uint256 expected = t < w ? tax + Math.ceilDiv((9900 - tax) * (w - t), w) : tax;
                assertEq(curve.buyRateBps(), expected, "schedule");
                if (t < w) assertGt(curve.buyRateBps(), tax, "every second inside the window pays more");
            }
            vm.revertToState(snap);
        }
    }

    function test_openingWindowLiterals() public {
        (, HedgeFunBondingCurve curve) = _launchWith(1, 8000, 60, true);
        uint256 t0 = curve.launchedAt();
        assertEq(curve.buyRateBps(), 9900);
        vm.warp(t0 + 30); assertEq(curve.buyRateBps(), 5450);   // 1000 + 8900 / 2
        vm.warp(t0 + 59); assertEq(curve.buyRateBps(), 1149);   // 1000 + ceil(8900 / 60)
        vm.warp(t0 + 60); assertEq(curve.buyRateBps(), 1000);
        (, curve) = _launchWith(2, 8000, 0, true);
        assertEq(curve.buyRateBps(), 1000, "0 is off: the flat tax from the launch block");
        (, curve) = _launchWith(3, 8000, 180, true);
        t0 = curve.launchedAt();
        vm.warp(t0 + 1); assertEq(curve.buyRateBps(), 9851);    // 1000 + ceil(8900 * 179 / 180)
        vm.warp(t0 + 179); assertEq(curve.buyRateBps(), 1050);  // 1000 + ceil(8900 / 180)
        vm.warp(t0 + 180); assertEq(curve.buyRateBps(), 1000);
    }

    function test_snipeSecondsOverTheBoundIsRefusedAtRegistration() public {
        assertEq(registry.MAX_SNIPE_SECONDS(), 180);
        vm.expectRevert(CurveDeployer.BadCurveConfig.selector);
        registry.setCurveConfig("V2", 0, 4400, 181);
        vm.expectRevert(CurveDeployer.BadCurveConfig.selector);
        registry.setCurveConfig("V2", 0, 4400, 255);
        registry.setCurveConfig("V2", 0, 4400, 180);
        registry.setCurveConfig("V2", 0, 4400, 0);
    }

    // ------------------------------------------------------------------------------------------------ defaults

    function test_noRegistrationGivesTheDefaults() public {
        assertEq(registry.DEFAULT_SALE_BPS(), 7931);
        (HedgeFunFactory.Request memory q, HedgeFunBondingCurve curve) = _launchWith(0, 0, 0, false);
        bytes32 salt = keccak256(abi.encode(q.symbol, q.creator, q.nonce));
        (uint16 rawSale, uint8 rawWindow) = registry.curveConfigOf(salt);
        assertEq(rawSale, 0, "nothing registered");
        assertEq(rawWindow, 0);
        assertEq(curve.minTokenReserve(), S * 2069 / 10_000, "7931");
        assertEq(curve.snipeSeconds(), 3, "the factory's snipeSeconds");
        (uint16 sale, uint8 window) = registry.curveConfig(salt, 3);
        assertEq(sale, 7931);
        assertEq(window, 3);

        // The default window is the factory's at quote time, not a constant.
        HedgeFunFactory.Defaults memory d = _defaults();
        d.snipeSeconds = 45;
        vm.prank(owner);
        factory.setDefaults(d);
        (, curve) = _launchWith(1, 0, 0, false);
        assertEq(curve.snipeSeconds(), 45);
        assertEq(curve.minTokenReserve(), S * 2069 / 10_000);
    }

    function test_registrationEmitsAndReadsBack() public {
        vm.expectEmit(true, false, false, true, address(registry));
        emit CurveConfigSet(address(this), "V2", 7, 6000, 60);
        registry.setCurveConfig("V2", 7, 6000, 60);
        (uint16 sale, uint8 window) = registry.curveConfigOf(keccak256(abi.encode("V2", address(this), uint96(7))));
        assertEq(sale, 6000);
        assertEq(window, 60);
        (sale, window) = registry.curveConfig(keccak256(abi.encode("V2", address(this), uint96(7))), 3);
        assertEq(sale, 6000, "a registration overrides the default");
        assertEq(window, 60);
    }

    // ------------------------------------------------------------------------------------------------ salt binding

    function test_strangerCannotChooseForAnotherCreatorsSalt() public {
        HedgeFunFactory.Request memory q = _request();          // the creator is this contract
        (,, bytes32 terms) = factory.predict(q);
        vm.prank(stranger);
        registry.setCurveConfig(q.symbol, q.nonce, 9000, 180);   // same symbol and nonce, from someone else
        bytes32 mine = keccak256(abi.encode(q.symbol, address(this), q.nonce));
        bytes32 theirs = keccak256(abi.encode(q.symbol, stranger, q.nonce));
        assertTrue(mine != theirs, "a different sender is a different salt");
        (uint16 sale, uint8 window) = registry.curveConfigOf(mine);
        assertEq(sale, 0);
        assertEq(window, 0);
        (sale, window) = registry.curveConfigOf(theirs);
        assertEq(sale, 9000);
        assertEq(window, 180);

        // The creator's pending quote is untouched: no Restated, and the curve is the default one.
        HedgeFunBondingCurve curve = HedgeFunBondingCurve(factory.curves(factory.launch(q, terms)));
        assertEq(curve.minTokenReserve(), S * 2069 / 10_000);
        assertEq(curve.snipeSeconds(), 3);

        // The stranger's registration reaches only a launch the stranger makes as its own creator.
        q.creator = stranger;
        (,, terms) = factory.predict(q);
        vm.prank(stranger);
        curve = HedgeFunBondingCurve(factory.curves(factory.launch(q, terms)));
        assertEq(curve.minTokenReserve(), S * 1000 / 10_000);
        assertEq(curve.snipeSeconds(), 180);
        assertEq(curve.creator(), stranger);
    }

    // ------------------------------------------------------------------------------------------------ terms

    function test_changingTheRaiseAfterPredictRestates() public {
        HedgeFunFactory.Request memory q = _request();
        registry.setCurveConfig(q.symbol, q.nonce, 6000, 60);
        (,, bytes32 terms) = factory.predict(q);
        registry.setCurveConfig(q.symbol, q.nonce, 6001, 60);
        vm.expectRevert(HedgeFunFactory.Restated.selector);
        factory.launch(q, terms);
        // back to the quoted choice, the quote is good again
        registry.setCurveConfig(q.symbol, q.nonce, 6000, 60);
        HedgeFunBondingCurve curve = HedgeFunBondingCurve(factory.curves(factory.launch(q, terms)));
        assertEq(curve.minTokenReserve(), S * 4000 / 10_000);
        assertEq(curve.snipeSeconds(), 60);
    }

    /// The window is not in any address `super._terms` covers (only the curve's, which is not in terms), so the
    /// V2 terms hash pins it explicitly. A window-only change must restate.
    function test_changingTheWindowAfterPredictRestates() public {
        HedgeFunFactory.Request memory q = _request();
        registry.setCurveConfig(q.symbol, q.nonce, 6000, 60);
        (,, bytes32 terms) = factory.predict(q);
        registry.setCurveConfig(q.symbol, q.nonce, 6000, 61);
        (,, bytes32 moved) = factory.predict(q);
        assertTrue(moved != terms, "the window is in the terms");
        vm.expectRevert(HedgeFunFactory.Restated.selector);
        factory.launch(q, terms);
        registry.setCurveConfig(q.symbol, q.nonce, 6000, 0);
        vm.expectRevert(HedgeFunFactory.Restated.selector);
        factory.launch(q, terms);
    }

    /// Terms cover the values, not the act of registering: a first registration after `predict` restates exactly
    /// when it differs from the defaults the quote was given.
    function test_firstRegistrationAfterPredictRestatesOnlyIfItChangesTheCurve() public {
        HedgeFunFactory.Request memory q = _request();
        (,, bytes32 terms) = factory.predict(q);
        registry.setCurveConfig(q.symbol, q.nonce, 4400, 60);
        vm.expectRevert(HedgeFunFactory.Restated.selector);
        factory.launch(q, terms);
        registry.setCurveConfig(q.symbol, q.nonce, 7931, 3);    // the defaults, spelt out
        factory.launch(q, terms);
    }

    // ------------------------------------------------------------------------------------------------ addresses

    /// `predictCurve` and an independent CREATE2 derivation from `type(HedgeFunBondingCurve).creationCode` with the
    /// creator's literal choices both name the deployed curve.
    function test_predictCurveIsTheDeployedCurveForCreatorChoices() public {
        HedgeFunFactory.Request memory q = _request();
        q.nonce = 11;
        registry.setCurveConfig(q.symbol, q.nonce, 6000, 60);
        address predicted = factory.predictCurve(q);
        (,, bytes32 terms) = factory.predict(q);
        uint256 id = factory.strategyCount();
        (address token, address treasury,) = factory.predict(q);
        vm.expectEmit(true, true, false, true, address(factory));
        emit CurveLaunched(id, predicted, 6000, V);
        factory.launch(q, terms);
        HedgeFunBondingCurve curve = HedgeFunBondingCurve(factory.curves(id));
        assertEq(address(curve), predicted);
        HedgeFunBondingCurve.Init memory p = HedgeFunBondingCurve.Init({
            factory: address(factory), token: token, stock: address(stock), treasury: treasury,
            protocol: protocol, creator: address(this), supply: S, virtualStock: V, saleBps: 6000,
            taxBps: q.taxBps, protocolBps: 2000, creatorBps: q.creatorBps, snipeBps: 9900, snipeSeconds: 60,
            openingTaxExemptions: new address[](0)
        });
        bytes32 salt = keccak256(abi.encode(q.symbol, q.creator, q.nonce));
        bytes32 initHash = keccak256(abi.encodePacked(type(HedgeFunBondingCurve).creationCode, abi.encode(p)));
        assertEq(address(curve), vm.computeCreate2Address(salt, initHash, address(registry)), "independent CREATE2");
        // the same launch at the defaults would have been a different curve
        p.saleBps = 4400;
        p.snipeSeconds = 3;
        assertTrue(registry.predict(salt, abi.encode(p)) != address(curve));
    }

    /// Registering is free and open to every sender: a creator needs no listing, no owner and no launch to do it,
    /// and re-registering a salt that already launched changes nothing that exists.
    function test_reRegisteringALaunchedSaltChangesNothingDeployed() public {
        (HedgeFunFactory.Request memory q, HedgeFunBondingCurve curve) = _launchWith(5, 6000, 60, true);
        registry.setCurveConfig(q.symbol, q.nonce, 9000, 0);
        assertEq(curve.minTokenReserve(), S * 4000 / 10_000);
        assertEq(curve.snipeSeconds(), 60);
    }
}

// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Script, console2} from "forge-std/Script.sol";
import {VmSafe} from "forge-std/Vm.sol";
import {IPoolManager} from "v4-core/src/interfaces/IPoolManager.sol";
import {HedgeFunFactory, TokenDeployer} from "../src/HedgeFunFactory.sol";
import {HedgeFunHook} from "../src/hooks/HedgeFunHook.sol";
import {HedgeFunV2Factory} from "../src/v2/HedgeFunV2Factory.sol";
import {V2TreasuryDeployer} from "../src/v2/V2TreasuryDeployer.sol";
import {HedgeFunV2BuybackTreasury} from "../src/v2/HedgeFunV2BuybackTreasury.sol";
import {CurveDeployer} from "../src/v2/CurveDeployer.sol";
import {HedgeFunBondingCurve} from "../src/v2/HedgeFunBondingCurve.sol";
import {HedgeFunV2TradeRouter} from "../src/v2/HedgeFunV2TradeRouter.sol";

/// @notice Simulates the V2 platform deployment against a local Robinhood Chain fork.
/// @dev Deliberately refuses chain 4663. This script neither lists a stock nor opens public launch.
///      Use `forge script` without `--broadcast` as described in docs/V2_DEPLOYMENT_REHEARSAL.md.
contract RehearseV2Launchpad is Script {
    address constant PM = 0x8366a39CC670B4001A1121B8F6A443A643e40951;
    address constant V3_FACTORY = 0x1f7d7550B1b028f7571E69A784071F0205FD2EfA;
    address constant USDG = 0x5fc5360D0400a0Fd4f2af552ADD042D716F1d168;
    uint160 constant HOOK_FLAGS = 0x2844;

    error ForkOnly();
    error BroadcastForbidden();
    error MissingCode(address target);
    error UnsafeRole(address who);
    error BadHook(address expected, address actual);
    error ReadbackFailed();

    struct Deployed {
        V2TreasuryDeployer treasury;
        TokenDeployer token;
        CurveDeployer curve;
        HedgeFunHook hook;
        HedgeFunV2Factory factory;
        HedgeFunV2TradeRouter router;
        address kindOneChunkA;
        address kindOneChunkB;
    }

    function run() external {
        if (block.chainid != 31337) revert ForkOnly();
        if (vm.isContext(VmSafe.ForgeContext.ScriptBroadcast)
            || vm.isContext(VmSafe.ForgeContext.ScriptResume)) revert BroadcastForbidden();
        if (PM.code.length == 0) revert MissingCode(PM);
        if (V3_FACTORY.code.length == 0) revert MissingCode(V3_FACTORY);
        if (USDG.code.length == 0) revert MissingCode(USDG);
        if (CREATE2_FACTORY.code.length == 0) revert MissingCode(CREATE2_FACTORY);

        address owner = vm.envAddress("OWNER");
        address protocol = vm.envAddress("PROTOCOL");
        address calendar = vm.envAddress("CALENDAR");
        _requireSafe(owner);
        _requireSafe(protocol);
        if (owner == msg.sender || protocol == msg.sender) revert UnsafeRole(msg.sender);
        if (calendar.code.length == 0) revert MissingCode(calendar);

        HedgeFunFactory.Defaults memory d = _defaults();
        (bytes32 salt, address mined) = _mineHook(vm.envOr("HOOK_SALT_START", uint256(0)));
        console2.log("V2 fork rehearsal; chain", block.chainid);
        console2.log("owner", owner);
        console2.log("protocol", protocol);
        console2.log("existing calendar", calendar);
        console2.log("hook salt", uint256(salt));
        console2.log("hook address", mined);

        // forge script without --broadcast simulates these transactions and discards them.
        Deployed memory x = _deploy(owner, protocol, d, salt, mined);
        _readBack(x, owner, protocol, d.lpFee);
        _readBackCurveChoices(x.curve, d.snipeSeconds);
        console2.log("V2 treasury deployer", address(x.treasury));
        console2.log("V2 treasury code chunk A", x.treasury.chunkA());
        console2.log("V2 treasury code chunk B", x.treasury.chunkB());
        console2.log("kind 1 buyback chunk A", x.kindOneChunkA);
        console2.log("kind 1 buyback chunk B", x.kindOneChunkB);
        console2.log("registered strategy kinds", x.treasury.kindCount());
        console2.log("token deployer", address(x.token));
        console2.log("curve deployer", address(x.curve));
        console2.log("curve code chunk", x.curve.curveChunk());
        console2.log("hook", address(x.hook));
        console2.log("V2 factory", address(x.factory));
        console2.log("V2 trade router", address(x.router));
        console2.log("lp fee", d.lpFee);
        // Raise size and opening window are each creator's choice (CurveDeployer.setCurveConfig), not the owner's.
        console2.log("creator saleBps range", x.curve.MIN_SALE_BPS(), x.curve.MAX_SALE_BPS());
        console2.log("default saleBps (no registration)", x.curve.DEFAULT_SALE_BPS());
        console2.log("creator snipeSeconds max", x.curve.MAX_SNIPE_SECONDS());
        console2.log("default snipeSeconds (no registration)", d.snipeSeconds);
        console2.log("public launch", x.factory.publicLaunch());
        console2.log("readback passed; no stock listed or launched");
    }

    function _deploy(address owner, address protocol, HedgeFunFactory.Defaults memory d, bytes32 salt, address mined)
        internal returns (Deployed memory x)
    {
        vm.startBroadcast();
        x.treasury = new V2TreasuryDeployer();
        x.token = new TokenDeployer();
        x.curve = new CurveDeployer();
        x.hook = new HedgeFunHook{salt: salt}(IPoolManager(PM));
        if (address(x.hook) != mined || uint160(address(x.hook)) & 0x3FFF != HOOK_FLAGS) {
            revert BadHook(mined, address(x.hook));
        }
        x.factory = new HedgeFunV2Factory(owner, PM, V3_FACTORY, USDG, protocol,
            address(x.treasury), address(x.token), address(x.hook), address(x.curve), d);
        x.router = new HedgeFunV2TradeRouter(x.factory);
        vm.stopBroadcast();

        // Rehearse the separate Safe-owned registration transaction without signing or broadcasting it. The code
        // chunks are permissionless to create; only the configured factory owner may append them as strategy kind 1.
        (x.kindOneChunkA, x.kindOneChunkB) =
            x.treasury.makeChunks(type(HedgeFunV2BuybackTreasury).creationCode);
        vm.prank(owner);
        if (x.treasury.registerKind(x.kindOneChunkA, x.kindOneChunkB) != 1) revert ReadbackFailed();
    }

    function _readBack(Deployed memory x, address owner, address protocol, uint24 lpFee) internal view {
        if (x.factory.owner() != owner || x.factory.protocol() != protocol || x.factory.publicLaunch()
            || address(x.factory.curveDeployer()) != address(x.curve)
            || x.treasury.factory() != address(x.factory) || x.token.factory() != address(x.factory)
            || x.curve.factory() != address(x.factory) || x.hook.factory() != address(x.factory)
            || address(x.router.factory()) != address(x.factory) || x.treasury.version() != 2
            || x.factory.getDefaults().lpFee != lpFee) revert ReadbackFailed();
        if (x.treasury.kindCount() != 2) revert ReadbackFailed();
        // The curve deployer creates its curve-code chunk in its own constructor; every curve address hashes it.
        if (keccak256(x.curve.curveChunk().code) != keccak256(type(HedgeFunBondingCurve).creationCode)) {
            revert ReadbackFailed();
        }
        (address a, address b) = x.treasury.kinds(1);
        if (a != x.kindOneChunkA || b != x.kindOneChunkB
            || keccak256(bytes.concat(a.code, b.code)) != keccak256(type(HedgeFunV2BuybackTreasury).creationCode)) {
            revert ReadbackFailed();
        }
    }

    /// @dev The creator's registry: the curve constructor's own sale bounds and nothing tighter, the 4400 default,
    ///      the 180-second window cap, and a registration keyed by the factory's salt (symbol, creator, nonce).
    ///      The registration is simulated from a throwaway address and discarded with everything else.
    function _readBackCurveChoices(CurveDeployer curve, uint8 defaultSnipeSeconds) internal {
        if (curve.MIN_SALE_BPS() != 1000 || curve.MAX_SALE_BPS() != 9000 || curve.DEFAULT_SALE_BPS() != 4400
            || curve.MAX_SNIPE_SECONDS() != 180) revert ReadbackFailed();
        address creator = address(uint160(uint256(keccak256("rehearsal creator"))));
        bytes32 salt = keccak256(abi.encode("REHEARSE", creator, uint96(1)));
        (uint16 sale, uint8 window) = curve.curveConfig(salt, defaultSnipeSeconds);
        if (sale != 4400 || window != defaultSnipeSeconds) revert ReadbackFailed();
        vm.prank(creator);
        curve.setCurveConfig("REHEARSE", 1, 6000, 60);
        (sale, window) = curve.curveConfig(salt, defaultSnipeSeconds);
        if (sale != 6000 || window != 60) revert ReadbackFailed();
    }

    function _mineHook(uint256 start) internal view returns (bytes32 salt, address hook) {
        bytes32 initHash = keccak256(abi.encodePacked(type(HedgeFunHook).creationCode, abi.encode(PM)));
        for (uint256 i = start; i < start + 2_000_000; ++i) {
            hook = address(uint160(uint256(keccak256(abi.encodePacked(bytes1(0xff), CREATE2_FACTORY, bytes32(i), initHash)))));
            if (uint160(hook) & 0x3FFF == HOOK_FLAGS && hook.code.length == 0) return (bytes32(i), hook);
        }
        revert("no hook salt");
    }

    function _requireSafe(address who) internal view {
        if (who.code.length == 0 || who.code.length == 23 && who.code[0] == 0xef) revert UnsafeRole(who);
        (bool ok, bytes memory result) = who.staticcall(abi.encodeWithSignature("getThreshold()"));
        if (!ok || result.length != 32) revert UnsafeRole(who);
        uint256 threshold = abi.decode(result, (uint256));
        (ok, result) = who.staticcall(abi.encodeWithSignature("getOwners()"));
        if (!ok || result.length < 64) revert UnsafeRole(who);
        uint256 owners = abi.decode(result, (address[])).length;
        if (threshold < 2 || threshold > owners) revert UnsafeRole(who);
    }

    function _defaults() internal pure returns (HedgeFunFactory.Defaults memory d) {
        d.supply = 1_000_000_000e18;
        d.lpFee = 3000; // V2's locked liquidity vault collects fees; V1's zero-fee default cannot be reused.
        d.tickSpacing = 60;
        d.minTaxBps = 100;
        d.maxTaxBps = 1500;
        d.protocolBps = 2000;
        d.maxCreatorBps = 3000;
        d.spikeBps = 0; // V2 LP fees can fund buybacks without strategy profit; no buyback-triggered sell spike.
        d.spikeSeconds = 0;
        d.sweepTipBps = 50;
        d.snipeBps = 9900;
        d.snipeSeconds = 3;
        d.bountyBps = 50;
        d.maxSlippageBps = 100;
        d.maxDeviationBps = 50;
        d.maxBuybackImpactBps = 300;
        d.buybackCooldown = 60;
        d.minLotUsdg = 5e6;
        d.buybackChunkUsdg = 500e6;
        d.sellChunkUsdg = 2_000e6;
        d.launchFeeCurrency = HedgeFunFactory.FeeCurrency.Usdg;
        d.launchFeeAmount = 25e6;
    }
}

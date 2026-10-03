// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {DeployV2CreatorTestnet} from "../script/DeployV2CreatorTestnet.s.sol";
import {DeployV2FeeUpgradeTestnet} from "../script/DeployV2FeeUpgradeTestnet.s.sol";
import {HedgeFunFactory} from "../src/HedgeFunFactory.sol";
import {HedgeFunV2Factory} from "../src/v2/HedgeFunV2Factory.sol";
import {HedgeFunV2Treasury} from "../src/v2/HedgeFunV2Treasury.sol";
import {HedgeFunV2AllInTreasury} from "../src/v2/HedgeFunV2AllInTreasury.sol";
import {HedgeFunBondingCurve} from "../src/v2/HedgeFunBondingCurve.sol";

contract CreatorCoreRoleInvoker {
    function deploy(DeployV2CreatorTestnet tool) external returns (DeployV2FeeUpgradeTestnet.Deployment memory) {
        return tool.deploy(0);
    }
}

contract TestnetV2CreatorCoreTest is Test {
    address private constant OLD_FEE_FACTORY = 0xACEB03aAeE5494Aa54929Ec840630ae32A9ade0A;
    address private constant OLD_FEE_REGISTRY = 0xe874fE425e14f3CBa3aDBA2Dd10B50E153Ac6064;
    address private constant CREATOR = 0xD4f69D180a9bc36F27D307E90E365d1E012816d5;

    function test_forkFreshCoreAllowsOneBpsAllRungsAndGraduatesOnExistingPools() public {
        if (!vm.envOr("CREATOR_CORE_FORK", false)) {
            vm.skip(true);
            return;
        }
        vm.createSelectFork("https://rpc.testnet.chain.robinhood.com", vm.envUint("CREATOR_CORE_FORK_BLOCK"));
        emit log_named_uint("creator core fork block", block.number);
        emit log_named_string("reviewed source", vm.envString("GIT_COMMIT"));
        bytes32 oldFactoryHash = OLD_FEE_FACTORY.codehash;
        bytes32 oldRegistryHash = OLD_FEE_REGISTRY.codehash;
        uint256 oldCount = HedgeFunV2Factory(OLD_FEE_FACTORY).strategyCount();
        DeployV2CreatorTestnet tool = new DeployV2CreatorTestnet();
        vm.etch(tool.OPERATOR(), address(new CreatorCoreRoleInvoker()).code);
        DeployV2FeeUpgradeTestnet.Deployment memory x = CreatorCoreRoleInvoker(tool.OPERATOR()).deploy(tool);
        assertEq(x.lines.length, 8);
        assertTrue(address(x.factory) != OLD_FEE_FACTORY);
        assertTrue(address(x.treasury) != OLD_FEE_REGISTRY);
        assertEq(x.treasury.allInTriggerCodeHash(), keccak256(type(HedgeFunV2AllInTreasury).creationCode));
        _launchAndGraduate(x);
        assertEq(OLD_FEE_FACTORY.codehash, oldFactoryHash);
        assertEq(OLD_FEE_REGISTRY.codehash, oldRegistryHash);
        assertEq(HedgeFunV2Factory(OLD_FEE_FACTORY).strategyCount(), oldCount);
    }

    function _launchAndGraduate(DeployV2FeeUpgradeTestnet.Deployment memory x) private {
        HedgeFunFactory.Request memory q;
        q.name = "Creator TSLA";
        q.symbol = "HFOPEN";
        q.stock = address(x.lines[3].stock);
        q.creator = CREATOR;
        q.taxBps = 300;
        q.creatorBps = 1000;
        q.tp1Bps = 1;
        q.tp2Bps = 2;
        q.dipBps = 1;
        q.stopBps = 1;
        q.lotBps = 2000;
        q.nonce = 202609300410;
        q.maxFee = x.defaults.launchFeeAmount;
        q.expectedOpenPriceE18 = x.lines[3].openPriceE18;
        vm.startPrank(CREATOR);
        x.curve.setCurveConfig(q.symbol, q.nonce, 4400, 0);
        (address token, address treasury, bytes32 terms) = x.factory.predict(q);
        assertTrue(IERC20(address(x.venue.usdg)).approve(address(x.factory), q.maxFee));
        assertEq(x.factory.launch(q, terms), 0);
        (address actualToken, address actualTreasury,,,) = x.factory.strategies(0);
        assertEq(actualToken, token);
        assertEq(actualTreasury, treasury);
        HedgeFunV2Treasury t = HedgeFunV2Treasury(treasury);
        assertEq(t.params().tp1Bps, 1);
        assertEq(t.params().tp2Bps, 2);
        assertEq(t.params().dipBps, 1);
        assertEq(t.params().stopBps, 1);
        HedgeFunBondingCurve curve = HedgeFunBondingCurve(x.factory.curves(0));
        (uint256 spent, uint256 output, uint256 burned) = curve.quoteBuyFor(24e18, CREATOR);
        assertGt(spent, 0);
        assertLe(spent, 24e18);
        assertEq(output, 440_000_000e18);
        assertEq(burned, 0);
        assertTrue(IERC20(q.stock).approve(address(curve), 24e18));
        curve.buy(24e18, output, CREATOR, block.timestamp + 300);
        vm.stopPrank();
        assertEq(uint8(curve.status()), 2);
        assertGt(t.bookedStock(), 0);
        assertEq(t.bookedStock() + t.buybackStock(), IERC20(q.stock).balanceOf(treasury));
    }
}

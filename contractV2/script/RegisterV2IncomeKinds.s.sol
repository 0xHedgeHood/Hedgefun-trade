// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Script, console2} from "forge-std/Script.sol";
import {HedgeFunV2Factory} from "../src/v2/HedgeFunV2Factory.sol";
import {V2TreasuryDeployer} from "../src/v2/V2TreasuryDeployer.sol";
import {HedgeFunV2DividendTreasury, HedgeFunV2BuybackDividendTreasury} from "../src/v2/HedgeFunV2IncomeTreasury.sol";

/// @notice Add the two income kinds to an existing V2 factory's treasury registry, for future launches.
/// @dev Requires OPERATOR and V2_FACTORY. The operator must be the factory owner. Four transactions: each kind's
///      creation code is stored as two chunks, then registered. Existing kinds and launched treasuries are untouched;
///      a creator opts in per launch with `V2TreasuryDeployer.setStrategyKind(symbol, nonce, kind)`.
///      Run a fork simulation before broadcasting, then `VerifyV2IncomeKinds` against the confirmed chain.
contract RegisterV2IncomeKinds is Script {
    error BadBinding(string what);
    error ReadbackFailed(string what);

    function run() external returns (uint8 dividendKind, uint8 splitKind) {
        address operator = vm.envAddress("OPERATOR");
        if (operator == address(0) || msg.sender != operator) revert BadBinding("operator sender");
        (dividendKind, splitKind) = register(operator, HedgeFunV2Factory(vm.envAddress("V2_FACTORY")));
        console2.log("dividend kind (100% of income to stakers)", dividendKind);
        console2.log("buyback + dividend kind (50% / 50%)", splitKind);
        console2.log("simulation readback passed; verify four receipts and live state after broadcast");
    }

    /// @notice every transaction, broadcast from `operator`; `run` adds the environment and the log
    function register(address operator, HedgeFunV2Factory factory) public returns (uint8 dividendKind, uint8 splitKind) {
        // Every binding check runs before the first broadcast transaction.
        if (address(factory).code.length == 0 || factory.owner() != operator) revert BadBinding("factory owner");
        V2TreasuryDeployer registry = V2TreasuryDeployer(address(factory.treasuryDeployer()));
        if (address(registry).code.length == 0 || registry.factory() != address(factory)) revert BadBinding("registry");
        if (registry.kindCount() + 2 > type(uint8).max) revert BadBinding("registry full");

        vm.startBroadcast(operator);
        (address a, address b) = registry.makeChunks(type(HedgeFunV2DividendTreasury).creationCode);
        dividendKind = registry.registerKind(a, b);
        (a, b) = registry.makeChunks(type(HedgeFunV2BuybackDividendTreasury).creationCode);
        splitKind = registry.registerKind(a, b);
        vm.stopBroadcast();

        // These checks are against Foundry's simulated state when broadcasting.
        check(registry, dividendKind, splitKind);
    }

    /// @notice The registered chunks hold exactly this source's creation code, and neither kind is an engine kind.
    function check(V2TreasuryDeployer registry, uint8 dividendKind, uint8 splitKind) public view {
        if (dividendKind == 0 || splitKind == 0 || dividendKind == splitKind) revert ReadbackFailed("kind ids");
        _same(registry, dividendKind, type(HedgeFunV2DividendTreasury).creationCode, "dividend kind code");
        _same(registry, splitKind, type(HedgeFunV2BuybackDividendTreasury).creationCode, "split kind code");
    }

    function _same(V2TreasuryDeployer registry, uint8 kind, bytes memory code, string memory what) private view {
        (uint32 engineVersion, uint32 schema, bytes32 codeHash,) = registry.kindManifest(kind);
        (address a, address b) = registry.kinds(kind);
        if (engineVersion != 0 || schema != 0 || codeHash != keccak256(code)
            || keccak256(bytes.concat(a.code, b.code)) != keccak256(code)) revert ReadbackFailed(what);
    }
}

/// @notice Read-only confirmation after all four registration transactions have succeeded on chain.
/// @dev Requires V2_FACTORY, DIVIDEND_KIND and SPLIT_KIND from the reviewed registration.
contract VerifyV2IncomeKinds is Script {
    function run() external {
        HedgeFunV2Factory factory = HedgeFunV2Factory(vm.envAddress("V2_FACTORY"));
        V2TreasuryDeployer registry = V2TreasuryDeployer(address(factory.treasuryDeployer()));
        uint8 dividendKind = uint8(vm.envUint("DIVIDEND_KIND"));
        uint8 splitKind = uint8(vm.envUint("SPLIT_KIND"));
        new RegisterV2IncomeKinds().check(registry, dividendKind, splitKind);
        console2.log("live income kinds verified", dividendKind, splitKind);
    }
}

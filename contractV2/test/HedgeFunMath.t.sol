// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {Hooks} from "v4-core/src/libraries/Hooks.sol";
import {HedgeFunMath, BPS} from "../src/libraries/HedgeFunMath.sol";

/// The library replaced arithmetic that was written out at every site. Each function is held here to the literal
/// expression it replaced, so the refactor is provably the same rule: same result, same rounding, same edge.
contract StrategyMathTest is Test {
    function testFuzz_bps_isTheWrittenOutExpression(uint128 x, uint16 rate) public pure {
        assertEq(HedgeFunMath.bps(x, rate), uint256(x) * rate / 1e4);
    }

    function testFuzz_reached_isTheTakeProfitComparison(uint128 p, uint128 cost, uint16 rate) public pure {
        bool notDue = uint256(p) * 1e4 < uint256(cost) * (1e4 + uint256(rate));          // what `takeProfit` used to revert on
        assertEq(HedgeFunMath.reached(p, cost, rate), !notDue);
    }

    function testFuzz_fellTo_isTheStopAndDipComparison(uint128 p, uint128 ref, uint16 rate) public pure {
        rate = uint16(bound(rate, 0, 9999));                                              // stopBps and dipBps are < 1e4 at birth
        bool notDue = uint256(p) * 1e4 > uint256(ref) * (1e4 - uint256(rate));
        assertEq(HedgeFunMath.fellTo(p, ref, rate), !notDue);
    }

    function testFuzz_exceeds_isTheDeviationGate(uint128 gap, uint128 ref, uint16 rate) public pure {
        assertEq(HedgeFunMath.exceeds(gap, ref, rate), uint256(gap) * 1e4 > uint256(ref) * rate);
    }

    function testFuzz_shift_isTheBuybackLimit(uint160 sqrtP, uint16 rate, bool zeroForOne) public pure {
        rate = uint16(bound(rate, 0, 9000));                                              // `drift` is capped at 9000, `half` at 500
        uint256 was = zeroForOne ? uint256(sqrtP) * (1e4 - rate) / 1e4 : uint256(sqrtP) * (1e4 + rate) / 1e4;
        assertEq(HedgeFunMath.shift(sqrtP, rate, !zeroForOne), was);
    }

    function test_theEdgesAreInclusive_exactlyAsBefore() public pure {
        assertTrue(HedgeFunMath.reached(110, 100, 1000));   assertFalse(HedgeFunMath.reached(109, 100, 1000));
        assertTrue(HedgeFunMath.fellTo(90, 100, 1000));     assertFalse(HedgeFunMath.fellTo(91, 100, 1000));
        assertFalse(HedgeFunMath.exceeds(1, 100, 100));     assertTrue(HedgeFunMath.exceeds(2, 100, 100));
        assertEq(BPS, 10_000);
    }

    function test_theHooksFourNamedFlagsAreTheLiteralItUsedToCheck() public pure {
        assertEq(Hooks.ALL_HOOK_MASK, 0x3FFF);
        assertEq(Hooks.BEFORE_INITIALIZE_FLAG | Hooks.BEFORE_ADD_LIQUIDITY_FLAG | Hooks.AFTER_SWAP_FLAG | Hooks.AFTER_SWAP_RETURNS_DELTA_FLAG, 0x2844);
    }
}

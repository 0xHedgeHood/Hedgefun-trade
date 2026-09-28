// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {
    EngineConfig,
    IStrategyPolicy,
    StrategyAction,
    StrategyCapabilities,
    StrategyContext,
    StrategyIntent
} from "../../src/v2/strategy/IStrategyPolicy.sol";

/// @dev Adversarial policy fixtures. None of these contracts is production policy code.
abstract contract StrategyPolicyMockBase is IStrategyPolicy {
    function policyMetadata()
        external
        pure
        virtual
        returns (uint32 engineVersion, uint32 configSchema, uint256 capabilities)
    {
        return (
            StrategyCapabilities.SPOT_ENGINE_V1,
            StrategyCapabilities.CONFIG_SCHEMA_V1,
            StrategyCapabilities.SPOT_BUY | StrategyCapabilities.SPOT_SELL
        );
    }

    function _intent(StrategyContext calldata context, StrategyAction action, uint256 amountIn)
        internal
        pure
        returns (StrategyIntent memory intent)
    {
        intent = StrategyIntent({
            configHash: context.configHash,
            nonce: context.nonce,
            action: action,
            amountIn: amountIn,
            nextState: bytes32(0)
        });
    }
}

contract HonestHoldPolicy is StrategyPolicyMockBase {
    function decide(StrategyContext calldata context, EngineConfig calldata, bytes32)
        external
        pure
        override
        returns (StrategyIntent memory)
    {
        return _intent(context, StrategyAction.Hold, 0);
    }
}

contract HonestBuyPolicy is StrategyPolicyMockBase {
    function decide(StrategyContext calldata context, EngineConfig calldata, bytes32)
        external
        pure
        override
        returns (StrategyIntent memory)
    {
        return _intent(context, StrategyAction.BuyStock, 1);
    }
}

contract RevertingStrategyPolicy is StrategyPolicyMockBase {
    error PolicyReverted();

    function decide(StrategyContext calldata, EngineConfig calldata, bytes32)
        external
        pure
        override
        returns (StrategyIntent memory)
    {
        revert PolicyReverted();
    }
}

/// @dev Returning through assembly bypasses Solidity's ordinary fixed-size ABI encoder.
contract HugeReturnStrategyPolicy is StrategyPolicyMockBase {
    uint256 internal constant RETURN_BYTES = 65_536;

    function decide(StrategyContext calldata, EngineConfig calldata, bytes32)
        external
        pure
        override
        returns (StrategyIntent memory)
    {
        assembly ("memory-safe") {
            mstore(0, 0)
            return(0, RETURN_BYTES)
        }
    }
}

contract MalformedReturnStrategyPolicy is StrategyPolicyMockBase {
    function decide(StrategyContext calldata, EngineConfig calldata, bytes32)
        external
        pure
        override
        returns (StrategyIntent memory)
    {
        assembly ("memory-safe") {
            mstore(0, 0)
            return(0, 159)
        }
    }
}

contract GasBombStrategyPolicy is StrategyPolicyMockBase {
    function decide(StrategyContext calldata, EngineConfig calldata, bytes32)
        external
        pure
        override
        returns (StrategyIntent memory intent)
    {
        intent;
        assembly ("memory-safe") {
            for {} 1 {} {}
        }
    }
}

contract WrongConfigHashStrategyPolicy is StrategyPolicyMockBase {
    function decide(StrategyContext calldata context, EngineConfig calldata, bytes32)
        external
        pure
        override
        returns (StrategyIntent memory intent)
    {
        intent = _intent(context, StrategyAction.BuyStock, 1);
        intent.configHash = bytes32(uint256(context.configHash) ^ 1);
    }
}

contract WrongNonceStrategyPolicy is StrategyPolicyMockBase {
    function decide(StrategyContext calldata context, EngineConfig calldata, bytes32)
        external
        pure
        override
        returns (StrategyIntent memory intent)
    {
        intent = _intent(context, StrategyAction.BuyStock, 1);
        unchecked {
            intent.nonce = context.nonce + 1;
        }
    }
}

contract ExcessiveAmountStrategyPolicy is StrategyPolicyMockBase {
    function decide(StrategyContext calldata context, EngineConfig calldata, bytes32)
        external
        pure
        override
        returns (StrategyIntent memory)
    {
        return _intent(context, StrategyAction.BuyStock, type(uint256).max);
    }
}

/// @dev A spot engine must reject this raw action word before attempting an enum ABI decode.
contract OptionsActionStrategyPolicy is StrategyPolicyMockBase {
    uint256 internal constant OPTIONS_ACTION = 64;

    function policyMetadata()
        external
        pure
        override
        returns (uint32 engineVersion, uint32 configSchema, uint256 capabilities)
    {
        return (
            StrategyCapabilities.SPOT_ENGINE_V1,
            StrategyCapabilities.CONFIG_SCHEMA_V1,
            StrategyCapabilities.OPTIONS_WRITE
        );
    }

    function decide(StrategyContext calldata context, EngineConfig calldata, bytes32)
        external
        pure
        override
        returns (StrategyIntent memory intent)
    {
        intent = _intent(context, StrategyAction.Hold, 1);
        assembly ("memory-safe") {
            mstore(add(intent, 0x40), OPTIONS_ACTION)
            return(intent, 0xa0)
        }
    }
}

/// @dev Same selector as IStrategyPolicy.decide, but deliberately non-view. A STATICCALL must trap on SSTORE.
contract StateWritingStrategyPolicy {
    uint256 public writes;

    function policyMetadata() external pure returns (uint32 engineVersion, uint32 configSchema, uint256 capabilities) {
        return
            (StrategyCapabilities.SPOT_ENGINE_V1, StrategyCapabilities.CONFIG_SCHEMA_V1, StrategyCapabilities.SPOT_BUY);
    }

    function decide(StrategyContext calldata context, EngineConfig calldata, bytes32)
        external
        returns (StrategyIntent memory intent)
    {
        ++writes;
        intent = StrategyIntent({
            configHash: context.configHash,
            nonce: context.nonce,
            action: StrategyAction.BuyStock,
            amountIn: 1,
            nextState: bytes32(writes)
        });
    }
}

/// @dev Runtime codehash alone cannot make policy semantics immutable when storage remains mutable.
contract MutableDecisionStrategyPolicy is StrategyPolicyMockBase {
    StrategyAction public action;

    function setAction(StrategyAction next) external {
        action = next;
    }

    function decide(StrategyContext calldata context, EngineConfig calldata, bytes32)
        external
        view
        override
        returns (StrategyIntent memory)
    {
        return StrategyIntent({
            configHash: context.configHash,
            nonce: context.nonce,
            action: action,
            amountIn: action == StrategyAction.Hold ? 0 : 1,
            nextState: bytes32(0)
        });
    }
}

/// @dev A proxy's own codehash stays fixed while its delegated policy semantics change.
contract MutableStrategyPolicyProxy {
    address public implementation;

    constructor(address implementation_) {
        implementation = implementation_;
    }

    function setImplementation(address implementation_) external {
        implementation = implementation_;
    }

    fallback() external {
        address target = implementation;
        assembly ("memory-safe") {
            calldatacopy(0, 0, calldatasize())
            let ok := delegatecall(gas(), target, 0, calldatasize(), 0, 0)
            returndatacopy(0, 0, returndatasize())
            if iszero(ok) { revert(0, returndatasize()) }
            return(0, returndatasize())
        }
    }
}

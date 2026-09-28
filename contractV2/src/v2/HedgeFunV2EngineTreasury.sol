// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Math} from "@openzeppelin/contracts/utils/math/Math.sol";
import {HedgeFunV2Treasury} from "./HedgeFunV2Treasury.sol";
import {
    EngineConfig,
    IStrategyPolicy,
    IV2StrategyRegistry,
    PolicyManifest,
    StrategyAction,
    StrategyCapabilities,
    StrategyContext,
    StrategyIntent
} from "./strategy/IStrategyPolicy.sol";

/// @notice Strategy kind 2: an immutable spot-policy engine.
///
/// The policy is advisory only. It is called with `STATICCALL`, has no custody and can propose one fixed-width
/// action. This treasury independently rechecks the policy code hash, nonce, config commitment, live oracle,
/// cooldown, allocation direction, per-action size and daily turnover before it calls the inherited bounded V3
/// swap. Routes, pools, recipients, approvals and callbacks never come from the policy or keeper.
///
/// Engine version 1 deliberately supports only a stock/USDG fixed-weight rebalance policy. Options capabilities
/// are reserved in the shared interface but rejected here: collateral, expiry, exercise and settlement require a
/// different engine version and different solvency invariants.
contract HedgeFunV2EngineTreasury is HedgeFunV2Treasury {
    uint256 private constant BPS = 10_000;
    uint256 private constant INTENT_RETURN_BYTES = 160;
    uint256 public constant MAX_POLICY_GAS = 500_000;
    uint256 private constant SPOT_CAPABILITIES = StrategyCapabilities.SPOT_BUY | StrategyCapabilities.SPOT_SELL;

    EngineConfig private _engineConfig;
    address public immutable policyImplementation;
    bytes32 public immutable policyRuntimeCodeHash;
    uint256 public immutable policyCapabilities;
    uint32 public immutable policyGasLimit;
    uint16 public immutable policyReturnLimit;
    bytes32 public immutable configHash;

    bytes32 public policyState;
    uint64 public strategyNonce;
    uint256 public lastStrategyAt;
    uint64 public turnoverEpoch;
    uint256 public turnoverInEpoch;

    struct ExecutionLimits {
        uint256 targetBps;
        uint256 deadbandBps;
        uint256 maxTrade;
        uint256 remainingDaily;
        uint256 totalValue;
        uint64 epoch;
        uint256 used;
    }

    struct ExecutionResult {
        Action action;
        uint256 actualInput;
        uint256 actualOutput;
        uint256 turnover;
    }

    error BadEngineConfig();
    error PolicyUnavailable();
    error PolicyFailure();
    error BadPolicyReturn();
    error BadIntent();

    event InventoryBooked(uint256 amount, uint256 stockInventory);
    event StrategyExecuted(
        uint64 indexed nonce,
        StrategyAction indexed action,
        uint256 requestedInput,
        uint256 actualInput,
        uint256 actualOutput,
        uint256 price,
        uint256 turnoverUsdg,
        bytes32 nextState
    );

    constructor(
        address usdg_,
        address stock_,
        address v3Pool_,
        address oracle_,
        address token_,
        address poolManager_,
        address factory_,
        Params memory p,
        EngineConfig memory c
    ) HedgeFunV2Treasury(usdg_, stock_, v3Pool_, oracle_, token_, poolManager_, factory_, p) {
        PolicyManifest memory manifest = IV2StrategyRegistry(msg.sender).policy(c.policyKey);
        _validateEngineConfig(c, p, manifest);

        _engineConfig = c;
        policyImplementation = manifest.implementation;
        policyRuntimeCodeHash = manifest.runtimeCodeHash;
        policyCapabilities = manifest.capabilities;
        policyGasLimit = manifest.maxGas;
        policyReturnLimit = manifest.maxReturnBytes;
        configHash = keccak256(
            abi.encode(
                block.chainid,
                address(this),
                factory_,
                stock_,
                usdg_,
                c,
                manifest.implementation,
                manifest.runtimeCodeHash,
                manifest.capabilities,
                manifest.maxGas,
                manifest.maxReturnBytes
            )
        );
    }

    function _validateEngineConfig(EngineConfig memory c, Params memory p, PolicyManifest memory manifest)
        private
        view
    {
        uint256 packed = uint256(c.words[0]);
        uint256 targetBps = uint16(packed);
        uint256 deadbandBps = uint16(packed >> 16);
        uint256 cooldown = uint32(packed >> 32);
        uint256 maxTradeUsdg = uint256(c.words[1]);
        uint256 maxDailyTurnoverUsdg = uint256(c.words[2]);
        bytes32 actualCodeHash = manifest.implementation.codehash;
        if (
            c.schema != StrategyCapabilities.CONFIG_SCHEMA_V1 || c.engineVersion != StrategyCapabilities.SPOT_ENGINE_V1
                || manifest.engineVersion != c.engineVersion || manifest.configSchema != c.schema
                || !manifest.enabledForNewLaunches || manifest.implementation == address(0)
                || actualCodeHash == bytes32(0) || actualCodeHash != manifest.runtimeCodeHash || manifest.maxGas == 0
                || manifest.maxGas > MAX_POLICY_GAS || manifest.maxReturnBytes != INTENT_RETURN_BYTES
                || manifest.capabilities & SPOT_CAPABILITIES == 0 || manifest.capabilities & ~SPOT_CAPABILITIES != 0
                || packed >> 64 != 0 || targetBps == 0 || targetBps >= BPS || deadbandBps == 0
                || deadbandBps >= targetBps || targetBps + deadbandBps >= BPS || cooldown == 0 || maxTradeUsdg == 0
                || maxTradeUsdg > p.sellChunkUsdg || maxDailyTurnoverUsdg < maxTradeUsdg
        ) revert BadEngineConfig();
    }

    function engineVersion() external pure returns (uint32) {
        return StrategyCapabilities.SPOT_ENGINE_V1;
    }

    function strategyId() external view returns (bytes32) {
        return _engineConfig.policyKey;
    }

    function engineConfig() external view returns (EngineConfig memory) {
        return _engineConfig;
    }

    /// @dev Engine inventory is a balance bucket, not a collection of cost-basis lots.
    function _canAddLot() internal pure override returns (bool) {
        return false;
    }

    /// @notice Classify newly arrived graduation/tax stock as rebalance inventory. LP stock fees still enter the
    ///         separate inherited `buybackStock` bucket through `creditLiquidityFee`.
    function book() public override nonReentrant returns (bool) {
        return _bookInventory();
    }

    function _bookInventory() internal returns (bool) {
        if (hook == address(0)) return false;
        uint256 pending = unbookedStock();
        if (pending == 0) return false;
        bookedStock += pending;
        totalStockReceived += pending;
        emit InventoryBooked(pending, bookedStock);
        return true;
    }

    /// @notice Simulate the immutable policy. `execute()` always recomputes the context and intent on chain.
    function preview() external view returns (bool due, StrategyAction action, uint256 amountIn) {
        (bool ok, uint256 p) = health();
        (bool live,) = _oracle.tryPrice();
        if (!ok || !live) return (false, StrategyAction.Hold, 0);
        StrategyContext memory context = _context(p, bookedStock + unbookedStock());
        StrategyIntent memory intent = _policyIntent(context);
        if (!_basicIntentValid(intent) || intent.action == StrategyAction.Hold) {
            return (false, StrategyAction.Hold, 0);
        }
        amountIn = _previewExecutableAmount(context, intent, p);
        if (amountIn == 0) return (false, StrategyAction.Hold, 0);
        return (true, intent.action, amountIn);
    }

    function _previewExecutableAmount(StrategyContext memory context, StrategyIntent memory intent, uint256 price)
        private
        view
        returns (uint256 offered)
    {
        ExecutionLimits memory limits;
        uint256 cooldown;
        uint256 maxDaily;
        (limits.targetBps, limits.deadbandBps, cooldown, limits.maxTrade, maxDaily) = _riskConfig();
        if (lastStrategyAt != 0 && block.timestamp < lastStrategyAt + cooldown) return 0;
        uint64 epoch = uint64(block.timestamp / 1 days);
        uint256 used = turnoverEpoch == epoch ? turnoverInEpoch : 0;
        if (used >= maxDaily) return 0;
        limits.remainingDaily = maxDaily - used;
        if (context.stockValueUsdg > type(uint256).max - context.usdgInventory) return 0;
        limits.totalValue = context.stockValueUsdg + context.usdgInventory;
        if (limits.totalValue == 0) return 0;

        if (intent.action == StrategyAction.SellStock) {
            return _previewSell(context, intent.amountIn, price, limits);
        }
        if (intent.action == StrategyAction.BuyStock) {
            return _previewBuy(context, intent.amountIn, limits);
        }
        return 0;
    }

    function _previewSell(
        StrategyContext memory context,
        uint256 requested,
        uint256 price,
        ExecutionLimits memory limits
    ) private view returns (uint256 offered) {
        if (policyCapabilities & StrategyCapabilities.SPOT_SELL == 0) return 0;
        uint256 upperValue = Math.mulDiv(limits.totalValue, limits.targetBps + limits.deadbandBps, BPS);
        if (context.stockValueUsdg <= upperValue) return 0;
        uint256 targetValue = Math.mulDiv(limits.totalValue, limits.targetBps, BPS);
        uint256 capUsdg =
            Math.min(Math.min(limits.maxTrade, limits.remainingDaily), context.stockValueUsdg - targetValue);
        offered = Math.min(requested, Math.min(context.stockInventory, _ruleStockFor(capUsdg, price)));
        if (offered == 0 || _ruleValue(offered, price) < _params.minLotUsdg) return 0;
    }

    function _previewBuy(StrategyContext memory context, uint256 requested, ExecutionLimits memory limits)
        private
        view
        returns (uint256 offered)
    {
        if (policyCapabilities & StrategyCapabilities.SPOT_BUY == 0) return 0;
        uint256 lowerValue = Math.mulDiv(limits.totalValue, limits.targetBps - limits.deadbandBps, BPS);
        if (context.stockValueUsdg >= lowerValue) return 0;
        uint256 targetValue = Math.mulDiv(limits.totalValue, limits.targetBps, BPS);
        uint256 capUsdg =
            Math.min(Math.min(limits.maxTrade, limits.remainingDaily), targetValue - context.stockValueUsdg);
        offered = Math.min(Math.min(requested, capUsdg), context.usdgInventory);
        if (offered < _params.minLotUsdg) return 0;
    }

    /// @notice Execute one bounded policy action. Keepers choose no action, route, lot, price or recipient.
    function execute() external override nonReentrant returns (Action action, uint256 id) {
        _bookInventory();
        (bool ok, uint256 p) = health();
        if (!ok) revert Unhealthy();
        (bool live,) = _oracle.tryPrice();
        if (!live) revert Unhealthy();

        StrategyContext memory context = _context(p, bookedStock);
        StrategyIntent memory intent = _policyIntent(context);
        if (!_basicIntentValid(intent) || intent.action == StrategyAction.Hold) revert NotDue();

        uint256 requested = intent.amountIn;
        ExecutionLimits memory limits = _executionLimits(context);
        _notePrice(p);
        _noteTokenSpot();
        ExecutionResult memory result = _executeIntent(context, intent, p, limits);
        action = result.action;
        // A V3 exact-input swap can stop at the price limit after consuming only dust. Treating that as a strategy
        // action would let a thin or deliberately positioned venue advance the nonce and renew the cooldown while
        // barely consuming the daily budget. The check must use the actual fill and must happen before any strategy
        // state is committed; reverting here rolls the swap and its transfers back atomically.
        if (result.turnover < _params.minLotUsdg) revert NotDue();
        if (result.turnover > limits.remainingDaily) revert BadIntent();
        turnoverEpoch = limits.epoch;
        turnoverInEpoch = limits.used + result.turnover;
        lastStrategyAt = block.timestamp;
        policyState = intent.nextState;
        ++strategyNonce;
        id = strategyNonce;
        emit StrategyExecuted(
            strategyNonce,
            intent.action,
            requested,
            result.actualInput,
            result.actualOutput,
            p,
            result.turnover,
            intent.nextState
        );
    }

    function _executionLimits(StrategyContext memory context) private view returns (ExecutionLimits memory limits) {
        uint256 cooldown;
        uint256 maxDaily;
        (limits.targetBps, limits.deadbandBps, cooldown, limits.maxTrade, maxDaily) = _riskConfig();
        if (lastStrategyAt != 0 && block.timestamp < lastStrategyAt + cooldown) revert Cooldown();
        limits.epoch = uint64(block.timestamp / 1 days);
        limits.used = turnoverEpoch == limits.epoch ? turnoverInEpoch : 0;
        if (limits.used >= maxDaily) revert NotDue();
        limits.remainingDaily = maxDaily - limits.used;
        if (context.stockValueUsdg > type(uint256).max - context.usdgInventory) revert BadIntent();
        limits.totalValue = context.stockValueUsdg + context.usdgInventory;
        if (limits.totalValue == 0) revert NotDue();
    }

    function _executeIntent(
        StrategyContext memory context,
        StrategyIntent memory intent,
        uint256 price,
        ExecutionLimits memory limits
    ) private returns (ExecutionResult memory result) {
        if (intent.action == StrategyAction.SellStock) {
            return _executeSell(context, intent.amountIn, price, limits);
        }
        if (intent.action == StrategyAction.BuyStock) {
            return _executeBuy(context, intent.amountIn, price, limits);
        }
        // Buy-back has its own TWAP/anchor/cooldown state machine and remains a separate entry point.
        revert BadIntent();
    }

    function _executeSell(
        StrategyContext memory context,
        uint256 requested,
        uint256 price,
        ExecutionLimits memory limits
    ) private returns (ExecutionResult memory result) {
        uint256 upperValue = Math.mulDiv(limits.totalValue, limits.targetBps + limits.deadbandBps, BPS);
        if (policyCapabilities & StrategyCapabilities.SPOT_SELL == 0 || context.stockValueUsdg <= upperValue) {
            revert BadIntent();
        }
        uint256 excessUsdg = context.stockValueUsdg - Math.mulDiv(limits.totalValue, limits.targetBps, BPS);
        uint256 capUsdg = Math.min(Math.min(limits.maxTrade, limits.remainingDaily), excessUsdg);
        uint256 offered = Math.min(requested, Math.min(bookedStock, _ruleStockFor(capUsdg, price)));
        if (offered == 0 || _ruleValue(offered, price) < _params.minLotUsdg) revert NotDue();
        (result.actualInput, result.actualOutput) = _swapStock(false, offered, price);
        bookedStock -= result.actualInput;
        result.turnover = _ruleValue(result.actualInput, price);
        result.action = Action.RebalanceSell;
    }

    function _executeBuy(
        StrategyContext memory context,
        uint256 requested,
        uint256 price,
        ExecutionLimits memory limits
    ) private returns (ExecutionResult memory result) {
        uint256 lowerValue = Math.mulDiv(limits.totalValue, limits.targetBps - limits.deadbandBps, BPS);
        if (policyCapabilities & StrategyCapabilities.SPOT_BUY == 0 || context.stockValueUsdg >= lowerValue) {
            revert BadIntent();
        }
        uint256 deficitUsdg = Math.mulDiv(limits.totalValue, limits.targetBps, BPS) - context.stockValueUsdg;
        uint256 capUsdg = Math.min(Math.min(limits.maxTrade, limits.remainingDaily), deficitUsdg);
        uint256 offered = Math.min(Math.min(requested, capUsdg), context.usdgInventory);
        if (offered < _params.minLotUsdg) revert NotDue();
        (result.actualInput, result.actualOutput) = _swapStock(true, offered, price);
        bookedStock += result.actualOutput;
        result.turnover = result.actualInput;
        result.action = Action.RebalanceBuy;
    }

    function _context(uint256 p, uint256 inventory) private view returns (StrategyContext memory context) {
        context = StrategyContext({
            configHash: configHash,
            price: p,
            stockInventory: inventory,
            stockValueUsdg: _ruleValue(inventory, p),
            usdgInventory: reserveUsdg(),
            buybackStock: buybackStock,
            lastActionAt: lastStrategyAt,
            nonce: strategyNonce
        });
    }

    function _basicIntentValid(StrategyIntent memory intent) private view returns (bool) {
        return intent.configHash == configHash && intent.nonce == strategyNonce
            && uint8(intent.action) <= uint8(StrategyAction.BuybackBurn);
    }

    function _riskConfig()
        private
        view
        returns (uint256 targetBps, uint256 deadbandBps, uint256 cooldown, uint256 maxTrade, uint256 maxDaily)
    {
        uint256 packed = uint256(_engineConfig.words[0]);
        targetBps = uint16(packed);
        deadbandBps = uint16(packed >> 16);
        cooldown = uint32(packed >> 32);
        maxTrade = uint256(_engineConfig.words[1]);
        maxDaily = uint256(_engineConfig.words[2]);
    }

    function _policyIntent(StrategyContext memory context) private view returns (StrategyIntent memory intent) {
        address implementation = policyImplementation;
        if (implementation.codehash != policyRuntimeCodeHash) revert PolicyUnavailable();
        bytes memory callData = abi.encodeCall(IStrategyPolicy.decide, (context, _engineConfig, policyState));
        bool success;
        uint256 size;
        uint256 gasLimit = policyGasLimit;
        assembly ("memory-safe") {
            success := staticcall(gasLimit, implementation, add(callData, 0x20), mload(callData), 0, 0)
            size := returndatasize()
        }
        if (!success) revert PolicyFailure();
        if (size != INTENT_RETURN_BYTES || size > policyReturnLimit) revert BadPolicyReturn();
        bytes memory result = new bytes(size);
        assembly ("memory-safe") { returndatacopy(add(result, 0x20), 0, size) }
        intent = abi.decode(result, (StrategyIntent));
    }
}

// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {HedgeFunTreasury} from "../HedgeFunTreasury.sol";
import {IPoolManager} from "v4-core/src/interfaces/IPoolManager.sol";
import {PoolKey} from "v4-core/src/types/PoolKey.sol";
import {PoolIdLibrary} from "v4-core/src/types/PoolId.sol";
import {StateLibrary} from "v4-core/src/libraries/StateLibrary.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {HedgeFunMath} from "../libraries/HedgeFunMath.sol";

/// @notice V2 parks fees and donations until graduation wires its permanent pool. Its stock
/// strategy runs through execute() with fixed stop/profit/buy priority. The deterministic
/// graduation price anchors the first token buyback.
contract HedgeFunV2Treasury is HedgeFunTreasury {
    using SafeERC20 for IERC20;
    using StateLibrary for IPoolManager;
    using PoolIdLibrary for PoolKey;
    address public liquidityVault;
    uint256 public constant MAX_STRATEGY_LOTS = 128;
    uint256 public constant STOP_REENTRY_COOLDOWN = 600;
    uint256 public lastStopPrice;
    uint256 public lastStopAt;
    uint256 public lastStopStockUpdatedAt;

    enum Action { Stop, TakeProfit, BuyDip }
    error UseExecute();
    event LotsCoalesced(uint256 indexed kept, uint256 indexed removed, uint256 qty, uint256 cost);

    constructor(address usdg_, address stock_, address v3Pool_, address oracle_, address token_,
        address poolManager_, address factory_, Params memory p)
        HedgeFunTreasury(usdg_, stock_, v3Pool_, oracle_, token_, poolManager_, factory_, p) {
        // A newly purchased lot uses its actual V3 fill as cost. A stop inside the maximum
        // buy execution friction can therefore be due without any new adverse market move.
        if (p.stopBps != 0 && p.stopBps <= p.maxSlippageBps + poolFeeBps + p.bountyBps) revert BadConfig();
    }

    /// @dev V1 keeps the individual entry points. V2 cannot let callers select their order or lot.
    function takeProfit(uint256) public pure override { revert UseExecute(); }
    function stopLoss(uint256) public pure override { revert UseExecute(); }
    function buyDip() public pure override { revert UseExecute(); }

    function _canAddLot() internal view virtual override returns (bool) { return lots.length < MAX_STRATEGY_LOTS; }

    function book() public virtual override nonReentrant returns (bool) { return _bookV2(); }

    function _bookV2() internal returns (bool booked) {
        booked = _book();
        if (booked || lots.length != MAX_STRATEGY_LOTS) return booked;
        uint256 un = unbookedStock();
        (bool healthy, uint256 p) = health();
        (bool live,) = _oracle.tryPrice();
        if (!healthy || !live || un == 0 || _ruleValue(un, p) < _params.minLotUsdg) return false;
        if (!_coalesceLots()) return false;
        return _book();
    }

    /// @dev Reclaim a slot only when it preserves every lot's price triggers. Distinct
    ///      bases remain separate; at full capacity the treasury can still sell but pauses buys.
    function _coalesceLots() internal returns (bool) {
        for (uint256 i; i < lots.length; ++i) {
            Lot storage A = lots[i];
            for (uint256 j = i + 1; j < lots.length; ++j) {
                Lot storage B = lots[j];
                if (A.cost != B.cost || A.half != B.half
                    || (A.tp1Left == 0) != (B.tp1Left == 0)) continue;
                A.qty += B.qty;
                A.tp1Left += B.tp1Left;
                emit LotsCoalesced(i, j, A.qty, A.cost);
                uint256 last = lots.length - 1;
                if (j != last) lots[j] = lots[last];
                lots.pop();
                return true;
            }
        }
        return false;
    }

    /// @notice Execute one bounded strategy action. A later call rechecks the oracle and every lot.
    ///         No new buy can jump over a due stop, including the remainder of a short fill.
    function execute() external virtual nonReentrant returns (Action action, uint256 id) {
        (bool ok, uint256 p) = health();
        if (!ok) revert Unhealthy();
        _book(); // at capacity, keep pending donations unbooked while urgent sales run
        (bool live,) = _oracle.tryPrice();
        if (live && _params.stopBps != 0) {
            (bool found, uint256 stopId) = _dueStop(p);
            if (found) {
                (bool valid,, uint256 updatedAt) = _oracle.lastPriceAt();
                if (!valid) revert Unhealthy();
                _stopLoss(stopId);
                (lastStopPrice, lastStopAt, lastStopStockUpdatedAt) = (p, block.timestamp, updatedAt);
                return (Action.Stop, stopId);
            }
        }
        (bool foundTp, uint256 dueId) = _dueProfit(p);
        if (foundTp) {
            _takeProfit(dueId);
            // A profit above a lot's cost means the market moved past the stop, so the dip rung is the
            // sale that just happened, not the stop. Without this the treasury could never buy back in.
            _clearStopGate();
            return (Action.TakeProfit, dueId);
        }

        // During a scheduled closure the pool can supply a bounded price for TP, but not
        // prove that no stop is due. A stop-enabled treasury therefore cannot add risk.
        if (!live && _params.stopBps != 0) revert NotDue();
        if (lastStopAt != 0) {
            if (!live || block.timestamp - lastStopAt < STOP_REENTRY_COOLDOWN
                || !HedgeFunMath.fellTo(p, lastStopPrice, _params.dipBps)) revert NotDue();
            (bool valid,, uint256 updatedAt) = _oracle.lastPriceAt();
            if (!valid || updatedAt <= lastStopStockUpdatedAt) revert NotDue();
        }
        // Only after ruling out all sales do we compact exact-matching lots for a buy.
        // A fresh booking costs p and cannot itself be stop- or profit-due at p.
        _bookV2();
        if (lots.length == MAX_STRATEGY_LOTS && !_coalesceLots()) revert NotDue();
        _buyDip();
        _clearStopGate();
        return (Action.BuyDip, lots.length - 1);
    }

    /// @dev The post-stop gate guards the first re-entry after a stop only. Once the treasury has sold at a
    ///      profit or bought again, `lastSalePrice` is a newer reference than the stop and the gate is done.
    function _clearStopGate() private {
        if (lastStopAt != 0) (lastStopPrice, lastStopAt, lastStopStockUpdatedAt) = (0, 0, 0);
    }

    function _dueStop(uint256 p) internal view returns (bool found, uint256 id) {
        uint256 highestCost;
        uint256 largestQty;
        for (uint256 i; i < lots.length; ++i) {
            Lot storage L = lots[i];
            if (HedgeFunMath.fellTo(p, L.cost, _params.stopBps)
                && (!found || L.cost > highestCost || (L.cost == highestCost && L.qty > largestQty))) {
                (found, id, highestCost, largestQty) = (true, i, L.cost, L.qty);
            }
        }
    }

    function _dueProfit(uint256 p) internal view returns (bool found, uint256 id) {
        // At one price, close an already-half-sold lot before starting another TP1.
        bool tp2;
        uint256 selectedCost;
        uint256 selectedQty;
        for (uint256 i; i < lots.length; ++i) {
            Lot storage L = lots[i];
            bool second = _params.tp2Bps == 0 || L.half;
            uint256 trigger = second && _params.tp2Bps != 0 ? _params.tp2Bps : _params.tp1Bps;
            if (HedgeFunMath.reached(p, L.cost, trigger)
                && (!found || (second && !tp2) || (second == tp2 &&
                    (L.cost < selectedCost || (L.cost == selectedCost && L.qty > selectedQty))))) {
                (found, tp2, id, selectedCost, selectedQty) = (true, second, i, L.cost, L.qty);
            }
        }
    }

    /// @notice The factory freezes the position owner before the first V4 pool is seeded.
    function setLiquidityVault(address vault) external {
        if (msg.sender != factory) revert NotFactory();
        if (liquidityVault != address(0)) revert AlreadyWired();
        liquidityVault = vault;
    }

    /// @notice Realized stock-side LP fees enter the buyback budget, never a strategy cost-basis lot.
    /// @dev Pulling under the reentrancy guard prevents token callbacks from booking the fee as principal.
    function creditLiquidityFee(uint256 amount) external nonReentrant {
        if (msg.sender != liquidityVault || amount == 0) revert NotFactory();
        _stock.safeTransferFrom(msg.sender, address(this), amount);
        buybackStock += amount;
    }

    /// @dev Every booking and stock-trading entry point in the base consults this virtual gate.
    function health() public view override returns (bool ok, uint256 p) {
        if (hook == address(0)) return (false, 0);
        return super.health();
    }

    /// @dev Unlike V1's one-sided opening, graduation already has a terminal curve price and
    /// balanced liquidity. Capture that price before the pool can trade. Later TWAP/anchor
    /// fallback stays usable even if high-frequency swaps exhaust the hook's observation ring.
    function wire(PoolKey calldata key) public override {
        super.wire(key);
        (uint160 price,,,) = poolManager.getSlot0(key.toId());
        if (price == 0) revert BadConfig();
        buybackAnchorSqrtP = price;
        buybackAnchorAt = block.timestamp;
    }
}

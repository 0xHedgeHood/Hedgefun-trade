// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {IERC20Metadata} from "@openzeppelin/contracts/token/ERC20/extensions/IERC20Metadata.sol";
import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";
import {Ownable2Step} from "@openzeppelin/contracts/access/Ownable2Step.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import {Math} from "@openzeppelin/contracts/utils/math/Math.sol";
import {CoveredCallDesk} from "./CoveredCallDesk.sol";
import {CashSecuredPutDesk} from "./CashSecuredPutDesk.sol";
import {PriceOracle} from "../PriceOracle.sol";

interface IEarnVaultSwapAdapter {
    function vault() external view returns (address);
    function usdg() external view returns (address);
    function stock() external view returns (address);
    function oracle() external view returns (address);
    function pool() external view returns (address);
    function maxDeviationBps() external view returns (uint16);
    function maxSlippageBps() external view returns (uint16);
    function buy(uint256 amountIn, uint256 minStockOut) external returns (uint256 spent, uint256 received);
}

/// @title EarnVault
/// @notice RHNVDA covered-call / cash-secured-put vault. hNVDA represents a pro-rata claim on its RHNVDA and USDG.
///         Deposits and redemptions are queued and priced together only after the current option terminates.
///         A physical exercise can leave USDG in the vault; redemptions then receive the actual asset basket.
/// @dev This is an asynchronous two-asset vault, not an ERC-4626 single-asset vault. The owner Safe
///      approves each RFQ; on-chain strike, premium, tenor, utilization, buyer and physical-settlement gates
///      limit that discretion. Deploy with PhysicalCallDesk, which shares CoveredCallDesk's external ABI but
///      requires full-strike physical exercise even after its trusted 14-day price backstop.
contract EarnVault is ERC20, Ownable2Step, ReentrancyGuard {
    using SafeERC20 for IERC20;

    uint256 public constant VALUE_SCALE = 1e30; // stock 18d * price 18d -> USDG 6d
    uint256 public constant MIN_DEPOSIT = 1e15; // 0.001 RHNVDA
    uint256 public constant MIN_REDEEM = 1e15; // 0.001 hNVDA, except a holder's entire balance
    uint256 public constant MAX_TENOR = 9 days;
    uint256 public constant MIN_TENOR = 12 hours;
    // The desk does not reprice at fill. Keep the buyer's free option on an old RFQ very short.
    uint256 public constant MAX_FILL_WINDOW = 5 minutes;
    uint256 public constant MAX_DEPOSITORS_PER_EPOCH = 64;
    uint256 public constant MIN_DEAD_SHARES = 1e12; // first seed permanently locks 0.000001 RHNVDA worth of shares
    address public constant DEAD_SHARES_HOLDER = 0x000000000000000000000000000000000000dEaD;

    IERC20 public immutable stock;
    IERC20 public immutable usdg;
    CoveredCallDesk public immutable desk;
    CashSecuredPutDesk public putDesk;
    PriceOracle public immutable oracle;
    address public immutable reinvestPool;
    IEarnVaultSwapAdapter public swapAdapter;

    address public operator;
    bool public paused;
    mapping(address => bool) public eligible;
    mapping(address => bool) public allowedBuyer;

    /// @notice The owner can tighten these settings but cannot lower strike below 98% of spot or premium
    ///         below 25 bps of spot notional. An RFQ is still a discretionary investment decision.
    uint16 public minStrikeBps = 10_000;
    uint16 public minPremiumBps = 25;
    uint16 public maxUtilizationBps = 10_000;
    uint16 public maxPutStrikeBps = 10_000;
    uint16 public minPutPremiumBps = 25;
    uint16 public maxPutUtilizationBps = 10_000;

    enum OptionKind {
        None,
        Call,
        Put
    }

    uint256 public currentEpoch = 1;
    uint256 public activeOptionId;
    OptionKind public activeOptionKind;
    uint256 public pendingDepositStock;
    uint256 public pendingRedeemShares;
    uint256 public claimableStock;
    uint256 public claimableUSDG;
    mapping(address => uint256) public owedRedeemStock;
    bool internal _escrowingRedeem;

    struct Epoch {
        bool closed;
        uint256 optionId;
        uint256 price; // USDG per whole stock, 1e18
        uint256 navBeforeFlows; // USDG base units
        uint256 supplyBeforeFlows; // hNVDA wei
        uint256 depositStockRemaining;
        uint256 depositSharesRemaining;
        uint256 redeemSharesRemaining;
        uint256 redeemStockRemaining;
        uint256 redeemUSDGRemaining;
    }

    mapping(uint256 => Epoch) public epochs;
    mapping(uint256 => OptionKind) public epochOptionKind;
    mapping(uint256 => mapping(address => uint256)) public depositRequests;
    mapping(uint256 => mapping(address => uint256)) public redeemRequests;
    mapping(uint256 => mapping(address => uint256)) public depositClaimShares;
    mapping(uint256 => mapping(address => uint256)) public refundableDepositStock;
    mapping(uint256 => address[]) internal _depositors;
    mapping(uint256 => mapping(address => uint256)) internal _depositorIndexPlusOne;

    event EligibilitySet(address indexed account, bool allowed);
    event BuyerSet(address indexed buyer, bool allowed);
    event OperatorSet(address indexed operator);
    event PausedSet(bool paused);
    event QuotePolicySet(uint16 minStrikeBps, uint16 minPremiumBps, uint16 maxUtilizationBps);
    event PutQuotePolicySet(uint16 maxStrikeBps, uint16 minPremiumBps, uint16 maxUtilizationBps);
    event PutDeskSet(address indexed putDesk);
    event SwapAdapterSet(address indexed adapter);
    event DepositRequested(uint256 indexed epoch, address indexed account, uint256 stockAmount);
    event DepositCancelled(uint256 indexed epoch, address indexed account, uint256 stockAmount);
    event RedeemRequested(uint256 indexed epoch, address indexed account, uint256 shares);
    event RedeemCancelled(uint256 indexed epoch, address indexed account, uint256 shares);
    event OptionOffered(uint256 indexed epoch, uint256 indexed optionId);
    event OptionCancelled(uint256 indexed epoch, uint256 indexed optionId);
    event DeskOwedClaimed(address indexed token, uint256 amount);
    event Reinvested(uint256 usdgSpent, uint256 stockReceived);
    event EpochClosed(
        uint256 indexed epoch,
        uint256 indexed optionId,
        uint256 price,
        uint256 navBeforeFlows,
        uint256 supplyBeforeFlows,
        uint256 depositShares,
        uint256 redeemStock,
        uint256 redeemUSDG
    );
    event DepositClaimed(uint256 indexed epoch, address indexed account, address indexed to, uint256 shares);
    event DepositRefunded(uint256 indexed epoch, address indexed account, address indexed to, uint256 stockAmount);
    event RedeemClaimed(
        uint256 indexed epoch, address indexed account, address indexed to, uint256 stockAmount, uint256 usdgAmount
    );

    error InvalidAddress();
    error InvalidConfiguration();
    error NotEligible();
    error NotOperator();
    error IsPaused();
    error BadAmount();
    error BadTerms();
    error Busy();
    error NotClosed();
    error NoRequest();
    error UnsafeAssets();
    error NothingToClaim();
    error QueueFull();
    error RenounceDisabled();

    constructor(address owner_, IERC20 stock_, IERC20 usdg_, CoveredCallDesk desk_, PriceOracle oracle_, address pool_)
        ERC20("Earn RHNVDA", "hNVDA")
        Ownable(owner_)
    {
        if (
            owner_ == address(0) || address(stock_) == address(0) || address(usdg_) == address(0)
                || address(desk_) == address(0) || address(oracle_) == address(0) || pool_ == address(0)
        ) {
            revert InvalidAddress();
        }
        if (
            address(stock_) == address(usdg_) || IERC20Metadata(address(stock_)).decimals() != 18
                || IERC20Metadata(address(usdg_)).decimals() != 6 || address(desk_.usdg()) != address(usdg_)
                || oracle_.stock() != address(stock_)
        ) revert InvalidConfiguration();
        stock = stock_;
        usdg = usdg_;
        desk = desk_;
        oracle = oracle_;
        reinvestPool = pool_;
    }

    modifier onlyOperator() {
        if (msg.sender != owner() && msg.sender != operator) revert NotOperator();
        _;
    }

    // Governance is expected to be a multisig. Eligibility applies to deposits and hNVDA transfers;
    // an existing holder may always request redemption even after losing eligibility.
    function setEligible(address account, bool allowed) external onlyOwner {
        if (account == address(0)) revert InvalidAddress();
        eligible[account] = allowed;
        emit EligibilitySet(account, allowed);
    }

    function setBuyer(address buyer, bool allowed) external onlyOwner {
        if (buyer == address(0)) revert InvalidAddress();
        allowedBuyer[buyer] = allowed;
        emit BuyerSet(buyer, allowed);
    }

    function setOperator(address newOperator) external onlyOwner {
        operator = newOperator;
        emit OperatorSet(newOperator);
    }

    function setPaused(bool value) external onlyOwner {
        paused = value;
        emit PausedSet(value);
    }

    function setQuotePolicy(uint16 strikeBps, uint16 premiumBps, uint16 utilizationBps) external onlyOwner {
        if (
            strikeBps < 9_800 || strikeBps > 12_000 || premiumBps < 25 || premiumBps > 2_000 || utilizationBps < 1_000
                || utilizationBps > 10_000
        ) revert InvalidConfiguration();
        minStrikeBps = strikeBps;
        minPremiumBps = premiumBps;
        maxUtilizationBps = utilizationBps;
        emit QuotePolicySet(strikeBps, premiumBps, utilizationBps);
    }

    function setPutQuotePolicy(uint16 strikeBps, uint16 premiumBps, uint16 utilizationBps) external onlyOwner {
        if (
            strikeBps < 8_000 || strikeBps > 10_200 || premiumBps < 25 || premiumBps > 2_000 || utilizationBps < 1_000
                || utilizationBps > 10_000
        ) revert InvalidConfiguration();
        maxPutStrikeBps = strikeBps;
        minPutPremiumBps = premiumBps;
        maxPutUtilizationBps = utilizationBps;
        emit PutQuotePolicySet(strikeBps, premiumBps, utilizationBps);
    }

    /// @notice One-time installation before the first deposit. The Safe must verify the desk bytecode and address.
    function setPutDesk(CashSecuredPutDesk newDesk) external onlyOwner {
        if (address(putDesk) != address(0) || address(newDesk) == address(0)) revert InvalidConfiguration();
        if (
            totalSupply() != 0 || pendingDepositStock != 0 || pendingRedeemShares != 0 || claimableStock != 0
                || claimableUSDG != 0 || activeOptionId != 0
        ) revert Busy();
        if (
            address(newDesk.usdg()) != address(usdg) || address(newDesk.calendar()) != address(desk.calendar())
                || newDesk.owner() != owner()
        ) revert InvalidConfiguration();
        putDesk = newDesk;
        emit PutDeskSet(address(newDesk));
    }

    /// @notice One-time installation after deployment, because the adapter has this vault as an immutable caller.
    function setSwapAdapter(IEarnVaultSwapAdapter adapter) external onlyOwner {
        if (
            address(swapAdapter) != address(0) || address(adapter) == address(0) || adapter.vault() != address(this)
                || adapter.usdg() != address(usdg) || adapter.stock() != address(stock)
                || adapter.oracle() != address(oracle) || adapter.pool() != reinvestPool
                || adapter.maxDeviationBps() == 0 || adapter.maxDeviationBps() > 50 || adapter.maxSlippageBps() > 100
                || adapter.maxDeviationBps() >= adapter.maxSlippageBps()
        ) revert InvalidConfiguration();
        swapAdapter = adapter;
        emit SwapAdapterSet(address(adapter));
    }

    function renounceOwnership() public pure override {
        revert RenounceDisabled();
    }

    /// @notice Stock and USDG held by the vault for current hNVDA holders, excluding pending deposits and
    ///         already-settled redemption claims. Collateral locked in the desk is not included until returned.
    function freeBalances() public view returns (uint256 freeStock, uint256 freeUSDG) {
        uint256 stockLiability = pendingDepositStock + claimableStock;
        uint256 usdgLiability = claimableUSDG;
        uint256 stockBalance = stock.balanceOf(address(this));
        uint256 usdgBalance = usdg.balanceOf(address(this));
        if (stockBalance < stockLiability || usdgBalance < usdgLiability) revert UnsafeAssets();
        freeStock = stockBalance - stockLiability;
        freeUSDG = usdgBalance - usdgLiability;
    }

    function requestDeposit(uint256 stockAmount) external nonReentrant {
        if (paused) revert IsPaused();
        if (!eligible[msg.sender]) revert NotEligible();
        if (stockAmount < MIN_DEPOSIT) revert BadAmount();
        uint256 epochId = currentEpoch;
        if (depositRequests[epochId][msg.sender] == 0) {
            if (_depositors[epochId].length >= MAX_DEPOSITORS_PER_EPOCH) revert QueueFull();
            _depositors[epochId].push(msg.sender);
            _depositorIndexPlusOne[epochId][msg.sender] = _depositors[epochId].length;
        }
        uint256 beforeBalance = stock.balanceOf(address(this));
        stock.safeTransferFrom(msg.sender, address(this), stockAmount);
        if (stock.balanceOf(address(this)) - beforeBalance != stockAmount) revert UnsafeAssets();
        depositRequests[epochId][msg.sender] += stockAmount;
        pendingDepositStock += stockAmount;
        emit DepositRequested(epochId, msg.sender, stockAmount);
    }

    function cancelDeposit(uint256 stockAmount) external nonReentrant {
        uint256 requested = depositRequests[currentEpoch][msg.sender];
        if (stockAmount == 0 || stockAmount > requested) revert BadAmount();
        uint256 epochId = currentEpoch;
        uint256 remaining = requested - stockAmount;
        if (remaining != 0 && remaining < MIN_DEPOSIT) revert BadAmount();
        depositRequests[epochId][msg.sender] = remaining;
        if (requested == stockAmount) {
            uint256 index = _depositorIndexPlusOne[epochId][msg.sender] - 1;
            address[] storage accounts = _depositors[epochId];
            address moved = accounts[accounts.length - 1];
            accounts[index] = moved;
            _depositorIndexPlusOne[epochId][moved] = index + 1;
            accounts.pop();
            delete _depositorIndexPlusOne[epochId][msg.sender];
        }
        pendingDepositStock -= stockAmount;
        _pushExact(stock, msg.sender, stockAmount);
        emit DepositCancelled(epochId, msg.sender, stockAmount);
    }

    function requestRedeem(uint256 shares) external nonReentrant {
        uint256 balance = balanceOf(msg.sender);
        if (
            shares == 0 || shares > balance || (shares < MIN_REDEEM && shares != balance)
                || (balance - shares != 0 && balance - shares < MIN_REDEEM)
        ) revert BadAmount();
        _escrowingRedeem = true;
        _transfer(msg.sender, address(this), shares);
        _escrowingRedeem = false;
        redeemRequests[currentEpoch][msg.sender] += shares;
        pendingRedeemShares += shares;
        emit RedeemRequested(currentEpoch, msg.sender, shares);
    }

    function cancelRedeem(uint256 shares) external nonReentrant {
        uint256 requested = redeemRequests[currentEpoch][msg.sender];
        if (shares == 0 || shares > requested) revert BadAmount();
        uint256 remaining = requested - shares;
        if (remaining != 0 && remaining < MIN_REDEEM) revert BadAmount();
        redeemRequests[currentEpoch][msg.sender] = remaining;
        pendingRedeemShares -= shares;
        _transfer(address(this), msg.sender, shares);
        emit RedeemCancelled(currentEpoch, msg.sender, shares);
    }

    /// @notice Publish a bounded, counterparty-specific physical covered-call RFQ. The desk itself must also
    ///         allowlist this vault as a writer and the buyer. Offer approval is exact and immediately cleared.
    function offer(CoveredCallDesk.Terms calldata t) external onlyOwner nonReentrant returns (uint256 id) {
        uint256 spot = _validateOffer(t.buyer, t.underlying, t.size, t.feed, t.expiry, t.fillDeadline);
        if (t.mode != CoveredCallDesk.Settlement.Physical) revert BadTerms();
        (uint256 freeStock,) = freeBalances();
        if (uint256(t.size) > Math.mulDiv(freeStock, maxUtilizationBps, 10_000)) revert BadTerms();
        if (uint256(t.strike) * 1e12 < Math.mulDiv(spot, minStrikeBps, 10_000)) revert BadTerms();
        // A permitted slightly in-the-money strike must still pay its intrinsic value PLUS
        // the minimum time-value premium; otherwise an underpriced RFQ could transfer NAV to the buyer.
        _validatePremium(t.size, t.strike, t.premium, spot, minPremiumBps, true);
        stock.forceApprove(address(desk), t.size);
        id = desk.offer(t);
        stock.forceApprove(address(desk), 0);
        CoveredCallDesk.Option memory o = desk.getOption(id);
        if (o.writer != address(this) || o.state != CoveredCallDesk.State.Offered) revert UnsafeAssets();
        activeOptionId = id;
        activeOptionKind = OptionKind.Call;
        emit OptionOffered(currentEpoch, id);
    }

    /// @notice Lock actual free USDG for a bounded, counterparty-specific cash-secured put RFQ.
    ///         No second call or put can be opened until this option terminates and the epoch closes.
    function offerPut(CashSecuredPutDesk.Terms calldata t) external onlyOwner nonReentrant returns (uint256 id) {
        CashSecuredPutDesk pd = putDesk;
        if (address(pd) == address(0)) revert InvalidConfiguration();
        uint256 spot = _validateOffer(t.buyer, t.underlying, t.size, t.feed, t.expiry, t.fillDeadline);

        uint256 collateral = Math.mulDiv(uint256(t.size), uint256(t.strike), 1e18, Math.Rounding.Ceil);
        (, uint256 freeUSDG) = freeBalances();
        if (collateral == 0 || collateral > Math.mulDiv(freeUSDG, maxPutUtilizationBps, 10_000)) {
            revert BadTerms();
        }
        uint256 strikeInOracleUnits = uint256(t.strike) * 1e12;
        if (strikeInOracleUnits > Math.mulDiv(spot, maxPutStrikeBps, 10_000)) revert BadTerms();
        _validatePremium(t.size, t.strike, t.premium, spot, minPutPremiumBps, false);

        usdg.forceApprove(address(pd), collateral);
        id = pd.offer(t);
        usdg.forceApprove(address(pd), 0);
        CashSecuredPutDesk.Option memory o = pd.getOption(id);
        if (o.writer != address(this) || o.state != CashSecuredPutDesk.State.Offered || o.collateral != collateral) {
            revert UnsafeAssets();
        }
        activeOptionId = id;
        activeOptionKind = OptionKind.Put;
        emit OptionOffered(currentEpoch, id);
    }

    function _validateOffer(
        address buyer,
        address underlying,
        uint128 size,
        address feed,
        uint64 expiry,
        uint64 fillDeadline
    ) internal view returns (uint256 spot) {
        if (paused) revert IsPaused();
        if (activeOptionId != 0) revert Busy();
        if (totalSupply() == 0) revert UnsafeAssets();
        if (
            underlying != address(stock) || buyer == address(0) || !allowedBuyer[buyer] || size == 0
                || feed != address(oracle.stockFeed()) || expiry < block.timestamp + MIN_TENOR
                || expiry > block.timestamp + MAX_TENOR || fillDeadline < block.timestamp
                || fillDeadline > block.timestamp + MAX_FILL_WINDOW || fillDeadline >= expiry
        ) revert BadTerms();
        spot = _freshPrice();
    }

    function _validatePremium(uint128 size, uint128 strike, uint128 premium, uint256 spot, uint16 minBps, bool isCall)
        internal
        pure
    {
        uint256 strikePrice = uint256(strike) * 1e12;
        uint256 intrinsic = isCall && spot > strikePrice
            ? Math.mulDiv(size, spot - strikePrice, VALUE_SCALE)
            : !isCall && strikePrice > spot ? Math.mulDiv(size, strikePrice - spot, VALUE_SCALE) : 0;
        uint256 notional = Math.mulDiv(size, spot, VALUE_SCALE);
        if (uint256(premium) < intrinsic + Math.mulDiv(notional, minBps, 10_000, Math.Rounding.Ceil)) {
            revert BadTerms();
        }
    }

    /// @notice If the buyer has missed its deadline, anyone may release the offered collateral.
    function cancelOffered() external nonReentrant {
        uint256 id = activeOptionId;
        if (id == 0) revert NoRequest();
        if (activeOptionKind == OptionKind.Call) {
            CoveredCallDesk.Option memory o = desk.getOption(id);
            if (o.state != CoveredCallDesk.State.Offered) revert Busy();
            if (msg.sender != owner() && msg.sender != operator && block.timestamp <= o.fillDeadline) {
                revert NotOperator();
            }
            desk.cancel(id);
        } else if (activeOptionKind == OptionKind.Put) {
            CashSecuredPutDesk.Option memory o = putDesk.getOption(id);
            if (o.state != CashSecuredPutDesk.State.Offered) revert Busy();
            if (msg.sender != owner() && msg.sender != operator && block.timestamp <= o.fillDeadline) {
                revert NotOperator();
            }
            putDesk.cancel(id);
        } else {
            revert UnsafeAssets();
        }
        emit OptionCancelled(currentEpoch, id);
    }

    /// @notice Pull a refused desk payout back into the vault. Anyone may call; only this vault's credit moves.
    function claimDeskOwed(address token) external nonReentrant {
        _claimOwed(address(desk), token);
    }

    /// @notice Recover a refused put-desk payout for this vault before epoch pricing.
    function claimPutDeskOwed(address token) external nonReentrant {
        CashSecuredPutDesk pd = putDesk;
        if (address(pd) == address(0)) revert InvalidConfiguration();
        _claimOwed(address(pd), token);
    }

    function _claimOwed(address optionDesk, address token) internal {
        if (token != address(stock) && token != address(usdg)) revert InvalidAddress();
        uint256 amount = CoveredCallDesk(optionDesk).owed(token, address(this));
        if (amount == 0) revert NothingToClaim();
        uint256 beforeBalance = IERC20(token).balanceOf(address(this));
        CoveredCallDesk(optionDesk).claim(token, address(this));
        if (IERC20(token).balanceOf(address(this)) - beforeBalance != amount) revert UnsafeAssets();
        emit DeskOwedClaimed(token, amount);
    }

    /// @notice Reinvest free premium or exercise proceeds through a one-time configured, price-bounded V3 adapter.
    ///         The operator supplies a transaction-level minimum stock output on top of the adapter's guards.
    function reinvestUSDG(uint256 amountIn, uint256 minStockOut)
        external
        onlyOperator
        nonReentrant
        returns (uint256 spent, uint256 received)
    {
        if (paused) revert IsPaused();
        IEarnVaultSwapAdapter adapter = swapAdapter;
        if (address(adapter) == address(0) || amountIn == 0 || minStockOut == 0) revert BadAmount();
        (, uint256 freeUSDG) = freeBalances();
        if (amountIn > freeUSDG) revert UnsafeAssets();
        // Require a fresh market-open oracle even if the adapter implementation changes.
        _freshPrice();
        uint256 beforeUSDG = usdg.balanceOf(address(this));
        uint256 beforeStock = stock.balanceOf(address(this));
        usdg.forceApprove(address(adapter), amountIn);
        (spent, received) = adapter.buy(amountIn, minStockOut);
        usdg.forceApprove(address(adapter), 0);
        if (
            spent == 0 || spent > amountIn || received < minStockOut
                || beforeUSDG - usdg.balanceOf(address(this)) != spent
                || stock.balanceOf(address(this)) - beforeStock != received
        ) revert UnsafeAssets();
        emit Reinvested(spent, received);
    }

    /// @notice Permissionless rollover checkpoint. No pricing or share mutation occurs while either option is open.
    ///         Both queues use the SAME pre-flow NAV; redemptions reserve the existing stock/USDG mix.
    function closeEpoch() external nonReentrant {
        uint256 id = activeOptionId;
        OptionKind kind = activeOptionKind;
        if (id != 0) {
            if (kind == OptionKind.Call) {
                if (!_terminal(uint8(desk.getOption(id).state))) revert Busy();
            } else if (kind == OptionKind.Put) {
                if (!_terminal(uint8(putDesk.getOption(id).state))) revert Busy();
            } else {
                revert UnsafeAssets();
            }
        } else if (kind != OptionKind.None) {
            revert UnsafeAssets();
        }
        if (desk.owed(address(stock), address(this)) != 0 || desk.owed(address(usdg), address(this)) != 0) {
            revert UnsafeAssets();
        }
        CashSecuredPutDesk pd = putDesk;
        if (
            address(pd) != address(0)
                && (pd.owed(address(stock), address(this)) != 0 || pd.owed(address(usdg), address(this)) != 0)
        ) {
            revert UnsafeAssets();
        }
        uint256 price = _freshPrice();
        (uint256 freeStock, uint256 freeUSDG) = freeBalances();
        uint256 supply = totalSupply();
        uint256 nav = Math.mulDiv(freeStock, price, VALUE_SCALE) + freeUSDG;
        if (
            (supply == 0 && (freeStock != 0 || freeUSDG != 0)) || (supply != 0 && nav == 0)
                || pendingRedeemShares > supply
        ) revert UnsafeAssets();

        uint256 redeemShares = pendingRedeemShares;
        uint256 redeemStock =
            redeemShares == supply ? freeStock : Math.mulDiv(freeStock, redeemShares, supply == 0 ? 1 : supply);
        uint256 redeemUSDG =
            redeemShares == supply ? freeUSDG : Math.mulDiv(freeUSDG, redeemShares, supply == 0 ? 1 : supply);
        uint256 epochId = currentEpoch;
        (uint256 depositShares, uint256 admittedStock, uint256 refundableStock) =
            _allocateDeposits(epochId, price, supply, nav);
        if (admittedStock + refundableStock != pendingDepositStock) revert UnsafeAssets();
        Epoch storage e = epochs[epochId];
        e.closed = true;
        e.optionId = id;
        epochOptionKind[epochId] = kind;
        e.price = price;
        e.navBeforeFlows = nav;
        e.supplyBeforeFlows = supply;
        e.depositStockRemaining = admittedStock;
        e.depositSharesRemaining = depositShares;
        e.redeemSharesRemaining = redeemShares;
        e.redeemStockRemaining = redeemStock;
        e.redeemUSDGRemaining = redeemUSDG;

        claimableStock += redeemStock + refundableStock;
        claimableUSDG += redeemUSDG;
        if (redeemShares != 0) _burn(address(this), redeemShares);
        if (supply == 0 && depositShares != 0) {
            // Make total initial supply equal admitted stock; the fixed floor and per-user rounding
            // dust are impossible to redeem and deter donation/rounding attacks on tiny supply.
            _mint(DEAD_SHARES_HOLDER, admittedStock - depositShares);
        }
        if (depositShares != 0) _mint(address(this), depositShares);
        pendingDepositStock = 0;
        pendingRedeemShares = 0;
        activeOptionId = 0;
        activeOptionKind = OptionKind.None;
        currentEpoch = epochId + 1;
        emit EpochClosed(epochId, id, price, nav, supply, depositShares, redeemStock, redeemUSDG);
    }

    function claimDeposit(uint256 epochId, address to) external nonReentrant returns (uint256 shares) {
        if (to == address(0) || to == address(this)) revert InvalidAddress();
        Epoch storage e = epochs[epochId];
        if (!e.closed) revert NotClosed();
        uint256 amount = depositRequests[epochId][msg.sender];
        if (amount == 0) revert NoRequest();
        shares = depositClaimShares[epochId][msg.sender];
        if (shares == 0) {
            uint256 refund = refundableDepositStock[epochId][msg.sender];
            if (refund != amount) revert UnsafeAssets();
            delete depositRequests[epochId][msg.sender];
            delete refundableDepositStock[epochId][msg.sender];
            claimableStock -= refund;
            _pushExact(stock, to, refund);
            emit DepositRefunded(epochId, msg.sender, to, refund);
            return 0;
        }
        if (!eligible[to]) revert NotEligible();
        delete depositRequests[epochId][msg.sender];
        delete depositClaimShares[epochId][msg.sender];
        e.depositStockRemaining -= amount;
        e.depositSharesRemaining -= shares;
        _transfer(address(this), to, shares);
        emit DepositClaimed(epochId, msg.sender, to, shares);
    }

    function claimRedeem(uint256 epochId, address to)
        external
        nonReentrant
        returns (uint256 stockAmount, uint256 usdgAmount)
    {
        if (to == address(0) || to == address(this)) revert InvalidAddress();
        Epoch storage e = epochs[epochId];
        if (!e.closed) revert NotClosed();
        uint256 shares = redeemRequests[epochId][msg.sender];
        if (shares == 0) revert NoRequest();
        stockAmount = shares == e.redeemSharesRemaining
            ? e.redeemStockRemaining
            : Math.mulDiv(shares, e.redeemStockRemaining, e.redeemSharesRemaining);
        usdgAmount = shares == e.redeemSharesRemaining
            ? e.redeemUSDGRemaining
            : Math.mulDiv(shares, e.redeemUSDGRemaining, e.redeemSharesRemaining);
        delete redeemRequests[epochId][msg.sender];
        e.redeemSharesRemaining -= shares;
        e.redeemStockRemaining -= stockAmount;
        e.redeemUSDGRemaining -= usdgAmount;
        claimableUSDG -= usdgAmount;
        if (usdgAmount != 0) _pushExact(usdg, to, usdgAmount);
        if (stockAmount != 0) {
            if (_tryPushExact(stock, to, stockAmount)) {
                claimableStock -= stockAmount;
            } else {
                owedRedeemStock[msg.sender] += stockAmount;
                stockAmount = 0;
            }
        }
        emit RedeemClaimed(epochId, msg.sender, to, stockAmount, usdgAmount);
    }

    /// @notice Reattempt stock that an issuer freeze or recipient block prevented delivering on redemption.
    function claimOwedRedeemStock(address to) external nonReentrant returns (uint256 amount) {
        if (to == address(0) || to == address(this)) revert InvalidAddress();
        amount = owedRedeemStock[msg.sender];
        if (amount == 0) revert NothingToClaim();
        owedRedeemStock[msg.sender] = 0;
        claimableStock -= amount;
        _pushExact(stock, to, amount);
    }

    /// @notice Recover unsolicited assets before the first seed while leaving pending deposits untouched.
    ///         Once any hNVDA exists, unsolicited transfers belong to the shareholders' NAV.
    function recoverOrphaned(address to) external onlyOwner nonReentrant {
        if (to == address(0) || to == address(this)) revert InvalidAddress();
        if (
            totalSupply() != 0 || pendingRedeemShares != 0 || claimableStock != 0 || claimableUSDG != 0
                || activeOptionId != 0
        ) revert Busy();
        (uint256 stockAmount, uint256 usdgAmount) = freeBalances();
        if (stockAmount != 0) _pushExact(stock, to, stockAmount);
        if (usdgAmount != 0) _pushExact(usdg, to, usdgAmount);
    }

    /// @dev Both immutable desk enums use Cancelled=2 and terminal settled/lapsed states 5 and above.
    function _terminal(uint8 state) internal pure returns (bool) {
        return state == 2 || state >= 5;
    }

    function _allocateDeposits(uint256 epochId, uint256 price, uint256 supply, uint256 nav)
        internal
        returns (uint256 shares, uint256 admittedStock, uint256 refundableStock)
    {
        uint256 initialUserSharePool;
        if (supply == 0 && pendingDepositStock != 0) {
            if (pendingDepositStock <= MIN_DEAD_SHARES) revert UnsafeAssets();
            initialUserSharePool = pendingDepositStock - MIN_DEAD_SHARES;
        }
        address[] storage accounts = _depositors[epochId];
        for (uint256 i; i < accounts.length; ++i) {
            address account = accounts[i];
            uint256 amount = depositRequests[epochId][account];
            uint256 accountShares = supply == 0
                ? Math.mulDiv(amount, initialUserSharePool, pendingDepositStock)
                : Math.mulDiv(Math.mulDiv(amount, price, VALUE_SCALE), supply, nav);
            if (accountShares == 0) {
                refundableDepositStock[epochId][account] = amount;
                refundableStock += amount;
            } else {
                depositClaimShares[epochId][account] = accountShares;
                admittedStock += amount;
                shares += accountShares;
            }
        }
    }

    function _freshPrice() internal view returns (uint256 p) {
        // PriceOracle gates market hours, issuer pause and both feed ages. The stock feed has a
        // 24-hour heartbeat, so a second 30-minute gate here can block healthy epoch closes.
        p = oracle.price();
    }

    function _pushExact(IERC20 token, address to, uint256 amount) internal {
        if (!_tryPushExact(token, to, amount)) revert UnsafeAssets();
    }

    function _tryPushExact(IERC20 token, address to, uint256 amount) internal returns (bool sent) {
        uint256 fromBefore = token.balanceOf(address(this));
        uint256 toBefore = token.balanceOf(to);
        sent = token.trySafeTransfer(to, amount);
        if (!sent) {
            // A broken token could move funds and still return false. Never record a new claim
            // unless this vault still holds the full reserved amount.
            if (token.balanceOf(address(this)) != fromBefore) revert UnsafeAssets();
            return false;
        }
        if (fromBefore - token.balanceOf(address(this)) != amount || token.balanceOf(to) - toBefore != amount) {
            revert UnsafeAssets();
        }
    }

    function _update(address from, address to, uint256 value) internal override {
        if (from == DEAD_SHARES_HOLDER) revert NotEligible();
        if (to == address(this) && from != address(0) && !_escrowingRedeem) revert BadTerms();
        if (
            from != address(0) && from != address(this) && to != address(0) && to != address(this)
                && (!eligible[from] || !eligible[to])
        ) revert NotEligible();
        super._update(from, to, value);
    }
}

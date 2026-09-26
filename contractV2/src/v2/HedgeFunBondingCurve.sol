// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {Math} from "@openzeppelin/contracts/utils/math/Math.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";

interface ICurveBurnable { function burn(uint256 amount) external; }
interface ICurveGraduation { function graduateCurve() external; }

/// @notice Fixed-term stock-denominated launch curve. Virtual stock is pricing data, never a payout reserve.
contract HedgeFunBondingCurve is ReentrancyGuard {
    using SafeERC20 for IERC20;
    struct Init {
        address factory; address token; address stock; address treasury;
        address protocol; address creator; uint256 supply; uint256 virtualStock;
        uint16 saleBps; uint16 taxBps; uint16 protocolBps; uint16 creatorBps;
        uint16 snipeBps; uint8 snipeSeconds;
    }
    enum Status { Active, Ready, Graduated }
    address public immutable factory;
    address public immutable token;
    address public immutable stock;
    address public immutable treasury;
    address public immutable protocol;
    address public immutable creator;
    uint256 public immutable initialSupply;
    uint256 public immutable virtualStock;
    uint256 public immutable minTokenReserve;
    uint256 public immutable invariant;
    uint256 public immutable terminalStock;
    uint16 public immutable taxBps;
    uint16 public immutable protocolBps;
    uint16 public immutable creatorBps;
    uint16 public immutable snipeBps;
    uint8 public immutable snipeSeconds;
    uint40 public immutable launchedAt;
    uint256 public tokenReserve;
    uint256 public realStockReserve;
    uint256 public totalFees;
    mapping(address => uint256) public claimable;
    Status public status;
    error BadConfig();
    error Closed();
    error BadTrade();
    error Expired();
    error Slippage();
    error Insolvent();
    error UnsupportedTransfer();
    error NotFactory();
    error GraduationFailed();
    event Bought(address indexed caller, address indexed recipient, uint256 stockSpent, uint256 tokensOut, uint256 burned);
    event Sold(address indexed caller, address indexed recipient, uint256 tokenIn, uint256 stockOut, uint256 fee);
    event FeesClaimed(address indexed recipient, uint256 amount);
    event Ready();
    event Released(uint256 stockAmount, uint256 tokenAmount);

    constructor(Init memory p) {
        if (p.factory == address(0) || p.token == address(0) || p.stock == address(0) || p.token == p.stock
            || p.treasury == address(0) || p.protocol == address(0) || p.creator == address(0)
            || p.supply == 0 || p.supply > type(uint128).max || p.virtualStock == 0
            || p.virtualStock > type(uint128).max || p.saleBps < 1000 || p.saleBps > 9000
            || p.taxBps >= 10000 || p.snipeBps > 9900
            || uint256(p.protocolBps) + p.creatorBps > 10000) revert BadConfig();
        factory = p.factory; token = p.token; stock = p.stock;
        treasury = p.treasury; protocol = p.protocol; creator = p.creator;
        initialSupply = p.supply; tokenReserve = p.supply; virtualStock = p.virtualStock;
        minTokenReserve = Math.mulDiv(p.supply, 10000 - p.saleBps, 10000);
        if (minTokenReserve == 0) revert BadConfig();
        invariant = p.supply * p.virtualStock;
        terminalStock = Math.ceilDiv(invariant, minTokenReserve);
        if (terminalStock > type(uint128).max) revert BadConfig();
        taxBps = p.taxBps; protocolBps = p.protocolBps; creatorBps = p.creatorBps;
        snipeBps = p.snipeBps; snipeSeconds = p.snipeSeconds; launchedAt = uint40(block.timestamp);
    }

    /// @notice Current buy-side burn rate. The short opening window applies equally to every buyer.
    function buyRateBps() public view returns (uint256 rate) {
        rate = taxBps;
        uint256 seconds_ = snipeSeconds;
        if (seconds_ == 0) return rate;
        uint256 elapsed = block.timestamp - launchedAt;
        if (elapsed >= seconds_) return rate;
        uint256 opening = uint256(snipeBps) * (seconds_ - elapsed) / seconds_;
        return opening > rate ? opening : rate;
    }

    function quoteBuy(uint256 maxStockIn) public view returns (uint256 stockSpent, uint256 tokensOut, uint256 taxTokens) {
        if (status != Status.Active) revert Closed();
        if (maxStockIn == 0) return (0, 0, 0);
        uint256 x = tokenReserve;
        uint256 y = virtualStock + realStockReserve;
        // Test the terminal cost first, so even an unlimited max input cannot overflow y + input.
        uint256 capCost = terminalStock - y;
        uint256 budget = maxStockIn < capCost ? maxStockIn : capCost;
        uint256 newReserve = budget == capCost ? minTokenReserve : Math.ceilDiv(invariant, y + budget);
        uint256 gross = x - newReserve;
        // Canonicalize both sides to ceil(k/x). Charging only this cost refunds raw-unit rounding
        // on every buy and prevents a later trader from extracting an earlier buyer's rounding.
        stockSpent = Math.ceilDiv(invariant, newReserve) - y;
        taxTokens = Math.mulDiv(gross, buyRateBps(), 10000);
        tokensOut = gross - taxTokens;
    }

    function buy(uint256 maxStockIn, uint256 minTokensOut, address recipient, uint256 deadline)
        external returns (uint256 stockSpent, uint256 tokensOut)
    {
        (stockSpent, tokensOut) = _buy(maxStockIn, minTokensOut, recipient, deadline);
        // Exit the trading lock before the factory calls release(), which has its own lock. Ready
        // exists only inside this transaction: a failed migration rolls back the crossing buy too.
        if (status == Status.Ready) {
            ICurveGraduation(factory).graduateCurve();
            if (status != Status.Graduated) revert GraduationFailed();
        }
    }

    function _buy(uint256 maxStockIn, uint256 minTokensOut, address recipient, uint256 deadline)
        private nonReentrant returns (uint256 stockSpent, uint256 tokensOut)
    {
        _tradeChecks(recipient, deadline);
        uint256 tax;
        (stockSpent, tokensOut, tax) = quoteBuy(maxStockIn);
        if (stockSpent == 0 || tokensOut == 0) revert BadTrade();
        if (tokensOut < minTokensOut) revert Slippage();
        tokenReserve -= tokensOut + tax;
        realStockReserve += stockSpent;
        if (tokenReserve == minTokenReserve) { status = Status.Ready; emit Ready(); }
        _receive(stock, stockSpent);
        if (tax != 0) ICurveBurnable(token).burn(tax);
        IERC20(token).safeTransfer(recipient, tokensOut);
        _solvent();
        emit Bought(msg.sender, recipient, stockSpent, tokensOut, tax);
    }

    function quoteSell(uint256 tokenIn) public view returns (uint256 stockOut, uint256 taxStock) {
        if (status != Status.Active) revert Closed();
        // The fixed-supply token cannot legitimately supply a larger input.
        if (tokenIn > initialSupply - tokenReserve) revert BadTrade();
        if (tokenIn == 0) return (0, 0);
        uint256 gross = virtualStock + realStockReserve - Math.ceilDiv(invariant, tokenReserve + tokenIn);
        if (gross > realStockReserve) revert Insolvent();
        taxStock = Math.mulDiv(gross, taxBps, 10000);
        stockOut = gross - taxStock;
    }

    function sell(uint256 tokenIn, uint256 minStockOut, address recipient, uint256 deadline)
        external nonReentrant returns (uint256 stockOut)
    {
        _tradeChecks(recipient, deadline);
        uint256 tax;
        (stockOut, tax) = quoteSell(tokenIn);
        if (tokenIn == 0 || stockOut == 0) revert BadTrade();
        if (stockOut < minStockOut) revert Slippage();
        tokenReserve += tokenIn;
        realStockReserve -= stockOut + tax;
        uint256 protocolFee = Math.mulDiv(tax, protocolBps, 10000);
        uint256 creatorFee = Math.mulDiv(tax, creatorBps, 10000);
        claimable[protocol] += protocolFee;
        claimable[creator] += creatorFee;
        claimable[treasury] += tax - protocolFee - creatorFee;
        totalFees += tax;
        _receive(token, tokenIn);
        _sendStock(recipient, stockOut);
        _solvent();
        emit Sold(msg.sender, recipient, tokenIn, stockOut, tax);
    }

    /// @notice Anyone may deliver a recipient's fees, but never redirect them. A rejected transfer rolls back only this claim.
    function claimFees(address recipient) external nonReentrant {
        uint256 amount = claimable[recipient];
        if (amount == 0) return;
        claimable[recipient] = 0;
        totalFees -= amount;
        _sendStock(recipient, amount);
        _solvent();
        emit FeesClaimed(recipient, amount);
        // Booking is separate: an oracle failure must never block fee delivery.
    }

    function release() external nonReentrant returns (uint256 stockAmount, uint256 tokenAmount) {
        if (msg.sender != factory) revert NotFactory();
        if (status != Status.Ready) revert Closed();
        _solvent();
        stockAmount = realStockReserve; tokenAmount = tokenReserve;
        realStockReserve = 0; tokenReserve = 0; status = Status.Graduated;
        _sendStock(factory, stockAmount);
        IERC20(token).safeTransfer(factory, tokenAmount);
        _solvent();
        emit Released(stockAmount, tokenAmount);
    }

    function _tradeChecks(address recipient, uint256 deadline) private view {
        if (block.timestamp > deadline) revert Expired();
        if (recipient == address(0) || recipient == address(this)) revert BadTrade();
        _solvent();
    }
    function _receive(address asset, uint256 amount) private {
        uint256 beforeBalance = IERC20(asset).balanceOf(address(this));
        uint256 beforeSender = IERC20(asset).balanceOf(msg.sender);
        IERC20(asset).safeTransferFrom(msg.sender, address(this), amount);
        if (IERC20(asset).balanceOf(address(this)) != beforeBalance + amount
            || IERC20(asset).balanceOf(msg.sender) != beforeSender - amount) revert UnsupportedTransfer();
    }
    // Issuer upgrades must not turn a nominal min-out or fee claim into an underpayment, nor
    // allow an extra sender charge to consume fee liabilities or donated stock.
    function _sendStock(address recipient, uint256 amount) private {
        uint256 beforeSelf = IERC20(stock).balanceOf(address(this));
        uint256 beforeRecipient = IERC20(stock).balanceOf(recipient);
        IERC20(stock).safeTransfer(recipient, amount);
        if (IERC20(stock).balanceOf(address(this)) != beforeSelf - amount
            || IERC20(stock).balanceOf(recipient) != beforeRecipient + amount) revert UnsupportedTransfer();
    }
    function _solvent() private view {
        if (IERC20(token).balanceOf(address(this)) < tokenReserve
            || IERC20(stock).balanceOf(address(this)) < realStockReserve + totalFees) revert Insolvent();
    }
}

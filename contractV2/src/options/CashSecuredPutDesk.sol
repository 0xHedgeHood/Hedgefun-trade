// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {IERC20Metadata} from "@openzeppelin/contracts/token/ERC20/extensions/IERC20Metadata.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";
import {Ownable2Step} from "@openzeppelin/contracts/access/Ownable2Step.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import {Math} from "@openzeppelin/contracts/utils/math/Math.sol";
import {SafeCast} from "@openzeppelin/contracts/utils/math/SafeCast.sol";
import {ITradingCalendar} from "../interfaces/ITradingCalendar.sol";
import {IAggregatorV3Rounds} from "./IAggregatorV3Rounds.sol";

interface IPutStockControls {
    function oraclePaused() external view returns (bool);
    function uiMultiplier() external view returns (uint256);
}

/// @title CashSecuredPutDesk
/// @notice Allowlisted European RFQ puts. A writer locks the entire USDG strike cost at offer. The buyer pays the
///         premium at fill; if the put finishes in the money, the buyer can deliver the stock and take the locked
///         USDG. No margin, rehypothecation, cash settlement, or shortfall is possible through this contract.
/// @dev Strike and oracle settlement prices are USDG base units per one whole 18-decimal stock token. The issuer's
///      standard USD Chainlink feed must already include its current uiMultiplier. USDG is assumed to equal USD.
///      The owner controls allowlists/listings and a delayed, trusted settlement backstop, but cannot move collateral.
contract CashSecuredPutDesk is Ownable2Step, ReentrancyGuard {
    using SafeERC20 for IERC20;
    using SafeCast for uint256;

    enum State {
        None,
        Offered,
        Cancelled,
        Active,
        Exercisable,
        Exercised,
        ExpiredOTM,
        Lapsed
    }
    enum PriceSource {
        Oracle,
        AcceptedByBuyer,
        AcceptedByWriter,
        Backstop
    }

    struct Listing {
        address feed;
        uint8 feedDecimals;
        bool enabled;
    }

    struct Terms {
        address buyer; // address(0): any allowlisted buyer may fill
        address underlying; // listed 18-decimal stock token
        uint128 size; // stock token wei
        uint128 strike; // USDG base units per one whole stock token
        uint128 premium; // USDG base units for the entire option
        uint64 expiry;
        uint64 fillDeadline;
        address feed; // exact listed feed agreed in RFQ
        uint32 exerciseWindow; // exact current desk window agreed in RFQ
        uint256 stockMultiplier; // exact issuer multiplier agreed in RFQ
    }

    struct Option {
        address writer;
        address buyer;
        address underlying;
        address feed;
        uint256 stockMultiplier;
        uint256 collateral; // exact USDG locked at offer, rounded up in buyer's favour
        uint64 expiry;
        uint64 fillDeadline;
        uint64 exerciseDeadline;
        uint32 exerciseWindow;
        uint8 feedDecimals;
        State state;
        uint128 size;
        uint128 strike;
        uint128 premium;
        uint128 settlementPrice;
    }

    struct Proposal {
        address by;
        uint64 at;
        uint128 price;
    }

    uint256 public constant MAX_SETTLEMENT_AGE = 26 hours;
    uint256 public constant SETTLE_DELAY = 5 minutes;
    uint256 public constant BACKSTOP_DELAY = 14 days;
    uint256 public constant PROPOSAL_TTL = 1 days;
    uint256 public constant MAX_TENOR = 400 days;
    uint32 public constant MIN_EXERCISE_WINDOW = 30 minutes;
    uint32 public constant MAX_EXERCISE_WINDOW = 3 days;

    IERC20 public immutable usdg;
    uint8 public immutable usdgDecimals;
    ITradingCalendar public immutable calendar;
    uint32 public exerciseWindow = 2 hours;
    uint256 public nextId = 1;
    bool public paused;

    mapping(address underlying => Listing) public listings;
    mapping(address => bool) public isWriter;
    mapping(address => bool) public isBuyer;
    mapping(address token => uint256) public reserved;
    mapping(address token => mapping(address owner => uint256)) public owed;
    mapping(uint256 id => Option) internal _options;
    mapping(uint256 id => Proposal) public proposals;

    event Listed(address indexed underlying, address feed, uint8 feedDecimals, bool enabled);
    event WriterSet(address indexed writer, bool allowed);
    event BuyerSet(address indexed buyer, bool allowed);
    event PausedSet(bool paused);
    event ExerciseWindowSet(uint32 window);
    event Offered(
        uint256 indexed id,
        address indexed writer,
        address indexed buyer,
        address underlying,
        uint256 size,
        uint256 strike,
        uint256 premium,
        uint256 collateral,
        uint64 expiry,
        uint64 fillDeadline,
        address feed,
        uint256 stockMultiplier
    );
    event Cancelled(uint256 indexed id);
    event Filled(uint256 indexed id, address indexed buyer, uint256 premium);
    event PriceProposed(uint256 indexed id, address indexed by, uint256 price);
    event ProposalWithdrawn(uint256 indexed id);
    event Determined(uint256 indexed id, uint256 price, PriceSource source);
    event ExpiredOTM(uint256 indexed id);
    event ExerciseOpened(uint256 indexed id, uint64 deadline);
    event Exercised(uint256 indexed id, address indexed to, uint256 stockDelivered, uint256 usdgPaid);
    event Lapsed(uint256 indexed id);
    event Owed(address indexed token, address indexed owner, uint256 amount);
    event Claimed(address indexed token, address indexed owner, address to, uint256 amount);
    event Swept(address indexed token, address to, uint256 amount);

    error IsPaused();
    error NotWriter();
    error NotBuyer();
    error NotParty();
    error NotListed();
    error BadTerms();
    error BadRecipient();
    error ExpiryMarketClosed();
    error WrongState();
    error TooEarly();
    error TooLate();
    error BadRound();
    error NotLastRoundBeforeExpiry();
    error NotInTheMoney();
    error NoProposal();
    error Unexpected();
    error BadListing();
    error StockControlsUnavailable();
    error StockControlsPaused();
    error StockMultiplierChanged();
    error RenounceDisabled();

    constructor(address owner_, IERC20 usdg_, ITradingCalendar calendar_) Ownable(owner_) {
        if (address(usdg_) == address(0)) revert BadTerms();
        uint8 d = IERC20Metadata(address(usdg_)).decimals();
        if (d > 18) revert BadTerms();
        usdg = usdg_;
        usdgDecimals = d;
        calendar = calendar_;
    }

    // Owner configuration affects only future offers. Pause does not block any exit.
    function list(address underlying, address feed, bool enabled) external onlyOwner {
        if (underlying == address(0) || feed == address(0) || underlying == address(usdg)) revert BadListing();
        if (IERC20Metadata(underlying).decimals() != 18) revert BadListing();
        uint8 d = IAggregatorV3Rounds(feed).decimals();
        if (d > 36) revert BadListing();
        listings[underlying] = Listing(feed, d, enabled);
        emit Listed(underlying, feed, d, enabled);
    }

    function setWriter(address writer, bool allowed) external onlyOwner {
        isWriter[writer] = allowed;
        emit WriterSet(writer, allowed);
    }

    function setBuyer(address buyer, bool allowed) external onlyOwner {
        isBuyer[buyer] = allowed;
        emit BuyerSet(buyer, allowed);
    }

    function setPaused(bool p) external onlyOwner {
        paused = p;
        emit PausedSet(p);
    }

    function setExerciseWindow(uint32 window) external onlyOwner {
        if (window < MIN_EXERCISE_WINDOW || window > MAX_EXERCISE_WINDOW) revert BadTerms();
        exerciseWindow = window;
        emit ExerciseWindowSet(window);
    }

    /// @notice Transfer only unsolicited excess tokens; locked collateral and failed payouts stay reserved.
    function sweep(address token, address to) external onlyOwner nonReentrant {
        if (to == address(0) || to == address(this)) revert BadRecipient();
        uint256 bal = IERC20(token).balanceOf(address(this));
        uint256 r = reserved[token];
        if (bal <= r) return;
        uint256 amount = bal - r;
        _transferExact(token, to, amount);
        emit Swept(token, to, amount);
    }

    function renounceOwnership() public pure override {
        revert RenounceDisabled();
    }

    /// @notice The writer must pre-fund the full exercise cost; collateral cannot be reduced after offer.
    function offer(Terms calldata t) external nonReentrant returns (uint256 id) {
        if (paused) revert IsPaused();
        if (!isWriter[msg.sender]) revert NotWriter();
        Listing memory l = listings[t.underlying];
        if (!l.enabled) revert NotListed();
        if (t.feed != l.feed || t.exerciseWindow != exerciseWindow || t.stockMultiplier == 0) revert BadTerms();
        if (t.buyer != address(0) && !isBuyer[t.buyer]) revert NotBuyer();
        if (t.size == 0 || t.strike == 0 || t.premium == 0) revert BadTerms();
        if (t.expiry <= block.timestamp || t.expiry > block.timestamp + MAX_TENOR) revert BadTerms();
        if (t.fillDeadline < block.timestamp || t.fillDeadline > t.expiry) revert BadTerms();
        if (address(calendar) != address(0) && calendar.isClosed(t.expiry)) revert ExpiryMarketClosed();
        uint256 collateral = Math.mulDiv(t.size, t.strike, 1e18, Math.Rounding.Ceil);
        if (collateral == 0) revert BadTerms();
        _checkStockControls(t.underlying, t.stockMultiplier);

        id = nextId++;
        _options[id] = Option({
            writer: msg.sender,
            buyer: t.buyer,
            underlying: t.underlying,
            feed: l.feed,
            stockMultiplier: t.stockMultiplier,
            collateral: collateral,
            expiry: t.expiry,
            fillDeadline: t.fillDeadline,
            exerciseDeadline: 0,
            exerciseWindow: t.exerciseWindow,
            feedDecimals: l.feedDecimals,
            state: State.Offered,
            size: t.size,
            strike: t.strike,
            premium: t.premium,
            settlementPrice: 0
        });
        _pullExact(address(usdg), msg.sender, collateral);
        _checkStockControls(t.underlying, t.stockMultiplier);
        emit Offered(
            id,
            msg.sender,
            t.buyer,
            t.underlying,
            t.size,
            t.strike,
            t.premium,
            collateral,
            t.expiry,
            t.fillDeadline,
            l.feed,
            t.stockMultiplier
        );
    }

    function cancel(uint256 id) external nonReentrant {
        Option storage o = _options[id];
        if (o.state != State.Offered) revert WrongState();
        if (msg.sender != o.writer) revert NotWriter();
        o.state = State.Cancelled;
        emit Cancelled(id);
        _release(address(usdg), o.writer, o.collateral, o.writer);
    }

    /// @notice The buyer pays the total premium, forwarded atomically or credited to the writer if blocked.
    function fill(uint256 id) external nonReentrant {
        if (paused) revert IsPaused();
        Option storage o = _options[id];
        if (o.state != State.Offered) revert WrongState();
        if (!isBuyer[msg.sender] || (o.buyer != address(0) && o.buyer != msg.sender)) revert NotBuyer();
        if (block.timestamp > o.fillDeadline || block.timestamp >= o.expiry) revert TooLate();
        _checkStockControls(o.underlying, o.stockMultiplier);
        o.buyer = msg.sender;
        o.state = State.Active;
        _pullExact(address(usdg), msg.sender, o.premium);
        _release(address(usdg), o.writer, o.premium, o.writer);
        _checkStockControls(o.underlying, o.stockMultiplier);
        emit Filled(id, msg.sender, o.premium);
    }

    /// @notice After an in-the-money settlement, deliver exactly `size` stock tokens and take all locked USDG.
    ///         If no one fixed the price, this call can determine it using the round current at expiry.
    /// @param to Recipient of USDG; zero means the buyer. If delivery fails, USDG is owed to the buyer.
    function exercise(uint256 id, uint80 roundId, address to) external nonReentrant {
        Option storage o = _options[id];
        if (msg.sender != o.buyer) revert NotBuyer();
        if (to == address(this)) revert BadRecipient();
        if (o.state == State.Active) {
            _determine(id, o, _oraclePrice(o.feed, o.feedDecimals, o.expiry, roundId), PriceSource.Oracle);
        }
        if (o.state != State.Exercisable) revert NotInTheMoney();
        if (block.timestamp > o.exerciseDeadline) revert TooLate();
        if (to == address(0)) to = msg.sender;

        o.state = State.Exercised;
        _pullExact(o.underlying, msg.sender, o.size);
        _release(o.underlying, o.writer, o.size, o.writer);
        _release(address(usdg), to, o.collateral, msg.sender);
        emit Exercised(id, to, o.size, o.collateral);
    }

    function settle(uint256 id, uint80 roundId) external nonReentrant {
        Option storage o = _options[id];
        if (o.state != State.Active) revert WrongState();
        _determine(id, o, _oraclePrice(o.feed, o.feedDecimals, o.expiry, roundId), PriceSource.Oracle);
    }

    function lapse(uint256 id) external nonReentrant {
        Option storage o = _options[id];
        if (o.state != State.Exercisable) revert WrongState();
        if (block.timestamp <= o.exerciseDeadline) revert TooEarly();
        o.state = State.Lapsed;
        emit Lapsed(id);
        _release(address(usdg), o.writer, o.collateral, o.writer);
    }

    // Mutual price agreement is usable when the feed cannot establish the round at expiry.
    function proposePrice(uint256 id, uint256 price) external nonReentrant {
        Option storage o = _options[id];
        if (o.state != State.Active) revert WrongState();
        if (block.timestamp <= o.expiry) revert TooEarly();
        if (msg.sender != o.writer && msg.sender != o.buyer) revert NotParty();
        if (price == 0) revert BadTerms();
        proposals[id] = Proposal(msg.sender, uint64(block.timestamp), price.toUint128());
        emit PriceProposed(id, msg.sender, price);
    }

    function withdrawProposal(uint256 id) external nonReentrant {
        if (proposals[id].by != msg.sender) revert NoProposal();
        delete proposals[id];
        emit ProposalWithdrawn(id);
    }

    function acceptPrice(uint256 id, uint256 price) external nonReentrant {
        Option storage o = _options[id];
        if (o.state != State.Active) revert WrongState();
        Proposal memory p = proposals[id];
        if (p.by == address(0) || p.price != price || block.timestamp > uint256(p.at) + PROPOSAL_TTL) {
            revert NoProposal();
        }
        address counterparty = p.by == o.writer ? o.buyer : o.writer;
        if (msg.sender != counterparty) revert NotParty();
        _determine(id, o, price, msg.sender == o.buyer ? PriceSource.AcceptedByBuyer : PriceSource.AcceptedByWriter);
    }

    /// @notice Trusted last resort, at least 14 days after expiry. ITM opens a full three-day physical window.
    function backstopSettle(uint256 id, uint256 price) external onlyOwner nonReentrant {
        Option storage o = _options[id];
        if (o.state != State.Active) revert WrongState();
        if (block.timestamp <= uint256(o.expiry) + BACKSTOP_DELAY) revert TooEarly();
        if (price == 0) revert BadTerms();
        _determine(id, o, price, PriceSource.Backstop);
    }

    /// @notice Claim a failed payout to a different address, for example after the issuer freezes a wallet.
    function claim(address token, address to) external nonReentrant {
        if (to == address(0) || to == address(this)) revert BadRecipient();
        uint256 amount = owed[token][msg.sender];
        if (amount == 0) return;
        owed[token][msg.sender] = 0;
        reserved[token] -= amount;
        _transferExact(token, to, amount);
        emit Claimed(token, msg.sender, to, amount);
    }

    function getOption(uint256 id) external view returns (Option memory) {
        return _options[id];
    }

    function exerciseCost(uint256 id) external view returns (uint256) {
        return _options[id].collateral;
    }

    function settlementPriceOf(uint256 id, uint80 roundId) external view returns (uint256) {
        Option storage o = _options[id];
        if (o.feed == address(0)) revert WrongState();
        return _oraclePrice(o.feed, o.feedDecimals, o.expiry, roundId);
    }

    function listingPriceAt(address underlying, uint64 expiry, uint80 roundId) external view returns (uint256) {
        Listing memory l = listings[underlying];
        if (l.feed == address(0)) revert NotListed();
        return _oraclePrice(l.feed, l.feedDecimals, expiry, roundId);
    }

    function _determine(uint256 id, Option storage o, uint256 price, PriceSource source) internal {
        o.settlementPrice = price.toUint128();
        delete proposals[id];
        emit Determined(id, price, source);
        if (price >= o.strike) {
            o.state = State.ExpiredOTM;
            emit ExpiredOTM(id);
            _release(address(usdg), o.writer, o.collateral, o.writer);
            return;
        }

        uint64 deadline;
        if (source == PriceSource.Oracle) {
            deadline = (uint256(o.expiry) + SETTLE_DELAY + o.exerciseWindow).toUint64();
        } else {
            uint32 window = source == PriceSource.AcceptedByBuyer ? o.exerciseWindow : MAX_EXERCISE_WINDOW;
            deadline = (block.timestamp + window).toUint64();
        }
        o.exerciseDeadline = deadline;
        o.state = State.Exercisable;
        emit ExerciseOpened(id, deadline);
    }

    /// @dev Same phase/successor proof as CoveredCallDesk: this must be the last feed round at expiry, not a
    ///      convenient earlier one. Standard Chainlink proxy required; SVR proxy cannot expose needed rounds.
    function _oraclePrice(address feedAddr, uint8 fd, uint64 expiry, uint80 roundId) internal view returns (uint256) {
        if (block.timestamp <= uint256(expiry) + SETTLE_DELAY) revert TooEarly();
        IAggregatorV3Rounds feed = IAggregatorV3Rounds(feedAddr);
        (uint80 rid, int256 answer,, uint256 t, uint80 answeredInRound) = feed.getRoundData(roundId);
        if (
            rid != roundId || answer <= 0 || t == 0 || t > expiry || expiry - t > MAX_SETTLEMENT_AGE
                || answeredInRound < rid
        ) revert BadRound();

        (uint80 latest,,,,) = feed.latestRoundData();
        if (latest != roundId) {
            uint256 phase = uint256(roundId) >> 64;
            uint256 tNext = _roundTime(feed, roundId + 1);
            if (tNext != 0 && tNext <= expiry) revert NotLastRoundBeforeExpiry();
            if ((uint256(latest) >> 64) != phase) {
                uint256 tPhase = _roundTime(feed, uint80(((phase + 1) << 64) | 1));
                if (tPhase == 0 || tPhase <= expiry) revert NotLastRoundBeforeExpiry();
            } else if (tNext == 0) {
                revert NotLastRoundBeforeExpiry();
            }
        }

        uint256 a = uint256(answer);
        uint8 ud = usdgDecimals;
        uint256 p = fd >= ud ? a / 10 ** (fd - ud) : a * 10 ** (ud - fd);
        if (p == 0) revert BadRound();
        return p;
    }

    function _roundTime(IAggregatorV3Rounds feed, uint80 id) internal view returns (uint256) {
        try feed.getRoundData(id) returns (uint80, int256, uint256, uint256 t, uint80) {
            return t;
        } catch {
            return 0;
        }
    }

    function _checkStockControls(address underlying, uint256 expectedMultiplier) internal view {
        bool oracleIsPaused;
        uint256 currentMultiplier;
        try IPutStockControls(underlying).oraclePaused() returns (bool p) {
            oracleIsPaused = p;
        } catch {
            revert StockControlsUnavailable();
        }
        try IPutStockControls(underlying).uiMultiplier() returns (uint256 m) {
            currentMultiplier = m;
        } catch {
            revert StockControlsUnavailable();
        }
        if (oracleIsPaused) revert StockControlsPaused();
        if (currentMultiplier != expectedMultiplier) revert StockMultiplierChanged();
    }

    function _pullExact(address token, address from, uint256 amount) internal {
        uint256 before = IERC20(token).balanceOf(address(this));
        IERC20(token).safeTransferFrom(from, address(this), amount);
        if (IERC20(token).balanceOf(address(this)) - before != amount) revert Unexpected();
        reserved[token] += amount;
    }

    function _release(address token, address to, uint256 amount, address creditTo) internal {
        if (amount == 0) return;
        uint256 before = IERC20(token).balanceOf(to);
        if (IERC20(token).trySafeTransfer(to, amount)) {
            if (IERC20(token).balanceOf(to) - before != amount) revert Unexpected();
            reserved[token] -= amount;
        } else {
            owed[token][creditTo] += amount;
            emit Owed(token, creditTo, amount);
        }
    }

    function _transferExact(address token, address to, uint256 amount) internal {
        uint256 before = IERC20(token).balanceOf(to);
        IERC20(token).safeTransfer(to, amount);
        if (IERC20(token).balanceOf(to) - before != amount) revert Unexpected();
    }
}

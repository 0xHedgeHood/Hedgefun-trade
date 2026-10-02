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

interface IDeskStockControls {
    function oraclePaused() external view returns (bool);
    function uiMultiplier() external view returns (uint256);
}

/// @title PhysicalCallDesk
/// @notice Fully collateralised, European physically settled covered calls on Robinhood Chain stock tokens,
///         written by allowlisted treasuries and bought by allowlisted market makers after an off-chain RFQ.
/// @dev This is a separate deployment from CoveredCallDesk V1. It preserves the V1 ABI and Terms/Option layouts so
///      existing vaults can call it, but refuses NetShare offers and never uses NetShare at the delayed backstop.
///
/// One option's life:
///   1. `offer`   -- the writer locks `size` of the stock token here, with the terms agreed in the RFQ
///                   (buyer, strike, premium, expiry, fill deadline, price feed, exercise window,
///                   stock multiplier). The feed and window must equal the listing's and the desk's at that moment,
///                   and the stock must be unpaused at the named multiplier, so a configuration or corporate-action
///                   race reverts instead of silently changing what the writer priced. Those terms then live in the
///                   option and nothing the owner changes reaches them.
///   2. `fill`    -- the buyer pays the premium in USDG; it is forwarded to the writer in the same call.
///      `cancel`  -- until it is filled, the writer can take the stock back.
///   3. after expiry the price is fixed ONCE: from the option's Chainlink feed at the round that was current at
///      expiry (`settle` by anyone, or `exercise` by the buyer, from `SETTLE_DELAY` after expiry); or at any time
///      after expiry by the two parties agreeing a price (`proposePrice` + `acceptPrice`); or -- only if none of that
///      has happened `BACKSTOP_DELAY` after expiry -- by the owner (`backstopSettle`).
///        price <= strike                 -> the stock goes back to the writer.
///        price >  strike                 -> the buyer may pay size*strike USDG (`exercise`) and take the whole size
///                                           until the exercise deadline; after it, `lapse` returns it to the writer.
///                                           An oracle-priced option's deadline is fixed at
///                                           `expiry + SETTLE_DELAY + exerciseWindow`, so neither party can choose
///                                           when its clock starts. A price the BUYER accepts opens the option's own
///                                           window; one the WRITER accepts opens MAX_EXERCISE_WINDOW. A backstop
///                                           price also opens MAX_EXERCISE_WINDOW. No stock is sent to the buyer
///                                           until the full strike is paid, even at the backstop.
///
/// Trust. Nobody -- the owner included -- can move stock or USDG that is locked for an option or owed to someone,
/// change an option's terms, feed, window or multiplier after `offer`, or stop `cancel`, `settle`, `exercise`, `lapse`
/// or `claim`
/// (pausing only stops new `offer`s and `fill`s). The owner's powers: the allowlists, listings and the exercise window
/// that FUTURE offers must name, sweeping tokens nobody is owed, and the backstop -- which is trusted: 14 days after an
/// expiry that neither side has settled or agreed, the owner names the price, whether or not the oracle could have.
///
/// Prices: strike and settlement price are USDG base units per ONE WHOLE stock token (1e18 wei). The Robinhood
/// equity feeds already include the token's uiMultiplier, so a per-token strike needs no adjustment for dividends
/// or splits. The feed is quoted in USD and USDG is taken at $1. List the STANDARD Chainlink proxy for a stock, never
/// its SVR (secondary) proxy: the SVR view hides fresh rounds, which would let two different rounds each look like
/// "the last before expiry" at different times.
///
/// Delivery: every payout is attempted, and one that the token refuses (a frozen or blocked address) is credited
/// here instead and can be `claim`ed to any other address, so one party's frozen wallet cannot lock the other's side.
/// (A freeze of this contract itself by the token's issuer locks everything in that token; nothing here can help.)
contract PhysicalCallDesk is Ownable2Step, ReentrancyGuard {
    using SafeERC20 for IERC20;
    using SafeCast for uint256;

    enum State {
        None,
        Offered, // stock locked, waiting for the buyer
        Cancelled, // writer took it back before a fill
        Active, // premium paid, waiting for expiry
        Exercisable, // Physical and in the money: buyer may exercise until `exerciseDeadline`
        Exercised, // buyer paid the strike and took the stock
        ExpiredOTM, // price <= strike: stock returned to the writer
        NetSettled, // retained for V1 ABI compatibility; unreachable in this deployment
        Lapsed // Physical, in the money, not exercised in time: stock returned to the writer
    }

    enum Settlement {
        Physical,
        NetShare // retained for V1 ABI compatibility; `offer` rejects it
    }

    enum PriceSource {
        Oracle,
        AcceptedByBuyer, // the writer proposed, the buyer accepted
        AcceptedByWriter, // the buyer proposed, the writer accepted -- and so chose when the clock starts
        Backstop
    }

    struct Listing {
        address feed; // the STANDARD Chainlink <stock> / USD proxy, which includes the uiMultiplier
        uint8 feedDecimals;
        bool enabled; // new offers only
    }

    /// @notice what the writer and the buyer agreed in the RFQ
    struct Terms {
        address buyer; // the RFQ winner; address(0) lets any allowlisted buyer fill
        address underlying; // a listed stock token
        uint128 size; // stock token wei (18 decimals)
        uint128 strike; // USDG base units per whole stock token
        uint128 premium; // USDG base units, for the whole size
        uint64 expiry; // unix seconds; European, and the price is the feed's at this instant
        uint64 fillDeadline; // the buyer must fill by this time (<= expiry)
        Settlement mode; // must be Physical; retained for V1 ABI compatibility
        address feed; // must equal the listing's feed: the writer names the oracle it agreed to
        uint32 exerciseWindow; // must equal the desk's `exerciseWindow`: the writer names the tail it priced
        uint256 stockMultiplier; // exact uiMultiplier the RFQ was priced against
    }

    struct Option {
        address writer;
        uint64 expiry;
        State state;
        Settlement mode;
        uint8 feedDecimals;
        address buyer;
        uint64 fillDeadline;
        uint32 exerciseWindow;
        address underlying;
        uint64 exerciseDeadline;
        address feed;
        uint128 size;
        uint128 strike;
        uint128 premium;
        uint128 settlementPrice;
        uint256 stockMultiplier;
    }

    struct Proposal {
        address by;
        uint64 at;
        uint128 price;
    }

    /// @notice the oldest round that may price an expiry. The equity feeds have a 24h heartbeat while the market is
    ///         open and none while it is shut, so an expiry inside a closure fails this and goes to the fallbacks.
    uint256 public constant MAX_SETTLEMENT_AGE = 26 hours;
    /// @notice the oracle may price an expiry only this long after it, so every round up to expiry is visible
    uint256 public constant SETTLE_DELAY = 5 minutes;
    /// @notice how long after expiry the owner must wait before it may price an option neither side has settled
    uint256 public constant BACKSTOP_DELAY = 14 days;
    /// @notice a proposed price can be accepted for this long; an agreement is always open to both sides, oracle or not
    uint256 public constant PROPOSAL_TTL = 1 days;
    uint256 public constant MAX_TENOR = 400 days;
    uint32 public constant MIN_EXERCISE_WINDOW = 30 minutes;
    uint32 public constant MAX_EXERCISE_WINDOW = 3 days;

    IERC20 public immutable usdg;
    uint8 public immutable usdgDecimals;
    /// @notice optional: when set, an expiry must fall while this calendar says the market is open
    ITradingCalendar public immutable calendar;

    mapping(address underlying => Listing) public listings;
    mapping(address => bool) public isWriter;
    mapping(address => bool) public isBuyer;
    bool public paused;
    /// @notice copied into each option at `offer`; changing it never touches an option already written
    uint32 public exerciseWindow = 2 hours;
    uint256 public nextId = 1;

    /// @notice tokens held here that belong to someone: locked stock, plus USDG and stock owed but undelivered
    mapping(address token => uint256) public reserved;
    /// @notice payouts the token refused to deliver, claimable by their owner to any other address
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
        uint64 expiry,
        uint64 fillDeadline,
        Settlement mode,
        address feed,
        uint256 stockMultiplier
    );
    event Cancelled(uint256 indexed id);
    event Filled(uint256 indexed id, address indexed buyer, uint256 premium);
    event PriceProposed(uint256 indexed id, address indexed by, uint256 price);
    event ProposalWithdrawn(uint256 indexed id);
    event Determined(uint256 indexed id, uint256 price, PriceSource source);
    event ExpiredOTM(uint256 indexed id);
    event NetSettled(uint256 indexed id, uint256 toBuyer, uint256 toWriter);
    event ExerciseOpened(uint256 indexed id, uint64 deadline);
    event Exercised(uint256 indexed id, address indexed to, uint256 cost);
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
        usdg = usdg_;
        usdgDecimals = IERC20Metadata(address(usdg_)).decimals();
        calendar = calendar_;
    }

    // ------------------------------------------------------------------------------------------------------------
    // Owner
    // ------------------------------------------------------------------------------------------------------------

    /// @notice list, re-list or disable a stock for NEW offers. An option already written keeps the feed it was
    ///         written with.
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

    /// @notice stops new offers and fills. Nothing already written is affected.
    function setPaused(bool p) external onlyOwner {
        paused = p;
        emit PausedSet(p);
    }

    function setExerciseWindow(uint32 window) external onlyOwner {
        if (window < MIN_EXERCISE_WINDOW || window > MAX_EXERCISE_WINDOW) revert BadTerms();
        exerciseWindow = window;
        emit ExerciseWindowSet(window);
    }

    /// @notice send away tokens that arrived here by mistake. Never reaches anything `reserved`.
    function sweep(address token, address to) external onlyOwner nonReentrant {
        uint256 bal = IERC20(token).balanceOf(address(this));
        uint256 r = reserved[token];
        if (bal <= r) return;
        _transferExact(token, to, bal - r);
        emit Swept(token, to, bal - r);
    }

    /// @notice the backstop needs an owner; losing it would leave a disputed option with no way out
    function renounceOwnership() public pure override {
        revert RenounceDisabled();
    }

    // ------------------------------------------------------------------------------------------------------------
    // Writer
    // ------------------------------------------------------------------------------------------------------------

    /// @notice lock `t.size` of `t.underlying` and publish the terms agreed in the RFQ
    function offer(Terms calldata t) external nonReentrant returns (uint256 id) {
        if (paused) revert IsPaused();
        if (!isWriter[msg.sender]) revert NotWriter();
        Listing memory l = listings[t.underlying];
        if (!l.enabled) revert NotListed();
        if (t.feed != l.feed || t.exerciseWindow != exerciseWindow || t.stockMultiplier == 0) revert BadTerms();
        if (t.buyer != address(0) && !isBuyer[t.buyer]) revert NotBuyer();
        if (t.mode != Settlement.Physical || t.size == 0 || t.strike == 0 || t.premium == 0) revert BadTerms();
        if (t.expiry <= block.timestamp || t.expiry > block.timestamp + MAX_TENOR) revert BadTerms();
        if (t.fillDeadline < block.timestamp || t.fillDeadline > t.expiry) revert BadTerms();
        if (address(calendar) != address(0) && calendar.isClosed(t.expiry)) revert ExpiryMarketClosed();
        _checkStockControls(t.underlying, t.stockMultiplier);

        id = nextId++;
        _options[id] = Option({
            writer: msg.sender,
            expiry: t.expiry,
            state: State.Offered,
            mode: t.mode,
            feedDecimals: l.feedDecimals,
            buyer: t.buyer,
            fillDeadline: t.fillDeadline,
            exerciseWindow: t.exerciseWindow,
            underlying: t.underlying,
            exerciseDeadline: 0,
            feed: l.feed,
            size: t.size,
            strike: t.strike,
            premium: t.premium,
            settlementPrice: 0,
            stockMultiplier: t.stockMultiplier
        });
        _pullExact(t.underlying, msg.sender, t.size);
        // A stock-token implementation may consult mutable issuer controls during transfer. Recheck after the
        // interaction so an offer cannot cross a corporate-action boundary in one call.
        _checkStockControls(t.underlying, t.stockMultiplier);
        emit Offered(
            id,
            msg.sender,
            t.buyer,
            t.underlying,
            t.size,
            t.strike,
            t.premium,
            t.expiry,
            t.fillDeadline,
            t.mode,
            l.feed,
            t.stockMultiplier
        );
    }

    /// @notice take the stock back from an offer nobody has filled -- at any time, before or after its deadline
    function cancel(uint256 id) external nonReentrant {
        Option storage o = _options[id];
        if (o.state != State.Offered) revert WrongState();
        if (msg.sender != o.writer) revert NotWriter();
        o.state = State.Cancelled;
        emit Cancelled(id);
        _release(o.underlying, o.writer, o.size, o.writer);
    }

    // ------------------------------------------------------------------------------------------------------------
    // Buyer
    // ------------------------------------------------------------------------------------------------------------

    /// @notice pay the premium and own the option. The premium is forwarded to the writer in this call.
    function fill(uint256 id) external nonReentrant {
        if (paused) revert IsPaused();
        Option storage o = _options[id];
        if (o.state != State.Offered) revert WrongState();
        if (!isBuyer[msg.sender] || (o.buyer != address(0) && o.buyer != msg.sender)) revert NotBuyer();
        if (block.timestamp > o.fillDeadline || block.timestamp >= o.expiry) revert TooLate();
        _checkStockControls(o.underlying, o.stockMultiplier);
        o.buyer = msg.sender;
        o.state = State.Active;
        emit Filled(id, msg.sender, o.premium);
        _pullExact(address(usdg), msg.sender, o.premium);
        _release(address(usdg), o.writer, o.premium, o.writer);
        _checkStockControls(o.underlying, o.stockMultiplier);
    }

    /// @notice Physical mode, in the money: pay size*strike USDG and take the whole size, delivered to `to`.
    ///         If nobody has fixed the price yet this call does it first, from the round current at expiry.
    ///         Stock `to` cannot receive is credited to the buyer, not to `to`.
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
        uint256 cost = exerciseCost(id);
        emit Exercised(id, to, cost);
        _pullExact(address(usdg), msg.sender, cost);
        _release(address(usdg), o.writer, cost, o.writer);
        _release(o.underlying, to, o.size, msg.sender);
    }

    // ------------------------------------------------------------------------------------------------------------
    // Settlement -- anyone
    // ------------------------------------------------------------------------------------------------------------

    /// @notice fix the price of an expired option from its feed's round that was current at expiry.
    /// @param roundId the LAST round of the option's feed with updatedAt <= expiry (see `settlementPriceOf`)
    function settle(uint256 id, uint80 roundId) external nonReentrant {
        Option storage o = _options[id];
        if (o.state != State.Active) revert WrongState();
        _determine(id, o, _oraclePrice(o.feed, o.feedDecimals, o.expiry, roundId), PriceSource.Oracle);
    }

    /// @notice Physical, in the money, and the buyer let the deadline pass: the stock goes back to the writer
    function lapse(uint256 id) external nonReentrant {
        Option storage o = _options[id];
        if (o.state != State.Exercisable) revert WrongState();
        if (block.timestamp <= o.exerciseDeadline) revert TooEarly();
        o.state = State.Lapsed;
        emit Lapsed(id);
        _release(o.underlying, o.writer, o.size, o.writer);
    }

    // ------------------------------------------------------------------------------------------------------------
    // Agreement and backstop -- the way out when the oracle cannot price an expiry, open to both sides at any time
    // ------------------------------------------------------------------------------------------------------------

    /// @notice writer or buyer proposes a settlement price (USDG base units per whole token), acceptable by the
    ///         other side for PROPOSAL_TTL. A new proposal replaces the old one.
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

    /// @notice the OTHER side accepts the standing proposal within PROPOSAL_TTL; `price` must match it, so a
    ///         proposal swapped in the same block cannot be accepted by accident
    function acceptPrice(uint256 id, uint256 price) external nonReentrant {
        Option storage o = _options[id];
        if (o.state != State.Active) revert WrongState();
        Proposal memory p = proposals[id];
        if (p.by == address(0) || p.price != price || block.timestamp > uint256(p.at) + PROPOSAL_TTL) {
            revert NoProposal();
        }
        address other = p.by == o.writer ? o.buyer : o.writer;
        if (msg.sender != other) revert NotParty();
        _determine(id, o, price, msg.sender == o.buyer ? PriceSource.AcceptedByBuyer : PriceSource.AcceptedByWriter);
    }

    /// @notice Last resort, trusted: BACKSTOP_DELAY after an expiry that neither side has settled or agreed,
    ///         the owner names the price. An ITM price opens a three-day physical exercise window.
    function backstopSettle(uint256 id, uint256 price) external onlyOwner nonReentrant {
        Option storage o = _options[id];
        if (o.state != State.Active) revert WrongState();
        if (block.timestamp <= uint256(o.expiry) + BACKSTOP_DELAY) revert TooEarly();
        if (price == 0) revert BadTerms();
        _determine(id, o, price, PriceSource.Backstop);
    }

    // ------------------------------------------------------------------------------------------------------------
    // Claims
    // ------------------------------------------------------------------------------------------------------------

    /// @notice collect a payout the token refused to deliver, to any address but this one
    function claim(address token, address to) external nonReentrant {
        if (to == address(this)) revert BadRecipient();
        uint256 amount = owed[token][msg.sender];
        if (amount == 0) return;
        owed[token][msg.sender] = 0;
        reserved[token] -= amount;
        _transferExact(token, to, amount);
        emit Claimed(token, msg.sender, to, amount);
    }

    // ------------------------------------------------------------------------------------------------------------
    // Views
    // ------------------------------------------------------------------------------------------------------------

    function getOption(uint256 id) external view returns (Option memory) {
        return _options[id];
    }

    /// @notice USDG a Physical exercise costs: size * strike, rounded up in the writer's favour
    function exerciseCost(uint256 id) public view returns (uint256) {
        Option storage o = _options[id];
        return Math.mulDiv(o.size, o.strike, 1e18, Math.Rounding.Ceil);
    }

    /// @notice the price `settle(id, roundId)` would fix -- lets a keeper check its round before sending
    function settlementPriceOf(uint256 id, uint80 roundId) external view returns (uint256) {
        Option storage o = _options[id];
        if (o.feed == address(0)) revert WrongState();
        return _oraclePrice(o.feed, o.feedDecimals, o.expiry, roundId);
    }

    /// @notice the same check against a stock's CURRENT listing, for an arbitrary expiry (quoting and ops tooling)
    function listingPriceAt(address underlying, uint64 expiry, uint80 roundId) external view returns (uint256) {
        Listing memory l = listings[underlying];
        if (l.feed == address(0)) revert NotListed();
        return _oraclePrice(l.feed, l.feedDecimals, expiry, roundId);
    }

    // ------------------------------------------------------------------------------------------------------------
    // Internals
    // ------------------------------------------------------------------------------------------------------------

    function _determine(uint256 id, Option storage o, uint256 price, PriceSource source) internal {
        o.settlementPrice = price.toUint128();
        delete proposals[id];
        emit Determined(id, price, source);

        uint256 strike = o.strike;
        if (price <= strike) {
            o.state = State.ExpiredOTM;
            emit ExpiredOTM(id);
            _release(o.underlying, o.writer, o.size, o.writer);
        } else {
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
    }

    /// @dev the answer of `feed`'s round that was current at `expiry`, in USDG base units per whole token.
    ///      Callable from SETTLE_DELAY after expiry. `roundId` must have updatedAt <= expiry, be at most
    ///      MAX_SETTLEMENT_AGE old at expiry, and be the last such round: the feed's latest round, or one whose
    ///      successor was published after expiry. The successor in the same phase is `roundId + 1`; if a newer phase
    ///      (aggregator) exists, its first round must also postdate expiry. The proxy's switch to a new aggregator is
    ///      not visible on chain: if the new aggregator was already reporting before expiry, its last round before
    ///      expiry is the one accepted, even when the proxy was still serving the old one at that moment. Both
    ///      aggregators report the same market, so the two candidates differ by at most one update's movement.
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
                revert NotLastRoundBeforeExpiry(); // same phase, not the latest, yet no successor: refuse
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

    /// @dev Quotes bind the economic meaning of one raw stock token. Corporate-action pauses and multiplier changes
    ///      fail closed for new offers/fills, while every exit and settlement path remains available.
    function _checkStockControls(address underlying, uint256 expectedMultiplier) internal view {
        bool oracleIsPaused;
        uint256 currentMultiplier;
        try IDeskStockControls(underlying).oraclePaused() returns (bool p) {
            oracleIsPaused = p;
        } catch {
            revert StockControlsUnavailable();
        }
        try IDeskStockControls(underlying).uiMultiplier() returns (uint256 m) {
            currentMultiplier = m;
        } catch {
            revert StockControlsUnavailable();
        }
        if (oracleIsPaused) revert StockControlsPaused();
        if (currentMultiplier != expectedMultiplier) revert StockMultiplierChanged();
    }

    /// @dev pull exactly `amount`; a token that delivers less (a transfer fee) is refused
    function _pullExact(address token, address from, uint256 amount) internal {
        uint256 before = IERC20(token).balanceOf(address(this));
        IERC20(token).safeTransferFrom(from, address(this), amount);
        if (IERC20(token).balanceOf(address(this)) - before != amount) revert Unexpected();
        reserved[token] += amount;
    }

    /// @dev pay `to` out of `reserved`; if the token refuses, the amount stays reserved and is owed to `creditTo`
    function _release(address token, address to, uint256 amount, address creditTo) internal {
        if (amount == 0) return;
        uint256 senderBefore = IERC20(token).balanceOf(address(this));
        uint256 recipientBefore = IERC20(token).balanceOf(to);
        if (IERC20(token).trySafeTransfer(to, amount)) {
            uint256 senderAfter = IERC20(token).balanceOf(address(this));
            uint256 recipientAfter = IERC20(token).balanceOf(to);
            if (
                senderBefore < amount || senderAfter != senderBefore - amount || recipientAfter < recipientBefore
                    || recipientAfter - recipientBefore != amount
            ) revert Unexpected();
            reserved[token] -= amount;
        } else {
            // A non-reverting token can transfer value while returning false. In that case recording an owed
            // amount would double-count collateral, so the entire transaction must revert.
            if (
                IERC20(token).balanceOf(address(this)) != senderBefore || IERC20(token).balanceOf(to) != recipientBefore
            ) revert Unexpected();
            owed[token][creditTo] += amount;
            emit Owed(token, creditTo, amount);
        }
    }

    /// @dev A successful transfer must deliver every token promised. In particular, fee-on-transfer stock cannot
    ///      silently turn a physical exercise into partial delivery.
    function _transferExact(address token, address to, uint256 amount) internal {
        uint256 senderBefore = IERC20(token).balanceOf(address(this));
        uint256 recipientBefore = IERC20(token).balanceOf(to);
        IERC20(token).safeTransfer(to, amount);
        uint256 senderAfter = IERC20(token).balanceOf(address(this));
        uint256 recipientAfter = IERC20(token).balanceOf(to);
        if (
            senderBefore < amount || senderAfter != senderBefore - amount || recipientAfter < recipientBefore
                || recipientAfter - recipientBefore != amount
        ) revert Unexpected();
    }
}

// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {Math} from "@openzeppelin/contracts/utils/math/Math.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";

/// @notice Stake a launch token, earn the stock its treasury actually transfers in. One instance per treasury.
/// @dev Deployed by an income-kind treasury in its own constructor, which is the immutable `incomeSource`.
///      Only that source may fund rewards, with a real transfer of a different asset than the staked one.
///      This contract enforces funding and time-weighted allocation; what counts as income is the source's rule.
///      Rewards stream over `duration`; stakes cannot enter and exit in a single funding transaction.
///      No administrator can withdraw principal, rewards or donations. Rounding dust stays reserved.
///      Rewards funded while nothing is staked are queued and start streaming with the first stake.
contract V2StakingIncome is ReentrancyGuard {
    using SafeERC20 for IERC20;

    uint256 private constant SCALE = 1e27;
    uint256 public constant RATE_SCALE = 1e18;
    IERC20 public immutable stakeToken;
    IERC20 public immutable rewardToken;
    address public immutable incomeSource;
    uint256 public immutable duration;
    uint256 public immutable minimumStakeTime;

    uint256 public totalStaked;
    uint256 public totalFunded;
    uint256 public totalClaimed;
    /// @notice Sub-raw-unit scheduling remainder, in raw reward units times RATE_SCALE.
    uint256 public queuedRewardsScaled;
    /// @notice Raw reward units times RATE_SCALE emitted per second.
    uint256 public rewardRateScaled;
    uint256 public periodFinish;
    uint256 public lastUpdate;
    uint256 public rewardPerTokenStored;
    mapping(address => uint256) public balanceOf;
    mapping(address => uint256) public unlockAt;
    mapping(address => uint256) public userRewardPerTokenPaid;
    mapping(address => uint256) public accruedRewards;

    error InvalidConfig();
    error InvalidAmount();
    error InvalidRecipient();
    error NotIncomeSource();
    error StakeLocked();
    error InexactTransfer();

    event Staked(address indexed account, uint256 amount, uint256 unlockAt);
    event Withdrawn(address indexed account, address indexed recipient, uint256 amount);
    event IncomeFunded(uint256 amount, uint256 queued, uint256 rate, uint256 finish);
    event RewardPaid(address indexed account, address indexed recipient, uint256 amount);

    constructor(IERC20 stake_, IERC20 reward_, address source_, uint256 duration_, uint256 lock_) {
        if (address(stake_).code.length == 0 || address(reward_).code.length == 0
            || address(stake_) == address(reward_) || source_ == address(0)
            || duration_ < 1 hours || duration_ > 30 days || lock_ < 1 hours || lock_ > 30 days) {
            revert InvalidConfig();
        }
        stakeToken = stake_;
        rewardToken = reward_;
        incomeSource = source_;
        duration = duration_;
        minimumStakeTime = lock_;
        lastUpdate = block.timestamp;
        periodFinish = block.timestamp;
    }

    function rewardPerToken() public view returns (uint256) {
        uint256 through = Math.min(block.timestamp, periodFinish);
        if (totalStaked == 0 || through <= lastUpdate) return rewardPerTokenStored;
        return rewardPerTokenStored + Math.mulDiv((through - lastUpdate) * rewardRateScaled, SCALE / RATE_SCALE, totalStaked);
    }

    function earned(address account) public view returns (uint256) {
        return accruedRewards[account]
            + Math.mulDiv(balanceOf[account], rewardPerToken() - userRewardPerTokenPaid[account], SCALE);
    }

    function stake(uint256 amount) external nonReentrant {
        if (amount == 0) revert InvalidAmount();
        _update(msg.sender);
        _pullExact(stakeToken, msg.sender, amount);
        balanceOf[msg.sender] += amount;
        totalStaked += amount;
        // Only the depositor can extend its own lock; no stakeFor/griefing entry point.
        unlockAt[msg.sender] = block.timestamp + minimumStakeTime;
        if (rewardRateScaled == 0 && queuedRewardsScaled != 0) _schedule(queuedRewardsScaled);
        emit Staked(msg.sender, amount, unlockAt[msg.sender]);
    }

    /// @notice Principal withdrawal does not attempt a reward payment; a blocked reward token cannot trap FUN.
    function withdraw(uint256 amount, address to) external nonReentrant {
        if (amount == 0 || amount > balanceOf[msg.sender]) revert InvalidAmount();
        _recipient(to);
        if (block.timestamp < unlockAt[msg.sender]) revert StakeLocked();
        _update(msg.sender);
        balanceOf[msg.sender] -= amount;
        totalStaked -= amount;
        if (totalStaked == 0) {
            queuedRewardsScaled += _remaining();
            rewardRateScaled = 0;
            periodFinish = block.timestamp;
            lastUpdate = block.timestamp;
        }
        _pushExact(stakeToken, to, amount);
        emit Withdrawn(msg.sender, to, amount);
    }

    /// @notice Newly funded rewards and any unvested remainder stream for a fresh duration.
    /// @dev The source controls funding timing; repeated funding can extend the stream, never claw back accrued rewards.
    function fund(uint256 amount) external nonReentrant {
        if (msg.sender != incomeSource) revert NotIncomeSource();
        if (amount == 0) revert InvalidAmount();
        _update(address(0));
        _pullExact(rewardToken, msg.sender, amount);
        totalFunded += amount;
        _schedule(amount * RATE_SCALE + queuedRewardsScaled + _remaining());
        emit IncomeFunded(amount, queuedRewardsScaled, rewardRateScaled, periodFinish);
    }

    function claim(address to) external nonReentrant returns (uint256 amount) {
        _recipient(to);
        _update(msg.sender);
        amount = accruedRewards[msg.sender];
        if (amount == 0) return 0;
        accruedRewards[msg.sender] = 0;
        totalClaimed += amount;
        _pushExact(rewardToken, to, amount);
        emit RewardPaid(msg.sender, to, amount);
    }

    function _remaining() private view returns (uint256) {
        return block.timestamp < periodFinish ? (periodFinish - block.timestamp) * rewardRateScaled : 0;
    }

    /// @dev budget, rate and queue are reward-token raw units scaled by RATE_SCALE.
    function _schedule(uint256 budget) private {
        lastUpdate = block.timestamp;
        if (totalStaked == 0 || budget < duration) {
            queuedRewardsScaled = budget;
            rewardRateScaled = 0;
            periodFinish = block.timestamp;
            return;
        }
        rewardRateScaled = budget / duration;
        queuedRewardsScaled = budget % duration;
        periodFinish = block.timestamp + duration;
    }

    function _update(address account) private {
        rewardPerTokenStored = rewardPerToken();
        uint256 through = Math.min(block.timestamp, periodFinish);
        if (through > lastUpdate) lastUpdate = through;
        if (account != address(0)) {
            accruedRewards[account] += Math.mulDiv(
                balanceOf[account], rewardPerTokenStored - userRewardPerTokenPaid[account], SCALE
            );
            userRewardPerTokenPaid[account] = rewardPerTokenStored;
        }
    }

    function _recipient(address to) private view {
        if (to == address(0) || to == address(this)) revert InvalidRecipient();
    }

    function _pullExact(IERC20 asset, address from, uint256 amount) private {
        uint256 beforeHere = asset.balanceOf(address(this));
        uint256 beforeThere = asset.balanceOf(from);
        asset.safeTransferFrom(from, address(this), amount);
        if (asset.balanceOf(address(this)) != beforeHere + amount
            || asset.balanceOf(from) != beforeThere - amount) revert InexactTransfer();
    }

    function _pushExact(IERC20 asset, address to, uint256 amount) private {
        uint256 beforeHere = asset.balanceOf(address(this));
        uint256 beforeThere = asset.balanceOf(to);
        asset.safeTransfer(to, amount);
        if (asset.balanceOf(address(this)) != beforeHere - amount
            || asset.balanceOf(to) != beforeThere + amount) revert InexactTransfer();
    }
}

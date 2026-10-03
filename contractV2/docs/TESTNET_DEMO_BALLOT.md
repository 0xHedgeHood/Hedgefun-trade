# Independent testnet demo poll

`src/demo/DemoBallot.sol` is a separate, non-binding poll for the hackathon demo.
It does not call the launch factory or treasury, delegate underlying stock votes,
change strategy rules, or implement X Money payments.

The constructor accepts `(token, candidateA, candidateB, deadline)` and only
deploys on Robinhood Chain Testnet, chain 46630. The token must be a contract;
candidate addresses must be nonzero and distinct. The deadline must be between
120 seconds and 30 days after the deployment block's timestamp.

Before the deadline, a wallet approves the ballot and calls `vote(choice, amount)`.
Choice 0 selects candidate A; choice 1 selects candidate B. Tokens move into the
ballot and stay locked. The same wallet may add tokens to its original choice,
but cannot change its choice or recover tokens while voting is open. A vote is
credited only after an exact token balance increase, preventing fee tokens and
failed transfers from receiving excess weight.

At the deadline, voting closes and `withdraw()` becomes available. It returns
only the caller's locked tokens. Both the ballot's exact balance decrease and
the caller's exact balance increase are checked. A failed or inexact transfer
reverts the withdrawal, retaining the caller's claim. There is no owner,
privileged withdrawal, early unlock, or token rescue function. Unsolicited token
donations are not credited and cannot be recovered through the ballot.

Read `locked(wallet)` to determine outstanding locked principal. `choiceOf` is
zero for an unvoted wallet, so it must not be used alone to identify a cast vote.
`votes(0)`, `votes(1)` and `totalVotes()` count locked token units during voting
and retain the final result after withdrawals. Their units are the token's raw
units, not a count of individual wallets. Candidate addresses are poll labels
and receive no funds or authority from this contract.

Events:

```solidity
event VoteCast(address indexed voter, uint8 indexed choice, uint256 amount);
event Withdrawn(address indexed voter, uint256 amount);
```

The immutable token must support exact, non-rebasing ERC20 transfers in both
directions. If its behavior later changes, transfers may be refused; the ballot
has no administrator who can replace the token or bypass that refusal.

Validation: `forge test --match-path test/DemoBallot.t.sol -vv` passes 25 tests,
including 256 fuzz cases. Coverage includes deadline boundaries, additional
deposits, fixed choices, transferring remaining tokens, vote backing, failed and
fee-charging transfers, no-op withdrawal transfers, reentry, caller-only
withdrawal and preservation of the final result. Tests send no transactions.

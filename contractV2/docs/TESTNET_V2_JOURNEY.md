# V2 testnet journey: creator wallet, curve, graduation, V4

This is the **dedicated Robinhood Chain testnet only** (chain 46630). The six phase functions in
[`script/TestnetV2Journey.s.sol`](../script/TestnetV2Journey.s.sol) refuse another chain, another sender, an unbroadcast
address book, or addresses other than this deployment's factory/router/tUSDG/GME pool. The creator is fixed to
`0xD4f69D180a9bc36F27D307E90E365d1E012816d5`. The operator is a different testnet wallet. No production token,
mainnet address or real money is used. The script holds no key and sends nothing unless the operator of this run
explicitly adds `--broadcast` and a testnet-only keystore.

The route deliberately exercises **tUSDG → GME in V3 → strategy curve**, then **strategy V4 pool → GME → tUSDG**.
GME here is the test stock in the address book, not Robinhood's faucet GME. The launch uses the deployed factory's
25 tUSDG fee, a 44% default curve sale and a three-second opening window. Wait at least four seconds after launch
before the first curve buy; the opening buy burn rate falls from 99% to 3% in that window. The GME V3 pool must have
a live 720-slot observation ring and be at least 10 minutes old before the launch. Check with
`python3 tools/v2_launch_check.py --testnet` and `--sale-bps 9000` before running.

## Rehearsal and broadcast

From the contract repository root, set these shell variables. `ACCOUNT` is the **encrypted, testnet-only creator
keystore**, never a mainnet keystore. Do not put a private key in a command line, environment variable, file, or chat.

```sh
export RPC=https://rpc.testnet.chain.robinhood.com
export CREATOR=0xD4f69D180a9bc36F27D307E90E365d1E012816d5
export ACCOUNT=YOUR_ENCRYPTED_TESTNET_CREATOR_KEYSTORE
export JOURNEY_ID=0 # replace with the id printed by launch() if the factory count changes

forge build
RUN_TESTNET_FORK=true forge test --match-contract TestnetV2JourneyForkTest --fork-url "$RPC" -vv
```

For **each** line below, first run the `forge script` command without `--account "$ACCOUNT" --broadcast --slow`.
It must end `SIMULATION COMPLETE` and show the expected new status and balances. Then append those three flags to
the same command to send its transactions, wait for its receipts, run `inspect()` against the live RPC, and continue
only if the readback matches. A `MIN_*` value is a nonzero absolute minimum: if the pool moves or taxes change, the
phase reverts instead of silently accepting less. The amounts below were exercised together against a live-RPC fork
on 2026-09-29. Re-run simulation immediately before each broadcast; do not lower a minimum merely to force a trade.

```sh
# 1. Launch GME strategy; exact predicted token/curve and id print before the transaction.
JOURNEY_NONCE=1 forge script script/TestnetV2Journey.s.sol:TestnetV2Journey \
  --sig 'launch()' --rpc-url "$RPC" --sender "$CREATOR" -vv

# 2. Curve buy: 100 tUSDG, at least 4 GME from V3 and 8 million strategy tokens.
JOURNEY_ID="$JOURNEY_ID" USDG_IN=100000000 MIN_STOCK_RECEIVED=4000000000000000000 \
MIN_FINAL_OUT=8000000000000000000000000 \
forge script script/TestnetV2Journey.s.sol:TestnetV2Journey \
  --sig 'curveBuy()' --rpc-url "$RPC" --sender "$CREATOR" -vv

# 3. Curve sell: 1 million strategy tokens, at least 7 tUSDG returned through V3.
JOURNEY_ID="$JOURNEY_ID" TOKEN_IN=1000000000000000000000000 MIN_FINAL_OUT=7000000 \
forge script script/TestnetV2Journey.s.sol:TestnetV2Journey \
  --sig 'curveSell()' --rpc-url "$RPC" --sender "$CREATOR" -vv

# 4. Buy through the curve ceiling, atomically graduating into V4. Unused GME is refunded.
JOURNEY_ID="$JOURNEY_ID" USDG_IN=20000000000 MIN_STOCK_RECEIVED=750000000000000000000 \
MIN_FINAL_OUT=350000000000000000000000000 \
forge script script/TestnetV2Journey.s.sol:TestnetV2Journey \
  --sig 'graduate()' --rpc-url "$RPC" --sender "$CREATOR" -vv

# 5. V4 buy: 100 tUSDG via V3 GME, at least 1 million strategy tokens.
JOURNEY_ID="$JOURNEY_ID" USDG_IN=100000000 MIN_STOCK_RECEIVED=4000000000000000000 \
MIN_FINAL_OUT=1000000000000000000000000 \
forge script script/TestnetV2Journey.s.sol:TestnetV2Journey \
  --sig 'v4Buy()' --rpc-url "$RPC" --sender "$CREATOR" -vv

# 6. V4 sell: 1 million strategy tokens, at least 5 tUSDG through V3 GME.
JOURNEY_ID="$JOURNEY_ID" TOKEN_IN=1000000000000000000000000 MIN_FINAL_OUT=5000000 \
forge script script/TestnetV2Journey.s.sol:TestnetV2Journey \
  --sig 'v4Sell()' --rpc-url "$RPC" --sender "$CREATOR" -vv
```

The script checks balances, stage and strategy ownership before every write. It limits each test trade's input:
curve buy at most 500 tUSDG; graduation 8,000–25,000 tUSDG; V4 buy at most 1,000 tUSDG; each sell at most half or
one quarter of the creator's current token balance, respectively. `inspect()` prints the exact live token, curve,
status, tUSDG/GME/strategy-token balances and curve reserve:

```sh
JOURNEY_ID="$JOURNEY_ID" forge script script/TestnetV2Journey.s.sol:TestnetV2Journey \
  --sig 'inspect()' --rpc-url "$RPC" -vv
```

## Receipt record

Foundry stores the actual broadcast receipts in
`broadcast/TestnetV2Journey.s.sol/46630/<phase>-latest.json`. Before the next phase overwrites that phase's
`latest` file, retain its JSON in a run directory. Run the read-only report tool after each phase. It
checks every receipt and canonical transaction against the recorded calldata, exact target, creator,
strategy ID, route, stage and matching contract event. It reads balances and curve status at the action
transaction's block, so older phases can still be checked after graduation. Save its JSON output alongside
the receipts:

```sh
python3 tools/report_testnet_v2_journey.py --phase launch --id "$JOURNEY_ID" \
  --receipts broadcast/TestnetV2Journey.s.sol/46630/launch-latest.json
```

The final report should contain all receipt hashes, the strategy id, token/curve/treasury addresses, the exact
balances after each phase, status `0` after launch and curve trades, status `2` after graduation and V4 trades,
and the explorer links (`https://explorer.testnet.chain.robinhood.com/tx/<hash>`). The six phases are separate so
live block time and oracle observation ages advance naturally. A failed simulation or receipt stops the sequence;
re-read chain state before retrying. Never assume a previous transaction failed just because a CLI process stopped.

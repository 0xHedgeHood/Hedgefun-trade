# Hedgefun contracts V1

This directory contains the Solidity source snapshot from commit `5c28050cae10e73166aa993bdfe3c2cbf0b71823`. The 22 files under `src/` retain their original contents and paths relative to the Foundry project. The compiler version, optimization settings, EVM version, metadata-hash setting, and dependency revisions are pinned to match that snapshot.

## Contracts

| File | Role |
| --- | --- |
| `src/HedgeFunFactory.sol`, `src/HedgeFunDeployers.sol` | Listing, launch, and per-strategy deployment |
| `src/HedgeFunToken.sol` | Fixed-supply strategy token and creator metadata |
| `src/hooks/HedgeFunHook.sol` | Shared Uniswap V4 hook for launch rules and swap tax |
| `src/HedgeFunTreasuryBase.sol`, `src/HedgeFunTreasury.sol`, `src/PoolTrader.sol` | Per-strategy trading rule and V3 execution |
| `src/HedgeFunLaunchRouter.sol`, `src/HedgeFunTradeRouter.sol` | Optional launch and trade entry points |
| `src/PriceOracle.sol`, `src/TradingCalendar.sol` | Price and market-calendar checks |

Interfaces and internal libraries remain in `src/interfaces/` and `src/libraries/`. The complete call paths should be reviewed together; the routers and deployer contracts are part of the published contract set.

## Build and test

Install [Foundry](https://getfoundry.sh/), then from the repository root:

```sh
git submodule update --init --recursive
cd contractV1
forge build
forge test
```

The included tests cover contract math, the trading calendar, and strategy-token metadata. Deployment and operations material is outside this source release.

`abi/` contains JSON ABIs generated from this source. Regenerate an ABI with `forge inspect HedgeFunFactory abi --json` (substitute the contract name as needed).

The strategy token does not give holders a claim on the treasury or a redemption right. Read the contract code and tests before integrating or deploying; this source snapshot alone is not a deployment record.

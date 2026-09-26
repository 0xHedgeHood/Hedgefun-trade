<div align="center">

# Hedgefun smart contracts

Solidity source for the Hedgefun strategy-token launchpad on Robinhood Chain.

[![License: MIT](https://img.shields.io/badge/license-MIT-1a2740?style=for-the-badge)](LICENSE)
[![Solidity 0.8.26](https://img.shields.io/badge/Solidity-0.8.26-1a2740?style=for-the-badge&logo=solidity&logoColor=white)](contractV1/foundry.toml)
[![Robinhood Chain](https://img.shields.io/badge/chain-Robinhood%20Chain-1a2740?style=for-the-badge)](#stack)

[![OpenZeppelin](https://img.shields.io/badge/dependency-OpenZeppelin-1a2740?style=flat-square)](#stack)
[![Uniswap V3](https://img.shields.io/badge/stock%20trades-Uniswap%20V3-1a2740?style=flat-square)](#stack)
[![Uniswap V4](https://img.shields.io/badge/strategy%20pool-Uniswap%20V4-1a2740?style=flat-square)](#stack)

</div>

The contracts live in [`contractV1/`](./contractV1/README.md). This is a self-contained Foundry project with source code, selected tests, ABIs, and pinned dependencies. V1 uses both Uniswap versions: a V4 pool for each strategy token and a V3 pool for stock/USDG execution.

The V2 source lives in [`contractV2/`](./contractV2/README.md), laid out the same way. V2 launches each strategy token on a stock-denominated bonding curve that graduates atomically into a locked V4 pool and a funded treasury. It is a separate deployment and is not yet deployed.

## Stack

| Component | Use in Hedgefun V1 |
| --- | --- |
| [OpenZeppelin Contracts](https://github.com/OpenZeppelin/openzeppelin-contracts) | ERC-20, ownership, token-transfer safety, reentrancy protection, and math |
| [Uniswap V4 core](https://github.com/Uniswap/v4-core) | Strategy-token pool, shared hook, and swap/liquidity types |
| Uniswap V3 pools | External stock/USDG execution; the required interfaces are in `contractV1/src/interfaces/IUniswapV3.sol` |
| [Foundry](https://getfoundry.sh/) | Reproducible Solidity build and tests |

The source snapshot is commit `5c28050cae10e73166aa993bdfe3c2cbf0b71823`. Contract source files are copied without Solidity changes. This repository omits deployment credentials, broadcast data, operations, and third-party reference source.

The original Hedgefun Solidity files are [MIT licensed](LICENSE). Git submodule dependencies retain their own licenses and copyright notices.

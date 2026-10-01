// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// A Chainlink push feed, with historical rounds. Round ids are `(phaseId << 64) | aggregatorRoundId`; a round that
/// does not exist yet in the current phase answers all zeros (it does not revert), and a round in a phase that does
/// not exist reverts. Both facts were checked against RHNVDA / USD on chain 4663 on 2026-09-29.
interface IAggregatorV3Rounds {
    function decimals() external view returns (uint8);

    function latestRoundData()
        external
        view
        returns (uint80 roundId, int256 answer, uint256 startedAt, uint256 updatedAt, uint80 answeredInRound);

    function getRoundData(uint80 roundId)
        external
        view
        returns (uint80 id, int256 answer, uint256 startedAt, uint256 updatedAt, uint80 answeredInRound);
}

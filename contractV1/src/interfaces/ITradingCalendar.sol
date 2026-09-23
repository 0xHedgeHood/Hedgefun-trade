// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

interface ITradingCalendar {
    function isClosed(uint256 ts) external view returns (bool);
    function isScheduledClosure(uint256 ts) external view returns (bool);
}

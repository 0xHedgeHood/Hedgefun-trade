// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Script, console2} from "forge-std/Script.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {IERC20Metadata} from "@openzeppelin/contracts/token/ERC20/extensions/IERC20Metadata.sol";
import {EarnVault} from "../src/options/EarnVault.sol";
import {EarnVaultV3SwapAdapter} from "../src/options/EarnVaultV3SwapAdapter.sol";
import {CoveredCallDesk} from "../src/options/CoveredCallDesk.sol";
import {PhysicalCallDesk} from "../src/options/PhysicalCallDesk.sol";
import {CashSecuredPutDesk} from "../src/options/CashSecuredPutDesk.sol";
import {PriceOracle} from "../src/PriceOracle.sol";
import {ITradingCalendar} from "../src/interfaces/ITradingCalendar.sol";
import {IOwned} from "../src/interfaces/IOwned.sol";

interface IEarnSafe {
    function getThreshold() external view returns (uint256);
    function getOwners() external view returns (address[] memory);
}

/// @notice Deploys an inert, strictly physical RHNVDA RFQ desk and user-vault stack. The Safe must install
///         both adapter and put desk before the first user deposit, then configure listings and allowlists.
contract DeployEarnVault is Script {
    uint256 internal constant RH_CHAIN_ID = 4663;
    address internal constant SAFE = 0x2910117dd2cB431173Ae9Fb6eAF30726321d1693;
    address internal constant STOCK = 0xd0601CE157Db5bdC3162BbaC2a2C8aF5320D9EEC;
    address internal constant USDG = 0x5fc5360D0400a0Fd4f2af552ADD042D716F1d168;
    address internal constant CALENDAR = 0xFE9E85f0C258Fc2757eB6Acd1ca032Ec860487F5;
    address internal constant ORACLE = 0x03c77f527Aa1B0B304602e3fB9Ac994dd1c157f8;
    address internal constant POOL = 0xd4EB21209C4D6093f80B5b84f5C45cc093EA14a3;

    function check() public view {
        require(block.chainid == RH_CHAIN_ID, "wrong chain");
        require(
            SAFE.code.length != 0 && IEarnSafe(SAFE).getThreshold() == 3 && IEarnSafe(SAFE).getOwners().length == 4,
            "bad Safe"
        );
        require(STOCK.code.length != 0 && IERC20Metadata(STOCK).decimals() == 18, "bad stock");
        require(USDG.code.length != 0 && IERC20Metadata(USDG).decimals() == 6, "bad USDG");
        require(CALENDAR.code.length != 0 && IOwned(CALENDAR).owner() == SAFE, "bad calendar");
        require(
            ORACLE.code.length != 0 && PriceOracle(ORACLE).stock() == STOCK
                && address(PriceOracle(ORACLE).calendar()) == CALENDAR,
            "bad oracle"
        );
        require(POOL.code.length != 0, "bad V3 pool");
    }

    function run()
        external
        returns (PhysicalCallDesk callDesk, EarnVault vault, EarnVaultV3SwapAdapter adapter, CashSecuredPutDesk putDesk)
    {
        check();
        vm.startBroadcast();
        callDesk = new PhysicalCallDesk(SAFE, IERC20(USDG), ITradingCalendar(CALENDAR));
        // PhysicalCallDesk intentionally preserves CoveredCallDesk's ABI and Option layout.
        vault = new EarnVault(
            SAFE, IERC20(STOCK), IERC20(USDG), CoveredCallDesk(address(callDesk)), PriceOracle(ORACLE), POOL
        );
        adapter = new EarnVaultV3SwapAdapter(address(vault), USDG, STOCK, POOL, ORACLE, 50, 100);
        putDesk = new CashSecuredPutDesk(SAFE, IERC20(USDG), ITradingCalendar(CALENDAR));
        vm.stopBroadcast();

        require(
            callDesk.owner() == SAFE && address(callDesk.usdg()) == USDG && address(callDesk.calendar()) == CALENDAR
                && callDesk.nextId() == 1 && !callDesk.paused(),
            "call desk readback"
        );
        require(
            vault.owner() == SAFE && vault.totalSupply() == 0 && vault.currentEpoch() == 1
                && vault.activeOptionId() == 0 && address(vault.swapAdapter()) == address(0)
                && vault.reinvestPool() == POOL && address(vault.desk()) == address(callDesk),
            "vault readback"
        );
        require(
            adapter.vault() == address(vault) && address(adapter.usdg()) == USDG && address(adapter.stock()) == STOCK
                && adapter.maxDeviationBps() == 50 && adapter.maxSlippageBps() == 100,
            "adapter readback"
        );
        require(
            putDesk.owner() == SAFE && address(putDesk.usdg()) == USDG && address(putDesk.calendar()) == CALENDAR,
            "put desk readback"
        );

        console2.log("PhysicalCallDesk     ", address(callDesk));
        console2.log("EarnVault             ", address(vault));
        console2.log("EarnVaultV3SwapAdapter", address(adapter));
        console2.log("CashSecuredPutDesk   ", address(putDesk));
        console2.log("Safe owner            ", vault.owner());
        console2.log("physical call desk    ", address(vault.desk()));
        console2.log("oracle                ", address(vault.oracle()));
        console2.log("NEXT: review, verify, record addresses, then Safe installs put desk/adapter before any deposit.");
    }
}

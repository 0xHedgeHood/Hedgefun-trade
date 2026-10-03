// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {AllStrategyHistoricalReplayForkTest, IAllStrategyV3Position} from "./AllStrategyHistoricalReplayFork.t.sol";
import {CoveredCallDesk} from "../src/options/CoveredCallDesk.sol";
import {RoundFeed, Calendar} from "./CoveredCallDesk.t.sol";
import {FunStakingIncome} from "../src/experimental/FunStakingIncome.sol";
import {HedgeFunToken} from "../src/HedgeFunToken.sol";
import {HedgeFunV2TradeRouter as Router} from "../src/v2/HedgeFunV2TradeRouter.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {Math} from "@openzeppelin/contracts/utils/math/Math.sol";
import {console2} from "forge-std/console2.sol";

/// @notice Explicit external-sponsor experiment: NOT an options engine installed in an old FUN treasury.
/// A separate $10k stock sponsor writes fully covered NetShare calls through the real desk; a $100k USDG
/// counterparty funds premiums once. Only settled portfolio income after disposed-stock cost, cumulative
/// distributions and a retained-capital NAV gate can leave the sponsor. The same amount then goes to
/// actual router buy-and-burn or the new actual-funded staking module (0/50/100% rewards).
/// Uses real testnet V3/V4 pools on a local fork, synthetic option quotes/feed rounds and an open calendar.
contract IncomeAllocationForkTest is AllStrategyHistoricalReplayForkTest {
    address internal constant SPONSOR = address(0xA110CA7E);
    address internal constant OPTION_BUYER = address(0xA110B0B);
    address internal constant STAKER_A = address(0x57A001);
    address internal constant STAKER_B = address(0x57A002);
    uint256 internal constant SPONSOR_CAPITAL = 10_000e6;
    uint256 internal constant MM_CAPITAL = 100_000e6;
    uint256 internal splitBps;
    uint256[] internal incomePrices;
    uint256[] internal incomeElapsed;
    string[] internal incomeDates;
    CoveredCallDesk internal incomeDesk;
    RoundFeed internal incomeFeed;
    FunStakingIncome internal staking;
    uint256 internal initialSponsorStock;
    uint256 internal sponsorStockCostE18;
    uint256 internal sponsorInitialNav;
    uint256 internal stakedA;
    uint256 internal stakedB;
    uint256 internal optionId;
    uint256 internal optionEnd;
    uint256 internal currentPremium;
    uint256 internal currentSize;
    uint256 internal currentStrike;
    uint256 internal optionOfferedAt;
    uint256 internal optionExpiresAt;
    uint256 internal optionsSettled;
    uint256 internal optionPremiums;
    uint256 internal collateralPaidToBuyer;
    int256 internal cumulativeRealizedNet;
    uint256 internal totalAllocated;
    uint256 internal sponsorBuybackCash;
    uint256 internal sponsorBurned;
    uint256 internal stakingFunded;
    uint256 internal dividendsPaidA;
    uint256 internal dividendsPaidB;
    uint256 internal sponsorLpStockFees;
    uint256 internal sponsorLpTokenFees;

    function testIncomeAllocation_TSLA_buyback100() public { _incomeRun("TSLA", 0); }
    function testIncomeAllocation_TSLA_split50() public { _incomeRun("TSLA", 5000); }
    function testIncomeAllocation_TSLA_dividend100() public { _incomeRun("TSLA", 10000); }
    function testIncomeAllocation_NVDA_buyback100() public { _incomeRun("NVDA", 0); }
    function testIncomeAllocation_NVDA_split50() public { _incomeRun("NVDA", 5000); }
    function testIncomeAllocation_NVDA_dividend100() public { _incomeRun("NVDA", 10000); }
    function testIncomeAllocation_META_buyback100() public { _incomeRun("META", 0); }
    function testIncomeAllocation_META_split50() public { _incomeRun("META", 5000); }
    function testIncomeAllocation_META_dividend100() public { _incomeRun("META", 10000); }

    function _incomeRun(string memory symbol, uint256 dividendBps) internal {
        splitBps = dividendBps;
        (incomePrices, incomeElapsed, incomeDates) = _prepare(symbol, 1, true);
        sponsorStockCostE18 = incomePrices[0];
        initialSponsorStock = Math.mulDiv(SPONSOR_CAPITAL, 1e30, sponsorStockCostE18);
        deal(address(stock), SPONSOR, initialSponsorStock);
        deal(address(USDG), OPTION_BUYER, MM_CAPITAL);
        sponsorInitialNav = Math.mulDiv(initialSponsorStock, sponsorStockCostE18, 1e30);
        incomeFeed = new RoundFeed();
        incomeDesk = new CoveredCallDesk(address(this), USDG, new Calendar());
        incomeDesk.list(address(stock), address(incomeFeed), true);
        incomeDesk.setWriter(SPONSOR, true);
        incomeDesk.setBuyer(OPTION_BUYER, true);
        staking = new FunStakingIncome(token, USDG, SPONSOR, 7 days, 7 days);
        vm.startPrank(SPONSOR);
        stock.approve(address(incomeDesk), type(uint256).max);
        USDG.approve(address(staking), type(uint256).max);
        USDG.approve(address(ROUTER), type(uint256).max);
        vm.stopPrank();
        vm.prank(OPTION_BUYER); USDG.approve(address(incomeDesk), type(uint256).max);
        stakedA = startingHolderFun * 3 / 10;
        stakedB = startingHolderFun * 2 / 10;
        vm.startPrank(HOLDER);
        token.transfer(STAKER_A, stakedA);
        token.transfer(STAKER_B, stakedB);
        vm.stopPrank();
        vm.startPrank(STAKER_A); token.approve(address(staking), stakedA); staking.stake(stakedA); vm.stopPrank();
        vm.startPrank(STAKER_B); token.approve(address(staking), stakedB); staking.stake(stakedB); vm.stopPrank();
        _incomeMetadata();
        uint256 daysToRun = vm.envOr("INCOME_ALLOCATION_DAYS", uint256(250));
        require(daysToRun >= 6 && daysToRun <= 250, "bounded probe days");
        for (uint256 i; i < daysToRun; ++i) this.incomeDay(i);
        if (daysToRun == 250) {
            assertEq(optionId, 0, "no unvalued option liability at year end");
            assertEq(stock.balanceOf(address(incomeDesk)), 0);
            assertGt(optionsSettled, 0);
        }
        assertEq(sponsorBuybackCash + stakingFunded, totalAllocated);
        assertEq(staking.totalFunded(), stakingFunded);
        assertEq(staking.totalClaimed(), dividendsPaidA + dividendsPaidB);
        assertEq(USDG.balanceOf(address(staking)) + dividendsPaidA + dividendsPaidB, stakingFunded);
        assertEq(token.balanceOf(address(staking)), stakedA + stakedB);
    }

    function incomeDay(uint256 i) external {
        require(msg.sender == address(this), "self only");
        uint256 price = incomePrices[i];
        if (i != 0) {
            vm.warp(runEpoch + incomeElapsed[i]);
            _move(price);
        }
        uint80 round = incomeFeed.push(1, uint64(i + 1), int256(price / 1e10), block.timestamp);
        if (optionId != 0 && i == optionEnd) _settleSponsorOption(price, round, i);
        if (i != 0) {
            _externalFlow();
            _processHookFees();
            _harvest();
            _buyback();
        }
        vm.prank(STAKER_A); dividendsPaidA += staking.claim(STAKER_A);
        vm.prank(STAKER_B); dividendsPaidB += staking.claim(STAKER_B);
        if (optionId == 0 && i < 249) _openSponsorOption(i);
        _incomeRow(i, price);
    }

    function _openSponsorOption(uint256 i) private {
        uint256 size = stock.balanceOf(SPONSOR);
        if (size == 0) return;
        optionEnd = Math.min(i + 5, 249);
        uint256 expiry = runEpoch + incomeElapsed[optionEnd] + 601;
        optionOfferedAt = block.timestamp;
        optionExpiresAt = expiry;
        currentSize = size;
        currentStrike = Math.mulDiv(incomePrices[i], 10500, 1e12 * 10000, Math.Rounding.Ceil);
        currentPremium = Math.mulDiv(Math.mulDiv(size, incomePrices[i], 1e30),
            50 * (expiry - block.timestamp), 10000 * 7 days, Math.Rounding.Ceil);
        // A fixed 50 bps/week synthetic quote, not a historical options price or independently fair RFQ.
        CoveredCallDesk.Terms memory t = CoveredCallDesk.Terms({buyer: OPTION_BUYER,
            underlying: address(stock), size: uint128(size), strike: uint128(currentStrike),
            premium: uint128(currentPremium), expiry: uint64(expiry), fillDeadline: uint64(block.timestamp + 300),
            mode: CoveredCallDesk.Settlement.NetShare, feed: address(incomeFeed),
            exerciseWindow: incomeDesk.exerciseWindow(), stockMultiplier: 1e18});
        vm.prank(SPONSOR); optionId = incomeDesk.offer(t);
        uint256 beforeSponsor = USDG.balanceOf(SPONSOR);
        uint256 beforeBuyer = USDG.balanceOf(OPTION_BUYER);
        vm.prank(OPTION_BUYER); incomeDesk.fill(optionId);
        assertEq(USDG.balanceOf(SPONSOR) - beforeSponsor, currentPremium);
        assertEq(beforeBuyer - USDG.balanceOf(OPTION_BUYER), currentPremium);
        optionPremiums += currentPremium;
        _incomeOption(i);
    }

    function _incomeOption(uint256 i) private {
        string memory n = "income_option";
        vm.serializeString(n, "ticker", ticker);
        vm.serializeUint(n, "dividendBps", splitBps);
        vm.serializeUint(n, "dayIndex", i);
        vm.serializeUint(n, "endIndex", optionEnd);
        vm.serializeUint(n, "offerTimestamp", optionOfferedAt);
        vm.serializeUint(n, "expiryTimestamp", optionExpiresAt);
        vm.serializeUint(n, "offerPriceE18", incomePrices[i]);
        vm.serializeUint(n, "optionSizeRaw", currentSize);
        vm.serializeUint(n, "strikeUsdgRaw", currentStrike);
        console2.log("INCOME_OPTION", vm.serializeUint(n, "premiumRaw", currentPremium));
    }

    function _settleSponsorOption(uint256 price, uint80 round, uint256 i) private {
        vm.warp(runEpoch + incomeElapsed[i] + 601 + incomeDesk.SETTLE_DELAY() + 1);
        uint256 buyerBefore = stock.balanceOf(OPTION_BUYER);
        incomeDesk.settle(optionId, round);
        uint256 disposedStock = stock.balanceOf(OPTION_BUYER) - buyerBefore;
        uint256 disposedCost = Math.mulDiv(disposedStock, sponsorStockCostE18, 1e30, Math.Rounding.Ceil);
        collateralPaidToBuyer += disposedStock;
        cumulativeRealizedNet += int256(currentPremium) - int256(disposedCost);
        ++optionsSettled;
        optionId = 0;
        assertEq(stock.balanceOf(SPONSOR) + collateralPaidToBuyer, initialSponsorStock);
        uint256 nav = USDG.balanceOf(SPONSOR) + Math.mulDiv(stock.balanceOf(SPONSOR), price, 1e30);
        uint256 available = cumulativeRealizedNet > int256(totalAllocated)
            ? uint256(cumulativeRealizedNet - int256(totalAllocated)) : 0;
        available = Math.min(available, USDG.balanceOf(SPONSOR));
        available = Math.min(available, nav > sponsorInitialNav ? nav - sponsorInitialNav : 0);
        string memory n = "income_epoch";
        vm.serializeString(n, "ticker", ticker);
        vm.serializeUint(n, "dividendBps", splitBps);
        vm.serializeUint(n, "dayIndex", i);
        vm.serializeUint(n, "optionSizeRaw", currentSize);
        vm.serializeUint(n, "strikeUsdgRaw", currentStrike);
        vm.serializeUint(n, "expiryPriceE18", price);
        vm.serializeUint(n, "offerTimestamp", optionOfferedAt);
        vm.serializeUint(n, "expiryTimestamp", optionExpiresAt);
        vm.serializeUint(n, "premiumRaw", currentPremium);
        vm.serializeUint(n, "disposedStockRaw", disposedStock);
        vm.serializeUint(n, "disposedCostRaw", disposedCost);
        vm.serializeInt(n, "cumulativeRealizedNetRaw", cumulativeRealizedNet);
        vm.serializeUint(n, "allocatedBeforeRaw", totalAllocated);
        vm.serializeUint(n, "sponsorClosedNavBeforeRaw", nav);
        console2.log("INCOME_EPOCH", vm.serializeUint(n, "eligibleIncomeRaw", available));
        if (available == 0) return;
        uint256 dividend = Math.mulDiv(available, splitBps, 10000);
        uint256 buy = available - dividend;
        totalAllocated += available;
        assertLe(int256(totalAllocated), cumulativeRealizedNet, "no distribution before realized losses recover");
        if (dividend != 0) {
            vm.prank(SPONSOR); staking.fund(dividend);
            stakingFunded += dividend;
        }
        if (buy != 0) _sponsorBuyAndBurn(buy);
        assertEq(USDG.balanceOf(SPONSOR), optionPremiums - totalAllocated);
    }

    function _sponsorBuyAndBurn(uint256 amount) private {
        Fees memory beforeFees = _probeFees();
        Router.Hop[] memory path = new Router.Hop[](1);
        path[0] = Router.Hop(stockPool, address(stock));
        Router.TradeParams memory p = Router.TradeParams(id, address(USDG), amount, 1, 1,
            block.timestamp + 300, 2, false);
        uint256 snapshot = vm.snapshotState();
        vm.prank(SPONSOR);
        (uint256 quote,) = ROUTER.buy(p, path);
        assertTrue(vm.revertToState(snapshot)); vm.deleteStateSnapshot(snapshot);
        p.minFinalOut = quote * 99 / 100;
        assertGt(p.minFinalOut, 0);
        uint256 beforeCash = USDG.balanceOf(SPONSOR);
        uint256 beforeSupply = token.totalSupply();
        vm.startPrank(SPONSOR);
        (uint256 bought, uint256 refund) = ROUTER.buy(p, path);
        assertEq(refund, 0);
        HedgeFunToken(address(token)).burn(bought);
        vm.stopPrank();
        assertEq(beforeCash - USDG.balanceOf(SPONSOR), amount);
        assertEq(beforeSupply - token.totalSupply(), bought);
        assertEq(token.balanceOf(SPONSOR), 0);
        sponsorBuybackCash += amount;
        sponsorBurned += bought;
        Fees memory afterFees = _probeFees();
        sponsorLpStockFees += afterFees.stockAmount - beforeFees.stockAmount;
        sponsorLpTokenFees += afterFees.tokenAmount - beforeFees.tokenAmount;
    }

    function _incomeMetadata() private {
        string memory n = "income_profile";
        vm.serializeString(n, "ticker", ticker);
        vm.serializeUint(n, "dividendBps", splitBps);
        vm.serializeAddress(n, "token", address(token));
        vm.serializeAddress(n, "staking", address(staking));
        vm.serializeAddress(n, "desk", address(incomeDesk));
        vm.serializeUint(n, "sponsorInitialStockRaw", initialSponsorStock);
        vm.serializeUint(n, "sponsorInitialCapitalRaw", sponsorInitialNav);
        vm.serializeUint(n, "productInitialExternalAssetsRaw", initialExternalAssets + sponsorInitialNav);
        vm.serializeUint(n, "stockCostE18", sponsorStockCostE18);
        vm.serializeUint(n, "stakedARaw", stakedA);
        vm.serializeUint(n, "stakedBRaw", stakedB);
        vm.serializeUint(n, "initialSupplyRaw", expectedInitialSupply);
        vm.serializeUint(n, "expectedV3Liquidity", expectedV3Liquidity);
        vm.serializeString(n, "scope", "External $10k stock sponsor plus existing $20k FUN fund; not native options-treasury integration. Actual desk/router/staking transfers; quote and expiry feed are synthetic.");
        console2.log("INCOME_PROFILE", vm.serializeUint(n, "counterpartyInitialCashRaw", MM_CAPITAL));
    }

    function _incomeRow(uint256 i, uint256 price) private {
        assertEq(token.balanceOf(address(staking)), stakedA + stakedB);
        assertEq(USDG.balanceOf(STAKER_A), dividendsPaidA);
        assertEq(USDG.balanceOf(STAKER_B), dividendsPaidB);
        assertEq(USDG.balanceOf(address(staking)) + dividendsPaidA + dividendsPaidB, stakingFunded);
        assertEq(USDG.balanceOf(OPTION_BUYER) + optionPremiums, MM_CAPITAL);
        assertEq(USDG.balanceOf(SPONSOR) + totalAllocated, optionPremiums);
        assertEq(stock.balanceOf(SPONSOR) + stock.balanceOf(address(incomeDesk)) + stock.balanceOf(OPTION_BUYER), initialSponsorStock);
        string memory n = "income_row";
        vm.serializeString(n, "ticker", ticker);
        vm.serializeUint(n, "dividendBps", splitBps);
        vm.serializeUint(n, "dayIndex", i);
        vm.serializeUint(n, "evmTimestamp", block.timestamp);
        vm.serializeString(n, "date", incomeDates[i]);
        vm.serializeUint(n, "stockOracleUsdE18", price);
        vm.serializeUint(n, "funOracleUsdE18", Math.mulDiv(_funPrice(), price, 1e18));
        vm.serializeUint(n, "fundExternalAssetsRaw", reader.totalAssets(price));
        (uint256 lpStock, uint256 lpFun) = _lp();
        vm.serializeUint(n, "lpStockRaw", lpStock);
        vm.serializeUint(n, "lpFunRaw", lpFun);
        vm.serializeUint(n, "sponsorStockRaw", stock.balanceOf(SPONSOR));
        vm.serializeUint(n, "sponsorCollateralRaw", stock.balanceOf(address(incomeDesk)));
        vm.serializeUint(n, "sponsorCashRaw", USDG.balanceOf(SPONSOR));
        uint256 sponsorGross = Math.mulDiv(stock.balanceOf(SPONSOR) + stock.balanceOf(address(incomeDesk)), price, 1e30) + USDG.balanceOf(SPONSOR);
        uint256 liabilityFloor = optionId != 0 && price / 1e12 > currentStrike
            ? Math.mulDiv(currentSize, price / 1e12 - currentStrike, 1e18) : 0;
        vm.serializeUint(n, "sponsorGrossAssetsRaw", sponsorGross);
        vm.serializeUint(n, "optionIntrinsicLiabilityFloorRaw", liabilityFloor);
        vm.serializeBool(n, "hasOpenOption", optionId != 0);
        vm.serializeUint(n, "optionsSettled", optionsSettled);
        vm.serializeUint(n, "optionPremiumsRaw", optionPremiums);
        vm.serializeInt(n, "cumulativeRealizedNetRaw", cumulativeRealizedNet);
        vm.serializeUint(n, "totalAllocatedRaw", totalAllocated);
        vm.serializeUint(n, "sponsorBuybackCashRaw", sponsorBuybackCash);
        vm.serializeUint(n, "sponsorBurnedRaw", sponsorBurned);
        vm.serializeUint(n, "sponsorLpStockFeesRaw", sponsorLpStockFees);
        vm.serializeUint(n, "sponsorLpTokenFeesRaw", sponsorLpTokenFees);
        vm.serializeUint(n, "stakingFundedRaw", stakingFunded);
        vm.serializeUint(n, "stakingCashRaw", USDG.balanceOf(address(staking)));
        vm.serializeUint(n, "dividendsPaidARaw", dividendsPaidA);
        vm.serializeUint(n, "dividendsPaidBRaw", dividendsPaidB);
        vm.serializeUint(n, "claimableARaw", staking.earned(STAKER_A));
        vm.serializeUint(n, "claimableBRaw", staking.earned(STAKER_B));
        vm.serializeUint(n, "totalSupplyRaw", token.totalSupply());
        vm.serializeUint(n, "fundBuybacks", buybacks);
        vm.serializeUint(n, "flowPairs", flowPairs);
        _incomeLedger(n);
        _incomeTotal(n, price, sponsorGross);
    }

    function _incomeTotal(string memory n, uint256 price, uint256 sponsorGross) private {
        // Daily gross excludes a full option MTM. Only terminal no-open-option rows establish net performance.
        console2.log("INCOME_ROW", vm.serializeUint(n, "productGrossPlusPaidRaw",
            reader.totalAssets(price) + sponsorGross + USDG.balanceOf(address(staking)) + dividendsPaidA + dividendsPaidB));
    }

    function _incomeLedger(string memory n) private {
        assertEq(IAllStrategyV3Position(stockPool).liquidity(), expectedV3Liquidity);
        assertEq(token.totalSupply() + burned + collectedTokenBurn + hookTokenBurn + sponsorBurned, expectedInitialSupply);
        assertEq(treasury.bookedStock() + treasury.unbookedStock() + treasury.buybackStock(), stock.balanceOf(address(treasury)));
        uint256 accounted = token.balanceOf(TRADER) + token.balanceOf(address(vault)) + token.balanceOf(HOLDER)
            + token.balanceOf(address(curve)) + token.balanceOf(address(FACTORY.poolManager()))
            + token.balanceOf(address(FACTORY.hook())) + token.balanceOf(KEEPER)
            + token.balanceOf(address(treasury)) + token.balanceOf(address(FACTORY)) + token.balanceOf(address(staking));
        assertEq(accounted, token.totalSupply(), "all FUN including staked principal accounted");
        assertEq(token.balanceOf(HOLDER) + stakedA + stakedB, startingHolderFun);
        assertEq(token.balanceOf(TRADER), 0);
        assertEq(USDG.balanceOf(TRADER), TRADER_INITIAL_USDG - traderUsdIn + traderUsdOut);
        assertEq(stock.balanceOf(address(vault)), 0);
        assertEq(token.balanceOf(address(ROUTER)), 0);
        assertEq(stock.balanceOf(address(ROUTER)), 0);
        assertEq(USDG.balanceOf(address(ROUTER)), 0);
        Fees memory fees = _probeFees();
        assertEq(collectedStockFees + fees.stockAmount, externalStockFeesGenerated + selfStockFeesGenerated + conversionStockFeesGenerated + sponsorLpStockFees);
        assertEq(collectedTokenBurn + fees.tokenAmount, externalTokenFeesGenerated + selfTokenFeesGenerated + conversionTokenFeesGenerated + sponsorLpTokenFees);
        vm.serializeUint(n, "funStockE18", _funPrice());
        vm.serializeUint(n, "stockPoolLiquidity", IAllStrategyV3Position(stockPool).liquidity());
        vm.serializeUint(n, "treasuryStockRaw", stock.balanceOf(address(treasury)));
        vm.serializeUint(n, "treasuryUsdgRaw", treasury.reserveUsdg());
        vm.serializeUint(n, "uncollectedLpStockRaw", fees.stockAmount);
        vm.serializeUint(n, "uncollectedLpFunRaw", fees.tokenAmount);
        vm.serializeUint(n, "collectedLpStockRaw", collectedStockFees);
        vm.serializeUint(n, "collectedLpFunBurnedRaw", collectedTokenBurn);
        vm.serializeUint(n, "fundBurnedRaw", burned);
        vm.serializeUint(n, "hookBurnedRaw", hookTokenBurn);
        vm.serializeUint(n, "externalGeneratedLpStockRaw", externalStockFeesGenerated);
        vm.serializeUint(n, "externalGeneratedLpFunRaw", externalTokenFeesGenerated);
        vm.serializeUint(n, "buybackGeneratedLpStockRaw", selfStockFeesGenerated);
        vm.serializeUint(n, "buybackGeneratedLpFunRaw", selfTokenFeesGenerated);
        vm.serializeUint(n, "conversionGeneratedLpStockRaw", conversionStockFeesGenerated);
        vm.serializeUint(n, "conversionGeneratedLpFunRaw", conversionTokenFeesGenerated);
    }
}

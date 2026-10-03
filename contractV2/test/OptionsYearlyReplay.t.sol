// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {console2} from "forge-std/console2.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {Math} from "@openzeppelin/contracts/utils/math/Math.sol";
import {CoveredCallDesk} from "../src/options/CoveredCallDesk.sol";
import {PhysicalCallDesk} from "../src/options/PhysicalCallDesk.sol";
import {CashSecuredPutDesk} from "../src/options/CashSecuredPutDesk.sol";
import {EarnVault, IEarnVaultSwapAdapter} from "../src/options/EarnVault.sol";
import {EarnVaultV3SwapAdapter} from "../src/options/EarnVaultV3SwapAdapter.sol";
import {PriceOracle} from "../src/PriceOracle.sol";
import {FreezableToken, RoundFeed, Calendar} from "./CoveredCallDesk.t.sol";
import {MockPool} from "./mocks/Mocks.sol";
import {FunStakingIncome} from "../src/experimental/FunStakingIncome.sol";

/// @notice Local source-contract replay, not a fork or historical options-price backtest.
/// Close feeds, open calendar and optional flat V3 pool are fixtures. Desks, vault,
/// adapter, collateral, premiums, exercise and redemption use their actual code.
/// Premium assumption is 50 bps per 7 calendar days (25-bps guard floor), not RFQ data.
contract OptionsYearlyReplayTest is Test {
    uint256 constant T0 = 1_800_000_000;
    uint256 constant SCALE = 1e30;
    address internal alice = makeAddr("options-investor");
    address internal mm = makeAddr("prefunded-option-counterparty");
    FreezableToken internal stock;
    FreezableToken internal usdg;
    RoundFeed internal stockFeed;
    RoundFeed internal usdgFeed;
    Calendar internal calendar;
    CoveredCallDesk internal desk;
    CashSecuredPutDesk internal putDesk;
    EarnVault internal vault;
    MockPool internal swapPool;
    PriceOracle internal oracle;
    address internal writer;
    uint64 internal nextRound = 1;
    uint256[] internal prices;
    uint256[] internal elapsed;
    string[] internal dates;
    string internal ticker;
    string internal profile;
    uint256 internal mode;
    uint256 internal premiumBps;
    uint256 internal activeId;
    bool internal activePut;
    uint256 internal expiryIndex;
    uint256 internal optionStartIndex;
    uint256 internal initialStock;
    uint256 internal initialMmStock;
    uint256 internal initialPoolStock;
    uint256 internal totalStockSupply;
    uint256 internal totalUsdgSupply;
    uint256 internal premiums;
    uint256 internal calls;
    uint256 internal puts;
    uint256 internal fills;
    uint256 internal cancellations;
    uint256 internal assignments;
    uint256 internal lapses;
    uint256 internal netSettlements;
    uint256 internal optionPayoutAtSettlementUsdg;
    uint256 internal reinvestSpent;
    uint256 internal reinvestStock;
    uint256 internal reinvestCashDue;
    uint256 internal reinvestExecutionCost;

    function testOptions_TSLA_netshare() public { _run("TSLA",0,50); }
    function testOptions_NVDA_netshare() public { _run("NVDA",0,50); }
    function testOptions_META_netshare() public { _run("META",0,50); }
    function testOptions_TSLA_physical() public { _run("TSLA",1,50); }
    function testOptions_NVDA_physical() public { _run("NVDA",1,50); }
    function testOptions_META_physical() public { _run("META",1,50); }
    function testOptions_TSLA_wheel() public { _run("TSLA",2,50); }
    function testOptions_NVDA_wheel() public { _run("NVDA",2,50); }
    function testOptions_META_wheel() public { _run("META",2,50); }
    function testOptions_TSLA_no_fill() public { _run("TSLA",3,50); }
    function testOptions_NVDA_no_fill() public { _run("NVDA",3,50); }
    function testOptions_META_no_fill() public { _run("META",3,50); }
    function testOptions_TSLA_no_exercise() public { _run("TSLA",4,50); }
    function testOptions_NVDA_no_exercise() public { _run("NVDA",4,50); }
    function testOptions_META_no_exercise() public { _run("META",4,50); }
    function testOptions_TSLA_premium_reinvest() public { _run("TSLA",5,50); }
    function testOptions_NVDA_premium_reinvest() public { _run("NVDA",5,50); }
    function testOptions_META_premium_reinvest() public { _run("META",5,50); }
    function testOptions_TSLA_wheel_premium25() public { _run("TSLA",2,25); }
    function testOptions_TSLA_wheel_premium100() public { _run("TSLA",2,100); }

    function _setup(string memory symbol, uint256 scenario, uint256 bps) internal {
        ticker=symbol; mode=scenario; premiumBps=bps;
        string[6] memory names=[string("netshare"),"physical","wheel","no_fill","no_exercise","premium_reinvest"];
        profile=names[scenario];
        if (bps != 50) profile=string.concat(profile,"_premium",vm.toString(bps));
        string memory input=vm.readFile("data/equity-history-2025.json");
        string memory prefix=string.concat(".windows.",symbol);
        prices=vm.parseJsonUintArray(input,string.concat(prefix,".pricesE18"));
        elapsed=vm.parseJsonUintArray(input,string.concat(prefix,".elapsedSeconds"));
        dates=vm.parseJsonStringArray(input,string.concat(prefix,".dates"));
        assertEq(prices.length,250); assertEq(elapsed.length,250); assertEq(dates.length,250);
        vm.warp(T0);
        stock=new FreezableToken(symbol,18); usdg=new FreezableToken("USDG",6);
        stockFeed=new RoundFeed(); usdgFeed=new RoundFeed(); calendar=new Calendar();
        _feed(prices[0],T0);
        if (mode==0) desk=new CoveredCallDesk(address(this),IERC20(address(usdg)),calendar);
        else desk=CoveredCallDesk(address(new PhysicalCallDesk(address(this),IERC20(address(usdg)),calendar)));
        putDesk=new CashSecuredPutDesk(address(this),IERC20(address(usdg)),calendar);
        oracle=new PriceOracle(address(stock),address(stockFeed),address(usdgFeed),address(calendar),26 hours,26 hours);
        swapPool=new MockPool(address(stock),address(usdg),false,3000,SCALE);
        swapPool.setPrice(prices[0]);
        initialStock=Math.mulDiv(10_000e6,SCALE,prices[0]);
        initialMmStock=Math.mulDiv(100_000e6,SCALE,prices[0]);
        initialPoolStock=initialMmStock;
        stock.mint(alice,initialStock); stock.mint(mm,initialMmStock); stock.mint(address(swapPool),initialPoolStock);
        usdg.mint(mm,100_000e6);
        if (mode==0) writer=alice;
        else {
            vault=new EarnVault(address(this),IERC20(address(stock)),IERC20(address(usdg)),desk,oracle,address(swapPool));
            vault.setPutDesk(putDesk); vault.setEligible(alice,true); vault.setBuyer(mm,true);
            EarnVaultV3SwapAdapter adapter=new EarnVaultV3SwapAdapter(address(vault),address(usdg),address(stock),address(swapPool),address(oracle),50,100);
            vault.setSwapAdapter(IEarnVaultSwapAdapter(address(adapter)));
            writer=address(vault);
            vm.prank(alice); stock.approve(writer,type(uint256).max);
            vm.prank(alice); vault.requestDeposit(initialStock);
            vault.closeEpoch(); vm.prank(alice); vault.claimDeposit(1,alice);
        }
        desk.list(address(stock),address(stockFeed),true); desk.setWriter(writer,true); desk.setBuyer(mm,true);
        putDesk.list(address(stock),address(stockFeed),true); putDesk.setWriter(writer,true); putDesk.setBuyer(mm,true);
        if(mode==0) { vm.prank(writer); stock.approve(address(desk),type(uint256).max); }
        vm.startPrank(mm);
        usdg.approve(address(desk),type(uint256).max); usdg.approve(address(putDesk),type(uint256).max);
        stock.approve(address(putDesk),type(uint256).max);
        vm.stopPrank();
        totalStockSupply=stock.totalSupply(); totalUsdgSupply=usdg.totalSupply();
    }

    function _feed(uint256 price,uint256 at) internal returns (uint80 round) {
        round=stockFeed.push(1,nextRound,int256(price/1e10),at);
        usdgFeed.push(1,nextRound++,1e8,at);
    }

    function _run(string memory symbol,uint256 scenario,uint256 bps) internal {
        _setup(symbol,scenario,bps); _metadata();
        _row(0); _open(0);
        for(uint256 i=1;i<250;i++) {
            vm.warp(T0+elapsed[i]); uint80 round=_feed(prices[i],block.timestamp);
            swapPool.setPrice(prices[i]);
            if(i==expiryIndex && activeId!=0) _settle(i,round);
            _conservation(); _row(i);
            if(i==expiryIndex && i<249) _open(i);
        }
        assertEq(activeId,0,"terminal assets have no open option liability");
        if(mode==2) { assertGt(calls,0); assertGt(puts,0); }
        if(mode==3) { assertEq(premiums,0); assertEq(stock.balanceOf(writer),initialStock); }
        if(mode!=0) _redeem();
    }

    function _premium(uint256 size,uint256 price,uint256 expiry) internal view returns (uint256) {
        uint256 notional=Math.mulDiv(size,price,SCALE);
        uint256 model=Math.mulDiv(notional,premiumBps*(expiry-block.timestamp),10_000*7 days,Math.Rounding.Ceil);
        return Math.max(model,Math.mulDiv(notional,25,10_000,Math.Rounding.Ceil));
    }

    function _terms(uint256 size,uint256 strike,uint256 premium,uint256 expiry) internal view returns(CoveredCallDesk.Terms memory) {
        return CoveredCallDesk.Terms(mm,address(stock),uint128(size),uint128(strike),uint128(premium),uint64(expiry),uint64(block.timestamp+300),mode==0?CoveredCallDesk.Settlement.NetShare:CoveredCallDesk.Settlement.Physical,address(stockFeed),desk.exerciseWindow(),1e18);
    }

    function _open(uint256 index) internal {
        expiryIndex=Math.min(index+5,249); optionStartIndex=index;
        while(elapsed[expiryIndex]-elapsed[index]>9 days) --expiryIndex;
        uint256 expiry=T0+elapsed[expiryIndex];
        uint256 size=stock.balanceOf(writer);
        uint256 strike=Math.mulDiv(prices[index],10500,1e12*10_000,Math.Rounding.Ceil);
        activePut=mode==2 && size<1e12;
        if(activePut) {
            strike=Math.mulDiv(prices[index],9500,1e12*10_000);
            size=Math.mulDiv(usdg.balanceOf(writer),1e18,strike);
        }
        if(size<1e12 || Math.mulDiv(size,prices[index],SCALE)<1e6) return; // $1 minimum modeled RFQ; assignment may leave cash.
        uint256 premium=_premium(size,prices[index],expiry);
        if(activePut) {
            CashSecuredPutDesk.Terms memory p=CashSecuredPutDesk.Terms(mm,address(stock),uint128(size),uint128(strike),uint128(premium),uint64(expiry),uint64(block.timestamp+300),address(stockFeed),putDesk.exerciseWindow(),1e18);
            activeId=vault.offerPut(p); ++puts;
        } else {
            CoveredCallDesk.Terms memory t=_terms(size,strike,premium,expiry);
            if(mode==0) { vm.prank(writer); activeId=desk.offer(t); }
            else activeId=vault.offer(t);
            ++calls;
        }
        if(mode==3) {
            vault.cancelOffered(); ++cancellations;
            vault.closeEpoch(); _optionEvent(index,0,2,0); activeId=0; return;
        }
        uint256 beforeCash=usdg.balanceOf(writer); uint256 beforeMm=usdg.balanceOf(mm);
        vm.prank(mm);
        if(activePut) putDesk.fill(activeId); else desk.fill(activeId);
        assertEq(usdg.balanceOf(writer)-beforeCash,premium,"actual premium receipt");
        assertEq(beforeMm-usdg.balanceOf(mm),premium,"counterparty pays entire premium");
        premiums+=premium; ++fills;
        if(mode==5) reinvestCashDue+=premium;
        _conservation();
    }

    function _settle(uint256 index,uint80 round) internal {
        uint256 id=activeId; uint256 payout; uint8 terminal;
        uint256 expiry=T0+elapsed[index];
        _feed(prices[index],expiry+1); vm.warp(expiry+301);
        if(activePut) {
            CashSecuredPutDesk.Option memory beforeOption=putDesk.getOption(id);
            putDesk.settle(id,round);
            if(uint8(putDesk.getOption(id).state)==4) {
                uint256 cashBefore=usdg.balanceOf(writer); uint256 stockBefore=stock.balanceOf(writer);
                vm.prank(mm); putDesk.exercise(id,0,mm);
                assertEq(stock.balanceOf(writer)-stockBefore,beforeOption.size);
                assertEq(usdg.balanceOf(writer),cashBefore);
                payout=beforeOption.collateral-Math.mulDiv(beforeOption.size,prices[index],SCALE);
                ++assignments;
            }
            terminal=uint8(putDesk.getOption(id).state);
        } else {
            CoveredCallDesk.Option memory beforeOption=desk.getOption(id);
            uint256 mmStockBefore=stock.balanceOf(mm);
            desk.settle(id,round);
            uint8 state=uint8(desk.getOption(id).state);
            if(state==4 && mode==4) {
                vm.warp(uint256(desk.getOption(id).exerciseDeadline)+1); desk.lapse(id); ++lapses;
            } else if(state==4) {
                uint256 beforeCash=usdg.balanceOf(writer); uint256 cost=desk.exerciseCost(id);
                vm.prank(mm); desk.exercise(id,0,mm);
                assertEq(usdg.balanceOf(writer)-beforeCash,cost);
                assertEq(stock.balanceOf(mm)-mmStockBefore,beforeOption.size);
                payout=Math.mulDiv(beforeOption.size,prices[index],SCALE)-cost; ++assignments;
            } else if(state==7) {
                payout=Math.mulDiv(stock.balanceOf(mm)-mmStockBefore,prices[index],SCALE); ++netSettlements;
            }
            terminal=uint8(desk.getOption(id).state);
        }
        optionPayoutAtSettlementUsdg+=payout;
        _feed(prices[index],block.timestamp);
        if(mode!=0) vault.closeEpoch();
        _optionEvent(index,prices[index],terminal,payout); activeId=0;
        if(mode==5 && reinvestCashDue>=1e6) _reinvest(index); // Smaller cash remains owned; do not swap USDG dust.
    }

    function _reinvest(uint256 index) internal {
            uint256 amount=reinvestCashDue; uint256 beforeStock=stock.balanceOf(writer); uint256 beforeCash=usdg.balanceOf(writer);
            (uint256 spent,uint256 got)=vault.reinvestUSDG(amount,Math.mulDiv(amount*997/1000,SCALE,prices[index])*99/100);
            assertEq(beforeCash-usdg.balanceOf(writer),spent); assertEq(stock.balanceOf(writer)-beforeStock,got);
            assertEq(spent,amount); reinvestSpent+=spent; reinvestStock+=got; reinvestCashDue=0;
            uint256 cost=spent-Math.mulDiv(got,prices[index],SCALE); reinvestExecutionCost+=cost;
            string memory n=string.concat("reinvest",vm.toString(index));
            vm.serializeString(n,"ticker",ticker); vm.serializeString(n,"profile",profile); vm.serializeUint(n,"index",index);
            vm.serializeUint(n,"spentUsdgRaw",spent); vm.serializeUint(n,"receivedStockRaw",got);
            console2.log("OPTIONS_REINVEST",vm.serializeUint(n,"executionCostAtTradeMarkUsdgRaw",cost));
    }

    function _conservation() internal view {
        assertEq(stock.totalSupply(),totalStockSupply,"no post-setup stock mint/burn");
        assertEq(usdg.totalSupply(),totalUsdgSupply,"no post-setup cash mint/burn");
        assertEq(stock.balanceOf(writer)+stock.balanceOf(mm)+stock.balanceOf(address(swapPool))+stock.balanceOf(address(desk))+stock.balanceOf(address(putDesk)),totalStockSupply);
        assertEq(usdg.balanceOf(writer)+usdg.balanceOf(mm)+usdg.balanceOf(address(swapPool))+usdg.balanceOf(address(desk))+usdg.balanceOf(address(putDesk)),totalUsdgSupply);
        assertEq(stock.balanceOf(address(desk)),desk.reserved(address(stock)));
        assertEq(usdg.balanceOf(address(putDesk)),putDesk.reserved(address(usdg)));
    }

    function _row(uint256 index) internal {
        uint256 heldStock=stock.balanceOf(writer); uint256 heldCash=usdg.balanceOf(writer);
        uint256 escrowStock=stock.balanceOf(address(desk)); uint256 escrowCash=usdg.balanceOf(address(putDesk));
        uint256 liability;
        if(activeId!=0) {
            if(activePut) {
                CashSecuredPutDesk.Option memory o=putDesk.getOption(activeId);
                if(uint256(o.strike)*1e12>prices[index]) liability=Math.mulDiv(o.size,uint256(o.strike)*1e12-prices[index],SCALE);
            } else {
                CoveredCallDesk.Option memory o=desk.getOption(activeId);
                if(prices[index]>uint256(o.strike)*1e12) liability=Math.mulDiv(o.size,prices[index]-uint256(o.strike)*1e12,SCALE);
            }
        }
        string memory n=string.concat("row",vm.toString(index));
        vm.serializeString(n,"ticker",ticker); vm.serializeString(n,"profile",profile); vm.serializeUint(n,"index",index); vm.serializeString(n,"date",dates[index]);
        vm.serializeUint(n,"elapsedSeconds",elapsed[index]); vm.serializeUint(n,"stockPriceE18",prices[index]);
        vm.serializeUint(n,"writerStockRaw",heldStock); vm.serializeUint(n,"writerUsdgRaw",heldCash);
        vm.serializeUint(n,"escrowStockRaw",escrowStock); vm.serializeUint(n,"escrowUsdgRaw",escrowCash);
        vm.serializeUint(n,"grossAssetsUsdgRaw",Math.mulDiv(heldStock+escrowStock,prices[index],SCALE)+heldCash+escrowCash);
        vm.serializeUint(n,"optionIntrinsicLowerBoundUsdgRaw",liability); vm.serializeBool(n,"hasUnsettledOption",activeId!=0);
        vm.serializeUint(n,"mmStockRaw",stock.balanceOf(mm)); vm.serializeUint(n,"mmUsdgRaw",usdg.balanceOf(mm));
        vm.serializeUint(n,"mmAssetsUsdgRaw",Math.mulDiv(stock.balanceOf(mm),prices[index],SCALE)+usdg.balanceOf(mm));
        vm.serializeUint(n,"poolStockRaw",stock.balanceOf(address(swapPool))); vm.serializeUint(n,"poolUsdgRaw",usdg.balanceOf(address(swapPool)));
        vm.serializeUint(n,"premiumReceivedUsdgRaw",premiums); vm.serializeUint(n,"optionPayoutAtSettlementUsdgRaw",optionPayoutAtSettlementUsdg);
        vm.serializeUint(n,"reinvestSpentUsdgRaw",reinvestSpent); vm.serializeUint(n,"reinvestStockRaw",reinvestStock);
        vm.serializeUint(n,"pendingReinvestCashUsdgRaw",reinvestCashDue);
        vm.serializeUint(n,"reinvestExecutionCostUsdgRaw",reinvestExecutionCost);
        vm.serializeUint(n,"calls",calls); vm.serializeUint(n,"puts",puts); vm.serializeUint(n,"fills",fills);
        vm.serializeUint(n,"cancellations",cancellations); vm.serializeUint(n,"assignments",assignments); vm.serializeUint(n,"lapses",lapses);
        vm.serializeUint(n,"netSettlements",netSettlements);
        vm.serializeUint(n,"earnSharesSupplyRaw",mode==0?0:vault.totalSupply());
        console2.log("OPTIONS_ROW",vm.serializeUint(n,"investorEarnSharesRaw",mode==0?0:vault.balanceOf(alice)));
    }

    function _optionEvent(uint256 endIndex,uint256 expiryPrice,uint8 state,uint256 payout) internal {
        string memory n=string.concat("option",vm.toString(calls+puts));
        vm.serializeString(n,"ticker",ticker); vm.serializeString(n,"profile",profile); vm.serializeUint(n,"number",calls+puts);
        vm.serializeBool(n,"put",activePut); vm.serializeUint(n,"startIndex",optionStartIndex); vm.serializeUint(n,"endIndex",endIndex);
        vm.serializeUint(n,"expiryPriceE18",expiryPrice); vm.serializeUint(n,"state",state); vm.serializeUint(n,"payoutAtExpiryUsdgRaw",payout);
        uint256 actualPremium;
        if(activePut) {
            CashSecuredPutDesk.Option memory o=putDesk.getOption(activeId);
            vm.serializeUint(n,"sizeRaw",o.size); vm.serializeUint(n,"strikeUsdgRaw",o.strike); vm.serializeUint(n,"premiumUsdgRaw",o.premium);
            vm.serializeUint(n,"collateralUsdgRaw",o.collateral); vm.serializeUint(n,"expiry",o.expiry);
            vm.serializeUint(n,"offerTimestamp",uint256(o.fillDeadline)-300); vm.serializeUint(n,"fillDeadline",o.fillDeadline);
            actualPremium=mode==3?0:o.premium;
        } else {
            CoveredCallDesk.Option memory o=desk.getOption(activeId);
            vm.serializeUint(n,"sizeRaw",o.size); vm.serializeUint(n,"strikeUsdgRaw",o.strike); vm.serializeUint(n,"premiumUsdgRaw",o.premium);
            vm.serializeUint(n,"collateralUsdgRaw",0); vm.serializeUint(n,"expiry",o.expiry);
            vm.serializeUint(n,"offerTimestamp",uint256(o.fillDeadline)-300); vm.serializeUint(n,"fillDeadline",o.fillDeadline);
            actualPremium=mode==3?0:o.premium;
        }
        vm.serializeInt(n,"signedSettledOptionPnlUsdgRaw",int256(actualPremium)-int256(payout));
        vm.serializeUint(n,"optionDeskFeesUsdgRaw",0);
        vm.serializeUint(n,"closedWriterStockRaw",stock.balanceOf(writer));
        vm.serializeUint(n,"closedWriterUsdgRaw",usdg.balanceOf(writer));
        vm.serializeUint(n,"closedWriterAssetsUsdgRaw",Math.mulDiv(stock.balanceOf(writer),prices[endIndex],SCALE)+usdg.balanceOf(writer));
        if(mode!=0) {
            (,,,uint256 closedNav,,,,,,)=vault.epochs(vault.currentEpoch()-1);
            assertEq(closedNav,Math.mulDiv(stock.balanceOf(writer),prices[endIndex],SCALE)+usdg.balanceOf(writer));
            vm.serializeUint(n,"actualEarnClosedEpochNavUsdgRaw",closedNav);
        }
        else vm.serializeUint(n,"actualEarnClosedEpochNavUsdgRaw",0);
        console2.log("OPTIONS_CONTRACT",vm.serializeBool(n,"filled",mode!=3));
    }

    function _metadata() internal {
        string memory n="metadata";
        vm.serializeString(n,"ticker",ticker); vm.serializeString(n,"profile",profile); vm.serializeUint(n,"premiumBpsPerSevenDays",premiumBps);
        vm.serializeUint(n,"premiumGuardFloorBps",25); vm.serializeUint(n,"callStrikeBps",10500); vm.serializeUint(n,"putStrikeBps",9500);
        vm.serializeUint(n,"minimumRfqNotionalUsdgRaw",1e6); vm.serializeUint(n,"minimumReinvestUsdgRaw",1e6);
        vm.serializeString(n,"expirySchedule","at most five trading observations, capped at nine calendar days by Earn MAX_TENOR; calendar-only rule");
        vm.serializeUint(n,"initialWriterStockRaw",initialStock); vm.serializeUint(n,"initialMmStockRaw",initialMmStock);
        vm.serializeUint(n,"initialPoolStockRaw",initialPoolStock); vm.serializeUint(n,"initialMmUsdgRaw",100_000e6);
        vm.serializeUint(n,"stockSupplyRaw",totalStockSupply); vm.serializeUint(n,"usdgSupplyRaw",totalUsdgSupply);
        vm.serializeBool(n,"sourceContractsLocalDeployment",true); vm.serializeBool(n,"funV4Connected",false);
        vm.serializeString(n,"premiumBasis","fixed hypothetical 50bps/week (25/100 sensitivity), calendar-prorated with guard floor; not historical options quotes");
        console2.log("OPTIONS_PROFILE",vm.serializeString(n,"reinvestmentVenue","actual EarnVaultV3SwapAdapter against finite-inventory flat-price 30bps MockPool; not historical/onchain V3 depth"));
    }

    function _redeem() internal {
        uint256 shares=vault.balanceOf(alice); uint256 supply=vault.totalSupply();
        uint256 expectedStock=Math.mulDiv(stock.balanceOf(writer),shares,supply);
        uint256 expectedCash=Math.mulDiv(usdg.balanceOf(writer),shares,supply);
        uint256 epoch=vault.currentEpoch();
        vm.prank(alice); vault.requestRedeem(shares); vault.closeEpoch();
        vm.prank(alice); (uint256 stockOut,uint256 cashOut)=vault.claimRedeem(epoch,alice);
        assertEq(stockOut,expectedStock); assertEq(cashOut,expectedCash);
        assertEq(stock.balanceOf(alice),stockOut); assertEq(usdg.balanceOf(alice),cashOut);
        assertEq(vault.balanceOf(alice),0); assertEq(vault.totalSupply(),vault.MIN_DEAD_SHARES());
        string memory n="redeem"; vm.serializeString(n,"ticker",ticker); vm.serializeString(n,"profile",profile);
        vm.serializeUint(n,"stockOutRaw",stockOut); vm.serializeUint(n,"usdgOutRaw",cashOut);
        vm.serializeUint(n,"deadShareStockResidualRaw",stock.balanceOf(writer));
        console2.log("OPTIONS_REDEEM",vm.serializeUint(n,"deadShareUsdgResidualRaw",usdg.balanceOf(writer)));
    }

    function testOptionsRisk_legacyPhysicalBackstopIsNetShare() public {
        _setup("TSLA",0,50);
        uint256 expiry=block.timestamp+7 days;
        CoveredCallDesk.Terms memory t=_terms(initialStock,400e6,100e6,expiry);
        t.mode=CoveredCallDesk.Settlement.Physical;
        vm.prank(writer); uint256 id=desk.offer(t);
        vm.prank(mm); desk.fill(id);
        uint256 cashBefore=usdg.balanceOf(mm); uint256 stockBefore=stock.balanceOf(mm);
        vm.warp(expiry+desk.BACKSTOP_DELAY()+1); desk.backstopSettle(id,500e6);
        assertEq(uint8(desk.getOption(id).state),uint8(CoveredCallDesk.State.NetSettled));
        assertEq(usdg.balanceOf(mm),cashBefore,"legacy backstop does not collect strike cash");
        assertGt(stock.balanceOf(mm),stockBefore); assertGt(stock.balanceOf(writer),0); _conservation();
    }

    function testOptionsRisk_EarnRejectsNetShareAndDoubleCollateral() public {
        _setup("TSLA",1,50);
        CoveredCallDesk.Terms memory t=_terms(initialStock,400e6,100e6,block.timestamp+7 days);
        t.mode=CoveredCallDesk.Settlement.NetShare;
        vm.expectRevert(EarnVault.BadTerms.selector); vault.offer(t);
        _open(0);
        t.mode=CoveredCallDesk.Settlement.Physical;
        vm.expectRevert(EarnVault.Busy.selector); vault.offer(t);
        vm.expectRevert(EarnVault.Busy.selector); vault.closeEpoch();
        assertEq(stock.balanceOf(address(desk)),initialStock); _conservation();
    }

    function _riskCallExpiry(bool itm) internal returns(uint80 round) {
        uint256 expiry=desk.getOption(activeId).expiry;
        uint256 p=itm?500e18:300e18;
        round=_feed(p,expiry); _feed(p,expiry+1); vm.warp(expiry+301);
    }

    function testOptionsRisk_FrozenVaultOwedBlocksCloseThenRecovers() public {
        _setup("TSLA",1,50); _open(0);
        uint80 round=_riskCallExpiry(false); stock.freeze(writer,true);
        desk.settle(activeId,round);
        assertEq(desk.owed(address(stock),writer),initialStock);
        vm.expectRevert(EarnVault.UnsafeAssets.selector); vault.closeEpoch();
        stock.freeze(writer,false); vault.claimDeskOwed(address(stock));
        assertEq(desk.owed(address(stock),writer),0); vault.closeEpoch();
        assertEq(stock.balanceOf(writer),initialStock); _conservation();
    }

    function testOptionsRisk_FrozenRedeemerGetsCashAndCanRecoverStock() public {
        _setup("TSLA",1,50); _open(0);
        uint80 round=_riskCallExpiry(false); desk.settle(activeId,round); vault.closeEpoch();
        uint256 epoch=vault.currentEpoch(); uint256 shares=vault.balanceOf(alice)/2;
        vm.prank(alice); vault.requestRedeem(shares); vault.closeEpoch();
        stock.freeze(alice,true);
        vm.prank(alice); (uint256 promisedStock,uint256 cash)=vault.claimRedeem(epoch,alice);
        assertGt(cash,0); assertEq(usdg.balanceOf(alice),cash); assertEq(promisedStock,0);
        uint256 owedStock=vault.owedRedeemStock(alice); assertGt(owedStock,0);
        assertEq(stock.balanceOf(alice),0);
        stock.freeze(alice,false);
        vm.prank(alice); vault.claimOwedRedeemStock(alice);
        assertEq(stock.balanceOf(alice),owedStock); assertEq(vault.owedRedeemStock(alice),0);
    }

    function testOptionsRisk_InsufficientCounterpartyCashCannotFill() public {
        _setup("TSLA",1,50);
        CoveredCallDesk.Terms memory t=_terms(initialStock,400e6,100_001e6,block.timestamp+7 days);
        uint256 id=vault.offer(t);
        vm.prank(mm); vm.expectRevert(); desk.fill(id);
        assertEq(uint8(desk.getOption(id).state),uint8(CoveredCallDesk.State.Offered));
        assertEq(usdg.balanceOf(mm),100_000e6); assertEq(usdg.balanceOf(writer),0);
        assertEq(stock.balanceOf(address(desk)),initialStock); _conservation();
    }

    function testOptionsRisk_PutStockRefusalPreservesCashUntilActualDelivery() public {
        _setup("TSLA",2,50); _open(0);
        uint80 round=_riskCallExpiry(true); desk.settle(activeId,round);
        vm.prank(mm); desk.exercise(activeId,0,mm); vault.closeEpoch();
        uint256 size=Math.mulDiv(usdg.balanceOf(writer),1e18,190e6);
        uint256 expiry=block.timestamp+7 days;
        _feed(200e18,block.timestamp);
        CashSecuredPutDesk.Terms memory p=CashSecuredPutDesk.Terms(mm,address(stock),uint128(size),190e6,100e6,uint64(expiry),uint64(block.timestamp+300),address(stockFeed),putDesk.exerciseWindow(),1e18);
        uint256 id=vault.offerPut(p); vm.prank(mm); putDesk.fill(id);
        round=_feed(100e18,expiry); _feed(100e18,expiry+1); vm.warp(expiry+301);
        putDesk.settle(id,round); uint256 collateral=usdg.balanceOf(address(putDesk));
        stock.freeze(mm,true);
        vm.prank(mm); vm.expectRevert(); putDesk.exercise(id,0,mm);
        assertEq(usdg.balanceOf(address(putDesk)),collateral); assertEq(stock.balanceOf(writer),0);
        assertEq(uint8(putDesk.getOption(id).state),4);
        stock.freeze(mm,false); vm.prank(mm); putDesk.exercise(id,0,mm);
        assertEq(stock.balanceOf(writer),size); assertEq(usdg.balanceOf(address(putDesk)),0);
        vault.closeEpoch(); _conservation();
    }

    function testOptionsBridge_ActualEarnRedemptionFundsIsolatedStaking() public {
        _setup("TSLA",1,50); _open(0);
        uint256 expiry=desk.getOption(activeId).expiry;
        uint80 round=_feed(prices[0],expiry); _feed(prices[0],expiry+1); vm.warp(expiry+301);
        desk.settle(activeId,round); vault.closeEpoch();
        assertEq(stock.balanceOf(writer),initialStock,"zero price change, stock principal returned");
        assertEq(usdg.balanceOf(writer),premiums,"only actual MM-paid, expired premium is cash income");
        uint256 epoch=vault.currentEpoch(); uint256 shares=vault.balanceOf(alice);
        vm.prank(alice); vault.requestRedeem(shares); vault.closeEpoch();
        vm.prank(alice); (,uint256 cash)=vault.claimRedeem(epoch,alice);
        assertGt(cash,0); assertLe(cash,premiums);
        FreezableToken fun=new FreezableToken("ISOLATED_TEST_FUN",18);
        address staker=makeAddr("independent-fun-staker"); fun.mint(staker,100 ether);
        FunStakingIncome income=new FunStakingIncome(IERC20(address(fun)),IERC20(address(usdg)),alice,7 days,7 days);
        vm.startPrank(staker); fun.approve(address(income),100 ether); income.stake(100 ether); vm.stopPrank();
        vm.startPrank(alice); usdg.approve(address(income),cash); income.fund(cash); vm.stopPrank();
        assertEq(usdg.balanceOf(alice),0); assertEq(usdg.balanceOf(address(income)),cash);
        vm.warp(block.timestamp+7 days); vm.prank(staker); uint256 claimed=income.claim(staker);
        assertApproxEqAbs(claimed,cash,1); assertEq(usdg.totalSupply(),totalUsdgSupply);
        assertEq(income.totalClaimed()+usdg.balanceOf(address(income)),cash);
        assertEq(fun.balanceOf(address(income)),100 ether,"stake principal intact");
        string memory n="bridge"; vm.serializeUint(n,"actualMmPremiumRaw",premiums); vm.serializeUint(n,"actualRedeemedCashRaw",cash);
        vm.serializeUint(n,"actualStakingFundingRaw",income.totalFunded()); vm.serializeUint(n,"actualStakerClaimRaw",claimed);
        console2.log("OPTIONS_STAKING_BRIDGE",vm.serializeBool(n,"deployedFunV4Integration",false));
    }
}

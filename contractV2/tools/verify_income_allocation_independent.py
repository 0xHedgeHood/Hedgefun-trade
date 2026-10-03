#!/usr/bin/env python3
"""Independent sponsor cost-basis, income eligibility, staking and FUN/LP audit.

Reconstructs option transfers and rewards from logged terms and timestamps.
Never imports the producer/exporter or signs/sends transactions.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
from decimal import Decimal, getcontext
import hashlib
import gzip
import json
from pathlib import Path
import re

getcontext().prec = 70
ROOT = Path(__file__).resolve().parents[2]
E18, E27, E30, DURATION = 10**18, 10**27, 10**30, 7*86400
def n(r, f): return int(r[f])
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def ceil_div(a,b): return (a+b-1)//b
def key(r): return r['ticker'],n(r,'dividendBps')


def audit_export(folder, log, profiles, daily, options, epochs, check):
    folder=folder.resolve()
    summary=json.loads((folder/'summary.json').read_text())
    ledger=json.loads(gzip.decompress((folder/'ledger.json.gz').read_bytes()))
    check(summary['executionLogSha256']==hashlib.sha256(log.encode()).hexdigest(),'export_log_provenance')
    check(gzip.decompress((folder/'forge.log.gz').read_bytes())==log.encode(),'export_archived_raw_log_exact')
    def record_key(r): return (*key(r),n(r,'dayIndex') if 'dayIndex' in r else -1)
    normalized=lambda r:{k:int(v) if isinstance(v,str) and re.fullmatch(r'-?\d+',v) else v for k,v in r.items()}
    fields=0
    for label,raw in [('profiles',profiles),('daily',daily),('offers',options),('epochs',epochs)]:
        actual={record_key(r):r for r in ledger[label]}; expected={record_key(r):normalized(r) for r in raw}
        check(len(actual)==len(raw)==len(ledger[label]) and set(actual)==set(expected),'export_raw_record_set',label)
        for k,r in expected.items():
            check(set(actual[k])==set(r),'export_raw_field_set',(label,k))
            for f,v in r.items():
                check(actual[k][f]==v,'export_raw_exact_field',(label,k,f)); fields+=1
    pmap={key(p):p for p in profiles}; dmap=defaultdict(list); emap=defaultdict(list)
    for r in daily:dmap[key(r)].append(r)
    for e in epochs:emap[key(e)].append(e)
    check(len(summary['cases'])==9 and {key(c) for c in summary['cases']}==set(pmap),'export_nine_summary_cases')
    errors=[]
    for c in summary['cases']:
        k=key(c); first,last=dmap[k][0],dmap[k][-1]; p=pmap[k]
        pct=lambda end,start:(Decimal(end)/start-1)*100
        expected={'days':250,'initialProductCapitalUsd':Decimal(n(p,'productInitialExternalAssetsRaw'))/10**6,
            'stockReturnPct':pct(n(last,'stockOracleUsdE18'),n(first,'stockOracleUsdE18')),
            'funMarkReturnPct':pct(n(last,'funOracleUsdE18'),n(first,'funOracleUsdE18')),
            'funVsStockRatioChangePct':pct(n(last,'funStockE18'),n(first,'funStockE18')),
            'terminalProductReturnPct':pct(n(last,'productGrossPlusPaidRaw'),n(p,'productInitialExternalAssetsRaw')),
            'lpStockUnitsChangePct':pct(n(last,'lpStockRaw'),n(first,'lpStockRaw')),
            'stakingPaidUsd':Decimal(n(last,'dividendsPaidARaw')+n(last,'dividendsPaidBRaw'))/10**6,
            'settlements':50,'blockedAllocationEpochs':sum(n(e,'eligibleIncomeRaw')==0 for e in emap[k])}
        for f,raw in {'terminalProductExternalAssetsPlusPaidUsd':'productGrossPlusPaidRaw','premiumsReceivedUsd':'optionPremiumsRaw',
            'settledPortfolioRealizedNetUsd':'cumulativeRealizedNetRaw','eligibleIncomeAllocatedUsd':'totalAllocatedRaw',
            'sponsorBuybackUsd':'sponsorBuybackCashRaw','stakingFundedUsd':'stakingFundedRaw','stakingRemainingCashUsd':'stakingCashRaw',
            'stakerACashUsd':'dividendsPaidARaw','stakerBCashUsd':'dividendsPaidBRaw','sponsorFinalCashUsd':'sponsorCashRaw',
            'sponsorFinalNavUsd':'sponsorGrossAssetsRaw'}.items(): expected[f]=Decimal(n(last,raw))/10**6
        for f,raw in {'lpStockUnits':'lpStockRaw','lpFunUnits':'lpFunRaw','sponsorFunBurned':'sponsorBurnedRaw',
                      'sponsorLpStockFeesUnits':'sponsorLpStockFeesRaw'}.items():expected[f]=Decimal(n(last,raw))/E18
        check(set(c)==set(expected)|{'ticker','dividendBps'},'export_summary_field_set',k)
        for f,v in expected.items():
            observed=Decimal(str(c[f])); error=abs(observed-v)
            # JSON floats are display fields; all raw amounts above match exactly.
            tolerance=max(Decimal('1e-10'),abs(Decimal(v))*Decimal('3e-16'))
            check(observed.is_finite() and error<=tolerance,'export_summary_independent_formula',(k,f,str(error)))
            errors.append(error)
    return {'status':'PASS','rawRecords':len(profiles)+len(daily)+len(options)+len(epochs),'rawFieldsCompared':fields,
        'summaries':9,'displayTolerance':'max(1e-10, abs(value) * 3e-16), raw integers exact',
        'maximumDisplayAbsoluteError':str(max(errors)),
        'files':{str(p.relative_to(ROOT)):sha(p) for p in (folder/'summary.json',folder/'ledger.json.gz',folder/'forge.log.gz',ROOT/'contractV2/tools/income_allocation_report.py')}}


def audit_calendar(path, data, check):
    """Independently recompute each counterfactual without importing its producer."""
    path=path.resolve()
    report=json.loads(path.read_text()); output=[]
    check(report['status']=='PASS' and report['broadcast'] is False,'calendar_report_status_scope')
    check(len(report['comparisons'])==3,'calendar_three_tickers')
    for comp in report['comparisons']:
        ticker=comp['ticker']; window=data['windows'][ticker]
        prices=list(map(int,window['pricesE18'])); elapsed=window['elapsedSeconds']; variants=[]
        for variant in range(5):
            starts=[0]; ends=[]
            while starts[-1]<249:
                start=starts[-1]; end=min(start+5,249)
                if variant==0:
                    end=max(j for j in range(start+1,end+1) if elapsed[j]-elapsed[start]<=9*86400)
                ends.append(end); starts.append(end)
            units=10_000*10**6*E30//prices[0]; initial=units*prices[0]//E30
            cash=premiums=cost_total=allocated=option_pnl=0; periods=[]
            for start,end in zip(starts,ends):
                tenor=elapsed[end]-elapsed[start]+(601 if start==0 and variant>=3 else 0)-(301 if start else 0)
                strike=ceil_div(prices[start]*105,10**14)
                notional=units*prices[start]//E30
                premium=ceil_div(notional*tenor,DURATION*200)
                if variant<2: premium=max(premium,ceil_div(notional,400))
                expiry=prices[end]//10**12
                delivered=units*max(0,expiry-strike)//expiry
                cost=ceil_div(delivered*prices[0],E30); payout=delivered*prices[end]//E30
                units-=delivered; cash+=premium; premiums+=premium; cost_total+=cost; option_pnl+=premium-payout
                nav=cash+units*prices[end]//E30
                allocation=min(max(0,premiums-cost_total-allocated),cash,max(0,nav-initial)) if variant==4 else 0
                allocated+=allocation;cash-=allocation
                periods.append(dict(startIndex=start,endIndex=end,tenorSeconds=tenor,premiumRaw=premium,strikeUsdgRaw=strike,
                    disposedStockRaw=delivered,stockAfterRaw=units,disposedCostRaw=cost,deliveryAtExpiryMarkRaw=payout,eligibleIncomeRaw=allocation))
            model=dict(periodCount=len(periods),terminalStockRaw=units,terminalCashRaw=cash,premiumRaw=premiums,
                disposedHistoricalCostRaw=cost_total,historicalCostRealizedPnlRaw=premiums-cost_total,expiryMarkOptionLegPnlRaw=option_pnl,
                allocatedRaw=allocated,terminalAssetsRaw=cash+units*prices[-1]//E30)
            row=comp['waterfall'][variant]
            for f,v in model.items():check(n(row,f)==v,'calendar_independent_variant_metric',(ticker,variant,f))
            check(row['isAdditionalEvmRun'] is False,'calendar_counterfactual_not_evm_claim',(ticker,variant))
            check(n(row,'deltaFromPreviousRaw')==(model['terminalAssetsRaw']-variants[-1]['terminalAssetsRaw'] if variants else 0),'calendar_delta_exact',(ticker,variant))
            if variant in (0,4):
                actual=comp['optionsActualPeriods' if variant==0 else 'incomeActualPeriods']
                check(len(actual)==len(periods),'calendar_actual_period_counts',(ticker,variant))
                for i,(a,e) in enumerate(zip(actual,periods)):
                    for f,v in e.items():check(n(a,f)==v,'calendar_independent_period_field',(ticker,variant,i,f))
            variants.append(model)
        output.append({'ticker':ticker,'terminalAssetsRawByVariant':[v['terminalAssetsRaw'] for v in variants]})
    provenance=report['provenance']
    targets={'dataSha256':ROOT/'contractV2/data/equity-history-2025.json',
        'optionsHarnessSha256':ROOT/'contractV2/test/OptionsYearlyReplay.t.sol',
        'incomeHarnessSha256':ROOT/'contractV2/test/IncomeAllocationFork.t.sol',
        'optionsRawLogSha256':ROOT/'artifacts/all-strategy-income-20261003/options/forge.log',
        'incomeRawLogSha256':ROOT/'artifacts/all-strategy-income-20261003/income/forge.log',
        'crosscheckToolSha256':ROOT/'contractV2/tools/options_income_crosscheck.py'}
    for f,p in targets.items():check(provenance[f]==sha(p),'calendar_provenance',f)
    return {'status':'PASS','tickers':3,'arithmeticVariants':15,'actualPeriodsCompared':306,
        'file':str(path.relative_to(ROOT)),'sha256':sha(path),'independentTerminalWaterfalls':output,
        'scope':'Two endpoint ledgers independently verified by the options and income raw audits; middle variants are arithmetic counterfactuals, not new EVM runs.'}


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--log',type=Path,default=ROOT/'artifacts/all-strategy-income-20261003/income/forge.log')
    ap.add_argument('--output',type=Path,default=ROOT/'artifacts/all-strategy-income-20261003/income/independent-review.json')
    ap.add_argument('--export-dir',type=Path)
    ap.add_argument('--calendar-crosscheck',type=Path)
    args=ap.parse_args(); log=args.log.read_text()
    source=ROOT/'contractV2/test/IncomeAllocationFork.t.sol'; staking=ROOT/'contractV2/src/experimental/FunStakingIncome.sol'
    data_path=ROOT/'contractV2/data/equity-history-2025.json'; data=json.loads(data_path.read_text())
    counts=Counter()
    def check(ok,label,context=None):
        if not ok: raise AssertionError(f'{label}: {context}')
        counts[label]+=1
    def records(label):
        return [json.loads(s.split(label+' ',1)[1]) for s in log.splitlines() if label+' ' in s]
    profiles,raw_rows,options,epochs=[records('INCOME_'+x) for x in ('PROFILE','ROW','OPTION','EPOCH')]
    expected={(t,b) for t in ('TSLA','NVDA','META') for b in (0,5000,10000)}
    check(len(profiles)==9 and {key(p) for p in profiles}==expected,'nine_frozen_allocation_cases')
    check(len(raw_rows)==2250,'2250_complete_daily_rows')
    check(len(re.findall(r'^\[PASS\] testIncomeAllocation_',log,re.M))==9 and '[FAIL' not in log,'nine_passes_no_failure')
    rows,terms,closes=defaultdict(list),defaultdict(list),defaultdict(list)
    for r in raw_rows: rows[key(r)].append(r)
    for o in options: terms[key(o)].append(o)
    for e in epochs: closes[key(e)].append(e)
    metrics=[]
    for p in profiles:
        k=key(p); ticker,split=k; rs=rows[k]; cs=closes[k]; os=terms[k]
        check(len(rs)==250 and [n(r,'dayIndex') for r in rs]==list(range(250)),'ordered_full_year',k)
        w=data['windows'][ticker]; prices=[int(x) for x in w['pricesE18']]
        initial=n(p,'sponsorInitialStockRaw'); basis=n(p,'stockCostE18'); capital=n(p,'sponsorInitialCapitalRaw')
        check(basis==prices[0] and initial==10_000*10**6*E30//basis and capital==initial*basis//E30,'independent_sponsor_initial_capital',k)
        check(abs(n(p,'productInitialExternalAssetsRaw')-30_000*10**6)<=3,'extra_sponsor_included_in_capital_denominator',k)
        check(n(p,'counterpartyInitialCashRaw')==100_000*10**6,'single_funded_counterparty',k)
        opens={n(o,'dayIndex'):o for o in os}; ends={n(e,'dayIndex'):e for e in cs}
        check(len(opens)==len(os)==len(cs)==len(ends)==50,'fifty_distinct_completed_options',k)
        start_at=n(rs[0],'evmTimestamp')
        # Fixed-price-lot cost ledger; no later stock buys reset sponsor basis.
        held,escrow,cash,disposed,premiums,realized,allocated,funded,buycash,settled=initial,0,0,0,0,0,0,0,0,0
        active=None
        # Independently replay a constant two-account stake, using actual row clock.
        qa,qb=n(p,'stakedARaw'),n(p,'stakedBRaw'); total_stake=qa+qb
        rate,queue,finish,last_update,index,paid_index,paid_a,paid_b=0,0,start_at,start_at,0,0,0,0
        eligible_path=[]
        for i,r in enumerate(rs):
            ctx=(*k,i); price=prices[i]; at=n(r,'evmTimestamp')
            check(r['date']==w['dates'][i] and n(r,'stockOracleUsdE18')==price,'daily_close_mapping',ctx)
            expected_at=start_at+w['elapsedSeconds'][i]+(601 if i else 0)+(301 if i in ends else 0)
            check(at==expected_at,'actual_synthetic_evm_clock',ctx)
            # Global staking index accrues to this actual timestamp before funding/claims.
            through=min(at,finish)
            if through>last_update:
                index+=(through-last_update)*rate*10**9//total_stake
                last_update=through
            if i in ends:
                e=ends[i]; check(active is not None and n(active,'endIndex')==i,'settlement_has_active_option',ctx)
                q,strike=n(active,'optionSizeRaw'),n(active,'strikeUsdgRaw'); p6=price//10**12
                delivered=q*max(0,p6-strike)//p6
                disposed_cost=ceil_div(delivered*basis,E30)
                disposed+=delivered; held=q-delivered; escrow=0
                realized+=n(active,'premiumRaw')-disposed_cost; settled+=1
                nav=cash+held*price//E30
                available=min(max(realized-allocated,0),cash,max(nav-capital,0))
                for f,value in dict(optionSizeRaw=q,strikeUsdgRaw=strike,expiryPriceE18=price,
                    offerTimestamp=n(active,'offerTimestamp'),expiryTimestamp=n(active,'expiryTimestamp'),premiumRaw=n(active,'premiumRaw'),
                    disposedStockRaw=delivered,disposedCostRaw=disposed_cost,cumulativeRealizedNetRaw=realized,
                    allocatedBeforeRaw=allocated,sponsorClosedNavBeforeRaw=nav,eligibleIncomeRaw=available).items():
                    check(n(e,f)==value,'independent_settlement_income_gate',(*ctx,f))
                dividend=available*split//10000
                buy=available-dividend
                allocated+=available; cash-=available; funded+=dividend; buycash+=buy
                check(cash+held*price//E30>=capital or available==0,'allocation_never_breaks_retained_capital',ctx)
                if dividend:
                    budget=dividend*E18+queue+max(finish-at,0)*rate
                    rate,queue=divmod(budget,DURATION); finish=at+DURATION; last_update=at
                eligible_path.append(available); active=None
            # Both fixed stakes claim daily at the same timestamp; no raw-unit claim is counted twice.
            delta=index-paid_index
            paid_a+=qa*delta//E27; paid_b+=qb*delta//E27; paid_index=index
            if i in opens:
                o=opens[i]; check(active is None,'no_overlapping_sponsor_options',ctx)
                end=min(i+5,249); expiry=start_at+w['elapsedSeconds'][end]+601
                strike=ceil_div(price*10500,10**12*10000)
                premium=ceil_div((held*price//E30)*50*(expiry-at),10000*DURATION)
                for f,value in dict(endIndex=end,offerTimestamp=at,expiryTimestamp=expiry,offerPriceE18=price,
                                    optionSizeRaw=held,strikeUsdgRaw=strike,premiumRaw=premium).items():
                    check(n(o,f)==value,'independent_ex_ante_sponsor_terms',(*ctx,f))
                premiums+=premium; cash+=premium; escrow=held; held=0; active=o
            expected_daily=dict(sponsorStockRaw=held,sponsorCollateralRaw=escrow,sponsorCashRaw=cash,
                optionsSettled=settled,optionPremiumsRaw=premiums,cumulativeRealizedNetRaw=realized,totalAllocatedRaw=allocated,
                sponsorBuybackCashRaw=buycash,stakingFundedRaw=funded,stakingCashRaw=funded-paid_a-paid_b,
                dividendsPaidARaw=paid_a,dividendsPaidBRaw=paid_b,claimableARaw=0,claimableBRaw=0)
            for f,value in expected_daily.items(): check(n(r,f)==value,'independent_sponsor_and_staking_cash_ledger',(*ctx,f))
            check(held+escrow+disposed==initial and cash+allocated==premiums,'sponsor_stock_and_cash_conservation',ctx)
            check(r['hasOpenOption']==(active is not None),'open_position_state',ctx)
            intrinsic=(n(active,'optionSizeRaw')*max(price//10**12-n(active,'strikeUsdgRaw'),0)//E18 if active else 0)
            check(n(r,'optionIntrinsicLiabilityFloorRaw')==intrinsic,'intrinsic_floor_only',ctx)
            sponsor_gross=(held+escrow)*price//E30+cash
            check(n(r,'sponsorGrossAssetsRaw')==sponsor_gross,'sponsor_gross_asset_mark',ctx)
            stock_assets=n(r,'treasuryStockRaw')+n(r,'lpStockRaw')+n(r,'uncollectedLpStockRaw')
            fund_assets=stock_assets*price//E30+n(r,'treasuryUsdgRaw')
            check(n(r,'fundExternalAssetsRaw')==fund_assets,'independent_fun_fund_external_assets',ctx)
            check(n(r,'funOracleUsdE18')==n(r,'funStockE18')*price//E18,'fun_usd_oracle_mark_formula',ctx)
            check(abs(n(r,'funStockE18')-n(r,'lpStockRaw')*E18//n(r,'lpFunRaw'))<=1,'independent_v4_principal_price_ratio',ctx)
            check(n(r,'stockPoolLiquidity')==n(p,'expectedV3Liquidity'),'fixed_actual_v3_active_liquidity',ctx)
            check(n(r,'totalSupplyRaw')+sum(n(r,f) for f in ('fundBurnedRaw','hookBurnedRaw','collectedLpFunBurnedRaw','sponsorBurnedRaw'))==n(p,'initialSupplyRaw'),'all_four_burn_sources',ctx)
            for asset,collected in (('Stock','collectedLpStockRaw'),('Fun','collectedLpFunBurnedRaw')):
                sponsor_fee=n(r,'sponsorLpStockFeesRaw' if asset=='Stock' else 'sponsorLpTokenFeesRaw')
                generated=sum(n(r,s+'GeneratedLp'+asset+'Raw') for s in ('external','buyback','conversion'))+sponsor_fee
                check(n(r,collected)+n(r,'uncollectedLp'+asset+'Raw')==generated,'all_four_lp_fee_origins_conserved',(*ctx,asset))
            check(n(r,'productGrossPlusPaidRaw')==fund_assets+sponsor_gross+funded,'product_assets_plus_paid_cash_once',ctx)
            check(n(r,'flowPairs')==i,'matched_daily_external_flow_count',ctx)
            if split==10000: check(n(r,'sponsorBurnedRaw')==n(r,'sponsorLpStockFeesRaw')==n(r,'sponsorLpTokenFeesRaw')==0,'all_dividend_no_sponsor_buyback',ctx)
            if split==0: check(funded==paid_a==paid_b==0,'all_buyback_no_cash_dividend',ctx)
        check(active is None and held+disposed==initial,'terminal_no_option_liability',k)
        last=rs[-1]; product_initial=n(p,'productInitialExternalAssetsRaw'); product_final=n(last,'productGrossPlusPaidRaw')
        metrics.append({'ticker':ticker,'dividendBps':split,'initialProductCapitalRaw':product_initial,'finalSettledProductAssetsPlusPaidRaw':product_final,
            'settledProductReturnPercent':str((Decimal(product_final)/product_initial-1)*100),
            'funMarkChangePercent':str((Decimal(n(last,'funOracleUsdE18'))/n(rs[0],'funOracleUsdE18')-1)*100),
            'cumulativeRealizedNetRaw':realized,'totalEligibleAllocatedRaw':allocated,'stakingFundedRaw':funded,
            'actualDividendsPaidRaw':paid_a+paid_b,'unpaidStakingCashRaw':funded-paid_a-paid_b,'sponsorBuybackCashRaw':buycash,
            'sponsorBurnedRaw':n(last,'sponsorBurnedRaw'),'eligibleByEpochRaw':eligible_path,
            'finalSponsorAssetsRaw':n(last,'sponsorGrossAssetsRaw'),'finalFundExternalAssetsRaw':n(last,'fundExternalAssetsRaw')})
    for ticker in ('TSLA','NVDA','META'):
        ms=[m for m in metrics if m['ticker']==ticker]
        for field in ('initialProductCapitalRaw','cumulativeRealizedNetRaw','totalEligibleAllocatedRaw','eligibleByEpochRaw','finalSponsorAssetsRaw'):
            check(all(m[field]==ms[0][field] for m in ms),'matched_sponsor_economics_identical_across_splits',(ticker,field))
        for field in ('stakedARaw','stakedBRaw','sponsorInitialStockRaw','stockCostE18','initialSupplyRaw','expectedV3Liquidity'):
            subset=[p for p in profiles if p['ticker']==ticker]
            check(len({str(p[field]) for p in subset})==1,'matched_initial_capital_and_stakes',(ticker,field))
    result={'schema':'hedgefun-income-allocation-independent-review-v1','status':'PASS_RAW_LEDGER',
        'evidence':{str(p.relative_to(ROOT)):sha(p) for p in (source,staking,data_path,args.log,Path(__file__).resolve())},
        'totals':{'cases':9,'dailyRows':len(raw_rows),'settledOptions':len(epochs),'openedOptions':len(options)},
        'checks':dict(sorted(counts.items())),'assertionCount':sum(counts.values()),'independentMetrics':metrics,
        'scopeLimits':['Separate sponsor contributes $10k capital in addition to the $20k FUN fund; not a deployed treasury withdrawal or options engine.',
            'Fixed hypothetical 50bps/week NetShare quote and synthetic open-calendar feed; no historical option RFQ/IV evidence.',
            'Realized cost-basis income deducts actual delivered-stock original cost and carries cumulative losses; it differs from premium minus expiry intrinsic.',
            'Daily row zero includes a just-opened option premium. Only terminal no-option assets support net return, divided by metadata initial $30k capital.',
            'Staking funded, paid, and remaining cash are separate; streaming still pending at year end is not cash already received.',
            'Four LP fee origins include external trader, hook conversion, fund buyback, and sponsor buyback. Self fees are not additional external profit.',
            'Marginal FUN marks and locked LP assets are not executable redemption proceeds. No modeled gas charge or corporate cash dividends.',
            'Source-reviewed assertions complement raw accounting; this does not independently replay every V3/V4 instruction.'], 'blockingFindings':[]}
    if args.export_dir:
        result['exportCrosscheck']=audit_export(args.export_dir,log,profiles,raw_rows,options,epochs,check)
        result['status']='PASS'
    if args.calendar_crosscheck:
        result['calendarCrosscheck']=audit_calendar(args.calendar_crosscheck,data,check)
    result['checks']=dict(sorted(counts.items())); result['assertionCount']=sum(counts.values())
    args.output.parent.mkdir(parents=True,exist_ok=True); args.output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({'status':result['status'],'totals':result['totals'],'assertionCount':result['assertionCount'],'output':str(args.output)}))


if __name__=='__main__': main()

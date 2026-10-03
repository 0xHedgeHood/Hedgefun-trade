#!/usr/bin/env python3
"""Read-only integer reconstruction of two frozen NetShare calendar rules.

Recomputes actual stock delivery and premium without calling either harness.
Counterfactual waterfall rows are arithmetic reconstructions, NOT extra EVM runs.
No public transactions, source edits, price downloads or parameter selection.
"""
from __future__ import annotations
import argparse
import gzip
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
SCALE=10**30


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def ceildiv(a,b):return (a+b-1)//b

def require(value,message):
    if not value:raise ValueError(message)


def raw(path):return gzip.decompress(path.read_bytes()).decode() if path.suffix=='.gz' else path.read_text()


def events(text,prefix,names):
    out={name:[] for name in names}
    for line in text.splitlines():
        for name in names:
            tag=prefix+name+' '
            if tag in line:
                item=json.loads(line.split(tag,1)[1])
                out[name].append({k:int(v) if isinstance(v,str) and v.lstrip('-').isdigit() else v for k,v in item.items()})
                break
    return out


def model(window,capped=True,guard=True,first_extra=0,distribute=False):
    price,elapsed=window['pricesE18'],window['elapsedSeconds']
    size=10_000*10**6*SCALE//price[0];initial=size*price[0]//SCALE
    cash=premiums=disposed_cost=allocated=cost_pnl=option_pnl=0
    index=0;periods=[]
    while index<249:
        end=min(index+5,249)
        if capped:
            while elapsed[end]-elapsed[index]>9*86400:end-=1
        # Both settle at expiry+301s; the income harness adds 601s to both
        # future expiries and post-first offers, so only its first quote is longer.
        tenor=elapsed[end]-elapsed[index]+(first_extra if index==0 else -301)
        strike=ceildiv(price[index]*10500,10**16)
        notional=size*price[index]//SCALE
        premium=ceildiv(notional*50*tenor,10_000*7*86400)
        if guard:premium=max(premium,ceildiv(notional*25,10_000))
        settlement_price=price[end]//10**12
        disposed=size*(settlement_price-strike)//settlement_price if settlement_price>strike else 0
        cost=ceildiv(disposed*price[0],SCALE)
        payout=disposed*price[end]//SCALE
        premiums+=premium;cash+=premium;disposed_cost+=cost
        cost_pnl+=premium-cost;option_pnl+=premium-payout;size-=disposed
        nav=size*price[end]//SCALE+cash
        allocation=min(max(0,cost_pnl-allocated),cash,max(0,nav-initial)) if distribute else 0
        allocated+=allocation;cash-=allocation
        periods.append({'startIndex':index,'endIndex':end,'tenorSeconds':tenor,'premiumRaw':premium,
                        'strikeUsdgRaw':strike,'disposedStockRaw':disposed,'stockAfterRaw':size,
                        'disposedCostRaw':cost,'deliveryAtExpiryMarkRaw':payout,'eligibleIncomeRaw':allocation})
        index=end
    return {'periodCount':len(periods),'terminalStockRaw':size,'terminalCashRaw':cash,'premiumRaw':premiums,
            'disposedHistoricalCostRaw':disposed_cost,'historicalCostRealizedPnlRaw':cost_pnl,
            'expiryMarkOptionLegPnlRaw':option_pnl,'allocatedRaw':allocated,
            'terminalAssetsRaw':cash+size*price[-1]//SCALE,'periods':periods}


def safe(value):
    if isinstance(value,dict):return {k:safe(v) for k,v in value.items()}
    if isinstance(value,list):return [safe(v) for v in value]
    return str(value) if type(value)is int and abs(value)>2**53-1 else value


def crosscheck(options,income,data):
    result=[]
    for ticker in ('TSLA','NVDA','META'):
        w=data['windows'][ticker]
        old_rows=[r for r in options['ROW'] if r['ticker']==ticker and r['profile']=='netshare']
        old_opts=sorted([r for r in options['CONTRACT'] if r['ticker']==ticker and r['profile']=='netshare'],key=lambda r:r['number'])
        require(len(old_rows)==250 and len(old_opts)==52,'wrong NetShare baseline counts')
        variants=[model(w),model(w,capped=False),model(w,capped=False,guard=False),
                  model(w,capped=False,guard=False,first_extra=601),model(w,capped=False,guard=False,first_extra=601,distribute=True)]
        a=variants[0];f=variants[-1];old=old_rows[-1]
        require((a['terminalAssetsRaw'],a['terminalStockRaw'],a['premiumRaw'])==
                (old['grossAssetsUsdgRaw'],old['writerStockRaw'],old['premiumReceivedUsdgRaw']),'options terminal did not reconstruct')
        require(a['expiryMarkOptionLegPnlRaw']==sum(r['signedSettledOptionPnlUsdgRaw'] for r in old_opts),'options PnL did not reconstruct')
        for expected,actual in zip(a['periods'],old_opts):
            for field in ('startIndex','endIndex','premiumRaw','strikeUsdgRaw'):
                source='premiumUsdgRaw' if field=='premiumRaw' else field
                require(expected[field]==actual[source],f'options period differs: {ticker} {field}')
        for split in (0,5000,10000):
            epochs=[r for r in income['EPOCH'] if r['ticker']==ticker and r['dividendBps']==split]
            rows=[r for r in income['ROW'] if r['ticker']==ticker and r['dividendBps']==split]
            require(len(epochs)==50 and len(rows)==250,'wrong income counts')
            final=rows[-1]
            for field,actual in (('terminalAssetsRaw','sponsorGrossAssetsRaw'),('terminalStockRaw','sponsorStockRaw'),
                                 ('premiumRaw','optionPremiumsRaw'),('allocatedRaw','totalAllocatedRaw'),
                                 ('historicalCostRealizedPnlRaw','cumulativeRealizedNetRaw')):
                require(f[field]==final[actual],f'income terminal differs: {ticker}/{split}/{field}')
            for expected,actual in zip(f['periods'],epochs):
                for field in ('endIndex','premiumRaw','strikeUsdgRaw','disposedStockRaw','disposedCostRaw','eligibleIncomeRaw'):
                    source='dayIndex' if field=='endIndex' else field
                    require(expected[field]==actual[source],f'income period differs: {ticker}/{split}/{field}')
        labels=['options_capped_calendar_actual','only_switch_to_uncapped_five_observations',
                'also_remove_premium_guard','also_add_first_quote_601_seconds','also_apply_income_allocation_actual']
        waterfall=[]
        for i,(name,variant) in enumerate(zip(labels,variants)):
            row={k:v for k,v in variant.items() if k!='periods'}
            row['case']=name;row['deltaFromPreviousRaw']=0 if i==0 else variant['terminalAssetsRaw']-variants[i-1]['terminalAssetsRaw']
            row['isAdditionalEvmRun']=False
            waterfall.append(row)
        first_div=next((x,y) for x,y in zip(a['periods'],variants[1]['periods']) if x['endIndex']!=y['endIndex'])
        result.append({'ticker':ticker,'waterfall':waterfall,
                       'firstCalendarDivergence':{'startDate':w['dates'][first_div[0]['startIndex']],
                                                 'optionsEndDate':w['dates'][first_div[0]['endIndex']],
                                                 'incomeEndDate':w['dates'][first_div[1]['endIndex']],
                                                 'options':first_div[0],'income':first_div[1]},
                       'optionsActualPeriods':a['periods'],'incomeActualPeriods':f['periods']})
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--options-log',type=Path,default=ROOT/'contractV2/deploy/all-strategy-income-2026-10-03/options/forge.log.gz')
    parser.add_argument('--income-log',type=Path,default=ROOT/'artifacts/all-strategy-income-20261003/income/forge.log')
    parser.add_argument('--output',type=Path,default=ROOT/'contractV2/deploy/all-strategy-income-2026-10-03/options/netshare-calendar-crosscheck.json')
    args=parser.parse_args();old_raw=raw(args.options_log);income_raw=raw(args.income_log)
    require('27 passed; 0 failed; 0 skipped' in old_raw,'options tests failed')
    require('9 passed; 0 failed; 0 skipped' in income_raw,'income tests failed')
    old=events(old_raw,'OPTIONS_',('ROW','CONTRACT'));inc=events(income_raw,'INCOME_',('PROFILE','OPTION','EPOCH','ROW'))
    data_path=ROOT/'contractV2/data/equity-history-2025.json';data=json.loads(data_path.read_text())
    findings=crosscheck(old,inc,data)
    result={'schema':'hedgefun-netshare-calendar-crosscheck-v1','status':'PASS','broadcast':False,
            'finding':'Both frozen actual ledgers reproduce exactly. The tenor cap changes later opening dates and strikes. No cash/stock mismatch was found.',
            'pnlBasis':'Options settledOptionPnl uses stock delivery at expiry Close. Income cumulativeRealizedNet uses disposed-stock historical initial cost. These are different decompositions, not different token transfers.',
            'counterfactualScope':'Waterfall intermediate variants are pure integer arithmetic reconstructions; only endpoints correspond to already recorded EVM tests. No rerun or strategy optimization was performed.',
            'provenance':{'dataSha256':sha(data_path),'optionsHarnessSha256':sha(ROOT/'contractV2/test/OptionsYearlyReplay.t.sol'),
                          'incomeHarnessSha256':sha(ROOT/'contractV2/test/IncomeAllocationFork.t.sol'),
                          'optionsRawLogSha256':hashlib.sha256(old_raw.encode()).hexdigest(),
                          'incomeRawLogSha256':hashlib.sha256(income_raw.encode()).hexdigest(),'crosscheckToolSha256':sha(Path(__file__))},
            'comparisons':findings}
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(safe(result),indent=2)+'\n')
    print(json.dumps({'status':'PASS','tickers':3,'actualIncomeArms':9,'output':str(args.output)}))

if __name__=='__main__':main()

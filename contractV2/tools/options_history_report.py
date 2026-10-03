#!/usr/bin/env python3
"""Validate local options/Earn replay; distinguish gross assets from settled NAV."""
from __future__ import annotations
import argparse
from collections import defaultdict
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re

from equity_history_report import event_json, packed_gzip, read_log, serialize_raw, write_csv, digest
from tsla_history_report import require, display, change_percent, drawdown

ROOT = Path(__file__).resolve().parents[2]
D = Decimal
E6, E18, SCALE = 10**6, 10**18, 10**30
TICKERS = ('TSLA', 'NVDA', 'META')
PROFILES = ('netshare', 'physical', 'wheel', 'no_fill', 'no_exercise', 'premium_reinvest')
EXPECTED = {(t, p) for t in TICKERS for p in PROFILES} | {('TSLA', 'wheel_premium25'), ('TSLA', 'wheel_premium100')}
LABELS = {'netshare':'NetShare call', 'physical':'Physical call', 'wheel':'Call/put wheel', 'no_fill':'No fill', 'no_exercise':'No exercise', 'premium_reinvest':'Premium reinvest', 'wheel_premium25':'Wheel 25bps', 'wheel_premium100':'Wheel 100bps'}


def key(row):
    return row['ticker'], row['profile']


def ceildiv(a, b):
    return (a+b-1)//b


def parse(raw):
    require('27 passed; 0 failed; 0 skipped' in raw and '[FAIL' not in raw, 'all 27 tests must pass')
    tests = re.findall(r'\[PASS\]\s+(testOptions[A-Za-z0-9_]+)\(\)', raw)
    require(len(tests) == len(set(tests)) == 27, 'test set differs')
    events = {name: [] for name in ('PROFILE','ROW','CONTRACT','REDEEM','REINVEST','STAKING_BRIDGE')}
    for line in raw.splitlines():
        for name in events:
            marker = 'OPTIONS_'+name+' '
            if marker in line:
                events[name].append(event_json(line.split(marker,1)[1])); break
    require(len(events['PROFILE']) == 20 and len(events['ROW']) == 5000 and len(events['REDEEM']) == 17, 'case/day/redemption count differs')
    require(len(events['STAKING_BRIDGE']) == 1, 'missing isolated actual-cash staking bridge')
    return events


def validate(events, data):
    meta = {key(row):row for row in events['PROFILE']}
    require(len(meta)==20 and set(meta)==EXPECTED, 'missing scenario')
    groups, contracts, reinvest = defaultdict(list), defaultdict(list), defaultdict(list)
    for row in events['ROW']: groups[key(row)].append(row)
    for row in events['CONTRACT']: contracts[key(row)].append(row)
    for row in events['REINVEST']: reinvest[key(row)].append(row)
    require(set(groups)==set(contracts)==EXPECTED, 'unexpected daily or option groups')
    redeems={key(row):row for row in events['REDEEM']}
    schema=set(events['ROW'][0])
    for identity, rows in groups.items():
        ticker, profile=identity; m=meta[identity]; window=data['windows'][ticker]
        require(m['funV4Connected'] is False and m['sourceContractsLocalDeployment'] is True, 'unsupported deployment/integration claim')
        require(m['initialMmUsdgRaw']==100_000*E6 and m['usdgSupplyRaw']==100_000*E6, 'cash must be funded once')
        require(m['initialWriterStockRaw']==10_000*E6*SCALE//window['pricesE18'][0], 'initial principal not equal $10k')
        require(m['stockSupplyRaw']==m['initialWriterStockRaw']+m['initialMmStockRaw']+m['initialPoolStockRaw'], 'initial supply mismatch')
        require(m['callStrikeBps']==10500 and m['putStrikeBps']==9500 and m['premiumGuardFloorBps']==25, 'quote policy changed')
        expected_bps=25 if profile.endswith('25') else 100 if profile.endswith('100') else 50
        require(m['premiumBpsPerSevenDays']==expected_bps, 'premium sensitivity mismatch')
        rows.sort(key=lambda r:r['index']); require([r['index'] for r in rows]==list(range(250)), 'missing/duplicate daily date')
        opts=sorted(contracts[identity],key=lambda r:r['number'])
        require([o['number'] for o in opts]==list(range(1,len(opts)+1)), 'missing/duplicate option')
        for o in opts:
            start,end=o['startIndex'],o['endIndex']; p=window['pricesE18'][start]
            expected_end=min(start+5,249)
            while window['elapsedSeconds'][expected_end]-window['elapsedSeconds'][start]>9*86400: expected_end-=1
            require(o['expiry']==1_800_000_000+window['elapsedSeconds'][expected_end], 'expiry calendar rule changed')
            require(o['fillDeadline']-o['offerTimestamp']==300, 'quote deadline changed')
            notional=o['sizeRaw']*p//SCALE
            require(notional>=E6, 'RFQ below fixed minimum')
            premium=max(ceildiv(notional*expected_bps*(o['expiry']-o['offerTimestamp']),10_000*7*86400),ceildiv(notional*25,10_000))
            require(o['premiumUsdgRaw']==premium, 'synthetic premium cannot be reproduced')
            strike=p*9500//(10**12*10_000) if o['put'] else ceildiv(p*10500,10**12*10_000)
            require(o['strikeUsdgRaw']==strike, 'strike changed')
            require(o['filled']==(profile!='no_fill'), 'unexpected fill policy')
            require(o['endIndex']==(expected_end if o['filled'] else start), 'settlement index mismatch')
            require(o['expiryPriceE18']==(window['pricesE18'][end] if o['filled'] else 0), 'settlement price mismatch')
            payout=0
            if o['state']==5:
                stock_value=o['sizeRaw']*window['pricesE18'][end]//SCALE
                cost=ceildiv(o['sizeRaw']*strike,E18)
                payout=cost-stock_value if o['put'] else stock_value-cost
            elif o['state']==7:
                require(profile=='netshare' and not o['put'], 'net settlement from unsupported profile')
                settlement_price=window['pricesE18'][end]//10**12
                delivered=o['sizeRaw']*(settlement_price-strike)//settlement_price
                payout=delivered*window['pricesE18'][end]//SCALE
            require(payout==o['payoutAtExpiryUsdgRaw'], 'actual settlement value mismatch')
            require(o['signedSettledOptionPnlUsdgRaw']==(premium if o['filled'] else 0)-payout, 'premium mistaken for net option PnL')
            require(o['optionDeskFeesUsdgRaw']==0, 'unmodeled desk fee')
            require(o['closedWriterAssetsUsdgRaw']==o['closedWriterStockRaw']*window['pricesE18'][end]//SCALE+o['closedWriterUsdgRaw'], 'closed assets arithmetic')
            require(o['actualEarnClosedEpochNavUsdgRaw']==(0 if profile=='netshare' else o['closedWriterAssetsUsdgRaw']), 'actual Earn close differs')
        for r in rows:
            i=r['index']; p=r['stockPriceE18']
            require(set(r)==schema, 'daily fields changed')
            require(r['date']==window['dates'][i] and p==window['pricesE18'][i] and r['elapsedSeconds']==window['elapsedSeconds'][i], 'daily input mismatch')
            gross=(r['writerStockRaw']+r['escrowStockRaw'])*p//SCALE+r['writerUsdgRaw']+r['escrowUsdgRaw']
            require(gross==r['grossAssetsUsdgRaw'], 'gross assets formula mismatch')
            require(r['mmAssetsUsdgRaw']==r['mmStockRaw']*p//SCALE+r['mmUsdgRaw'], 'counterparty gross assets mismatch')
            require(sum(r[n] for n in ('writerStockRaw','escrowStockRaw','mmStockRaw','poolStockRaw'))==m['stockSupplyRaw'], 'daily stock conservation')
            require(sum(r[n] for n in ('writerUsdgRaw','escrowUsdgRaw','mmUsdgRaw','poolUsdgRaw'))==m['usdgSupplyRaw'], 'daily cash conservation')
            issued=[o for o in opts if o['startIndex']<i]
            settled=[o for o in issued if o['endIndex']<=i]
            active=[o for o in issued if o['endIndex']>i and o['filled']]
            require(len(active)<=1 and r['hasUnsettledOption']==bool(active), 'concurrent/unreported option')
            require(r['premiumReceivedUsdgRaw']==sum(o['premiumUsdgRaw'] for o in issued if o['filled']), 'daily premium paid differs')
            require(r['optionPayoutAtSettlementUsdgRaw']==sum(o['payoutAtExpiryUsdgRaw'] for o in settled), 'daily actual settlement differs')
            intrinsic=0
            if active:
                o=active[0]; difference=o['strikeUsdgRaw']*10**12-p if o['put'] else p-o['strikeUsdgRaw']*10**12
                intrinsic=o['sizeRaw']*max(0,difference)//SCALE
            require(r['optionIntrinsicLowerBoundUsdgRaw']==intrinsic, 'intrinsic lower bound mismatch')
            trades=[t for t in reinvest[identity] if t['index']<=i]
            require(r['reinvestSpentUsdgRaw']==sum(t['spentUsdgRaw'] for t in trades), 'reinvest cash differs')
            require(r['reinvestStockRaw']==sum(t['receivedStockRaw'] for t in trades), 'reinvest stock differs')
            require(r['reinvestExecutionCostUsdgRaw']==sum(t['executionCostAtTradeMarkUsdgRaw'] for t in trades), 'reinvest fees differ')
            for t in trades:
                require(t['spentUsdgRaw']-t['receivedStockRaw']*window['pricesE18'][t['index']]//SCALE==t['executionCostAtTradeMarkUsdgRaw'], 'actual swap cost mismatch')
            require(r['reinvestSpentUsdgRaw']+r['pendingReinvestCashUsdgRaw']==(r['premiumReceivedUsdgRaw'] if profile=='premium_reinvest' else 0), 'premium-only reinvest budget')
        last=rows[-1]; require(last['hasUnsettledOption'] is False and last['optionIntrinsicLowerBoundUsdgRaw']==0, 'terminal comparison has open liability')
        cash=m['initialMmUsdgRaw']-last['premiumReceivedUsdgRaw']; writer_cash=last['premiumReceivedUsdgRaw']-last['reinvestSpentUsdgRaw']
        stock=m['initialWriterStockRaw']+last['reinvestStockRaw']
        for o in opts:
            if o['state']==5:
                cost=ceildiv(o['sizeRaw']*o['strikeUsdgRaw'],E18)
                cash+=cost if o['put'] else -cost; writer_cash+=-cost if o['put'] else cost
                stock+=o['sizeRaw'] if o['put'] else -o['sizeRaw']
            elif o['state']==7:
                price=o['expiryPriceE18']//10**12
                stock-=o['sizeRaw']*(price-o['strikeUsdgRaw'])//price
        require(cash==last['mmUsdgRaw'] and writer_cash==last['writerUsdgRaw'] and stock==last['writerStockRaw'], 'terminal premium/strike/principal ledger mismatch')
        if profile!='netshare':
            redeem=redeems[identity]; shares=last['investorEarnSharesRaw']; supply=last['earnSharesSupplyRaw']
            require(redeem['stockOutRaw']==last['writerStockRaw']*shares//supply and redeem['usdgOutRaw']==last['writerUsdgRaw']*shares//supply, 'actual share redemption differs')
            require(redeem['stockOutRaw']+redeem['deadShareStockResidualRaw']==last['writerStockRaw'] and redeem['usdgOutRaw']+redeem['deadShareUsdgResidualRaw']==last['writerUsdgRaw'], 'dead-share residual not accounted')
    bridge=events['STAKING_BRIDGE'][0]
    require(0<bridge['actualStakerClaimRaw']<=bridge['actualStakingFundingRaw']==bridge['actualRedeemedCashRaw']<=bridge['actualMmPremiumRaw'], 'bridge funding exceeds actual cash income')
    require(bridge['actualStakingFundingRaw']-bridge['actualStakerClaimRaw']<=1 and bridge['deployedFunV4Integration'] is False, 'bridge dust/integration claim invalid')
    return groups, meta, contracts


def summarize(identity, rows, m, contracts):
    first,last=rows[0],rows[-1]; checkpoints=[r for r in rows if not r['hasUnsettledOption']]
    hold=m['initialWriterStockRaw']*last['stockPriceE18']//SCALE
    mm_hold=m['initialMmStockRaw']*last['stockPriceE18']//SCALE+m['initialMmUsdgRaw']
    pool_hold=m['initialPoolStockRaw']*last['stockPriceE18']//SCALE
    pool_end=last['poolStockRaw']*last['stockPriceE18']//SCALE+last['poolUsdgRaw']
    return {'ticker':identity[0], 'profile':identity[1], 'observations':250, 'settledCheckpoints':len(checkpoints),
            'initialAssetsUsdg':display(D(first['grossAssetsUsdgRaw'])/E6,6), 'terminalSettledAssetsUsdg':display(D(last['grossAssetsUsdgRaw'])/E6,6),
            'terminalSettledAssetsChangePercent':display(change_percent(first['grossAssetsUsdgRaw'],last['grossAssetsUsdgRaw'])),
            'stockCloseChangePercent':display(change_percent(first['stockPriceE18'],last['stockPriceE18'])),
            'checkpointDrawdownPercent':drawdown([r['grossAssetsUsdgRaw'] for r in checkpoints],[r['date'] for r in checkpoints])['percent'],
            'premiumReceivedUsdg':display(D(last['premiumReceivedUsdgRaw'])/E6,6),
            'settledOptionPnlUsdg':display(D(sum(o['signedSettledOptionPnlUsdgRaw'] for o in contracts))/E6,6),
            'reinvestmentExecutionCostUsdg':display(D(last['reinvestExecutionCostUsdgRaw'])/E6,6),
            'writerTerminalDeltaVersusStockHoldUsdg':display(D(last['grossAssetsUsdgRaw']-hold)/E6,6),
            'counterpartyTerminalDeltaVersusInitialHoldUsdg':display(D(last['mmAssetsUsdgRaw']-mm_hold)/E6,6),
            'poolTerminalDeltaVersusInitialHoldUsdg':display(D(pool_end-pool_hold)/E6,6),
            'terminalWriterCashUsdg':display(D(last['writerUsdgRaw'])/E6,6),
            'terminalWriterStock':display(D(last['writerStockRaw'])/E18,18),
            'fills':last['fills'],'assignments':last['assignments'],'lapses':last['lapses'],'netSettlements':last['netSettlements'],
            'calls':last['calls'],'puts':last['puts'],'cancellations':last['cancellations'],
            'terminalHasUnsettledOption':False}


LIMITS=[
 'Source-only contracts instantiated locally, with historical stock Close inputs; not deployed-chain execution or historical options quotations. Synthetic open calendar and oracle rounds do not reproduce exchange expiry timestamps or intraday observations.',
 'RFQs use a specified allowlisted buyer: writer/owner offer and buyer fill perform actual collateral and cash transfers. This desk has no EIP-712 signed-RFQ verification.',
 'The frozen 2025 universe was selected in-sample by equity volume/volatility, not prospectively or by strategy returns. Stock Close excludes dividends; NVDA/META dividends are not distributed in this model.',
 'Call strikes are 105% and put strikes 95% of start Close. Synthetic premium is 50 bps of spot notional per seven actual calendar days, with 25-bps floor; TSLA wheel also shows 25/100-bps sensitivity. No historical IV, auction, RFQ spread or executable quote is claimed.',
 'Each RFQ spans at most five trading observations and no more than nine calendar days. The 2025 market calendar includes holiday intervals longer than the Earn nine-day tenor; shortening is calendar-only. Minimum modeled RFQ and reinvestment are $1 to avoid meaningless dust trades; failed probes are retained separately.',
 'Only initial funding is minted: writer $10k stock, option counterparty $100k cash plus $100k stock, finite reinvestment pool $100k stock. There is no mid-year top-up. Counterparty initial inventory and terminal mark are separate from the writer return.',
 'Daily records are gross assets including escrow and an intrinsic-value LOWER BOUND of open option liabilities. Neither gross assets nor gross minus intrinsic is fair net NAV. Returns use the terminal fully settled position; drawdown is only at settled checkpoints, not an all-day option NAV drawdown.',
 'Per-option settled PnL is premium minus the actual ITM delivery value at settlement (desk fees are zero). Principal strike payments are not premium income. Stock holding mark changes, cash inventory and reinvestment cost remain separate; positive premium alone is not distributable profit.',
 'NetShare uses the legacy CoveredCallDesk directly. Earn only permits physical calls; its intended PhysicalCallDesk and CashSecuredPutDesk are separate implementations. The same share supply cycles cash and stock without double-counting collateral.',
 'Physical-only retains strike cash after assignment and does not buy back stock; wheel uses free cash to collateralize puts. Premium-reinvest converts only received premium after settlement using the actual EarnVaultV3SwapAdapter against a finite flat-price MockPool charging 30bps; this is not V3 historical execution depth or a FUN/V4 LP.',
 'No-fill cancels actual unfilled offers and earns zero premium; no-exercise deliberately makes the buyer lapse ITM rights, so it is a risk-behavior control rather than a rational-option-pricing return forecast.',
 'Earn shares are not FUN dividends. The separate staking demonstration redeems actual cash from one OTM Earn epoch with unchanged stock price, funds experimental FunStakingIncome and claims after seven days. Its test FUN is isolated and is not the deployed FUN/V4 market.',
 'No gas bill, taxes, financing, issuer dividends, exchange option quotes or market impact beyond the stated mock swap fee is modeled. No APY or universally optimal strategy is inferred.'
]


def charts(summaries, output):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    output.mkdir(parents=True,exist_ok=True)
    plt.rcParams.update({'svg.hashsalt':'options-2025','svg.fonttype':'path','figure.facecolor':'#f8fafc','savefig.facecolor':'#f8fafc','font.size':10})
    fig,axes=plt.subplots(1,3,figsize=(15,6),sharey=True)
    colors=['#2563eb','#d97706','#16a34a','#94a3b8','#9333ea','#dc2626']
    for ax,ticker in zip(axes,TICKERS):
        rows=[next(s for s in summaries if key(s)==(ticker,p)) for p in PROFILES]
        ax.bar(range(6),[float(s['terminalSettledAssetsChangePercent']) for s in rows],color=colors)
        ax.axhline(float(rows[0]['stockCloseChangePercent']),ls='--',color='#172b4d',label='Stock Close hold')
        ax.axhline(0,color='#64748b',lw=.7); ax.set_xticks(range(6),[LABELS[p] for p in PROFILES],rotation=45,ha='right')
        ax.set_title(ticker);ax.grid(axis='y',alpha=.15);ax.legend(frameon=False)
    axes[0].set_ylabel('Fully settled terminal asset change (%)')
    fig.suptitle('2025 local options replay — hypothetical 50bps/week premium',fontsize=17,x=.06,ha='left')
    fig.text(.06,.015,'Start $10k stock; source contracts, actual prefunded transfers. No dividends or gas. No-fill/no-exercise are behavior controls.\nEarn shares are separate from FUN. Open-option daily gross assets are not net NAV; no options market quotes are used.',fontsize=9,color='#536579')
    fig.subplots_adjust(top=.87,bottom=.28,left=.07,right=.98,wspace=.16)
    for ext in ('png','svg'):fig.savefig(output/('settled-terminal-comparison.'+ext),dpi=180,bbox_inches='tight',**({'metadata':{'Date':None}} if ext=='svg' else {}))
    plt.close(fig)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--log',type=Path,default=ROOT/'artifacts/all-strategy-income-20261003/options/forge.log')
    parser.add_argument('--output',type=Path,default=ROOT/'contractV2/deploy/all-strategy-income-2026-10-03/options')
    parser.add_argument('--no-charts',action='store_true');args=parser.parse_args()
    raw=read_log(args.log);events=parse(raw); data_path=ROOT/'contractV2/data/equity-history-2025.json'
    data=json.loads(data_path.read_text());groups,meta,contracts=validate(events,data)
    summaries=[summarize(k,groups[k],meta[k],contracts[k]) for k in sorted(EXPECTED)]
    rows=[row for k in sorted(EXPECTED) for row in groups[k]]
    options=[row for k in sorted(EXPECTED) for row in sorted(contracts[k],key=lambda r:r['number'])]
    result={'schema':'hedgefun-options-2025-local-replay-v1','testsPassed':27,'annualCases':20,'dailyRows':5000,
            'filledOrCancelledOptions':len(options),'reinvestmentTrades':len(events['REINVEST']),
            'broadcast':False,'deployedFunV4Connected':False,'summaries':summaries,
            'profiles':[serialize_raw(meta[k]) for k in sorted(EXPECTED)],'isolatedActualCashStakingBridge':events['STAKING_BRIDGE'][0],
            'assumptionsAndLimits':LIMITS,'dailyValuation':'gross_assets_and_option_intrinsic_lower_bound_only_not_net_nav',
            'terminalValuation':'fully_settled_stock_close_plus_cash','provenance':{'dataSha256':digest(data_path),
            'harnessSha256':digest(ROOT/'contractV2/test/OptionsYearlyReplay.t.sol'),'reportToolSha256':digest(Path(__file__)),
            'rawLogSha256':hashlib.sha256(raw.encode()).hexdigest(),
            'stakingExperimentalSourceSha256':digest(ROOT/'contractV2/src/experimental/FunStakingIncome.sol'),
            'fixtureSourceSha256':{name:digest(ROOT/name) for name in ('contractV2/test/CoveredCallDesk.t.sol','contractV2/test/mocks/Mocks.sol')},
            'sourceSha256':{str(p.relative_to(ROOT)):digest(p) for p in sorted((ROOT/'contractV2/src/options').glob('*.sol'))}}}
    args.output.mkdir(parents=True,exist_ok=True)
    for name,items in [('daily',rows),('option_contracts',options),('redemptions',events['REDEEM']),('reinvestments',events['REINVEST'])]:
        write_csv(args.output/(name+'.csv'),items)
        if name=='daily':(args.output/'daily.jsonl.gz').write_bytes(packed_gzip((''.join(json.dumps(serialize_raw(r),separators=(',',':'))+'\n' for r in rows)).encode()))
    write_csv(args.output/'summary.csv',summaries)
    (args.output/'forge.log.gz').write_bytes(packed_gzip(raw.encode()))
    (args.output/'results.json').write_text(json.dumps(result,separators=(',',':'))+'\n')
    lines=['# 2025 Options / Earn / Wheel 本地回放','', '27 项测试通过：20 个年度情景、5000 个日末记录、6 项异常路径、1 项真实现金质押桥接。','',
           '以下只比较期末全部期权已结算的资产。日内/未到期记录是 gross assets，不是净 NAV。保费是假设报价，股息与 gas 未计。','',
           '| 股票 | 情景 | 期末资产变化 | 持股价格变化 | 已收保费 USDG | 已结算期权腿 PnL USDG | 复投执行成本 USDG | call / put |',
           '|---|---|---:|---:|---:|---:|---:|---:|']
    for s in summaries:lines.append(f"| {s['ticker']} | {s['profile']} | {s['terminalSettledAssetsChangePercent']}% | {s['stockCloseChangePercent']}% | {s['premiumReceivedUsdg']} | {s['settledOptionPnlUsdg']} | {s['reinvestmentExecutionCostUsdg']} | {s['calls']} / {s['puts']} |")
    lines+=['','## 范围与计价','']+['- '+x for x in LIMITS]
    (args.output/'GENERATED_REPORT.md').write_text('\n'.join(lines)+'\n')
    if not args.no_charts:charts(summaries,args.output/'charts')
    paths=sorted(p for p in args.output.rglob('*') if p.is_file() and p.name!='SHA256SUMS')
    (args.output/'SHA256SUMS').write_text(''.join(f'{digest(p)}  {p.relative_to(args.output)}\n' for p in paths))
    print(json.dumps({'annualCases':20,'rows':len(rows),'options':len(options),'output':str(args.output)},indent=2))

if __name__=='__main__':main()

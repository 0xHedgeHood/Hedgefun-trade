#!/usr/bin/env python3
"""Independently reconstruct funded writer/MM/venue token ledgers from option terms.

No exporter imports, RPC, signatures, or mutation of replay evidence. Daily gross
assets and intrinsic-only bounds are deliberately not called active option NAV.
"""
from __future__ import annotations
import argparse
import csv
from collections import Counter, defaultdict
from decimal import Decimal, getcontext
import gzip
import hashlib
import json
from pathlib import Path
import re

getcontext().prec = 70
ROOT = Path(__file__).resolve().parents[2]
E18, E30, DAY = 10**18, 10**30, 86400
T0 = 1_800_000_000


def n(row, field): return int(row[field])
def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def ceil_div(x, y): return (x + y - 1) // y
def key(row): return row['ticker'], row['profile']


def audit_export(directory, log, profiles, daily, contracts, reinvest, redeemed, bridge, check):
    doc=json.loads((directory/'results.json').read_text())
    check(doc['testsPassed']==27 and doc['annualCases']==20 and doc['dailyRows']==5000
          and not doc['broadcast'] and not doc['deployedFunV4Connected'],'export_scope_and_counts')
    check(doc['dailyValuation']=='gross_assets_and_option_intrinsic_lower_bound_only_not_net_nav'
          and doc['terminalValuation']=='fully_settled_stock_close_plus_cash','export_valuation_labels')
    check(gzip.decompress((directory/'forge.log.gz').read_bytes()).decode()==log,'export_raw_log_exact')
    for name in ('forge.log.gz','daily.jsonl.gz'):
        check((directory/name).read_bytes()[4:8]==b'\0\0\0\0','export_deterministic_gzip_mtime',name)
    exported=[json.loads(s) for s in gzip.decompress((directory/'daily.jsonl.gz').read_bytes()).decode().splitlines()]
    row_id=lambda r:(*key(r),n(r,'index'))
    raw={row_id(r):r for r in daily}; out={row_id(r):r for r in exported}
    check(len(raw)==len(out)==len(exported)==5000 and raw.keys()==out.keys(),'export_exact_daily_keyset')
    checked_fields=0
    for k,r in raw.items():
        check(r.keys()==out[k].keys(),'export_no_lost_or_extra_daily_fields',k)
        for field,value in r.items():
            check(str(out[k][field])==str(value),'export_exact_raw_daily_field',(k,field)); checked_fields+=1
    def csv_exact(name,records,identifier):
        actual=list(csv.DictReader((directory/name).open()))
        wanted={identifier(r):{f:str(v) for f,v in r.items()} for r in records}
        seen={identifier(r):r for r in actual}
        check(len(actual)==len(wanted)==len(seen) and seen==wanted,'export_csv_exact_records',name)
    csv_exact('daily.csv',daily,row_id)
    csv_exact('option_contracts.csv',contracts,lambda r:(*key(r),n(r,'number')))
    csv_exact('reinvestments.csv',reinvest,lambda r:(*key(r),n(r,'index')))
    csv_exact('redemptions.csv',redeemed,key)
    pmap={key(p):p for p in profiles}; emap={key(p):p for p in doc['profiles']}
    check(pmap.keys()==emap.keys(),'export_profile_keyset')
    for k,p in pmap.items(): check({f:str(v) for f,v in p.items()}=={f:str(v) for f,v in emap[k].items()},'export_profiles_exact',k)
    check({f:str(v) for f,v in bridge.items()}=={f:str(v) for f,v in doc['isolatedActualCashStakingBridge'].items()},'export_bridge_exact')
    grouped=defaultdict(list)
    for r in daily:grouped[key(r)].append(r)
    smap={key(s):s for s in doc['summaries']}
    check(smap.keys()==grouped.keys(),'export_summary_keyset')
    tolerance=Decimal('5.1e-11'); maximum_error=Decimal(0)
    for k,rs in grouped.items():
        s,p=smap[k],pmap[k]; first,last=rs[0],rs[-1]
        settled=[r for r in rs if not r['hasUnsettledOption']]
        peak=n(settled[0],'grossAssetsUsdgRaw'); drawdown=Decimal(0)
        for r in settled:
            value=n(r,'grossAssetsUsdgRaw'); peak=max(peak,value)
            drawdown=max(drawdown,Decimal(peak-value)*100/peak)
        exact={
            'initialAssetsUsdg':Decimal(n(first,'grossAssetsUsdgRaw'))/10**6,
            'terminalSettledAssetsUsdg':Decimal(n(last,'grossAssetsUsdgRaw'))/10**6,
            'terminalSettledAssetsChangePercent':(Decimal(n(last,'grossAssetsUsdgRaw'))/n(first,'grossAssetsUsdgRaw')-1)*100,
            'stockCloseChangePercent':(Decimal(n(last,'stockPriceE18'))/n(first,'stockPriceE18')-1)*100,
            'checkpointDrawdownPercent':drawdown,
            'premiumReceivedUsdg':Decimal(n(last,'premiumReceivedUsdgRaw'))/10**6,
            'settledOptionPnlUsdg':Decimal(n(last,'premiumReceivedUsdgRaw')-n(last,'optionPayoutAtSettlementUsdgRaw'))/10**6,
            'reinvestmentExecutionCostUsdg':Decimal(n(last,'reinvestExecutionCostUsdgRaw'))/10**6,
            'terminalWriterCashUsdg':Decimal(n(last,'writerUsdgRaw'))/10**6,
            'terminalWriterStock':Decimal(n(last,'writerStockRaw'))/E18,
            'writerTerminalDeltaVersusStockHoldUsdg':Decimal(n(last,'grossAssetsUsdgRaw')-n(p,'initialWriterStockRaw')*n(last,'stockPriceE18')//E30)/10**6,
            'counterpartyTerminalDeltaVersusInitialHoldUsdg':Decimal(n(last,'mmAssetsUsdgRaw')-n(p,'initialMmStockRaw')*n(last,'stockPriceE18')//E30-n(p,'initialMmUsdgRaw'))/10**6,
            'poolTerminalDeltaVersusInitialHoldUsdg':Decimal(n(last,'poolStockRaw')*n(last,'stockPriceE18')//E30+n(last,'poolUsdgRaw')-n(p,'initialPoolStockRaw')*n(last,'stockPriceE18')//E30)/10**6,
        }
        for field,value in exact.items():
            error=abs(Decimal(s[field])-value); maximum_error=max(maximum_error,error)
            check(error <= (tolerance if field.endswith('Percent') else Decimal(0)),'export_summary_independent_numeric',(k,field,str(error)))
        check(s['observations']==250 and s['settledCheckpoints']==len(settled) and s['terminalHasUnsettledOption'] is False,'export_summary_checkpoint_scope',k)
        for field in ('fills','assignments','lapses','netSettlements','calls','puts','cancellations'):
            check(n(s,field)==n(last,field),'export_summary_actions',(k,field))
    csv_exact('summary.csv',doc['summaries'],key)
    provenance=doc['provenance']
    for field,path in (('dataSha256','contractV2/data/equity-history-2025.json'),('harnessSha256','contractV2/test/OptionsYearlyReplay.t.sol'),
                       ('reportToolSha256','contractV2/tools/options_history_report.py'),('stakingExperimentalSourceSha256','contractV2/src/experimental/FunStakingIncome.sol')):
        check(provenance[field]==sha(ROOT/path),'export_provenance_exact',field)
    check(provenance['rawLogSha256']==hashlib.sha256(log.encode()).hexdigest(),'export_log_provenance')
    for group in ('fixtureSourceSha256','sourceSha256'):
        for path,value in provenance[group].items(): check(sha(ROOT/path)==value,'export_source_provenance',path)
    names=('results.json','daily.csv','daily.jsonl.gz','option_contracts.csv','redemptions.csv','reinvestments.csv','summary.csv','forge.log.gz')
    return {'status':'PASS','rawFieldsCompared':checked_fields,'summaryGroups':20,'maximumDisplayedNumericError':str(maximum_error),
            'percentTolerance':str(tolerance),'hashes':{f:sha(directory/f) for f in names}}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--log', type=Path, default=ROOT/'artifacts/all-strategy-income-20261003/options/forge.log')
    ap.add_argument('--output', type=Path, default=ROOT/'artifacts/all-strategy-income-20261003/options/independent-review.json')
    ap.add_argument('--export-dir',type=Path,help='Cross-check archived tables and independently recalculate summary metrics')
    args = ap.parse_args()
    data_path = ROOT/'contractV2/data/equity-history-2025.json'
    source_path = ROOT/'contractV2/test/OptionsYearlyReplay.t.sol'
    data = json.loads(data_path.read_text())
    log = args.log.read_text()
    counts = Counter()

    def check(ok, name, context=None):
        if not ok: raise AssertionError(f'{name}: {context}')
        counts[name] += 1

    def records(label):
        return [json.loads(s.split(label+' ', 1)[1]) for s in log.splitlines() if label+' ' in s]

    profiles, daily, contracts, reinvest, redeemed, bridges = [records('OPTIONS_'+s) for s in
        ('PROFILE', 'ROW', 'CONTRACT', 'REINVEST', 'REDEEM', 'STAKING_BRIDGE')]
    expected = {(t, p) for t in ('TSLA','NVDA','META') for p in
                ('netshare','physical','wheel','no_fill','no_exercise','premium_reinvest')}
    expected |= {('TSLA','wheel_premium25'),('TSLA','wheel_premium100')}
    check(len(profiles) == 20 and {key(p) for p in profiles} == expected, 'complete_twenty_case_matrix')
    check(len(daily) == 5000, 'five_thousand_daily_snapshots')
    check(len(re.findall(r'^\[PASS\] testOptions_', log, re.M)) == 20, 'twenty_annual_test_passes')
    check(len(re.findall(r'^\[PASS\] testOptionsRisk_', log, re.M)) == 6, 'six_risk_tests_pass')
    check(len(re.findall(r'^\[PASS\] testOptionsBridge_', log, re.M)) == 1 and '[FAIL' not in log, 'bridge_pass_no_failures')
    rows, series, swaps = defaultdict(list), defaultdict(list), defaultdict(list)
    for r in daily: rows[key(r)].append(r)
    for c in contracts: series[key(c)].append(c)
    for q in reinvest: swaps[key(q)].append(q)
    redeem = {key(r): r for r in redeemed}
    check(len(redeemed) == 17 and set(redeem) == {k for k in expected if k[1] != 'netshare'}, 'all_earn_groups_redeem')
    results = []
    for p in profiles:
        k = key(p); ticker, profile = k; rs = rows[k]; cs = sorted(series[k], key=lambda c:n(c,'number'))
        w = data['windows'][ticker]; prices = [int(x) for x in w['pricesE18']]
        check(len(rs) == 250 and [n(r,'index') for r in rs] == list(range(250)), 'ordered_250_observations', k)
        check(p['funV4Connected'] is False and p['sourceContractsLocalDeployment'] is True, 'local_options_not_fun_integration', k)
        check(n(p,'initialWriterStockRaw') == 10_000*10**6*E30//prices[0], 'equal_initial_writer_capital', k)
        check(n(p,'initialMmStockRaw') == n(p,'initialPoolStockRaw') == 100_000*10**6*E30//prices[0], 'prefunded_counterparty_and_venue_stock', k)
        check(n(p,'initialMmUsdgRaw') == n(p,'usdgSupplyRaw') == 100_000*10**6, 'finite_counterparty_cash', k)
        check(n(p,'stockSupplyRaw') == sum(n(p,f) for f in ('initialWriterStockRaw','initialMmStockRaw','initialPoolStockRaw')), 'initial_stock_supply', k)
        opens, ends, executions = defaultdict(list), defaultdict(list), defaultdict(list)
        for j,c in enumerate(cs,1):
            check(n(c,'number') == j, 'ordered_contract_numbers', (k,j))
            start, end = n(c,'startIndex'), n(c,'endIndex')
            expiry, offered, size = n(c,'expiry'), n(c,'offerTimestamp'), n(c,'sizeRaw')
            check(0 <= start <= end < 250 and offered >= T0+w['elapsedSeconds'][start], 'term_start_not_before_observation', (k,j))
            check(n(c,'fillDeadline') == offered+300 and 12*3600 <= expiry-offered <= 9*DAY, 'rfq_time_and_tenor_bounds', (k,j))
            max_end = min(start+5,249)
            while w['elapsedSeconds'][max_end]-w['elapsedSeconds'][start] > 9*DAY: max_end -= 1
            check(expiry == T0+w['elapsedSeconds'][max_end], 'calendar_only_expiry_rule', (k,j))
            check(end == (start if profile == 'no_fill' else max_end), 'terminal_observation_mapping', (k,j))
            strike = (prices[start]*9500//(10**12*10000) if c['put'] else ceil_div(prices[start]*10500,10**12*10000))
            check(n(c,'strikeUsdgRaw') == strike, 'ex_ante_strike_rule', (k,j))
            notional = size*prices[start]//E30
            premium = max(ceil_div(notional*n(p,'premiumBpsPerSevenDays')*(expiry-offered),10000*7*DAY),ceil_div(notional*25,10000))
            check(n(c,'premiumUsdgRaw') == premium and notional >= 10**6, 'ex_ante_calendar_premium_exact', (k,j))
            check(n(c,'collateralUsdgRaw') == (ceil_div(size*strike,E18) if c['put'] else 0), 'put_fully_collateralized_at_offer', (k,j))
            check(c['filled'] == (profile != 'no_fill') and n(c,'optionDeskFeesUsdgRaw') == 0, 'fill_and_fee_model', (k,j))
            opens[start].append(c)
            if c['filled']: ends[end].append(c)
        for q in swaps[k]: executions[n(q,'index')].append(q)
        # Six independent wallets/buckets, updated from transfers implied by immutable option terms.
        ws, wc = n(p,'initialWriterStockRaw'), 0
        ms, mc = n(p,'initialMmStockRaw'), n(p,'initialMmUsdgRaw')
        ps, pc, es, ec = n(p,'initialPoolStockRaw'), 0, 0, 0
        active = None
        cumulative = Counter()

        def closed(c, price):
            check(n(c,'closedWriterStockRaw') == ws and n(c,'closedWriterUsdgRaw') == wc, 'independent_terminal_writer_token_ledger', (k,n(c,'number')))
            marked = ws*price//E30+wc
            check(n(c,'closedWriterAssetsUsdgRaw') == marked, 'closed_gross_assets_formula', (k,n(c,'number')))
            check(n(c,'actualEarnClosedEpochNavUsdgRaw') == (0 if profile=='netshare' else marked), 'real_earn_checkpoint_nav', (k,n(c,'number')))

        for i,r in enumerate(rs):
            price = prices[i]
            for c in ends[i]:
                check(active is c, 'one_active_option_and_actual_settlement_order', (k,i))
                q, strike = n(c,'sizeRaw'), n(c,'strikeUsdgRaw')
                settlement_price = price//10**12  # Feed8 -> desk USDG6, independent of mark18 precision.
                payout = 0
                if c['put']:
                    collateral = n(c,'collateralUsdgRaw')
                    if settlement_price < strike:
                        state = 5; ms -= q; ws += q; mc += collateral; ec -= collateral
                        payout = collateral-q*price//E30; cumulative['assignments'] += 1
                    else:
                        state = 6; wc += collateral; ec -= collateral
                elif settlement_price <= strike:
                    state = 6; ws += q; es -= q
                elif profile == 'netshare':
                    state = 7; delivered = q*(settlement_price-strike)//settlement_price
                    ms += delivered; ws += q-delivered; es -= q
                    payout = delivered*price//E30; cumulative['netSettlements'] += 1
                elif profile == 'no_exercise':
                    state = 8; ws += q; es -= q; cumulative['lapses'] += 1
                else:
                    state = 5; cost = ceil_div(q*strike,E18)
                    mc -= cost; wc += cost; ms += q; es -= q
                    payout = q*price//E30-cost; cumulative['assignments'] += 1
                check(n(c,'state') == state and n(c,'expiryPriceE18') == price, 'independent_settlement_state', (k,i))
                check(n(c,'payoutAtExpiryUsdgRaw') == payout and n(c,'signedSettledOptionPnlUsdgRaw') == n(c,'premiumUsdgRaw')-payout,
                      'independent_settled_derivative_pnl', (k,i))
                cumulative['optionPayoutAtSettlementUsdgRaw'] += payout
                closed(c,price); active = None
            for q in executions[i]:
                spent = n(q,'spentUsdgRaw'); got = (spent*997000//1000000)*E30//price
                check(profile == 'premium_reinvest' and spent >= 10**6 and n(q,'receivedStockRaw') == got, 'finite_mock_pool_swap_exact_output', (k,i))
                cost = spent-got*price//E30
                check(n(q,'executionCostAtTradeMarkUsdgRaw') == cost, 'reinvest_execution_cost', (k,i))
                check(spent == cumulative['pendingReinvestCashUsdgRaw'], 'reinvest_only_actual_received_premium_cash', (k,i))
                ws += got; wc -= spent; ps -= got; pc += spent
                cumulative['pendingReinvestCashUsdgRaw'] = 0
                cumulative['reinvestSpentUsdgRaw'] += spent; cumulative['reinvestStockRaw'] += got
                cumulative['reinvestExecutionCostUsdgRaw'] += cost
            expected_balances = dict(writerStockRaw=ws,writerUsdgRaw=wc,mmStockRaw=ms,mmUsdgRaw=mc,poolStockRaw=ps,poolUsdgRaw=pc,escrowStockRaw=es,escrowUsdgRaw=ec)
            for f,value in expected_balances.items(): check(n(r,f) == value and value >= 0, 'independent_daily_token_balance', (k,i,f))
            check(ws+ms+ps+es == n(p,'stockSupplyRaw') and wc+mc+pc+ec == n(p,'usdgSupplyRaw'), 'no_hidden_mint_or_funding', (k,i))
            check(r['date'] == w['dates'][i] and n(r,'stockPriceE18') == price and n(r,'elapsedSeconds') == w['elapsedSeconds'][i], 'daily_close_time_mapping', (k,i))
            check(n(r,'grossAssetsUsdgRaw') == (ws+es)*price//E30+wc+ec and n(r,'mmAssetsUsdgRaw') == ms*price//E30+mc, 'daily_gross_asset_marks_only', (k,i))
            liability = 0
            if active:
                intrinsic = n(active,'strikeUsdgRaw')*10**12-price if active['put'] else price-n(active,'strikeUsdgRaw')*10**12
                liability = n(active,'sizeRaw')*max(0,intrinsic)//E30
            check(r['hasUnsettledOption'] == (active is not None) and n(r,'optionIntrinsicLowerBoundUsdgRaw') == liability, 'intrinsic_only_bound_and_position_state', (k,i))
            for f in ('calls','puts','fills','cancellations','assignments','lapses','netSettlements','premiumReceivedUsdgRaw','optionPayoutAtSettlementUsdgRaw','reinvestSpentUsdgRaw','reinvestStockRaw','pendingReinvestCashUsdgRaw','reinvestExecutionCostUsdgRaw'):
                check(n(r,f) == cumulative[f], 'independent_cumulative_execution_ledger', (k,i,f))
            supply = 0 if profile=='netshare' else n(p,'initialWriterStockRaw')
            check(n(r,'earnSharesSupplyRaw') == supply and n(r,'investorEarnSharesRaw') == (supply-10**12 if supply else 0), 'shares_and_dead_share_floor', (k,i))
            for c in opens[i]:
                check(active is None, 'no_overlapping_call_and_put', (k,i))
                q = n(c,'sizeRaw'); collateral = n(c,'collateralUsdgRaw')
                cumulative['puts' if c['put'] else 'calls'] += 1
                if c['put']: wc -= collateral; ec += collateral
                else: ws -= q; es += q
                if c['filled']:
                    premium = n(c,'premiumUsdgRaw'); wc += premium; mc -= premium
                    cumulative['premiumReceivedUsdgRaw'] += premium; cumulative['fills'] += 1
                    if profile=='premium_reinvest': cumulative['pendingReinvestCashUsdgRaw'] += premium
                    active = c
                else:
                    if c['put']: wc += collateral; ec -= collateral
                    else: ws += q; es -= q
                    cumulative['cancellations'] += 1
                    check(n(c,'state') == 2 and n(c,'signedSettledOptionPnlUsdgRaw') == n(c,'payoutAtExpiryUsdgRaw') == 0, 'unfilled_offer_no_income', (k,i))
                    closed(c,price)
        check(active is None and es == ec == 0, 'terminal_no_unsettled_option_liability', k)
        actual_final = n(rs[-1],'grossAssetsUsdgRaw')
        if profile != 'netshare':
            rd = redeem[k]; supply=n(p,'initialWriterStockRaw'); shares=supply-10**12
            stock_out, cash_out = ws*shares//supply, wc*shares//supply
            check(n(rd,'stockOutRaw') == stock_out and n(rd,'usdgOutRaw') == cash_out, 'actual_earn_pro_rata_redemption', k)
            check(n(rd,'deadShareStockResidualRaw') == ws-stock_out and n(rd,'deadShareUsdgResidualRaw') == wc-cash_out, 'locked_dead_share_residual', k)
            actual_final = stock_out*prices[-1]//E30+cash_out
        final = rs[-1]; initial = n(rs[0],'grossAssetsUsdgRaw')
        derivative_pnl = cumulative['premiumReceivedUsdgRaw']-cumulative['optionPayoutAtSettlementUsdgRaw']
        results.append({'ticker':ticker,'profile':profile,'contracts':len(cs),'finalSettledWriterAssetsUsdgRaw':n(final,'grossAssetsUsdgRaw'),
            'finalRedeemableInvestorMarkUsdgRaw':actual_final,'initialWriterAssetsUsdgRaw':initial,
            'settledWriterReturnPercent':str((Decimal(n(final,'grossAssetsUsdgRaw'))/initial-1)*100),
            'investorRedemptionMarkedReturnPercent':str((Decimal(actual_final)/initial-1)*100),
            'cumulativeSettledDerivativePnlUsdgRaw':derivative_pnl,'reinvestExecutionCostUsdgRaw':cumulative['reinvestExecutionCostUsdgRaw'],
            'grossPremiumUsdgRaw':cumulative['premiumReceivedUsdgRaw'],'assignments':cumulative['assignments'],'lapses':cumulative['lapses'],
            'valuation':'terminal no open options; daily observations are gross asset marks, not option NAV'})
    check(len(bridges)==1, 'one_real_earn_to_isolated_staking_bridge')
    bridge=bridges[0]
    check(0<n(bridge,'actualRedeemedCashRaw')<=n(bridge,'actualMmPremiumRaw') and n(bridge,'actualStakingFundingRaw')==n(bridge,'actualRedeemedCashRaw'), 'bridge_actual_premium_redemption_funding')
    check(0<=n(bridge,'actualStakingFundingRaw')-n(bridge,'actualStakerClaimRaw')<=1 and bridge['deployedFunV4Integration'] is False, 'bridge_actual_claim_and_scope')
    result={'schema':'hedgefun-options-independent-review-v1','status':'PASS_RAW_LEDGER','evidence':{str(p.relative_to(ROOT)):sha(p) for p in (source_path,data_path,args.log,Path(__file__).resolve())},
        'totals':{'annualCases':20,'dailySnapshots':len(daily),'closedOptions':len(contracts),'reinvestments':len(reinvest),'actualEarnRedemptions':len(redeemed),'targetedRiskTests':6,'isolatedStakingBridgeTests':1},
        'checks':dict(sorted(counts.items())),'assertionCount':sum(counts.values()),'independentMetrics':results,'bridge':bridge,
        'method':'Independent event-order reconstruction of every writer/MM/mock venue transfer from immutable terms; no producer formula imports.',
        'scopeLimits':['Synthetic premium, strikes and calendar-open feed; not historical RFQ/IV or actual options liquidity.',
            'Stock Close excludes cash corporate dividends; USDG assumed one dollar.',
            'Finite flat-price 30bps MockPool has no AMM slippage or historical depth; it is not live V3 evidence.',
            'Active gross assets minus intrinsic alone is not fair NAV. Only terminal unencumbered assets and closed-epoch values support reported returns.',
            'Settled derivative premium minus intrinsic delivery is not total portfolio PnL, deployable cash, or distributable income by itself.',
            'The extra bridge is a flat-price OTM example using actual Earn redemption and separately minted fixture FUN; no live FUN/V4 connection.',
            'No public transactions, gas cost charged to portfolios, cash dividends, or production deployment demonstrated.'], 'blockingFindings':[]}
    if args.export_dir:
        result['exportCrosscheck']=audit_export(args.export_dir,log,profiles,daily,contracts,reinvest,redeemed,bridge,check)
        result['status']='PASS'; result['checks']=dict(sorted(counts.items())); result['assertionCount']=sum(counts.values())
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({'status':result['status'],'totals':result['totals'],'assertionCount':result['assertionCount'],'output':str(args.output)}))


if __name__ == '__main__': main()

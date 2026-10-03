#!/usr/bin/env python3
"""Export the frozen 51-case all-strategy replay without network calls or trades.

Reuses numeric formatting and mark/drawdown helpers from the earlier exporter.
The independent verifier is separate and never imports this producer.
"""
from __future__ import annotations
import argparse
from collections import defaultdict
from datetime import date
from decimal import Decimal as D
import hashlib
import json
from pathlib import Path
import re
import shutil

import equity_history_report as common

ROOT = Path(__file__).resolve().parents[2]
TICKERS = ('TSLA', 'NVDA', 'META')
PROFILES = ('baseline','fee_only','all_in_p500','pure_buyback','fixed70_payout0','fixed70_payout50','nav70_payout0','nav70_payout50','cycle_p500')
LABELS = dict(zip(PROFILES, ('Passive','Fee only','All-in 5%','Pure buyback','Fixed 70 / 0%','Fixed 70 / 50%','NAV 70 / 0%','NAV 70 / 50%','Cycle 5%')))
CASES = [(p,h) for p in PROFILES for h in ((False,) if p=='baseline' else (False,True))]
EXPECTED = {(t,p,h) for t in TICKERS for p,h in CASES}
E6, E18 = D(10)**6, D(10)**18
LIMITS = [
 '价格窗口为 2025-01-02 首个 Close 至 2025-12-31 最后 Close；不是上年末到当年末的标准年度收益。股票筛选使用同年数据，属于样本内机制实验。',
 '真实 Yahoo 日线 Close 已按拆股口径调整；NVDA/META 的现金分红没有注入本实验。开盘时间戳不当作收盘可得时间；仅用日期和历日秒数。',
 '固定 Robinhood 测试网区块 128172359、真实 V3/V4 与 deployed factory/registry；kind0 使用既有 runtime，其余为本地 fork 新注册的候选源码。不是公开部署或生产认可。',
 '本地授权 market maker 将原 V3 LP 区间拓宽，保留每股原 active liquidity，每日逐次断言相等。它不是历史深度；各股票 L 与费率不同，跨股票差异不能全归因于价格或策略。',
 '各组开局国库股票和锁仓 LP 股票本金各 $10,000；固定 40% sale / 50% LP 配置。每组重置，匹配 harvest 开关的地址、初始余额与订单相同。',
 '非 baseline 组用独立钱包一次预资 100,000 tUSDG，每个非首日真实花 100 tUSDG 买 FUN 并卖回净收到的全部 FUN，共 249 轮。无每日补资；这是假设交易负载，不是历史 FUN 需求。',
 '相同流量的 fee-only 对照保留税费结算与 buyback 机会，但不 execute。无流量 baseline 不能用来单独证明策略 alpha；模拟交易者的损失是必须单列的资金来源。',
 '每天顺序为外部交易、hook sweep、owner 有界转换 FUN 税、再次 sweep、可选 LP harvest、最多一次策略 execute 和一次 buyback。纯回购改为 permissionless book 后 buyback，不调用不支持的 execute。',
 '转换使用本地 snapshot 实际报价的 99% min-out、300 秒 deadline、50bps sqrt-price limit；不是无滑点成交。LP 收集 permissionless，税费转换依赖 owner；两者不可混称。',
 '70% 目标只针对可交易国库 stock+cash，不包括 LP 或回购预算。国库 $10k、LP 股票 $10k 时，国库 70/30 约等于基金整体 85% 股票敞口。',
 '固定金额引擎每次与每日上限均 $1,000；NAV 引擎均为当前 external-assets 的 5%，初始同为 $1,000，再受 sellChunk $2,000 限制。payout 0/50% 表示卖出收益留作现金或半数转为股票回购预算，不是持有人分红。',
 '外部资产 = 国库 USDG + 国库股票 + 锁仓 LP 股票本金 + 未领 LP 股票费的 oracle mark；不计自身发行 FUN，不计未转换 hook FUN 或未领取的初始 curve fees。锁仓资产不是可赎回 NAV。',
 'LP harvest 只重分类已拥有费用并烧毁收取的 FUN；回购把自有股票转入自有 LP，瞬时外部资产守恒。外部订单费、税费转换生成的 LP 费、自回购生成的 LP 费分别记录；后者不是新增外部收入。',
 'FUN/股票来自真实 V4 边际价格，FUN/美元使用历史 Close oracle mark；另保留成交后的实际 V3 spot mark。边际标价不是可兑现持仓总收益。',
 '模拟市场始终 open，每次外生股票价格变化后等待 601 秒；每天仅一个策略机会，不保证清完所有到期 lot。未建模盘中路径、MEV、历史深度/订单量、真实 gas 账单；estimatedActionGas 是累计本地 harness 估计。',
]


def require(ok, message):
    if not ok: raise ValueError(message)


def safe(obj):
    if isinstance(obj,dict): return {k:safe(v) for k,v in obj.items()}
    if isinstance(obj,list): return [safe(v) for v in obj]
    if type(obj) is int and abs(obj)>2**53-1: return str(obj)
    return obj


def load(log, data):
    raw=common.read_log(log)
    require('51 passed; 0 failed; 0 skipped' in raw and '[FAIL' not in raw,'requires one clean 51-case suite; attempts cannot be spliced')
    names=re.findall(r'\[PASS\]\s+(testStrategy_\w+)\(\)',raw)
    require(len(names)==len(set(names))==51,'wrong passing test set')
    events={k:[] for k in ('ROW','PROFILE','VENUE','DEPTH')}
    for line in raw.splitlines():
        for k in events:
            marker='STRATEGY_'+k+' '
            if marker in line: events[k].append(common.event_json(line.split(marker,1)[1])); break
    require([len(events[k]) for k in events]==[12750,51,51,408],'wrong record counts')
    metas={common.case_key(r):r for r in events['PROFILE']}
    require(set(metas)==EXPECTED,'wrong profile matrix')
    groups=defaultdict(list)
    for r in events['ROW']: groups[common.case_key(r)].append(r)
    require(set(groups)==EXPECTED,'wrong row matrix')
    for key,g in groups.items():
        g.sort(key=lambda r:r['index']); m=metas[key]; first=g[0]
        require([r['index'] for r in g]==list(range(250)),'incomplete daily series')
        require(first['externalAssetsUsdgRaw']==20_000*10**6,'unequal capital')
        require(m['forkBlock']==128172359,'fork changed')
        for r in g:
            i=r['index'];p=r['stockOracleUsdE18']
            require(r['date']==data['windows'][key[0]]['dates'][i] and p==data['windows'][key[0]]['pricesE18'][i],'history mismatch')
            require(r['healthyAfterAction'],'unhealthy row')
            require(r['funOracleUsdE18']==r['funStockE18']*p//10**18,'mark mismatch')
            require(r['externalAssetsUsdgRaw']==r['treasuryUsdgRaw']+(r['treasuryStockRaw']+r['lpStockRaw']+r['uncollectedLpStockRaw'])*p//10**30,'asset ledger mismatch')
            require(r['actions']==sum(r[f] for f in ('stopActions','tpActions','dipActions','rebalanceBuyActions','rebalanceSellActions','recoveryActions')),'action count mismatch')
            require(r['actions']+r['executeNotDue']==(i if m['strategyEnabled'] else 0),'execute schedule mismatch')
            require(r['buybacks']+r['buybackNotDue']==(i if m['buybackEnabled'] else 0),'buyback schedule mismatch')
            require(r['totalSupplyRaw']+r['burnedRaw']+r['collectedLpFunBurnedRaw']+r['hookTokenBurnRaw']==m['initialSupplyRaw'],'burn ledger mismatch')
    return raw,events,groups,metas


def drawdown(values, dates):
    # Pure buyback can legitimately empty the treasury; a zero mark is a 100% drawdown.
    require(values[0] > 0 and all(type(v) is int and v >= 0 for v in values), 'invalid drawdown marks')
    peak, peak_i, worst, pair = values[0], 0, D(0), (0, 0)
    for i, value in enumerate(values):
        if value > peak: peak, peak_i = value, i
        loss = (1-D(value)/D(peak))*100
        if loss > worst: worst, pair = loss, (peak_i,i)
    return {'percent':common.display(worst),'peakDate':dates[pair[0]],'troughDate':dates[pair[1]],
            'peakIndex':pair[0],'troughIndex':pair[1], 'peakMarkRaw':str(values[pair[0]]),
            'troughMarkRaw':str(values[pair[1]]),'sampling':'daily end-of-replay-point oracle marks only'}


def case_summary(group):
    first, last = group[0], group[-1]
    dates = [row["date"] for row in group]
    result = {"ticker": first["ticker"], "profile": first["profile"], "harvestEnabled": first["harvestEnabled"],
              "firstDate": first["date"], "lastDate": last["date"], "observations": len(group),
              "valuationBasis": "historical_close_oracle_mark",
              "initialTreasuryNavUsd": first["treasuryNavUsdOracleMark"], "finalTreasuryNavUsd": last["treasuryNavUsdOracleMark"],
              "initialExternalAssetsUsdg": first["externalAssetsUsdg"], "finalExternalAssetsUsdg": last["externalAssetsUsdg"],
              "traderCashChangeUsdg": last["traderCashChangeUsdg"],
              "cumulativeBuybackStock": common.display(D(last["buybackStockSpentRaw"])/E18, 18),
              "cumulativeBuybackUsdAtExecutionMarks": common.display(D(last["buybackOracleUsdgValueRaw"])/E6, 6),
              "buybackBurnedFun": common.display(D(last["burnedRaw"])/E18, 18),
              "collectedLpBurnedFun": common.display(D(last["collectedLpFunBurnedRaw"])/E18, 18),
              "hookBurnedFun": common.display(D(last["hookTokenBurnRaw"])/E18, 18),
              "hookConvertedFun": common.display(D(last["convertedFeeTokensRaw"])/E18, 18),
              "hookConvertedStock": common.display(D(last["convertedFeeStockRaw"])/E18, 18),
              "hookStockDeliveredToTreasury": common.display(D(last["hookStockDeliveredRaw"])/E18, 18),
              "collectedLpStock": common.display(D(last["collectedLpStockRaw"])/E18, 18),
              "collectedLpUsdAtExecutionMarks": common.display(D(last["collectedLpOracleUsdgValueRaw"])/E6, 6),
              "terminalClaimableLpStock": common.display(D(last["uncollectedLpStockRaw"])/E18, 18),
              "terminalClaimableLpFun": common.display(D(last["uncollectedLpFunRaw"])/E18, 18),
              "priceMarkContributionUsdg": common.display(D(last["priceMarkDeltaPositiveUsdgRaw"]-last["priceMarkDeltaNegativeUsdgRaw"])/E6, 6),
              "actionContributionUsdg": common.display(D(last["actionDeltaPositiveUsdgRaw"]-last["actionDeltaNegativeUsdgRaw"])/E6, 6)}
    for field, label in (("stockOracleUsdE18", "stockCloseChangePercent"), ("funStockE18", "funStockChangePercent"),
                         ("funOracleUsdE18", "funOracleMarkChangePercent"), ("treasuryNavOracleUsdE18", "treasuryNavChangePercent"),
                         ("externalAssetsUsdgRaw", "externalAssetsChangePercent")):
        result[label] = common.display(common.change_percent(first[field], last[field]))
        result[label.replace("ChangePercent", "MaxDailyDrawdown")] = drawdown([row[field] for row in group], dates)
    for field in ("actions", "stopActions", "tpActions", "dipActions", "buybacks", "executeNotDue", "buybackNotDue", "flowPairs", "conversionCount", "estimatedActionGas"):
        result[field] = last[field]
    result["terminalRaw"] = dict(last)
    return result


def summaries(groups):
    rows=[];out=[]
    for t in TICKERS:
        for p,h in CASES:
            g=groups[(t,p,h)]; daily=[common.enrich(r,g[0]) for r in g];rows+=daily
            s=case_summary(daily)
            for k in ('strategyFamily','rebalanceBuyActions','rebalanceSellActions','recoveryActions','buybackBookCalls'):
                s[k]=g[-1][k]
            for source in ('external','conversion','buyback'):
                field=source+'GeneratedLpStockRaw'
                value=sum((r[field]-(g[i-1][field] if i else 0))*r['stockOracleUsdE18']//10**30 for i,r in enumerate(g))
                s[source+'LpStockFeeAtDailyOracleMarksUsdg']=common.display(D(value)/E6,6)
            s['finalTreasuryCashUsdg']=common.display(D(g[-1]['treasuryUsdgRaw'])/E6,6)
            s['finalTreasuryStockOracleUsdg']=common.display(D(g[-1]['treasuryStockRaw'])*D(g[-1]['stockOracleUsdE18'])/D(10)**36,6)
            s['finalLpStockOracleUsdg']=common.display(D(g[-1]['lpStockRaw'])*D(g[-1]['stockOracleUsdE18'])/D(10)**36,6)
            require(abs(D(s['finalTreasuryStockOracleUsdg'])+D(s['finalTreasuryCashUsdg'])-D(s['finalTreasuryNavUsd'])) <= D('0.000002'), 'displayed treasury dollar units do not reconcile')
            out.append(s)
    return rows,out


def comparisons(ss):
    by={common.case_key(s):s for s in ss};out={'harvest':[],'strategyVsFeeOnly':[],'payout50Vs0':[]}
    def delta(a,b,kind):
        return {'ticker':a['ticker'],'profile':a['profile'],'harvestEnabled':a['harvestEnabled'],'comparison':kind,
            'externalAssetsDeltaUsdg':common.display(D(a['finalExternalAssetsUsdg'])-D(b['finalExternalAssetsUsdg']),6),
            'funOracleChangeDeltaPercentagePoints':common.display(D(a['funOracleMarkChangePercent'])-D(b['funOracleMarkChangePercent'])),
            'traderCashChangeDeltaUsdg':common.display(D(a['traderCashChangeUsdg'])-D(b['traderCashChangeUsdg']),6)}
    for t in TICKERS:
        for p in PROFILES[1:]:out['harvest'].append(delta(by[t,p,True],by[t,p,False],'harvest on minus off'))
        for p in PROFILES[2:]:
            for h in (False,True):out['strategyVsFeeOnly'].append(delta(by[t,p,h],by[t,'fee_only',h],'strategy minus matched fee-only control'))
        for p in ('fixed70','nav70'):
            for h in (False,True):out['payout50Vs0'].append(delta(by[t,p+'_payout50',h],by[t,p+'_payout0',h],'50% payout minus zero payout'))
    return out


def charts(ss,rows,pairs,out):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import matplotlib.dates as md
    from matplotlib.colors import TwoSlopeNorm,Normalize,LinearSegmentedColormap
    plt.rcParams.update({'figure.facecolor':'#f8fafc','savefig.facecolor':'#f8fafc','font.size':9,'svg.fonttype':'path','svg.hashsalt':'allstrategy2025'})
    out.mkdir(parents=True,exist_ok=True)
    def save(fig,name):
        for ext in ('png','svg'):
            fig.savefig(out/(name+'.'+ext),dpi=180,bbox_inches='tight',**({'metadata':{'Date':None}} if ext=='svg' else {}))
        plt.close(fig)
    by={common.case_key(s):s for s in ss}
    fig,axs=plt.subplots(1,3,figsize=(16,12))
    for ax,field,title in zip(axs,('funOracleMarkChangePercent','treasuryNavChangePercent','externalAssetsChangePercent'),('FUN / USD oracle mark','Treasury assets','External assets incl. locked LP')):
        grid=[[float(by[t,p,h][field]) for t in TICKERS] for p,h in CASES]
        lo=min(map(min,grid));hi=max(map(max,grid));norm=TwoSlopeNorm(vmin=lo,vcenter=0,vmax=hi) if lo<0<hi else Normalize(min(0,lo),max(.01,hi))
        cmap='RdYlGn' if lo<0<hi else LinearSegmentedColormap.from_list('positive',['#f2f8f4','#42ad7b'])
        ax.imshow(grid,cmap=cmap,norm=norm,aspect='auto');ax.set_xticks(range(3),TICKERS);ax.xaxis.tick_top()
        ax.set_yticks(range(len(CASES)),[LABELS[p]+('' if p=='baseline' else (' [on]' if h else ' [off]')) for p,h in CASES],fontsize=8)
        ax.set_title(title+' change',pad=25)
        for y,line in enumerate(grid):
            for x,v in enumerate(line):ax.text(x,y,f'{v:+.1f}%',ha='center',va='center',fontsize=9)
    fig.suptitle('2025 Close replay | complete 51-case matrix',fontsize=19,fontweight='bold',y=.98)
    fig.text(.04,.02,'Each panel has its own color scale. [on/off] = LP harvest. Payout labels are gain-to-buyback shares, not dividends.\n$10k treasury stock + $10k locked LP stock initially. Synthetic $100 daily roundtrip except Passive; not an investor-return forecast.',fontsize=10)
    fig.subplots_adjust(top=.89,bottom=.11,left=.14,right=.98,wspace=.65);save(fig,'full-strategy-matrix')
    grouped=defaultdict(list)
    for r in rows:grouped[common.case_key(r)].append(r)
    colors=plt.get_cmap('tab10').colors
    fig,axs=plt.subplots(3,2,figsize=(15,12),sharex=True)
    for i,t in enumerate(TICKERS):
        for j,field in enumerate(('funOracleMarkIndex','externalAssetsIndex')):
            ax=axs[i,j]
            for k,p in enumerate(PROFILES):
                g=grouped[t,p,p!='baseline'];ax.plot([date.fromisoformat(r['date']) for r in g],[float(r[field]) for r in g],label=LABELS[p],color=colors[k],lw=1.5,ls='--' if p=='baseline' else '-')
            if j==0:ax.set_yscale('log')
            ax.set_title(t+' | '+('FUN/USD mark (log scale)' if j==0 else 'External assets incl. locked LP'),loc='left')
            ax.set_ylabel('First Close = 100');ax.grid(alpha=.18);ax.xaxis.set_major_locator(md.MonthLocator(bymonth=(1,4,7,10)));ax.xaxis.set_major_formatter(md.DateFormatter('%b'))
    hs,ls=axs[0,0].get_legend_handles_labels();fig.legend(hs,ls,loc='lower center',bbox_to_anchor=(.5,.03),ncol=3,frameon=False)
    fig.suptitle('2025 daily paths | harvest enabled, plus zero-flow Passive',fontsize=18,fontweight='bold',y=.985)
    fig.subplots_adjust(top=.94,bottom=.13,left=.07,right=.98,hspace=.3,wspace=.18);save(fig,'daily-harvest-on-paths')
    fig,axs=plt.subplots(1,2,figsize=(15,6.5));pm={(r['ticker'],r['profile']):r for r in pairs['harvest']}
    for ax,field,title in zip(axs,('externalAssetsDeltaUsdg','funOracleChangeDeltaPercentagePoints'),('Final external assets: on - off (tUSDG)','FUN mark change: on - off (percentage points)')):
        for i,t in enumerate(TICKERS):ax.bar([x+(i-1)*.24 for x in range(8)],[float(pm[t,p][field]) for p in PROFILES[1:]],width=.23,label=t)
        ax.set_xticks(range(8),[LABELS[p] for p in PROFILES[1:]],rotation=35,ha='right');ax.axhline(0,color='#64748b',lw=.7);ax.grid(axis='y',alpha=.2);ax.legend(frameon=False);ax.set_title(title)
    fig.suptitle('Matched LP-harvest effects | identical profile and funded order plan',fontsize=17,y=.98)
    fig.text(.04,.02,'Includes subsequent buybacks and strategy interactions. Collection itself moves existing assets; it does not create wealth.',fontsize=10)
    fig.subplots_adjust(top=.86,bottom=.28,left=.07,right=.98,wspace=.22);save(fig,'matched-harvest-effects')
    fig,axs=plt.subplots(1,3,figsize=(15,6.5),sharey=True)
    for ax,t in zip(axs,TICKERS):
        bottoms=[0.0]*8
        for source,color,label in (('external','#2563eb','External orders'),('conversion','#d97706','Hook conversion'),('buyback','#64748b','Own buybacks')):
            vals=[float(by[t,p,True][source+'LpStockFeeAtDailyOracleMarksUsdg']) for p in PROFILES[1:]]
            ax.barh(range(8),vals,left=bottoms,color=color,label=label);bottoms=[a+b for a,b in zip(bottoms,vals)]
        ax.set_yticks(range(8),[LABELS[p] for p in PROFILES[1:]]);ax.invert_yaxis();ax.set_title(t);ax.set_xlabel('Stock fees at each daily oracle mark (tUSDG)');ax.grid(axis='x',alpha=.15)
    hs,ls=axs[0].get_legend_handles_labels();fig.legend(hs,ls,loc='lower center',bbox_to_anchor=(.5,.05),ncol=3,frameon=False)
    fig.suptitle('LP stock-fee sources | harvest-on cases',fontsize=18,y=.98)
    fig.text(.04,.01,'Own-buyback fees recycle existing fund stock. FUN-side LP fees and burns are separate raw ledgers, not added as external assets.',fontsize=10)
    fig.subplots_adjust(top=.88,bottom=.18,left=.13,right=.98,wspace=.25);save(fig,'lp-stock-fee-sources')


def report(ss,pairs,meta):
    lines=['# 2025 全现货策略与 LP 费用回放','',
      '51/51 个真实合约本地 fork 实验通过，12,750 个日末快照、408 笔实际 V3 深度报价；没有公共交易。',
      '', '范围覆盖：部署中的 kind0 All-in、候选纯回购、固定金额再平衡、NAV 百分比再平衡和 PR12 Cycle。每种都与 LP harvest 开关配对；保留零流量 baseline 与同流量 fee-only 控制。', '',
      '| Profile | 每日策略 | 参数 |','|---|---|---|',
      '| All-in / Cycle | 最多一次 execute | TP 5%/10%，dip/stop 5%，lot 20%；Cycle 额外允许实售后的 5% 上涨恢复买入 |',
      '| Pure buyback | book + buyback | 全部国库股票进入回购预算，不交易股票、不建立 lot |',
      '| Fixed rebalance | 最多一次 execute | 国库 stock target 70%，band 5%，600 秒冷却；$1k/action、$1k/day；payout 0/50% |',
      '| NAV rebalance | 最多一次 execute | 同 target/band/cooldown；5% external-assets/action/day、sellChunk 封顶 $2k；payout 0/50% |',
      '', '初始资本严格相同：国库股票 $10,000 + 锁仓 LP 股票本金 $10,000。非 baseline 外部交易者一次预资 $100,000，全年 249 次 $100 买入后卖回；这笔交易损失和协议、creator 收入不会伪装成策略利润。发射阶段 gross 股票支出和 25 tUSDG launch fee 在 profile 单列，不作为本表的投资者收益基准。', '',
      '## 全矩阵与路径','', '![Complete matrix](charts/full-strategy-matrix.png)', '', '![Daily paths](charts/daily-harvest-on-paths.png)', '', '## 实际执行结果','',
      '| 股票 | Profile | LP | FUN美元边际标价变化 | 外部资产变化 | 最大日末回撤 | stop/TP/dip/recovery | rebalance买/卖 | 回购 | 外部交易者现金变化 |',
      '|---|---|---|---:|---:|---:|---:|---:|---:|---:|']
    for s in ss:
        lines.append(f"| {s['ticker']} | {LABELS[s['profile']]} | {'on' if s['harvestEnabled'] else 'off'} | {D(s['funOracleMarkChangePercent']):+.2f}% | {D(s['externalAssetsChangePercent']):+.2f}% | {D(s['externalAssetsMaxDailyDrawdown']['percent']):.2f}% | {s['stopActions']}/{s['tpActions']}/{s['dipActions']}/{s['recoveryActions']} | {s['rebalanceBuyActions']}/{s['rebalanceSellActions']} | {s['buybacks']} | {s['traderCashChangeUsdg']} |")
    lines+=['','## 可解释的真实效果','']
    for t in TICKERS:
        cycle=next(s for s in ss if s['ticker']==t and s['profile']=='cycle_p500' and s['harvestEnabled'])
        pure=next(s for s in ss if s['ticker']==t and s['profile']=='pure_buyback' and s['harvestEnabled'])
        lines.append(f"- {t} Cycle harvest-on 实际执行 {cycle['recoveryActions']} 次恢复买入；纯回购 {pure['buybacks']} 次，回购股票按成交日 oracle 合计 {pure['cumulativeBuybackUsdAtExecutionMarks']} tUSDG。")
    lines+=['','纯回购 harvest-on 三股期末国库均为零，资金已移入锁仓 LP；FUN 美元边际标价分别大涨，不能据此认定可兑现收益同幅增长。LP 收集瞬时外部资产守恒；自身回购也只是把股票从国库转入自有 LP。FUN 标价与销毁量可显著变化，而外部资产变化可能很小。因此需要同时看国库、LP、未领费用和外部交易者账本。', '',
      '![Matched harvest](charts/matched-harvest-effects.png)', '', '![LP fee sources](charts/lp-stock-fee-sources.png)', '', '## 计价、资本与执行边界','']+['- '+s for s in LIMITS]
    lines+=['','## 证据与复跑','', '- 本报告仅包含现货策略与 LP；期权、earn 和 FUN 质押分配属于独立实验，不混入这51组。',
      '- 归档：`contractV2/deploy/all-strategy-income-2026-10-03/spot/`；`results.json` 含51组汇总、实际注册ID/codehash/config、匹配差值和深度报价。',
      '- `daily.jsonl.gz` 保存全部每日原始字段及派生计价；大整数为十进制字符串。`forge.log.gz` 是单次干净51组原始输出，失败尝试不拼装。',
      '- `compiled-source-manifest.json` 绑定测试源码、40个本地依赖、输入数据与命令。`independent-review.json` 是另一个 agent 独立整数/Decimal 审计。',
      '- 图表在 `charts/`；`SHA256SUMS` 覆盖归档。筛选数据和之前kind0参数扫描保留原档案，不被本轮覆盖。', '',
      '```sh', 'cd contractV2', meta['command'], 'python3 tools/all_strategy_history_report.py  # 图表依赖 matplotlib；可加 --no-charts 仅导出数据', '```','']
    return '\n'.join(lines)


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--log',type=Path,default=ROOT/'artifacts/all-strategy-income-20261003/spot/forge.log')
    ap.add_argument('--output',type=Path,default=ROOT/'contractV2/deploy/all-strategy-income-2026-10-03/spot')
    ap.add_argument('--no-charts',action='store_true');args=ap.parse_args()
    source=ROOT/'contractV2/test/AllStrategyHistoricalReplayFork.t.sol';data_path=ROOT/'contractV2/data/equity-history-2025.json'
    meta_path=ROOT/'artifacts/all-strategy-income-20261003/spot/compiled-source-manifest.json';meta=json.loads(meta_path.read_text())
    require(common.digest(source)==meta['harnessSha256'],'frozen source changed')
    require(common.digest(data_path)==meta['inputDataSha256'],'input data changed')
    data=json.loads(data_path.read_text());raw,events,groups,profiles=load(args.log,data)
    rows,ss=summaries(groups);pairs=comparisons(ss);out=args.output;out.mkdir(parents=True,exist_ok=True)
    result={'schema':'hedgefun-all-strategy-2025-spot-v1','broadcast':False,'forkBlock':128172359,'chainId':46630,'testsPassed':51,'measuredSnapshots':12750,'actualDepthProbes':408,'valuationBasis':'historical_close_oracle_mark','profiles':list(profiles.values()),'summaries':ss,'matchedComparisons':pairs,'actualForkDepthProbes':events['DEPTH'],'venues':events['VENUE'],'assumptionsAndLimits':LIMITS,'provenance':{'harnessSha256':common.digest(source),'normalizedDataSha256':common.digest(data_path),'rawUncompressedLogSha256':hashlib.sha256(raw.encode()).hexdigest(),'reportToolSha256':common.digest(Path(__file__))}}
    (out/'results.json').write_text(json.dumps(safe(result),separators=(',',':'),ensure_ascii=False)+'\n')
    stream=''.join(json.dumps(common.serialize_raw(r),separators=(',',':'),ensure_ascii=False)+'\n' for r in rows).encode()
    (out/'daily.jsonl.gz').write_bytes(common.packed_gzip(stream));(out/'forge.log.gz').write_bytes(common.packed_gzip(raw.encode()))
    shutil.copyfile(meta_path,out/meta_path.name);shutil.copyfile(source,out/'AllStrategyHistoricalReplayFork.compiled.sol')
    review=meta_path.parent/'independent-review.json'
    if review.exists():shutil.copyfile(review,out/review.name)
    md=report(ss,pairs,meta);(out/'GENERATED_REPORT.md').write_text(md)
    docs=ROOT/'contractV2/docs/ALL_STRATEGY_SPOT_REPLAY_2026_10_03.md';docs.write_text(md.replace('(charts/', '(../deploy/all-strategy-income-2026-10-03/spot/charts/'))
    if not args.no_charts:charts(ss,rows,pairs,out/'charts')
    (out/'SHA256SUMS').write_text(''.join(f'{common.digest(p)}  {p.relative_to(out)}\n' for p in sorted(out.rglob('*')) if p.is_file() and p.name!='SHA256SUMS'))
    print(json.dumps({'cases':len(ss),'rows':len(rows),'output':str(out),'docs':str(docs)},indent=2))

if __name__=='__main__':main()

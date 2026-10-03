#!/usr/bin/env python3
"""Render measured TSLA scenario paths and public persona results with Matplotlib."""
import argparse
import json
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager

ROOT=Path(__file__).resolve().parents[2]
COLORS={'passive':'#607d8b','scripted_flow':'#007f73','keeper':'#e17c24'}
NAMES={'passive':'仅持有','scripted_flow':'预设买卖行为','keeper':'1 bps 策略执行'}
SCENARIOS={'up20':'TSLA 逐步上涨 20%','down20':'TSLA 逐步下跌 20%',
           'crash50_recover':'TSLA 下跌 50% 后恢复','flat_one_bps_noise':'TSLA 上下波动 1 bp 后恢复'}


def style():
    font=Path('/System/Library/Fonts/STHeiti Light.ttc')
    if font.exists():
        font_manager.fontManager.addfont(font)
        plt.rcParams['font.family']=font_manager.FontProperties(fname=font).get_name()
    plt.rcParams.update({'figure.facecolor':'#f8fafc','axes.facecolor':'#ffffff',
        'axes.edgecolor':'#d4dde6','axes.labelcolor':'#334155','text.color':'#172b4d',
        'xtick.color':'#536579','ytick.color':'#536579','font.size':10,'axes.unicode_minus':False,
        'axes.spines.top':False,'axes.spines.right':False,'savefig.facecolor':'#f8fafc'})


def series(rows,scenario,mode):
    selected=[r for r in rows if r['stageLabel']=='Graduated' and r['scenario']==scenario and r['mode']==mode]
    points={}
    for row in selected:
        point=int(row['point'])
        if point not in points or row['phase']!='after_stock_move':points[point]=row
    assert len(points)==4
    return [points[i] for i in range(4)]


def save(fig,out,name):
    out.mkdir(parents=True,exist_ok=True)
    fig.savefig(out/(name+'.png'),dpi=180,bbox_inches='tight')
    fig.savefig(out/(name+'.svg'),bbox_inches='tight')
    plt.close(fig)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--prices',type=Path,default=ROOT/'artifacts/persona-price-impact-20261003/results.json')
    p.add_argument('--output',type=Path,default=ROOT/'contractV2/deploy/persona-2026-10-03/charts')
    p.add_argument('--personas',type=Path,default=ROOT/'contractV2/deploy/persona-2026-10-03/independent-verification.json')
    a=p.parse_args();data=json.loads(a.prices.read_text());style()
    fig,axes=plt.subplots(2,2,figsize=(13,8.5))
    fig.suptitle('TSLA 股价怎样影响已毕业的 FUN？',fontsize=20,fontweight='bold',x=.07,ha='left',y=.99)
    for ax,(scenario,title) in zip(axes.flat,SCENARIOS.items()):
        for mode in ('passive','scripted_flow','keeper'):
            group=series(data['rows'],scenario,mode)
            ax.plot(range(4),[float(r['funUsdOracleMarkIndex']) for r in group],
                    marker='o',lw=2.2,color=COLORS[mode],label=NAMES[mode])
        ax.set_title(title,loc='left',pad=12,fontweight='bold')
        ax.set_xticks(range(4),['开始','情景点 1','情景点 2','情景点 3'])
        ax.set_ylabel('FUN 美元计价值指数（初始 = 100）')
        ax.grid(axis='y',color='#e5ebf1',lw=.7);ax.axhline(100,color='#9aaaba',lw=.7,ls=':')
    axes[0,0].legend(frameon=False,fontsize=9)
    fig.text(.07,.015,'真实合约的隔离 fork：20 组实验、116 个快照。美元按 TSLA 预言机/情景输入价估值。\n买卖假设：上涨或持平买 100 tUSDG，下跌卖所持 FUN 的 20%；keeper 组独立运行，未混入此买卖流。不是价格预测。',fontsize=10,color='#536579')
    fig.subplots_adjust(top=.90,bottom=.13,hspace=.37,wspace=.23)
    save(fig,a.output,'tsla-fun-price-paths')

    fig,axes=plt.subplots(1,2,figsize=(13,5.5))
    fig.suptitle('国库净值与币价是两件事',fontsize=20,fontweight='bold',x=.07,ha='left',y=.99)
    for ax,scenario in zip(axes,('flat_one_bps_noise','crash50_recover')):
        for mode in ('passive','keeper'):
            group=series(data['rows'],scenario,mode)
            base=float(group[0]['treasuryNavUsdOracleMark'])
            ax.plot(range(4),[float(r['treasuryNavUsdOracleMark'])/base*100 for r in group],
                    color=COLORS[mode],lw=2.4,marker='o',label=NAMES[mode])
        group=series(data['rows'],scenario,'keeper')
        ax.plot(range(4),[float(r['funUsdOracleMarkIndex']) for r in group],
                color='#5265b2',lw=1.8,ls='--',label='策略组 FUN 美元计价')
        ax.set_title(SCENARIOS[scenario],loc='left',fontweight='bold',pad=12)
        ax.set_xticks(range(4),['开始','情景点 1','情景点 2','情景点 3'])
        ax.set_ylabel('指数（初始 = 100）');ax.grid(axis='y',color='#e5ebf1')
        ax.legend(frameon=False,fontsize=9)
    fig.text(.07,.018,'本图策略参数 TP1/TP2/dip/stop = 1/2/1/1 bps。模拟开市，每次价格更新后等待 601 秒观察窗口。\nNAV 包含手续费和回购资金流出的影响；不是投资者总回报，也不代表 FUN 的保底或兑付价格。',fontsize=10,color='#536579')
    fig.subplots_adjust(top=.82,bottom=.19,wspace=.25)
    save(fig,a.output,'treasury-nav-vs-token')
    if a.personas.exists():
        proof=json.loads(a.personas.read_text())
        roles=['sniper','opening_buyer','diamond_hands','paper_hands','kol','follower_1','follower_2','late_fomo']
        labels=['狙击手','抢开盘','钻石手','纸手','KOL','跟随者 1','跟随者 2','追涨者']
        marked=[float(proof['actors'][r]['markToMarketPnlUsdg']) for r in roles]
        liquidated=[float(proof['actors'][r]['liquidationQuote'].get('profitVersusFundingUsdg',
                          proof['actors'][r]['markToMarketPnlUsdg'])) for r in roles]
        fig,(top,bottom)=plt.subplots(2,1,figsize=(13,9),gridspec_kw={'height_ratios':[1,1.3]})
        fig.suptitle('8 个独立钱包的真实测试网角色实验',fontsize=20,fontweight='bold',x=.07,ha='left',y=.99)
        samples=[x for x in proof['prices'] if x['label']!='final']
        top.plot(range(1,len(samples)+1),[float(x['usdgPerFun'])*1e6 for x in samples],color='#007f73',marker='o',lw=2)
        graduate=next(i+1 for i,x in enumerate(samples) if x['stage']==2)
        top.axvline(graduate,color='#e17c24',ls='--',label='毕业：切换 V4')
        top.set_ylabel('tUSDG / 百万 FUN（同区块现价）')
        top.set_xlabel('已确认角色买卖序号');top.set_xticks(range(1,20));top.grid(axis='y',color='#e5ebf1');top.legend(frameon=False)
        for i,(mark,liquid) in enumerate(zip(marked,liquidated)):
            bottom.bar(i-.18,mark,width=.35,color='#5265b2',label='按池边际现价估值' if i==0 else None)
            bottom.bar(i+.18,liquid,width=.35,color='#e17c24',label='独立卖成 TSLA 后估值' if i==0 else None)
        bottom.axhline(0,color='#66778a',lw=.8);bottom.grid(axis='y',color='#e5ebf1',alpha=.5)
        bottom.set_xticks(range(8),labels);bottom.set_ylabel('相对注资基线的盈亏（tUSDG 估值）')
        bottom.legend(frameon=False,loc='lower left')
        fig.text(.07,.012,'80 笔成功链上交易（含 19 笔角色买卖）。水龙头、资金注入已剔除；gas 用 test ETH 单列。\n橙色柱：逐个独立模拟卖出剩余 FUN 得到 TSLA，再按同区块现价折算；未模拟其后 TSLA→tUSDG，不可同时成交。',fontsize=10,color='#536579')
        fig.subplots_adjust(top=.91,bottom=.13,hspace=.35)
        save(fig,a.output,'persona-outcomes')
    print('Rendered measured price/NAV figures and available persona outcomes (PNG + SVG).')


if __name__=='__main__':main()

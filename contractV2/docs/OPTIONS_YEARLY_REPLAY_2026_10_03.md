# 2025 Covered Call / Earn / Wheel 回放

本次运行 27 项测试全部通过：20 个独立年度情景、5000 条日末记录、760 张实际 offer 后成交或取消的期权、11 笔真实 adapter 复投、17 次 Earn 实际赎回，另有 6 项风险路径与 1 项真实现金质押桥接。所有交易仅在本地 EVM 中执行，没有公链广播。

代码使用导入的 CoveredCallDesk、PhysicalCallDesk、CashSecuredPutDesk、EarnVault 和 EarnVaultV3SwapAdapter。价格 feed、交易日历、可冻结 ERC20 以及复投池是测试夹具；实际托管、保费付款、行权交付、份额赎回均调用合约本身的入口。只有 NetShare 的 EOA writer 直接 approve；Earn 的抵押授权由 vault 自己 exact approve/clear，不冒充 vault 签名。

## 接入边界

- NetShare covered call 直接使用 legacy CoveredCallDesk；Earn 拒绝 NetShare，使用单独的 PhysicalCallDesk。legacy Physical 的 owner 延迟 backstop 可变成 NetShare，已有定向测试确认，不能混同两份 call desk。
- Earn 是异步股票/USDG 双资产份额金库，份额不是 FUN，当前没有已部署 FUN/V4 LP 的自动收入接入。desk 的协议是 allowlisted writer/owner offer 与 buyer fill，没有链下 EIP-712 RFQ 签名验签实现。
- 复投使用真实 EarnVaultV3SwapAdapter，但接的是一次预资、固定报价、30 bps 手续费的 MockPool。它检验转账与额度，不代表历史或测试网 V3 流动性，不能据此推断 FUN 币价或 LP 收益。

## 事先固定的情景

复用冻结的 TSLA、NVDA、META 2025 年 250 个日线 Close。原始 Yahoo 时间戳是开盘标签，不表示收盘价在开盘已知；本测试把已知 Close 放入合成时钟与始终开放日历。Close 已拆股调整，NVDA/META 分红未派发。股票筛选来自完整 2025 年成交额/波动率，因此属于样本内描述性选股。

- writer 首日股票本金约 $10,000；买方一次预资 $100,000 USDG 加首日价值 $100,000 股票；复投池另有 $100,000 股票。全年不增发、不补资。writer、买方、池的本金和期末资产分别列账。
- call strike 是起始 Close 的 105%，put 是 95%。保费是假设的每 7 自然日名义额 50 bps，按实际秒数比例并设置 25 bps 最低保护；不是历史期权价、隐含波动率或可成交 RFQ。
- 每张最多覆盖 5 个交易观察点，且最多 9 个自然日以符合 Earn MAX_TENOR；节假日期间仅按日历缩短期限。RFQ 最小名义额与复投最小金额均为 $1，避免尘埃调用。触发这两项运行边界的失败探针保留于本地 artifacts。
- 三股分别测试 NetShare、Physical、call→put→call wheel、无成交、买方不行权、只将收到的保费复投六种情景。另加 TSLA wheel 25/100 bps 两组敏感性，共 20 组。
- Physical 在交割后保留行权现金；wheel 才用实际现金锁定 put 抵押。只复投保费的情景不挪用行权本金。无成交零保费；不行权情景是买方放弃 ITM 权利的风险对照，不能作为理性市场报价收益预测。

## 完整年度结果

以下为首日 Close 到末日 Close、期末全部期权已经终止后的资产变化。不是标准跨年收益、APY、FUN 回报或参数推荐。已收保费与期权腿净损益分开；期权腿损益不包含持股跨日涨跌。

| 股票 | 情景 | 期末资产变化 | 收到保费 USDG | 已结算期权腿净损益 USDG |
|---|---|---:|---:|---:|
| META | netshare | +11.6792% | 2,507.43 | +273.16 |
| META | no_exercise | +38.9241% | 2,876.95 | +2,876.95 |
| META | no_fill | +10.1545% | 0.00 | +0.00 |
| META | physical | +8.9668% | 159.61 | +91.50 |
| META | premium_reinvest | +9.0498% | 160.69 | +91.12 |
| META | wheel | -3.1054% | 2,610.87 | +325.94 |
| NVDA | netshare | +9.3447% | 2,156.90 | -1,200.31 |
| NVDA | no_exercise | +63.4138% | 2,857.18 | +2,857.18 |
| NVDA | no_fill | +34.8420% | 0.00 | +0.00 |
| NVDA | physical | -6.3379% | 250.93 | -21.40 |
| NVDA | premium_reinvest | -6.4105% | 253.71 | -25.36 |
| NVDA | wheel | -2.3256% | 2,396.17 | -325.51 |
| TSLA | netshare | -11.1173% | 1,852.53 | -1,892.16 |
| TSLA | no_exercise | +42.7683% | 2,419.63 | +2,419.63 |
| TSLA | no_fill | +18.5720% | 0.00 | +0.00 |
| TSLA | physical | +10.3715% | 109.16 | -207.84 |
| TSLA | premium_reinvest | +10.2154% | 111.51 | -207.52 |
| TSLA | wheel | -12.8968% | 2,316.81 | -876.67 |
| TSLA | wheel_premium100 | +14.1711% | 5,281.63 | +1,767.16 |
| TSLA | wheel_premium25 | -23.9056% | 1,099.44 | -1,949.63 |

50 bps 情景下，TSLA/NVDA/META wheel 都收到保费，但期末资产分别变化 −12.8968% / −2.3256% / −3.1054%。META 的期权腿净损益为正，整体仍亏损，说明持股损益不能省略。TSLA wheel 在 25/50/100 bps 假设下分别为 −23.9056% / −12.8968% / +14.1711%，结论明显依赖无法由股票日线验证的保费假设。

## 会计与实际资金证明

每日 OPTIONS_ROW 包含钱包/托管股票及现金、买方与池余额、grossAssets、期权 intrinsic 下界、累计已收保费、实际交割价值和复投成本。未到期期权包含时间价值；grossAssets 或 grossAssets−intrinsic 都不能冒称 fair net NAV。汇总回报仅使用无未结算期权的期末点，回撤仅为已结算检查点回撤。

OPTIONS_CONTRACT 记录 offerTimestamp、fillDeadline、expiry、数量、行权价、模型保费、实际终态、actualEarnClosedEpochNav，以及带符号的 settledOptionPnl = 实付保费 − 实际 ITM 交付在到期价的价值 − desk 费用（当前为零）。行权价支付属于本金交换，不是保费。复投费用在 OPTIONS_REINVEST 单列。现金与股票供应逐日守恒，MM 不能无限获得资金；最终 Earn 赎回到账和 dead-share 残余精确核对。

六项风险测试覆盖 legacy Physical backstop 的 NetShare 行为、Earn 拒绝 NetShare/重复抵押、冻结 vault 后 owed 阻止关账并恢复、冻结赎回者先收现金后领取股票、对手现金不足无法成交，以及 put 股票交付被拒时现金抵押不得流出。

独立桥接用一个价格不变的 OTM call：MM 实付保费 57.142858 USDG；Earn 收回全部股票本金，投资者实际赎回现金 57.142855 USDG；这笔现金通过真实 transferFrom 注入 FunStakingIncome，7 日后真实领取 57.142854 USDG，剩余 0.000001 USDG 留在奖励合约。质押本金仍完整保留。这里的 FUN 是隔离测试币，未接已部署 V4。这条正收益路径不代表全年保费均可分配；累计亏损、现金可用性和本金偿付需由收入源另行校验。

## 重跑与证据

```sh
forge test --offline --match-contract OptionsYearlyReplayTest -vv
python tools/options_history_report.py --log ../artifacts/all-strategy-income-20261003/options/forge.log
python -m unittest discover -s tools/tests -p test_options_history_report.py -v
```

上述命令以 contractV2 为工作目录。图表需要已有 Matplotlib 环境；不需要网络或私钥。原始归档可用 --log deploy/all-strategy-income-2026-10-03/options/forge.log.gz 重新导出。

- [测试源码](../test/OptionsYearlyReplay.t.sol)
- [导出器](../tools/options_history_report.py)
- [完整机器汇总](../deploy/all-strategy-income-2026-10-03/options/results.json)
- [逐期实际结算账](../deploy/all-strategy-income-2026-10-03/options/option_contracts.csv)
- [逐日余额](../deploy/all-strategy-income-2026-10-03/options/daily.csv)
- [期末对比图](../deploy/all-strategy-income-2026-10-03/options/charts/settled-terminal-comparison.png)

没有计入真实期权市场价、gas、税费、融资、分红、历史 V3 深度或 FUN/V4 价格反馈。覆盖到的合约路径与报价模型限制应同时阅读。

## 与 IncomeAllocation 的 NetShare 差异核对

另一份 [IncomeAllocationFork.t.sol](../test/IncomeAllocationFork.t.sol) 为已部署 FUN/V4 的独立外部 sponsor 测试，使用相同股票 Close 与 105% call strike，但没有沿用此处的九自然日期限上限。这里为了同 Earn 对齐，每期最多五观察点且至多九自然日；Income 固定每五观察点重开。legacy CoveredCallDesk 本身允许更长期限，因此两者都是有效但不同的日历压力情景，不能当成同一 covered-call 基线横比。

第一次分叉是 2025-01-17：本回放在 01-24 到期，Income 在 01-27 到期（十自然日）。此后的开仓日期、起始 Close、行权价和交付股票数量随之不同。本回放每股 52 期，Income 每股 50 期。MM 是否另有原始股票库存不影响这些仅收保费并净股交割的 writer 账；双方所用现金都一次预资且足够。

独立整数重建逐期对应两份实际 EVM 日志，三股、Income 的三个分配比例全部精确匹配。下表中间列是透明的算术反事实拆分，不是额外 EVM 回测或挑选参数。金额为 USDG：

| 股票 | Options实际终值 | 只改为固定五观察点 | 移除保费guard | 首期额外601秒计息后 | 扣Income实际分配后的终值 |
|---|---:|---:|---:|---:|---:|
| TSLA | 8888.271978 | 6376.787607 | 6376.787607 | 6376.837292 | 6319.644749 |
| NVDA | 10934.471998 | 10437.916260 | 10437.916260 | 10437.965945 | 10375.045618 |
| META | 11167.919544 | 11315.499172 | 11315.499172 | 11315.548857 | 10811.360887 |

TSLA 的精确差额为：日历/重开仓路径 −2511.484371；移除 25 bps guard 为 0；首期 +601 秒增加保费 +0.049685；实际分配 −57.192543。最终由 8888.271978 对应到 6319.644749。剩余股票从 15.644712218763620649 TSLA 变为 10.916450729335108090 TSLA，主要差异来自日历导致的多期股票交付，并非本次小额分红选择。

损益列也有不同口径：Options 的 `settledOptionPnl` 按交付股票在到期 Close 的价值计期权腿损益；Income 的 `cumulativeRealizedNet` 按首日股票历史成本计已实现账。相同的 52 期 TSLA 原始转账，前者是 −1892.156414，后者是 −2213.741596。Income 的 50 期相应为 −3546.085262 / −4392.117528。这是损益拆分与分配门槛差异，不改变钱包余额或终值。

Income 三种分配比例的 sponsor 余额、已实现账和可分配总额完全一致；只改变该笔收入去向。TSLA / NVDA / META 实际可分配总额分别为 57.192543 / 62.920327 / 504.187970。TSLA 与 NVDA 年末已实现账仍是负数，不能把曾分过现金称为全年净盈利。META 的 100% 分红组到年末实际已领 459.705235，另 44.482735 仍在七日 stream 合约，不能把 funded 全额写成已领取。

Income 产品的初始资本是独立 $20k FUN fund 加 $10k sponsor，共约 $30k；只在期末没有开放期权时，才可比较 fund 外部资产 + sponsor 股票/现金 + staking 留存现金 + 已付现金。FUN 的边际标价涨幅不是这个组合的净回报，也不能与 sponsor 自身的 $10k 回报分母混用。

复现命令（仓库根目录）：

```sh
python contractV2/tools/options_income_crosscheck.py
```

- [可复现交叉核对脚本](../tools/options_income_crosscheck.py)
- [精确逐期核对与waterfall JSON](../deploy/all-strategy-income-2026-10-03/options/netshare-calendar-crosscheck.json)

核对 provenance：

| 对象 | SHA-256 |
|---|---|
| dataSha256 | `9042271c25a5d3e53a9b1ce09e88e87ae2544d5ce50a665c0bb8f31d11b5eea0` |
| optionsHarnessSha256 | `b6c995f19073a5229b5aac68ed652f661780476dda88bf3c29d0cdbb53cdd81a` |
| incomeHarnessSha256 | `0bb1a071586e07b3bd94c8ce4582fb9028ee21ce39ec54ef63d80a8fee8c8066` |
| optionsRawLogSha256 | `298c4c52267b736f4f13ea8ee8145dbdf0f19fd4fc21683a6c04f74d01cbedb9` |
| incomeRawLogSha256 | `4588ab963272025d80b481cca19161eee6b227a71fe58bde2fe7eb8046540b65` |
| crosscheckToolSha256 | `cf4a1c3ab450d6d742b6bc70855c748988a52fbb449f7996256a6a4dd176ea0d` |

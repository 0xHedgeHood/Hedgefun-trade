# RHNVDA 金库：现金担保 put 与 covered call 轮动

**状态（2026-10-01）：源码与本地测试阶段，未部署 put desk，也未开放 put RFQ 或用户存款。** 本文描述目标机制与上线门槛；实际合约行为以部署和审计后的代码为准。金库资产是链上 RHNVDA Stock Token 与 USDG，不是券商账户里的 NVDA 股票。[PhysicalCallDesk](../src/options/PhysicalCallDesk.sol) 负责 call 的实物交割；新增 [CashSecuredPutDesk](../src/options/CashSecuredPutDesk.sol) 锁定 USDG、处理 put 的实物行权，再由 [EarnVault](../src/options/EarnVault.sol) 在同一期份额账本里轮动。

## 一笔本金如何轮动

```text
持有 RHNVDA ──锁币卖 covered call──> 到期
       ↑                            │ call 实物行权：交付 RHNVDA，收到行权价 USDG
       │                            ↓
put 实物行权：支付行权价 USDG，收到 RHNVDA <──锁 USDG 卖 cash-secured put── 持有 USDG
```

每期最多开一个方向的 RFQ，且只使用金库实际拥有的抵押品：call 锁定不超过可用 RHNVDA，put 在**报出/成交前**锁定不少于 `行权价 × 数量` 的 USDG（计入代币精度），直到取消、到期结算或行权完成。期权权利金以 USDG 收入金库净值，但它不替代所需抵押品。call 到期未行权时仍持有 RHNVDA；put 到期未行权时仍持有 USDG，下一期可以继续选择同一方向。只有发生并完成实物交割，仓位才自然切换。

**只有 RHNVDA、没有 USDG 时，先从 covered call 开始。** call 成交后收到的 USDG 权利金可以留作未来 put 抵押，但不能拿本次尚未成交的 put 权利金预付它自己的行权款：put desk 在 `offer` 时就要锁足现金，而买方在 `fill` 时才付权利金。当前金库每期只允许一张 call **或** put；call 尚未终止时，即便收到权利金，也不能并行再开 put。若 call 到期未行权，继续持有 RHNVDA，下一期可选择继续卖 call 或仅用累积的现金卖小额 put。若 call **正常实物行权**，金库交付 RHNVDA 后收到 `行权价 × 数量` 的 USDG，下一期才有足够现金考虑相近规模的 put。把每期权利金立即复投买 RHNVDA，则相应现金不再可用于 put 抵押。

put 买方行权时须交付相应数量的 RHNVDA，金库才支付锁定的行权价 USDG；若价格跌得更低，金库仍按行权价买入。put 行权后的每枚代币简单净买入成本为 `K − 本期每枚 put 权利金`，但这不是价格下跌时的亏损上限。put 与 call 的正常交割均要求标的与结算代币实际可转移；发行方冻结、价格源异常、行权窗口或 desk 的回退结算均可能延迟轮动或改变交割资产。**“每周卖”是运营目标，不是每周必然结算或支付现金的承诺。**

## $100k 金库与 400 枚的资金占用

以下沿用讨论时的 **$231.704** 参考价、400 枚、7 天和 **44.06% 的历史波动率**，以零利率、零分红的 Black–Scholes put 定价作教学估算。它不是当前行情、隐含波动率、Wintermute RFQ 或可实现权利金。

| 7 天 put 行权价 | 模型总权利金 | 400 枚必锁 USDG | 若到期价 $200，put 腿按市价计的损益 |
| ---: | ---: | ---: | ---: |
| $225 | 约 $1,135 | $90,000 | 约 −$8,865 |
| $230 | 约 $1,923 | $92,000 | 约 −$10,077 |

put 腿的到期损益公式是 `总权利金 − 数量 × max(K − 到期价, 0)`，未扣 RFQ 价差、gas、兑换成本、手续费和其他持仓损益。价格跌至零时，最大股票方向损失接近锁定的行权款减权利金。高权利金并不等于高净收益或稳定股息。

约 $100k 若已用 400 枚 RHNVDA 担保 call，**不能同时**把相同股票算作 put 的 USDG 抵押品。若同周再卖 400 枚 $225 / $230 put，须在原有股票之外另备 $90k / $92k USDG；借款凑足现金则另有利息、清算与再融资风险，不属于这里的全额现金担保轮动。用同一笔本金轮动时，应等 call 确实交割、金库收到 USDG，再按届时可用现金决定 put 数量。相同到期和行权价附近，现金担保 put 与持股卖 call 的到期损益形状非常接近；不能把两段不同周的模型权利金相加，宣称同一周或同一笔本金的双倍 APY。[OIC 的策略比较](https://www.optionseducation.org/videolibrary/same-p-l-different-trade-cash-secured-put-vs-covered-call)

例如，若一周 covered call **实际**收取 $500 权利金且未行权，按 $225 put 行权价最多只能担保约 `500 / 225 = 2.22` 枚 RHNVDA（若 RFQ 只接受整枚则为 2 枚），距离 400 枚需要的 $90,000 很远；这只是资金上限，实际最小 RFQ 数量、赎回预留和费用还会缩小可报价规模。相反，若 400 枚 $235 call 正常实物行权，行权款为 $94,000；加上已收权利金、扣除赎回预留后，才可能覆盖 400 枚 $225 put 的 $90,000 行权款。两种结果都取决于实际成交和行权，并非到期自动发生。

## hNVDA、用户存取与每期净值

[EarnVault](../src/options/EarnVault.sol) 的 hNVDA 是对金库资产的份额，不是固定 1:1 可兑 RHNVDA、固定美元本金或固定股息。存入需要通过金库的地址准入；已持有份额者失去准入后仍可申请赎回。金库在期权未终止时不以未实现的权利金预测给新老用户定价。用户提交的 RHNVDA 存款先进入待处理队列，不得拿去担保当前期权；赎回申请中的 hNVDA 在结算前仍计入份额供应。一个期权到达最终状态、锁定抵押品及 desk 欠款完成处理后，使用新鲜价格源把**可用 RHNVDA + USDG**计算为同一期的份额净值，按同一边界处理新存款与赎回。

赎回领取的是当时金库 RHNVDA / USDG **按比例分配的资产篮子**，因此在 call 行权后的现金阶段可能主要拿到 USDG，在 put 行权后的持币阶段可能主要拿到 RHNVDA。已预留给前期赎回的资产和本期待入金不得再用于 put/call 抵押或复投。金库通过 `activeOptionKind` 保证同一 epoch **call 或 put 只能有一个未终止头寸**，且 `closeEpoch` 同时检查两类 desk 的未领取款项及锁定状态。未来页面若展示历史模型，应与真实金库 NAV、RFQ 成交价、已实现权利金及净回报分开标示。

源码接口：Safe 一次性调用 `EarnVault.setPutDesk` 绑定同一 USDG、calendar 和 owner 的 put desk，并在 desk 将金库设为 writer、将做市商设为 buyer。每期 Safe 用 `EarnVault.offerPut` 创建指定对手方的 put；`activeOptionKind` / `epochOptionKind` 区分 call 与 put；`closeEpoch` 在该期权终止且两个 desk 的欠款均清零后定价；失败的 put desk 支付可经 `claimPutDeskOwed` 取回。上述设置与报价调用都是待部署操作，并非已经开放的产品功能。

## RFQ 与风险控制

1. **对手方与报价：** Safe 批准限定买方、具体数量、行权价、总权利金、到期与短暂成交时限；独立核对 Wintermute 或其他做市商报价和实时隐含波动率。历史波动率不能代替可成交 IV。源码默认 put 行权价不高于当时 oracle 现价、权利金至少为内在价值加现价名义金额的 25 bps、到期在 12 小时至 9 天之间、成交窗口不超过 5 分钟，最多占用全部可用 USDG；这些仅是防明显错误的边界，绝非公允价格保证。
2. **抵押和偿付：** put desk 在 offer 时实际托管全部行权款；买方行权应原子交付 RHNVDA 并取得 USDG。期权仍有效时不得把同一 USDG 用于赎回、买币、借出或第二张 put。到期未行权后退回全部未用抵押品。每张 put 的代币面额、USDG 6 位精度与 RHNVDA 18 位精度必须由测试覆盖。
3. **异常路径：** 明确 oracle 轮次缺失、到期后未行权、发行方冻结或黑名单、付款/交付失败的欠款记录、desk 紧急回退和可领取路径。现有 call desk 的 owner 可在 14 天后用 backstop 对 *Physical* call 做类似 NetShare 的结算；不能向用户承诺所有异常情形必为实物交割。新增 put desk 的 owner 回退在 14 天后指定价格；若价内，只开启最长 3 天的实物行权窗口，买方仍须交付 RHNVDA 才能领取 USDG，而买方不行权则抵押款最终返还。
4. **风险敞口：** put 行权后 RHNVDA 仍可继续下跌；call 可能封顶上涨收益。轮动不对冲 NVDA 方向风险。不得把模型单周期权利金简单乘以 52 或复利，作为金库 APY。借贷放大、分层股息流和 hNVDA 抵押借贷均需另立合约、独立压力测试和风险披露，当前轮动不包含这些能力。

## 上线前验证

- 对新增 put desk 和修改后的 EarnVault 进行独立安全审查、完整本地与链上 fork 测试：OTM 退还现金、ITM 实物行权、买方不行权、取消过期 RFQ、issuer 冻结、feed 陈旧、待处理存取款、少量金额和小数位舍入、两类 desk 的欠款、用户领取、跨期 NAV。
- 核对 RHNVDA、USDG、价格源、calendar、Safe 与做市商地址及权限；先完成 Safe 白名单和限额配置，再小额全流程试运行。记录每期真实成交价、IV/Delta、收取权利金、抵押占用、到期价、交割资产、NAV 和净回报。
- 部署地址、审查与试运行未完成之前，前端应保持 put 交易和用户存款入口禁用；对外只能展示清楚标记的模拟情景，不能将本文模型权利金或此前 44% 波动率写为实际 APY。

策略机制参考：[Options Industry Council 的 Wheel 教育资料](https://www.optionseducation.org/videolibrary/what-is-the-wheel-strategy)、[Cash-Secured Put 说明](https://www.optionseducation.org/strategies/all-strategies/cash-secured-put)。这些资料描述传统股票期权；RHNVDA、USDG、链上 RFQ 和合约交割路径以本项目代码与实际部署为准。

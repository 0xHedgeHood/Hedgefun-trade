# V2双向手续费：基础费收入与销毁分别记账

用户选择的发布配置为基础费3%，费用按协议20%、创作者10%、金库70%分账。
新代码支持这组参数，同时保留已有的、有上限的按launch选费率机制；它不把所有自定义launch硬编码为3%。
发布请求须采用 `taxBps=300`、`creatorBps=1000`，Factory defaults采用 `protocolBps=2000`。
每个launch的参数在创建时冻结；更改未来默认值不能追改已发行的池子。

## 当前改动

| 阶段与交易 | 基础手续费 | 收入如何实现 |
|---|---|---|
| 曲线买入 | 实际股票支付额中扣基础费 | 净本金进入曲线，股票费用即时记入三方可领取账本 |
| 曲线卖出 | 实际毛股票输出中扣基础费 | 三方可领取账本，和买入共用分账函数 |
| V4固定投入金额买入 | 实际策略币输出中扣基础费 | 先记录每池ERC-6909策略币手续费claims，批量兑换股票后分账 |
| V4固定投入金额卖出 | 实际毛股票输出中扣基础费 | 股票claims由正常sweep结算到三方 |
| V4指定买到数量的买入 | 维持现有股票输入gross-up费 | 股票claims按相同分成结算；不支持指定卖出所得的卖出 |

V2毕业后的 `sweepTipBps=0`，基础费不会先被清算奖励稀释。实际分账对协议和创作者取整，剩余归金库。
曲线买入的开盘附加税仍单独烧策略币，归一化比例为 `(当前总名义税率−基础费率)/(10000−基础费率)`。
普通基础费不再用于买入即销毁。股票侧LP手续费仍属于独立的金库回购预算，策略币侧LP手续费仍销毁。
V4的0.30% LP费是额外费用，不能称整个swap只收3%，也不能计成协议地址收入。

## 费用账本与兑换边界

`HedgeFunV2Hook`是独立的新hook，沿用真实PoolManager的交易结算与每池税收账本。
旧 `HedgeFunHook` 的买入销毁行为保留；基础类只开放继承所需的可覆盖函数和共享重入锁。
新版Factory拒绝不支持双向收入的旧hook。

普通买入的税首先出现在 `accrued(poolId).inToken`。任何人调用 `sweep` 后，税移入
`pendingTokenFees(poolId)`；这仍是PoolManager中的ERC-6909 claims，未兑换，不是股票现金、USDG收入或销毁供应。
不把这些策略币赎回成hook的长期ERC20库存，避免与其它池子的股票赔付余额混合。

Factory当前owner可以通过 `convertFees(key,maxTokens,minStockOut,sqrtPriceLimitX96,deadline)` 执行兑换。
owner负责独立审阅最小输出与时限；普通用户交易不等待这次兑换。minOut必须非零；兑换限价必须位于
当前sqrt价格的0.5%范围内（约1%池价变化），并且方向正确。这个边界限制单次成交冲击，不证明市场公允价格。
兑换依据实际成交消耗输入claims，未成交部分保留；股票输出重新mint成该池股票claims，正常sweep之后才支付。
`FeesConverted`区分消耗数量、产生的股票claims和剩余库存，不能把它误记成已支付USDG。

兑换使用绑定输入hash的单独unlock上下文，和分配共享重入锁；不能在已有sweep中再次unlock。
旧股票赔付亏损在新收入到来前核对；内部交易避免重复手续费并补写价格观测。
失败的兑换整体回滚，包括该次入口的预先sweep；独立的正常sweep仍可继续领取已有股票费用。

## 前端、部署与旧证据

曲线 `quoteBuy` / `quoteBuyFor` ABI保持不变，但第三个返回值现在只表示开盘附加销毁。
基础股票手续费为 `floor(实际stockSpent×taxBps/10000)`。报价、费用展示必须分别显示基础费和附加销毁。
超出毕业cap的退款不收手续费；毕业迁移的股票金额仅为真实净本金，不包含三方未领取债务。

新版的curve、hook和Factory需重新部署，只应用于新launch。不能把新ABI或新收入模型套到已部署旧池。
部署脚本采用新hook的creationCode重新找地址并readback `version()==2`，地址簿标记新fee featureVersion。
本次不签名或广播任何链上交易，也不迁移旧池。

旧资本/持仓/供应模拟保持其旧规则的历史含义。由于基础买入销毁变成收费分账、曲线本金扣费和
V4费用兑换都会改变储备与供应，旧金额不能作为新版测量结果。
这次只验证收入、资产负债、退款、部分成交、隔离和交易边界，不测算协调拉盘或退出收益。

## 参考与验证

[pump.fun官方费用](https://pump.fun/docs/fees)分别列出creator、protocol和LP费用；
[Pons官方V2源码](https://github.com/ponsdotdev/pons-labs)采用曲线quote收费，以及V4策略币费用批量换quote后分配。
本实现的参数、启动税与金库策略独立，不声称复制两者的经济模型。

实现：[`HedgeFunBondingCurve`](../src/v2/HedgeFunBondingCurve.sol)、
[`HedgeFunV2Hook`](../src/hooks/HedgeFunV2Hook.sol)、[`HedgeFunV2Factory`](../src/v2/HedgeFunV2Factory.sol)。
验证覆盖实际PoolManager下买卖收入、claims到股票到账的分账、币种排序、封顶退款与费用负债，
并回归旧V1销毁及股票受阻的独立领取路径。最终检查与审查结论记录在PR中。

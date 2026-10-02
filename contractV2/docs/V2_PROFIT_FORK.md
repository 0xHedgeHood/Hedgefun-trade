# V2 回购销毁：真实场地 Fork 验证

[`StrategyForkTestV2LiveVenue`](../test/V2LiveVenueFork.t.sol) 在 Robinhood Chain 的固定区块 `70,786,980` 上重放真实 USDG/GME V3 池和已部署的 V4 PoolManager。V2 工厂、曲线、Hook、国库和路由只在本地 Fork 内新部署；测试不会签名或向链上广播。用户测试资金来自 Fork 中被模拟身份的真实池，喂价答案来自链上但时间戳被刷新，交易日历采用 `AlwaysOpen`。这些条件使测试能核对真实交易场地与合约调用，不代表历史区块原始喂价或交易时段可用。

```sh
RH_FORK=1 RH_RPC=https://rpc-robinhood.blockmachine.io \
  forge test --threads 1 --mc StrategyForkTestV2LiveVenue -vv
```

固定区块需要可读取历史状态的 RPC；`foundry.toml` 默认的 `robinhood` 公共端点目前无法提供这一旧区块。`RH_FORK_BLOCK=0` 可改用当前区块做非确定性复跑。

测试完成两人曲线买卖、毕业、V4 买卖、Hook 税与 LP 费结算后，`V2LiquidityVault.collectFees()` 把**实际产生的**股票侧 LP 费记入 `HedgeFunV2Treasury.buybackStock()`。先在开盘买单推高 FUN 价格的状态下调用回购：国库因毕业价格锚点拒绝追价，回购预算不被消耗。再由持币人通过真实 V4/V3 路由卖出，使价格回到可成交范围，国库才调用真实的 `buyback()`。测试核对实际股票花费、预算扣减、FUN 调用者赏金、总供应及累计销毁量、国库不留回购 FUN、所有受测代币的余额守恒，以及回购后卖税仍保持平税率。V2 现已冻结 `spikeBps = 0`，避免 LP 手续费资助的回购反复触发高额卖税。

固定区块本次结果：LP 股票费 `0.001215108843997113 GME`，全部被回购花费，净销毁 `45.964517405139036009 FUN`；另有 LP FUN 费 `35.356092336806782012 FUN` 按已有逻辑销毁。这些数值是本地 Fork 的一次执行记录，测试通过的是精确余额关系，不依赖上述常数。

同一套件还注册并选择 exact `HedgeFunV2BuybackTreasury` creation code，完成 kind-1 的
`registerKind → setStrategyKind → predict → launch → graduate → book → buyback`。50/50 下毕业预算为
`2.063232381580000001 GME`，首次实际回购 `0.031514325436919898 GME` 并净销毁约 `1,194 FUN`；70/30
下预算为 `1.237939428948000001 GME`，首次回购 `0.044120055611687857 GME` 并净销毁约 `1,671.6 FUN`。
为把 archive 读取控制在可接受范围，此 fork 的毕业池仅约 100 USDG 深，kind-1 场景明确把测试
`minLotUsdg` 调到 `0.25 USDG`；生产参数不能照抄，必须保证 impact cap 允许的最小实际成交不低于
`minLotUsdg`，否则预算存在但 `buyback()` 会持续 `NotDue`。

**边界：这不是“策略净盈利后分红”的 Fork 证明。** 回购资金来自 LP 手续费，不是策略净盈利。当前 V2 `buybackStock` 还可能来自逐 lot 止盈，但它混合了 LP 费，既没有全金库净利润/历史亏损门槛，也没有持币人分红、快照或领取接口。以领取时的 ERC-20 余额分红会允许同一批 FUN 在不同钱包重复领取。要在 Fork 上真实验收“只用策略净盈利分红，同时继续回购”，须先实现分账与全金库盈亏门槛、跨期亏损结转、合格持币/质押份额的确定、领取负债及对领取与回购的并发保护，再在真实 V3/V4 路径上覆盖亏损抵消和转币重复领取等场景。**当前结论：回购销毁 GO；持币分红 NO-GO。**

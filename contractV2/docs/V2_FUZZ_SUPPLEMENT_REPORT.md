# V2 fuzz 补测：百分比额度、成本记账与 dust 后续执行

本轮补测针对原 fuzz 报告的三处性质缺口。使用 **Foundry fuzz / stateful invariant**；原报告的 Echidna harness 未提供，本轮独立建立测试。

## 版本与环境

- 目标仓库：`0xHedgeHood/Hedgefun-trade`，默认分支 `codex/contract-v1`。
- 生产代码基线：`179c4a4c510d0f627440b41f644743d942c4e5c6`。
- 实际测试提交：`86ada137e39f612af095ea4de545170450ba9243`，仅补测试和运行脚本，生产合约未修改。
- Foundry：`1.5.0`，工具提交 `1c57854462289b2e71ee7654cd6666217ed86ffd`。
- Solc `0.8.26`，Cancun，optimizer runs `1`，bytecode hash `none`。
- 三个完整 seed：`0x2026100301`、`0x2026100302`、`0x2026100303`。
- 运行使用 `--offline`，不需要 RPC、签名或测试网资金。

## 测试性质

### 1. NAV 百分比额度

文件：[V2AssetPercentLimitsFuzz.t.sol](../test/V2AssetPercentLimitsFuzz.t.sol)、[V2AssetPercentInvariant.t.sol](../test/V2AssetPercentInvariant.t.sol)。

- 每次成交前独立读取真实 V4 position 和资产余额，计算 NAV、单次百分比额度及当日百分比额度。
- 根据实际 USDG 支出，或实际库存出售与 buyback 分桶变化重建经济成交量；用独立 trading-date 账本核对累计 turnover。
- 改变价格、现金/股票余额和配置，检查 NAV 收缩不改写历史消耗，新增 NAV 只释放当日额度差额，跨日才重置账本。
- 覆盖部分成交、低于最小成交门槛的失败、即时 cooldown 失败；失败时资产、nonce、时间戳、策略状态与奖励保持原状。
- 每个混合路径样例强制执行 **4 买、4 卖、16 次原子拒绝、5 次成功部分成交、1 次交易日切换**。没有通过 `assume` 丢弃输入。
- stateful handler 只选择 12 个修改状态的入口，getter 不计入调用数。

### 2. 两种引擎的成本记账

文件：[V2EngineCostInvariant.t.sol](../test/V2EngineCostInvariant.t.sol)。

- 分别测试固定 USDG 引擎和 NAV 百分比引擎，通过真实 factory 注册、曲线交易及毕业创建 fund。
- 独立维护预期 `avgCost`、`bookedStock`、`buybackStock`、`totalStockReceived`，成本预期不从生产 `avgCost()` 回读。
- book 按本次认证价格计入新增库存；买入按实际 USDG 支出与扣 keeper 奖励后的净入库库存更新向上取整成本。
- 卖出核对真实 venue / treasury / keeper 余额差、盈利分桶、奖励和部分成交，剩余库存成本保持不变。
- 每个随机多步骤样例在两个 schema 各自执行一次成功 book、一次盈利部分卖出、一次亏损部分卖出、一次部分买入及两次原子失败。
- stateful handler 只选择 9 个修改状态的入口；nonce 与成功成交计数相等。

### 3. Dust 后续执行与收款会计

文件：[V2DustProgressFuzz.t.sol](../test/V2DustProgressFuzz.t.sol)。

6 条路径分别运行于普通 treasury / AllIn treasury 与两种 stock 排序，共 24 个 fuzz 测试：

- stop / profit residual 清理只改变库存账本，不制造出售事件、资产转移、keeper 奖励或 stop gate 更新。
- 清理同一调用继续执行后续真实 stop / profit；奖励精确等于实际成交应付金额。
- stop 后重新入场遵守 cooldown、新价格报告和更深跌价三个条件，满足条件后真实 dip 买入成功。
- 释放库存再 book 时，只把新增 donation 计入 `totalStockReceived`，原 residual 不重复计数。
- 128 lot 加 pending donation 时保留清理及重新 book，库存分区相等、lot 账本相等，不虚构成交或奖励。

随机变量包括 residual、donation、后续 lot 数量、现金和 cooldown age；各样例断言具体成功或拒绝结果，没有用 `assume` 排除失败样例。

## 运行预算与结果

2026-10-03 完成三个 seed 的完整预算：**76,800 个随机样例、393,216 次 stateful handler 调用；99 个测试结果通过，0 失败、0 跳过**。共 33 个测试定义，每个 seed 分别运行一次。

| 组别 | 每 seed 预算 | 三 seed 的实际计数 |
|---|---|---:|
| 百分比 stateful invariant，2 条性质 | 256 runs × 128 depth / 性质 | 196,608 handler calls |
| 两 schema 成本 stateful invariant，2 条性质 | 256 runs × 128 depth / 性质 | 196,608 handler calls |
| 百分比随机序列，3 个 fuzz 测试 | 1,024 样例 / 测试 | 9,216 样例 |
| 两 schema 成本随机序列，1 个 fuzz 测试 | 1,024 样例 | 3,072 样例 |
| Dust 后续执行，20 个 fuzz 测试 | 1,024 样例 / 测试 | 61,440 样例 |
| Dust 128 lot 容量，4 个 fuzz 测试 | 256 样例 / 测试 | 3,072 样例 |

计数口径：随机样例包含多个合约动作；handler calls 包含 donation、价格/时间变动、book 和 execute 尝试。两类计数不相加为链上交易数，也不代表独立状态数量。每条 invariant 分别运行，所以 handler calls 包含不同性质的独立 campaign。

## 复跑与原始证据

在目标仓库的 `contractV2/` 下运行：

```bash
bash tools/run_v2_fuzz_supplement.sh /tmp/v2-fuzz-supplement
```

脚本默认使用上述三个 seed 和完整预算；环境变量 `V2_FUZZ_RUNS`、`V2_CAPACITY_FUZZ_RUNS`、`V2_INVARIANT_RUNS`、`V2_INVARIANT_DEPTH`、`V2_FUZZ_SEEDS` 可指定预算。每组保存原始日志和完整命令，并拒绝没有 PASS 或含 SKIP 的运行；handler 意外 revert 会使 campaign 失败。

完整证据保存在 [fuzz/v2-supplement-2026-10-03](fuzz/v2-supplement-2026-10-03/metadata.txt)：

- [逐测试结果与实际次数](fuzz/v2-supplement-2026-10-03/summary.json)。
- [工具版本、提交、预算、完整 seeds 与测试 SHA256](fuzz/v2-supplement-2026-10-03/metadata.txt)。
- [V2 源码 SHA256](fuzz/v2-supplement-2026-10-03/v2-source-sha256.txt)。
- 每个 seed 的五组 `.log` 原始日志、`.command` 完整命令和 [运行时间](fuzz/v2-supplement-2026-10-03/durations.txt)。

这些 campaign 未发现所定义性质被打破。stateful 日志中的 `reverts: 0` 指没有意外的 **handler** revert；测试内主动制造并检查的 treasury 拒绝仍被计入成功验证的回滚路径。

额外回归门槛：

- [`forge build --offline --sizes`](fuzz/v2-supplement-2026-10-03/build-sizes.log) 完成，生产合约尺寸门槛通过。
- [`forge test --offline --threads 4`](fuzz/v2-supplement-2026-10-03/offline-suite.log)：88 个 suite，763 通过、0 失败、35 跳过。这 35 个跳过属于独立启用的测试，不计入上述补测的 99 个结果；本轮没有重新运行真实 RPC fork。

## 适用范围

- 百分比与成本测试使用真实本地 factory、曲线毕业、V4 PoolManager/hook/vault；外部 V3 成交与价格源使用 mock。
- Dust 使用真实本地 V4 集中流动性镜像和模拟价格源。
- 本轮 calendar 使用 AlwaysOpen 的 UTC trading date；纽约交易日及 DST 已有单独测试，本补测没有重新执行该边界。
- 成本模型读取 preview 的 offered quantity 来核对卖出的收益拆分；输入输出和成本仍使用外部余额差。额度约束由独立百分比测试校验。
- 本报告只描述这些性质、参数范围和预算内的结果。真实 RPC fork、发行方升级、生产流动性与价格源故障不在本轮验证范围。

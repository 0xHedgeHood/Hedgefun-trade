# 2026-10-03 fuzz 与测试网联调记录

基线：`179c4a4c510d0f627440b41f644743d942c4e5c6`，GitHub `0xHedgeHood/Hedgefun-trade`。
环境：macOS arm64，Foundry v1.5.0 (`1c578544`)，solc 0.8.26，仓库固定 submodule；optimizer=1、Cancun、无 metadata hash。
本次包含工作区内的测试及部署脚本修复；`src/` 合约与编译设置未修改。

后续实网更新：用户已为专用钱包补充 0.01 test ETH，公共测试网完整交易闭环现已完成。见下文“公共测试网实际交易”；早期只读快照和 fork 结果仍保留原始时点及范围。

最新追加：已在分支 `codex/testnet-launch-20261003` 部署新 creator 核心（源码提交 `d7e686123362f58583b6e0662701e8e19fa0c9c9`），40 笔部署交易和 19 笔 HFLATEST / ID 0 生命周期交易全部成功，包括 owner 转换 V4 token fees 与最终分配。TP1/TP2/dip/stop 使用 1/2/1/1 bps，saleBps=4000。新增部署回归 19 通过、creator 参数套件 162 通过、部署后完整流程 fork 1 通过。前端应采用 [最新交接及边界说明](TESTNET_LAUNCH_HANDOFF_2026-10-03.md)，下文 HFFRESH 及旧地址保留为历史证据。

证据已加入本分支：本文原 `artifacts/fuzz-2026-10-03/`、`artifacts/pr19-review/`、`artifacts/testnet-live-2026-10-03/` 分别复制到 `deploy/verification-2026-10-03/` 下同名目录；最新部署与完整流程在 `deploy/verification-2026-10-03/fresh-creator/`。历史日志和 patch 保留原始内容。PR #19 的确切 head 已合入本地分支；此前 GitHub API 合并曾因 403 未执行；2026-10-03 已通过用户配置的 SSH 身份正常快进合并，GitHub 确认为 merged，合并提交 `719cbd3ff92ef79b83b58ef9aafe4bfc99b412e5`。归档 review-summary 保留当时的失败记录。

## 测试结果

最终加强轮已结束：**739 项通过、0 失败、35 项显式 opt-in 跳过**。46 个 fuzz property 共报告 **188417** 组输入（每项 4096，另重放 1 个已保存样例）；6 个状态不变量各运行 512×500，共 **1536000** 次 handler 调用，未出现 handler revert。双账户会计套件耗时约 802 秒。

部署脚本修复发生在加强轮运行期间，随后以受影响的部署套件单独验证，11 项通过，其中 3 项为新增。因此离线 V2 的最终唯一通过用例合计为 **742**；下表保留各次真实运行计数，不将重复回归相加充数。

| 检查 | 结果 | 证据（仓库根目录相对路径） |
|---|---|---|
| V2 首轮，seed `0x20261003`，每项 fuzz 1024 次 | 732 通过，0 失败，35 跳过 | `artifacts/fuzz-2026-10-03/v2-baseline.log` |
| V2 加强轮，seed `0x20261004`，fuzz 4096、invariant 512×500 | 739 通过，0 失败，35 跳过 | `artifacts/fuzz-2026-10-03/v2-final.log` |
| V1，seed `0x20261003`，每项 fuzz 4096 次 | 39 通过，0 失败 | `artifacts/fuzz-2026-10-03/v1.log` |
| Python 验证工具 | 57 + 20 通过 | `python-tests.log`、`python-tools.log` |
| 部署脚本修复回归 | 11 通过，包括 3 个新回归 | `deployment-regression.log` |
| 已部署 whitelist core 的测试网 fork | 1 通过；曲线买卖、毕业、V4 买卖 | `testnet-journey-fork.log` |
| Native/bridge/ETH 标的测试网 fork | 4 通过，0 跳过；区块 127976901 | `testnet-native-fork.log` |
| 新 creator core 的测试网 fork | 修复后 1 通过；区块 127978606 | `testnet-creator-fork-final.log` |
| 构建与大小 | 通过；V2Factory runtime 24501 bytes，余量 75 bytes | `build-sizes.log` |

表中未写完整路径的日志同样在 `artifacts/fuzz-2026-10-03/`。原始失败日志保留，用于区分测试预期错误、环境漂移和最终结果。
机器可读统计为 `artifacts/fuzz-2026-10-03/summary.json`。新增工具已通过 Python 语法检查、只读快照实际执行和 Bash 语法检查；`git diff --check` 通过。

## 新增覆盖

`V2LaunchNativeRouter.t.sol` 新增 7 个 fuzz property，随机首购金额、nonce、税率、creator 分成、支付偏差、恶意回调类型和捐赠金额：

- 以独立 snapshot 中直接发射的曲线报价对照 ETH 发射结果，覆盖 Active 与 Graduated、实际扣款、费用、token 输出、退款和授权归零。
- stock 或最终 token 滑点比报价多 1 单位时，token/treasury/curve 部署、发射费及 V3 余额全部回滚。
- 多付/少付、V3 过量扣款、错误方向、重复回调、无支付、部分成交和少输出均不留下半个发射。
- 末笔毕业产生退款时必须允许 partial fill。
- 交易不能消费路由之前收到的 ETH/WETH/stock 捐赠。
- 任意 callback data 不得改变支付币种或把款项发送给伪造地址；此类 data 被设计为忽略，不能误断言一定 revert。

现有增强测试同时覆盖：6/18 位 stock 精度与两种 token 排序，多账户买卖/捐赠/领费/毕业账本，毕业失败原子性，V3/V4 回调，双侧手续费，固定额度与 NAV 百分比策略，短成交、keeper 奖励、日额度、oracle/calendar、lot dust、权限和 metadata。

## 已修复发现

### 新 creator core 被共享市场新增资产阻塞

`DeployV2FeeUpgradeTestnet.deploy()` 要求 `market.poolCount() == 8`。在测试网区块 **127978606** 实测数量已经是 **9**，新 creator-core fork 因 `BadBinding("eight markets")` 失败。

修复仅允许 registry 追加其他市场：改为 `poolCount() >= 8`。种子仍必须恰好 8 个；每个指定股票的 owner、oracle、canonical V3 pool、原前 8 个 registry 位置、价格、流动性和 observation ring 仍逐项核验。第 9 个资产不会自动被新 factory 上市。新增回归证明：追加不会阻塞或修改旧状态；替换原池仍拒绝；少于 8 个仍拒绝。

### 周末毕业的测试预期

当前 testnet calendar 关闭，股票 `tryPrice()` 返回 `(false, 0)`。毕业会为 treasury 转入 stock；best-effort `book()` 暂不成功。旧 creator-core fork 无条件断言已入账，因而在周末误报失败。

修复后的测试断言：仍正常毕业、真实 stock 到账、booked 为 0、全部保留在 unbooked；只在本地 fork 推进至下次开市，oracle 恢复健康后 `book()` 入账，账本与真实余额相等。测试不会放过开市时异常的不健康 oracle。

## 公共测试网初始只读快照

只读快照：`artifacts/fuzz-2026-10-03/testnet-readiness-final.json`，chain **46630**，区块 **127978557**，含 block hash、合约 code SHA256、地址和逐项原始读取。可用 `tools/read_testnet_readiness.py` 重做。

- fee factory：`0xACEB03aAeE5494Aa54929Ec840630ae32A9ade0A`；trade router：`0xB291B34CD2D32C4a2DeFCe074107824654D427eF`，绑定正确。
- public launch 已开启，4 个现有策略的 curve.status 均为 **2 / Graduated**。
- 默认发射费为 **25 tUSDG**，不是 Native ETH。
- 指定 V3 factory 中，canonical WETH/tUSDG 的 fee 500 和 3000 池均为零地址。不能把 fork 日志中创建的 ETH 池或 launcher 地址当作已部署公共地址。
- 8 个股票 listing 均 enabled；calendarClosed=true，8 个 oracleHealthy=false，周末价格健康门拒绝工作。前端可显示“已毕业，等待复市入账”。
- operator 原生余额为 **0.02070306702 test ETH**；完整 ETH market 的预检要求至少 **1.001 ETH**。小额 bridge 是另一路径，不能把完整 market 的资金需求套用给所有买卖。
- 该初始检查阶段没有签名或广播公共链交易；ETH fork 明确使用模拟资金、角色代码注入和时间推进。当前源码与历史部署代码的全面等价核验不在该只读快照的结论内。

## 公共测试网实际交易

用户为新钱包 `0xCeCAd0eBB0CAb4fbB2fe6213E3cd6dE82e4D164B` 提供 0.01 test ETH 后，在 chain **46630** 使用既有 fee factory 完成 **19 笔成功交易、0 笔失败交易**。每笔广播前 eth_call 与 gas estimate，广播后核对回执、calldata、sender、nonce、chain ID 和 canonical block hash；最后复核高度 **128000111**。所有交易的 native value 都为 0，实际 gas 总消耗 **0.00013704238 test ETH**。

流程：项目公开 `drip()` 各领一次（10,000 tUSDG、15 test TSLA）→配置曲线→支付 25 tUSDG 发币费→100 tUSDG 曲线买入→卖出 100 万 FUN→9,000 tUSDG 买入并自动毕业→100 tUSDG V4 买入→卖出 100 万 FUN→向既定 creator、treasury、protocol 领取曲线费用→sweep V4 股票费用。交易授权为有限额度，五笔买卖均验证了实际资产变动；最低 stock 到账及最低最终输出采用即时模拟报价的 99%，毕业明确允许返还剩余 stock。

| 项目 | 已验证值 |
|---|---|
| 策略 | HFFRESH，ID **4**，stage **2 / Graduated** |
| Token | `0x53264d48f3d495a932d366eaae6978c0c31bd6b2` |
| Curve | `0xb29cbaf52c4711544a9f3cfbc7a3f78e86495779` |
| Treasury | `0x6a214957209da33bfa35e39e57c357967d59d5ae` |
| 发币 | [成功回执](https://explorer.testnet.chain.robinhood.com/tx/0x97fd898b1ef1f45733e13af2af43e7ab1158ad8217e1b2a32aab0dc31ea676d6) |
| 自动毕业 | [成功回执](https://explorer.testnet.chain.robinhood.com/tx/0xa4cc4fe3e5026f8ff160e34774f548438842a13d8ba076577cbe44d605cfb49b) |
| V4 买入 / 卖出 | [买入](https://explorer.testnet.chain.robinhood.com/tx/0x889adc3d5a3a52491092adee7fb8a27d7ccbf7f69ef2178893ca24ff1917fcfd)、[卖出](https://explorer.testnet.chain.robinhood.com/tx/0xbeccf594685cecd3de7471fb3366e2759aa0b40283d042bcc8616d733bacdf45) |

最终状态快照高度 **127999417**：钱包余 **0.00986295762 test ETH、814.864782 tUSDG、18.902483872563661996 test TSLA**。router 的三种币余额及该钱包给予 router 的三种币授权均为 0，发币费授权也已耗尽。Treasury 实持 **10.864489120365632062 test TSLA**，周末日历关闭，因此全部属于 unbookedStock，bookedStock=0；这不是毕业失败。毕业事件记录了约 10.4107142857 TSLA 注入 LP、等额股票转入 treasury，另有后续费用到账。

曲线的三方费用已全部到账，curve.totalFees=0；V4 股票费用已分配，三方 owed=0。V4 买入产生的 **95,960.4325476000832563 FUN** 费用已转为 pendingTokenFees，转换需要 factory owner，本专用普通钱包没有执行该 owner 操作。通过只读 eth_call 验证了旧 Active 报价以 `StageChanged(2)` 拒绝、过期报价以 `Expired()` 拒绝，未发送失败交易。

完整证据位于仓库根目录 `artifacts/testnet-live-2026-10-03/`：`journey.json` 保存每笔完整签名请求（不含密钥）、交易和回执；`integration.json` 提供前端地址、PoolKey/PoolId、交易链接、毕业事件、最终余额和边界说明；`verify_journey.py` 仅执行只读复核。钱包密钥仅在 `.local/` 加密保存，未写入证据或 Git。

这次实网结论覆盖现有 fee core 的 **stock/tUSDG** 路径；不能据此宣称 Native ETH bridge、新 creator core 或 mainsite UI 已部署完成。

## PR #19 独立复核

审查 `codex/v2-fuzz-three-gaps` 的确切 head `cff3001b825614c39634c80d4c5cdbd5e59208fe`，未发现阻止合并的问题。隔离工作区重放三个固定种子：**99 次测试结果通过、76,800 fuzz 样本、393,216 handler 调用、0 失败、0 跳过**；相同 head 的 CI 全部成功。证据：`artifacts/pr19-review/review-summary.json` 及 `supplement/`。已尝试按用户授权合并，GitHub connector 返回 403 `Resource not accessible by integration`，该次合并未执行。后续已于 2026-10-03 成功合并，见本文开头更新；精确 reviewed head 未变。

## 联调交接与剩余工作

已通知“克隆 hegefun_mainsite 仓库”会话（`01a10046-b11a-71f3-a621-7389cde2bd07`）。早期有一次发送被应用权限拒绝，后续已成功补发周末入账、第 9 市场修复和新钱包 fork 完整流程；不能继续把那次拒绝视为最终交接状态。前端是否已拉取和接入应以该会话实际进度为准。

股票/tUSDG 的公共测试网新钱包闭环现已完成，可使用上述 ID 4 和 `integration.json` 验证前端读取与毕业效果；本记录不代替浏览器 UI 验收。若采用 ETH 发射，先实际发布并核验 native venue/bridge 与 launch router 的交易回执、授权和 defaults，再开放前端入口。毕业应以成功回执后的链上 stage=2 为准；Ready 通常是末笔 buy 内的瞬时状态，毕业失败整笔买入回滚。

未运行的 opt-in 范围包括主网 archive fork、历史 keeper/all-in 发布阶段重演和预算历史模拟；全量日志中的 SKIP 不计入通过。对当前测试网完成了前述 6 个 fork 用例，不能将它们扩展为所有历史 fork 均已验证。

## 复现

在仓库根目录，准备 Foundry v1.5.0 和 Python >=3.11 后：

```sh
git submodule update --init --recursive
FORGE_BIN="$PWD/.local/bin/forge" PYTHON_BIN=python3 bash contractV2/tools/run_fuzz_campaign.sh
```

runner 默认 fuzz 4096 次、seed `0x20261004`、invariant runs=512/depth=500，并保存工具版本、提交、工作区 diff 和依赖 pin。默认不启用 RPC fork；日志明确保留跳过项。

```sh
cd contractV2
python3 tools/read_testnet_readiness.py --cast ../.local/bin/cast --output ../artifacts/testnet-readiness.json
RUN_TESTNET_FORK=true ../.local/bin/forge test --mc '^TestnetV2JourneyForkTest$' -vv
CREATOR_CORE_FORK=true CREATOR_CORE_FORK_BLOCK=127978606 \
  GIT_COMMIT=179c4a4c510d0f627440b41f644743d942c4e5c6 \
  ../.local/bin/forge test --mc '^TestnetV2CreatorCoreTest$' -vv
```

公共 RPC 可能修剪历史状态；重放时若旧区块已不可用，应重新固定一个可用区块并在结果中记录。`GIT_COMMIT` 标记上游基线；本报告明确包含工作区修复，不能用其声称未经修改的基线已经通过该修复回归。

ETH fork 另按 CI 的 `native-launch-testnet-fork` 步骤准备 byte-exact fee book 和 `tools/native_testnet_config.py` 的窄文件权限配置，设置 `V2_NATIVE_LAUNCH_FORK`、`ETH_MARKET_FORK`、`ETH_BRIDGE_FORK` 及三者相同的当前区块环境变量；不得把 fork 生成文件发布成真实部署证明。

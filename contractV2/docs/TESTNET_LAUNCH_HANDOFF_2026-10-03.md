# 最新 creator 核心：测试网发射交接

2026-10-03 在 Robinhood testnet（chain ID **46630**）实际部署最新 creator 核心，并完成发币、曲线买卖、毕业、V4 买卖、曲线领费、V4 费用转换与最终分配。**40 笔部署交易、19 笔生命周期交易全部成功**。该结论适用于股票/tUSDG 测试网链路；浏览器交互验收由 mainsite 会话继续执行。

分支：`codex/testnet-launch-20261003`。部署源码提交：`d7e686123362f58583b6e0662701e8e19fa0c9c9`。部署后证据、参数和复核工具也保存在同一分支。`src/` 和编译设置未因本次部署修改。

## 前端采用的文件与地址

- [已验证地址簿](../deploy/testnet-v2-fresh-creator.json)：`broadcast=true`，包含部署交易、代码 hash、已注册 kinds 和原股票市场。
- [最新参数测试 profile](../deploy/fresh-creator-launch-profile.json)：本次成功发射值及参数边界。
- [完整接入数据](../deploy/verification-2026-10-03/fresh-creator/integration.json)：PoolKey、PoolId、交易链接、毕业事件、费用和最终余额。
- ABI 使用本分支 `abi/` 中的 `HedgeFunV2Factory`、`CurveDeployer`、`HedgeFunV2TradeRouter`、`HedgeFunBondingCurve`、`HedgeFunV2Treasury`、`HedgeFunToken`、`HedgeFunV2Hook`、`V2TreasuryDeployer`。
- RPC：`https://rpc.testnet.chain.robinhood.com`；浏览器：`https://explorer.testnet.chain.robinhood.com`。

| 合约 | 地址 |
|---|---|
| Factory | `0x2363B102D37BBa1dc3f9aBEdC4d6121C8e90B9cF` |
| Trade router | `0xEF6bE3C3A19F33C0F62beC3FFb37228e8c47F755` |
| CurveDeployer / creator 配置 registry | `0x2a5aA00A1B9Dd43536cB645335704dD45e3da1A8` |
| V4 Hook | `0x99c9b8A1B890727ba85D954909646278591a2844` |
| Treasury deployer | `0x0548fF6a60C690804e1179B0EC6FC59c353ce1A7` |
| PoolManager | `0x8366a39CC670B4001A1121B8F6A443A643e40951` |
| tUSDG（6 位） | `0x539574139BB4Ac74Fd2183ba0E38ed777E30B58d` |
| TSLA（18 位） | `0xcee322837F181Bd93AC2d71e4dDf334BFF565b98` |
| TSLA/tUSDG V3，fee=3000 | `0x04083643FF9E8c27f66C9dD99947743A9B777244` |

新 Factory owner 为专用测试钱包 `0xCeCAd0eBB0CAb4fbB2fe6213E3cd6dE82e4D164B`。协议收款及既有股票市场 owner 仍是 `0x75Cee941B0eF3A83feA0397BbF903C12c1D7e96D`。本次仅部署新核心，复用原八个股票市场。策略身份必须使用 **chainId + factory + strategyId**；新核心 ID 0 与旧 Factory ID 0 是不同资产。

地址簿的 `nativeRouter` 是交易包装器。此次未部署可用的 Native ETH 发射入口或建立 WETH/tUSDG bridge；前端继续关闭 Native ETH 发射/交易入口。当前部署注册 kind **0 AllIn、1 Buyback、2 SpotEngine schema 1**；NAV 百分比 Engine 未在该 registry 注册，不能因 ABI 或 fuzz 测试存在就开放选项。

## 最新参数与创建流程

本次真实发射为 kind 0：TP1 **1 bps**、TP2 **2 bps**、dip **1 bps**、stop **1 bps**、lot **2000 bps**、band **0**；`saleBps=4000`（40%）、`snipeSeconds=180`。基础税 `taxBps=300`；creator 占已收税费的 `creatorBps=1000`，不是在交易额上再加 10%。1 bps=0.01%；这组值用于验证参数下界，不是收益或策略建议。

kind 0 creator 参数格式允许：TP1 1..uint32.max；TP2 为 0 或严格大于 TP1；dip 1..9999；stop 0..9999（0 关闭 stop）；saleBps 1000..9000；snipeSeconds 0..180。其他 kind 按自己的 schema 验证。费用、税率限额、listing 和其 gates 必须实时读取，不能全用本次快照常量。1 bps 下界不会绕过 oracle/calendar、最小交易额、滑点、keeper 奖励和 cooldown 检查。

1. 检查 chain ID、钱包账户与余额；读取 `getDefaults()`、`listings(stock)`、`listingGates(stock)`、kind manifest。当前 `publicLaunch=true`，发射费 **25 tUSDG**，native value=0。
2. 由创建者设置 `setCurveConfig(symbol, nonce, saleBps, snipeSeconds)`。如需要，随后设置 `setOpeningTaxExemptions`；creator 自动豁免，不占最多 32 个附加名额。这两项都应在最终预测前完成。
3. 用新 symbol/nonce 构造 Request，读取最新 `expectedOpenPriceE18` 和 `maxFee`，调用 `predict()`、`predictCurve()`，保存其 terms 和预计地址。设置后任何影响 terms 的变更都需要重新预测。
4. 精确授权 Factory 25 tUSDG（以实际 defaults 为准），模拟后调用 `launch` 或 `launchWithMetadata`。Metadata 的 `Info` 包含 logo、description、socials（twitter/telegram/discord/website/farcaster）、extraURI；链上保存字符串不等于第三方行情站自动收录 logo。
5. 等待成功回执，使用 `Launched`、`CurveLaunched` 和链上读数取得真实 ID/地址，再进入交易页。切换账户或网络时清空旧授权、terms 和报价缓存。

本次 HFLATEST 已经发射，不能直接复用其 creator/symbol/nonce 再广播。所有金额和 uint 参数使用 BigInt；tUSDG 为 6 位，stock/FUN 为 18 位。`openPriceE18` 按合约单位原样传递，不能把网页显示的美元价格直接代入。

## 买卖、毕业及状态展示

- 调用 Trade router 的 `buy` / `sell`。tUSDG 买入路径为 `[{pool: TSLA_USDG_POOL, tokenOut: TSLA}]`；卖出到 tUSDG 为相反方向。直接用 stock 买入时 path 为空。
- 每次取得当前 stage 和即时模拟报价；买入分别保护 `minStockReceived`、`minFinalOut`，设置 deadline。本次采用即时模拟的 99% 最低到账、300 秒 deadline。前端报价必须由同一支付账户模拟，避免误用 creator 的开盘税豁免。
- 毕业末笔允许 partial fill，并清晰显示 **stock 退款**；即使支付的是 tUSDG，也不会自动把退款换回 tUSDG。卖出 partial fill 可能返还 FUN。最低最终输出始终是绝对值。
- Stage：0 Active，1 Ready，2 Graduated。Ready 通常存在于触发毕业的同一笔交易内。只有成功回执和链上 stage=2 才切换为已毕业；毕业失败会回滚整笔买入。
- Active 进度可读取 `realStockReserve / (terminalStock - virtualStock)`。stage=2 后固定显示 100%，不要因毕业后曲线储备归零把进度显示成 0%。
- 毕业后重新读取 `graduationConfig(id)` 的 PoolKey，以 `keccak256(abi.encode(PoolKey))` 得到 PoolId；PoolId 是 bytes32，不能当作独立池合约地址。曲线与 V4 使用同一个 Trade router。
- 索引 `Bought`、`Sold`、`Graduated`、`GraduationCapitalSplit`；发生 `StageChanged`、`Expired`、`Restated` 或最低到账失败时丢弃旧报价/terms，重新读链，不能自动把保护值改成 0。
- 本次周末日历关闭，treasury 实持股票但 bookedStock=0，全部为 unbookedStock。显示“已毕业，等待复市入账”。这次没有在公共链上证明开市后的 keeper 策略执行。
- 曲线 `claimFees`、V4 `sweep`、owner `convertFees` 是不同环节。此次新核心三方费用均已送达，V4 pendingTokenFees=0；不要套用旧 HFFRESH 仍有 pending token fees 的快照。

## 实际交易与验收证据

HFLATEST / ID **0**：

| 项目 | 地址或回执 |
|---|---|
| Token | `0x4d7e94f18e2d08406a392811a4f79e2945c8555f` |
| Treasury | `0x7d2bf810edc4de1ce6fb7b3e187caf91a7391b24` |
| Curve | `0x870f784aa0f1e1a2154582174ba17b40776aa1c1` |
| 发射 | [成功回执](https://explorer.testnet.chain.robinhood.com/tx/0x5b8ee397d047864d745b344ce859a23e83522159baa2ce161cb23060f12a78d8) |
| 毕业 | [成功回执](https://explorer.testnet.chain.robinhood.com/tx/0x362862f93a2162570e257ec50816ab480feb541d3d71b19ebc76ab92f6a1e6e6) |
| V4 买 / 卖 | [买入](https://explorer.testnet.chain.robinhood.com/tx/0xed483ecfddfb916939bf694cd413a963b3adb68ba9df5b9d34b2791788aa63cd)、[卖出](https://explorer.testnet.chain.robinhood.com/tx/0x18153fdffdf8479828294432f201013a416430580d86a08ffe933262ed32f554) |
| 代币费用转换 / 最终分配 | [转换](https://explorer.testnet.chain.robinhood.com/tx/0x23f5d9197e2f1c00fc0b2822fba1e46f35621204fb020e39ead1cd021aa3b361)、[分配](https://explorer.testnet.chain.robinhood.com/tx/0xec6479154a8f8ce4913cd96200a29ab071b6376d71999b5ba0d81ed69fb19878) |

新部署 gas **0.00050608576 test ETH**；19 笔生命周期 gas **0.00014407461 test ETH**。最终快照区块 **128015608**：钱包余 **0.00921279725 test ETH、626.039199 tUSDG、0.995787515688598704 TSLA**。Treasury 实持 **9.224471047528745562 TSLA**，全部 unbooked；router 三种币余额、钱包对 router 三种授权均为 0。每笔实际交易都核对 canonical 回执、sender、chain ID、nonce、calldata、native value=0 和 gas 总和，五次交易核对真实余额差。过期报价和旧 Active 报价拒绝检查采用只读 eth_call。

最新部署回归 **19 通过**，creator 参数套件 **162 通过**（含 4096 次 fuzz），部署后固定区块完整流程 fork **1 通过**，均 0 失败。早期全量 fuzz、PR #19 三 seed 重放及旧核心实网证据参见 [fuzz 报告](FUZZ_TESTNET_2026-10-03.md)。这些是不同批次结果，不应把重复回归相加当成唯一用例数。

前端还需完成浏览器验收：连接/切网、创建及 metadata、精确授权、报价过期和 stage 变化、stock 退款、曲线进度、毕业事件及 PoolId 切换、V4 买卖、关闭 Native 入口、周末入账文案。30 钱包模拟需使用其独立钱包与 nonce；本专用 owner 钱包不进入网页或 30 钱包脚本。

## 本地复核

从仓库根目录使用 Python >=3.11、固定 Foundry v1.5.0 与依赖：

```sh
git submodule update --init --recursive
cd contractV2
../.local/bin/forge build --offline
FRESH_CREATOR_LIFECYCLE_FORK=true FRESH_CREATOR_LIFECYCLE_FORK_BLOCK=128008898 \
  ../.local/bin/forge test --offline --match-path test/TestnetV2FreshCreatorLifecycle.t.sol -vv
python3 tools/verify_testnet_creator_journey.py
```

只读 verifier 会更新本地复核证据；如果后续他人改变链上状态/钱包 nonce，或 RPC 修剪历史区块，严格复核可能失败。应保留归档的历史结果并记录新时点，不覆盖成一个虚假的新通过。`tools/testnet_creator_journey.py` 是这次特定钱包与 nonce 的广播记录器，**不是可随意重跑的通用部署命令**；它仅用 `.local/` 内加密 keystore，密钥和密码不进入 Git。地址簿 `broadcast=false` 的 candidate/dryrun 仅为计划，前端只消费上面的 verified book。

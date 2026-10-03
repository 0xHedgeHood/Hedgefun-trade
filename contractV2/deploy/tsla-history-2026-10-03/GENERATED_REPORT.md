# TSLA 历史收盘价回放：自动生成结果

12 项合约测试通过；1003 个历史日线输入 × 3 个模式，共 3009 个日末快照。全部为本地 fork 调用，没有广播。

下表从每年首个观测收盘价到最后一个观测收盘价计算，每年、每个模式独立重置。不是标准年度收益，也不是连续四年的组合收益。美元计价采用历史 Close 预言机输入；国库 NAV 扣除了回购资金流出，不能当作投资者总回报。

| 年份 | 模式 | 首末日期 | TSLA 变化 | FUN 美元计价变化 | FUN/TSLA 变化 | 国库 NAV 变化 | NAV 最大日末回撤 | 执行/回购次数 | 烧毁 FUN |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| 2022 | 被动持有 | 2022-01-03 → 2022-12-30 | -69.1994% | -69.1994% | 0% | -69.1994% | 72.72% | 0/0 | 0 |
| 2022 | 500 bps 策略 | 2022-01-03 → 2022-12-30 | -69.1994% | -58.9638% | 33.2317% | -23.4339% | 24.1778% | 33/11 | 15957141.35 |
| 2022 | 1 bps 策略 | 2022-01-03 → 2022-12-30 | -69.1994% | -59.2172% | 32.4089% | -27.7833% | 27.7833% | 81/24 | 15636243.05 |
| 2023 | 被动持有 | 2023-01-03 → 2023-12-29 | 129.8612% | 129.8612% | 0% | 129.8612% | 32.7197% | 0/0 | 0 |
| 2023 | 500 bps 策略 | 2023-01-03 → 2023-12-29 | 129.8612% | 164.4042% | 15.0278% | -0.3008% | 3.538% | 2/6 | 8072360.72 |
| 2023 | 1 bps 策略 | 2023-01-03 → 2023-12-29 | 129.8612% | 145.8764% | 6.9673% | -0.3008% | 3.5605% | 2/3 | 3954021.48 |
| 2024 | 被动持有 | 2024-01-02 → 2024-12-31 | 62.5634% | 62.5634% | 0% | 62.5634% | 42.8186% | 0/0 | 0 |
| 2024 | 500 bps 策略 | 2024-01-02 → 2024-12-31 | 62.5634% | 93.2382% | 18.8694% | -11.4083% | 11.4083% | 20/8 | 9886106.68 |
| 2024 | 1 bps 策略 | 2024-01-02 → 2024-12-31 | 62.5634% | 71.9754% | 5.7897% | -15.3504% | 15.3504% | 40/9 | 3313261.16 |
| 2025 | 被动持有 | 2025-01-02 → 2025-12-31 | 18.572% | 18.572% | 0% | 18.572% | 48.1902% | 0/0 | 0 |
| 2025 | 500 bps 策略 | 2025-01-02 → 2025-12-31 | 18.572% | 42.0004% | 19.7587% | -8.6359% | 14.1445% | 10/7 | 10293479.92 |
| 2025 | 1 bps 策略 | 2025-01-02 → 2025-12-31 | 18.572% | 59.3247% | 34.3696% | -9.5631% | 14.9961% | 37/18 | 16396052.67 |

## 回购支出单列

回购实际花费 TSLA，以下美元数仅为各执行日 Close 标价之和；没有将其加回 NAV 宣称总回报。

| 年份 | 模式 | 最后国库 NAV（美元标价） | 累计回购 TSLA | 回购按执行日 Close 标价合计 | Stop / TP / Dip 次数 |
|---|---|---:|---:|---:|---:|
| 2022 | 被动持有 | 1088.09 | 0 | 0 | 0 / 0 / 0 |
| 2022 | 500 bps 策略 | 2704.8413 | 1.36673413 | 299.165856 | 11 / 9 / 13 |
| 2022 | 1 bps 策略 | 2551.1885 | 1.33510735 | 296.514594 | 25 / 23 / 33 |
| 2023 | 被动持有 | 2194.9066 | 0 | 0 | 0 / 0 / 0 |
| 2023 | 500 bps 策略 | 952.0109 | 0.64243179 | 75.758978 | 0 / 2 / 0 |
| 2023 | 1 bps 策略 | 952.0107 | 0.30345177 | 34.019517 | 0 / 2 / 0 |
| 2024 | 被动持有 | 3567.2533 | 0 | 0 | 0 / 0 / 0 |
| 2024 | 500 bps 策略 | 1944.0359 | 0.79980762 | 141.246063 | 6 / 7 / 7 |
| 2024 | 1 bps 策略 | 1857.5304 | 0.25287303 | 49.738445 | 15 / 9 / 16 |
| 2025 | 被动持有 | 3972.5267 | 0 | 0 | 0 / 0 / 0 |
| 2025 | 500 bps 策略 | 3060.9784 | 0.83587431 | 348.42367 | 4 / 2 / 4 |
| 2025 | 1 bps 策略 | 3029.9127 | 1.41031101 | 472.895195 | 9 / 14 / 14 |

## 实验边界

- Each year starts with a fresh launch and graduation at that year's first observed Close; all three modes reset independently. These changes are not standard calendar-year returns and cannot be chained into a four-year portfolio return.
- Historical inputs are saved Yahoo TSLA Close values already adjusted for the 2022 split. Close equals Adj Close for every saved bar. No second share/price split adjustment is applied.
- Yahoo daily timestamps label New York session open, not Close availability. Only session dates determine synthetic calendar-day gaps; real close-release times, DST, early closes and intraday moves are not simulated.
- Current deployed creator contracts execute in a local fork of chain 46630. This is a historical-price replay of current contracts, not execution on the historical chain or a forecast.
- The market calendar is mocked open. Each new Close is applied to V3 and the feed, then held for a synthetic 601 seconds before the daily observation and keeper opportunity; no public time or public prices are changed.
- The local fork replaces the existing narrow TSLA V3 position with a full-range position holding exactly the same active liquidity. Prices are not rescaled. This fixes today's testnet depth across historical dates, not historical liquidity.
- An impersonated local test-market operator can mint test assets needed for price moves. External arbitrage/price-setting P&L and acquisition costs are excluded.
- Passive mode performs no keeper calls. Keeper modes attempt exactly one execute and one buyback after each noninitial Close; no user order flow is added. One daily opportunity cannot reproduce a 1 bps intraday strategy.
- Default dollar marks use the historical Close feed input. Actual post-action V3 spot marks are exported separately; both are valuations, not executable liquidation quotes or redeemable NAV.
- Treasury NAV includes remaining stock and cash and falls when stock is spent on FUN buybacks. Buyback stock spend and its value at each execution's Close are reported separately; neither treasury NAV nor NAV plus those marks is investor total return.
- The FUN holder never trades. Token units, LP principal, treasury assets, and keeper rewards are distinct accounts; they are not added into a fabricated investor portfolio.
- Estimated action gas is gasleft-based test-call usage including harness control and excluding transaction intrinsic gas. No native-gas price or actual gas cost is deducted; all calls are local and unsigned.

## 核验

- 固定 fork 区块：128172359。
- 实测 V3 spot 与 Close 预言机最大绝对偏差：0.481307616338 bps。
- 日末最大回撤按每组历史峰值到后续谷值计算；不代表盘中最大回撤。
- 详细数据：`results.json`、`daily.csv`、`summary.csv`。图：`charts/treasury-nav-by-year.{png,svg}`、`charts/fun-oracle-mark-by-year.{png,svg}`、`charts/annual-window-summary.{png,svg}`。
- `estimatedActionGas` 仅为测试调用 gas 测量，包含 harness 控制开销、不含交易基础 gas；未计入真实交易成本。
- historySourceUrl: `https://query1.finance.yahoo.com/v8/finance/chart/TSLA?period1=1640995200&period2=1767225600&interval=1d&events=div%2Csplits&includeAdjustedClose=true`
- historySourcePage: `https://finance.yahoo.com/quote/TSLA/history/`
- historySourceSha256: `4dc21458ad5d6dba9b66b214a4da65cdd082b645f933831df87b4f4e7b3e4d77`
- normalizedHistorySha256: `a4b8f96e7f22d253a20787764de98ff98810ca4a3add10d2e2583cb81b68e7e1`
- harnessSha256: `a44b5a2e16ecf4badd296a5085becc559d104f2761f7e19092f2e2476bf8c475`
- forgeLogSha256: `d74e31b65c41ee20aeea3d91fa5c2fd7a7afedcb04b7df134bc8164c629660aa`
- reportToolSha256: `23a7c44202105b60a88b7eb915142eba79be752075d8bd3d90dfbcb426376d5b`

# 2025 多股票参数与 LP 手续费回放

39 个隔离实验、9750 个日末快照，全部真实合约本地调用；没有公开广播。

初始国库股票与 LP 股票本金各约 10,000 tUSDG。比较从 2025 首个 Close 到最后一个 Close；分红未派发，所有参数均为样本内比较。

| 股票 | 参数 | LP harvest | 股票价格变化 | FUN 美元标价变化 | 国库 NAV 变化 | 外部资产变化 | 外部资产最大日末回撤 | execute / buyback | 外部交易者现金变化 |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| TSLA | 无流量基线 | off | 18.572% | 18.572% | 18.572% | 18.572% | 48.19% | 0 / 0 | 0 |
| TSLA | 仅费用 | off | 18.572% | 20.507% | 31.897% | 26.205% | 47.638% | 0 / 0 | -1743.21082 |
| TSLA | 仅费用 | on | 18.572% | 22.485% | 31.898% | 26.205% | 47.638% | 0 / 249 | -1743.24396 |
| TSLA | 1% | off | 18.572% | 36.684% | 8.444% | 18.376% | 27.417% | 39 / 14 | -1743.704302 |
| TSLA | 1% | on | 18.572% | 38.842% | 8.445% | 18.376% | 27.417% | 39 / 249 | -1743.734945 |
| TSLA | 3% | off | 18.572% | 40.246% | 11.183% | 20.572% | 26.265% | 34 / 14 | -1743.798593 |
| TSLA | 3% | on | 18.572% | 42.442% | 11.183% | 20.572% | 26.265% | 34 / 249 | -1743.828754 |
| TSLA | 5% | off | 18.572% | 44.535% | 9.414% | 20.669% | 26.038% | 118 / 75 | -1743.887043 |
| TSLA | 5% | on | 18.572% | 46.778% | 9.415% | 20.669% | 26.038% | 118 / 249 | -1743.916681 |
| TSLA | 10% | off | 18.572% | 50.676% | 1.298% | 17.991% | 31.024% | 78 / 54 | -1743.871515 |
| TSLA | 10% | on | 18.572% | 52.985% | 1.298% | 17.991% | 31.024% | 78 / 249 | -1743.900541 |
| TSLA | 5% 无止损 | off | 18.572% | 112.953% | 8.831% | 34.415% | 39.184% | 192 / 176 | -1744.340102 |
| TSLA | 5% 无止损 | on | 18.572% | 115.9% | 8.831% | 34.415% | 39.184% | 192 / 249 | -1744.366202 |
| NVDA | 无流量基线 | off | 34.842% | 34.842% | 34.842% | 34.842% | 36.887% | 0 / 0 | 0 |
| NVDA | 仅费用 | off | 34.842% | 36.71% | 47.701% | 42.208% | 35.876% | 0 / 0 | -1628.178946 |
| NVDA | 仅费用 | on | 34.842% | 38.614% | 47.701% | 42.208% | 35.876% | 0 / 249 | -1628.203045 |
| NVDA | 1% | off | 34.842% | 58.792% | 7.776% | 27.539% | 19.912% | 55 / 29 | -1628.622462 |
| NVDA | 1% | on | 34.842% | 60.913% | 7.776% | 27.539% | 19.912% | 55 / 249 | -1628.644538 |
| NVDA | 3% | off | 34.842% | 48.913% | 8.082% | 25.372% | 20.858% | 49 / 23 | -1628.45472 |
| NVDA | 3% | on | 34.842% | 50.938% | 8.082% | 25.373% | 20.858% | 49 / 249 | -1628.477664 |
| NVDA | 5% | off | 34.842% | 46.076% | -0.562% | 20.37% | 25.245% | 121 / 77 | -1628.309408 |
| NVDA | 5% | on | 34.842% | 48.073% | -0.562% | 20.37% | 25.245% | 121 / 249 | -1628.332899 |
| NVDA | 10% | off | 34.842% | 38.515% | -4.127% | 16.742% | 27.538% | 81 / 59 | -1628.200041 |
| NVDA | 10% | on | 34.842% | 40.438% | -4.127% | 16.742% | 27.538% | 81 / 249 | -1628.223997 |
| NVDA | 5% 无止损 | off | 34.842% | 116.25% | 10.456% | 41.133% | 33.943% | 189 / 174 | -1628.760335 |
| NVDA | 5% 无止损 | on | 34.842% | 118.911% | 10.456% | 41.133% | 33.943% | 189 / 249 | -1628.780535 |
| META | 无流量基线 | off | 10.155% | 10.155% | 10.155% | 10.155% | 34.209% | 0 / 0 | 0 |
| META | 仅费用 | off | 10.155% | 11.626% | 20.284% | 15.957% | 33.53% | 0 / 0 | -1744.698434 |
| META | 仅费用 | on | 10.155% | 13.125% | 20.285% | 15.957% | 33.53% | 0 / 249 | -1744.720742 |
| META | 1% | off | 10.155% | 28.536% | 6.652% | 13.205% | 18.54% | 131 / 80 | -1745.056274 |
| META | 1% | on | 10.155% | 30.198% | 6.652% | 13.205% | 18.54% | 131 / 249 | -1745.076967 |
| META | 3% | off | 10.155% | 30.81% | 8.427% | 14.618% | 18.543% | 112 / 69 | -1745.14054 |
| META | 3% | on | 10.155% | 32.493% | 8.427% | 14.618% | 18.543% | 112 / 249 | -1745.160946 |
| META | 5% | off | 10.155% | 35.209% | 9.442% | 16.129% | 18.828% | 87 / 51 | -1745.265668 |
| META | 5% | on | 10.155% | 36.933% | 9.442% | 16.129% | 18.828% | 87 / 249 | -1745.285679 |
| META | 10% | off | 10.155% | 45.055% | 6.595% | 16.895% | 20.302% | 44 / 25 | -1745.439783 |
| META | 10% | on | 10.155% | 46.871% | 6.595% | 16.895% | 20.302% | 44 / 249 | -1745.458968 |
| META | 5% 无止损 | off | 10.155% | 68.283% | 6.769% | 21.869% | 25.34% | 113 / 101 | -1745.625947 |
| META | 5% 无止损 | on | 10.155% | 70.312% | 6.77% | 21.869% | 25.34% | 113 / 249 | -1745.643964 |

## 解释边界

- 2025-01-02 Close to 2025-12-31 Close only; not a standard year-on-year return or a continuous multi-year portfolio. Universe selection is descriptive in-sample volume/volatility screening, not prospective selection or out-of-sample validation.
- All eight enabled deployed equities are screened using median Close×Volume >= $2bn and sample daily-log-return annualized volatility >=30%; the three highest qualifying volatilities are selected without ranking returns. Equity turnover is not onchain depth.
- Close is already split-adjusted. NVDA and META pay dividends, but this price-only stock-token model does not distribute dividends. Adj Close is retained as source data, not used as an executable/feed price.
- Provider timestamps label session open, not Close availability. Daily prices are idealized known Close inputs applied to a synthetic open market; 601-second settling is not real historical execution latency or intraday trading.
- Current pinned deployed contracts run only in an ephemeral fork. Local listing price is normalized to approximately $10,000 treasury stock and $10,000 locked LP-stock principal, with the same initial configuration for matched harvest pairs.
- Current narrow V3 positions are locally widened to full range at exactly the original active liquidity. Actual fork swaps probe $100/$1k/$2k/$10k in both directions at each ticker's first 2025 Close. The separate current-state analytical estimates are not actual quotes and use a later block and different price.
- All nonbaseline cases use the same once-prefunded $100,000 external trader and 249 daily round trips: buy $100 tUSDG of FUN and sell exactly the received net FUN. No artificial tax or fee credit is injected; trader cost is reported separately.
- Fee-only controls disable strategy execute but retain the matched flow, hook-fee settlement/conversion, and harvest on/off policy. They separate strategy effects from externally funded order flow; the zero-flow baseline alone cannot establish strategy alpha.
- Both harvest arms settle hook fees daily: sweep, owner conversion with a 99% snapshot-quote floor, 300-second deadline and 50bps sqrt-price limit, then sweep again. Partial conversion may remain pending. Initial launch curve fees remain unclaimed and outside the graduated-period reader assets/action budget.
- LP harvest reallocates already-owned stock fees and burns collected FUN fees. External-trader-generated, hook-conversion-generated and own-buyback-generated LP fees must be distinguished; recycling one's own fees is not new external income.
- External asset value counts treasury stock/cash plus locked LP stock and claimable LP-stock fees, excluding self-issued FUN. Treasury NAV, FUN marginal marks, reader external assets and the external trader are separate accounts; none is a fabricated total-investor return or liquidation guarantee.
- Oracle Close marks and actual post-action V3 spot marks are separate. Buyback spending is reported as stock and its mark at each execution; adding it back does not create a validated investor total return.
- Each daily noninitial point allows at most one strategy execution and one buyback. No intraday trigger replay, depth history, real fee-volume history, dividend cash, MEV or real gas bill is modeled. Gas counters are local call estimates, including harness control and excluding transaction intrinsic gas.
- The complete frozen parameter matrix is shown. Any strongest endpoint is in-sample scenario output, not an optimal parameter recommendation or evidence of future performance.

## 归档

- results.json：配置、39 组汇总、on/off 与同流量控制组差值。
- daily.jsonl.gz：全部原始字段及派生计价，整数为十进制字符串；daily.csv 为表格形式。
- forge.log.gz：原始 Foundry 输出，gzip mtime=0；SHA256SUMS 覆盖归档文件。
- fork 区块 128172359；另行当前深度筛选区块 128288416。实际 fork probes 与当前状态解析估计分别存储。

# 2025 Options / Earn / Wheel 本地回放

27 项测试通过：20 个年度情景、5000 个日末记录、6 项异常路径、1 项真实现金质押桥接。

以下只比较期末全部期权已结算的资产。日内/未到期记录是 gross assets，不是净 NAV。保费是假设报价，股息与 gas 未计。

| 股票 | 情景 | 期末资产变化 | 持股价格变化 | 已收保费 USDG | 已结算期权腿 PnL USDG | 复投执行成本 USDG | call / put |
|---|---|---:|---:|---:|---:|---:|---:|
| META | netshare | 11.6791954512% | 10.1545353485% | 2507.431653 | 273.162907 | 0 | 52 / 0 |
| META | no_exercise | 38.9240665339% | 10.1545353485% | 2876.953118 | 2876.953118 | 0 | 52 / 0 |
| META | no_fill | 10.154535351% | 10.1545353485% | 0 | 0 | 0 | 52 / 0 |
| META | physical | 8.9668334909% | 10.1545353485% | 159.607527 | 91.496766 | 0 | 3 / 0 |
| META | premium_reinvest | 9.0498213909% | 10.1545353485% | 160.690174 | 91.118104 | 0.481303 | 4 / 0 |
| META | wheel | -3.1053563103% | 10.1545353485% | 2610.869161 | 325.937988 | 0 | 15 / 37 |
| NVDA | netshare | 9.3447199909% | 34.8420239246% | 2156.899323 | -1200.312921 | 0 | 52 / 0 |
| NVDA | no_exercise | 63.4137882063% | 34.8420239246% | 2857.176427 | 2857.176427 | 0 | 52 / 0 |
| NVDA | no_fill | 34.8420239335% | 34.8420239246% | 0 | 0 | 0 | 52 / 0 |
| NVDA | physical | -6.3378926806% | 34.8420239246% | 250.926064 | -21.396718 | 0 | 5 / 0 |
| NVDA | premium_reinvest | -6.4105092506% | 34.8420239246% | 253.708275 | -25.358267 | 0.760469 | 6 / 0 |
| NVDA | wheel | -2.3256075902% | 34.8420239246% | 2396.17129 | -325.514333 | 0 | 17 / 35 |
| TSLA | netshare | -11.1172802111% | 18.5720319201% | 1852.53198 | -1892.156414 | 0 | 52 / 0 |
| TSLA | no_exercise | 42.7682848443% | 18.5720319201% | 2419.625291 | 2419.625291 | 0 | 52 / 0 |
| TSLA | no_fill | 18.5720319319% | 18.5720319201% | 0 | 0 | 0 | 52 / 0 |
| TSLA | physical | 10.371499531% | 18.5720319201% | 109.155031 | -207.840592 | 0 | 2 / 0 |
| TSLA | premium_reinvest | 10.215359151% | 18.5720319201% | 111.509301 | -207.523986 | 0.331907 | 12 / 0 |
| TSLA | wheel | -12.8967651313% | 18.5720319201% | 2316.809265 | -876.668929 | 0 | 14 / 38 |
| TSLA | wheel_premium100 | 14.1711131114% | 18.5720319201% | 5281.630039 | 1767.163207 | 0 | 14 / 38 |
| TSLA | wheel_premium25 | -23.9056229424% | 18.5720319201% | 1099.444082 | -1949.629104 | 0 | 14 / 38 |

## 范围与计价

- Source-only contracts instantiated locally, with historical stock Close inputs; not deployed-chain execution or historical options quotations. Synthetic open calendar and oracle rounds do not reproduce exchange expiry timestamps or intraday observations.
- RFQs use a specified allowlisted buyer: writer/owner offer and buyer fill perform actual collateral and cash transfers. This desk has no EIP-712 signed-RFQ verification.
- The frozen 2025 universe was selected in-sample by equity volume/volatility, not prospectively or by strategy returns. Stock Close excludes dividends; NVDA/META dividends are not distributed in this model.
- Call strikes are 105% and put strikes 95% of start Close. Synthetic premium is 50 bps of spot notional per seven actual calendar days, with 25-bps floor; TSLA wheel also shows 25/100-bps sensitivity. No historical IV, auction, RFQ spread or executable quote is claimed.
- Each RFQ spans at most five trading observations and no more than nine calendar days. The 2025 market calendar includes holiday intervals longer than the Earn nine-day tenor; shortening is calendar-only. Minimum modeled RFQ and reinvestment are $1 to avoid meaningless dust trades; failed probes are retained separately.
- Only initial funding is minted: writer $10k stock, option counterparty $100k cash plus $100k stock, finite reinvestment pool $100k stock. There is no mid-year top-up. Counterparty initial inventory and terminal mark are separate from the writer return.
- Daily records are gross assets including escrow and an intrinsic-value LOWER BOUND of open option liabilities. Neither gross assets nor gross minus intrinsic is fair net NAV. Returns use the terminal fully settled position; drawdown is only at settled checkpoints, not an all-day option NAV drawdown.
- Per-option settled PnL is premium minus the actual ITM delivery value at settlement (desk fees are zero). Principal strike payments are not premium income. Stock holding mark changes, cash inventory and reinvestment cost remain separate; positive premium alone is not distributable profit.
- NetShare uses the legacy CoveredCallDesk directly. Earn only permits physical calls; its intended PhysicalCallDesk and CashSecuredPutDesk are separate implementations. The same share supply cycles cash and stock without double-counting collateral.
- Physical-only retains strike cash after assignment and does not buy back stock; wheel uses free cash to collateralize puts. Premium-reinvest converts only received premium after settlement using the actual EarnVaultV3SwapAdapter against a finite flat-price MockPool charging 30bps; this is not V3 historical execution depth or a FUN/V4 LP.
- No-fill cancels actual unfilled offers and earns zero premium; no-exercise deliberately makes the buyer lapse ITM rights, so it is a risk-behavior control rather than a rational-option-pricing return forecast.
- Earn shares are not FUN dividends. The separate staking demonstration redeems actual cash from one OTM Earn epoch with unchanged stock price, funds experimental FunStakingIncome and claims after seven days. Its test FUN is isolated and is not the deployed FUN/V4 market.
- No gas bill, taxes, financing, issuer dividends, exchange option quotes or market impact beyond the stated mock swap fee is modeled. No APY or universally optimal strategy is inferred.

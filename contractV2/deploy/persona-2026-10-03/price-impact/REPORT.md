# TSLA price-path fork experiment

Verified 6 tests and 116 snapshots at testnet block 128172359.
No public transactions. Prices below are scenario inputs; all FUN/TSLA changes come from actual contract executions.
The table uses oracle/scenario-input dollar marks. Actual post-action V3 spot dollar marks are exported separately in JSON/CSV.
The maximum observed absolute V3 spot deviation from the oracle input is 0.435955662935 bps.

| Stage | Mode | Path | TSLA % | FUN/TSLA % | FUN/USDG oracle mark % | Treasury NAV oracle mark final | Keeper actions/buybacks |
|---|---|---|---:|---:|---:|---:|---:|
| Active | passive | crash50_recover | 0 | 0 | 0 | 0 | 0/0 |
| Active | passive | down20 | -20 | 0 | -20 | 0 | 0/0 |
| Active | passive | flat_one_bps_noise | 0 | 0 | 0 | 0 | 0/0 |
| Active | passive | up20 | 20 | 0 | 20 | 0 | 0/0 |
| Active | scripted_flow | crash50_recover | 0 | 3.03092431 | 3.03092431 | 0 | 0/0 |
| Active | scripted_flow | down20 | -20 | -3.14295935 | -22.51436748 | 0 | 0/0 |
| Active | scripted_flow | flat_one_bps_noise | 0 | 1.87790984 | 1.87790984 | 0 | 0/0 |
| Active | scripted_flow | up20 | 20 | 4.84981782 | 25.81978138 | 0 | 0/0 |
| Graduated | keeper | crash50_recover | 0 | 0 | 0 | 1568.506859 | 1/0 |
| Graduated | keeper | down20 | -20 | 0 | -20 | 2923.082948458195252388 | 3/0 |
| Graduated | keeper | flat_one_bps_noise | 0 | 0.0099194 | 0.0099194 | 3144.758892 | 2/1 |
| Graduated | keeper | up20 | 20 | 9.32613814 | 31.19136577 | 3240.797678097646595489 | 2/3 |
| Graduated | passive | crash50_recover | 0 | 0 | 0 | 3162.33333333333333393 | 0/0 |
| Graduated | passive | down20 | -20 | 0 | -20 | 2529.866666666666667144 | 0/0 |
| Graduated | passive | flat_one_bps_noise | 0 | 0 | 0 | 3162.33333333333333393 | 0/0 |
| Graduated | passive | up20 | 20 | 0 | 20 | 3794.800000000000000716 | 0/0 |
| Graduated | scripted_flow | crash50_recover | 0 | 6.23675535 | 6.23675535 | 3162.33333333333333393 | 0/0 |
| Graduated | scripted_flow | down20 | -20 | -19.80094648 | -35.84075718 | 2529.866666666666667144 | 0/0 |
| Graduated | scripted_flow | flat_one_bps_noise | 0 | 1.75412278 | 1.75412278 | 3162.33333333333333393 | 0/0 |
| Graduated | scripted_flow | up20 | 20 | 17.65851901 | 41.19022281 | 3794.800000000000000716 | 0/0 |

## Explicit experiment assumptions

- Local actors funded with Foundry deal; no cost or faucet acquisition model.
- Calendar mocked open for strategy scenarios; separate test restores actual weekend and asserts rejection.
- TestnetMarket owner impersonated only inside fork; its real V3 swaps and feed setter move TSLA together.
- Each price path waits 601 seconds for the actual V3 observation window; not real wall-clock latency.
- Scripted flow buys 100 tUSDG on nonnegative print, sells 20% of current FUN holdings on negative print.
- Keeper branch attempts exactly one execute and one buyback per noninitial price point; no user trades.

## What these results cannot establish

- These are deterministic stress paths, not TSLA forecasts or guaranteed profits.
- FUN has no independent USDG venue in this experiment; TSLA repricing alone gives no FUN/TSLA arbitrage target.
- The external market maker's mint-funded V3 price moves stand in for price discovery/arbitrage; its P&L is excluded.
- Default dollar marks use the TSLA oracle/scenario-input price, not the post-trade V3 spot. Separate ActualSpotMark columns use the measured V3 spot; neither is a liquidation quote. Taxes, slippage, gas and exit size matter.
- Active curve treasury is deliberately unwired; only graduated treasury can execute its strategy.
- LP marks are underlying full-range principal at actual sqrt price; accrued uncollected LP fees are excluded.
- No actor gas is subtracted because calls execute inside a Foundry test, not public signed transactions.

## Verification

- Solidity SHA-256: `efb54136d99f4fa79d46ad3b030554e14af078910d8461ddcc7097d7f644817f`
- Forge log SHA-256: `09f22b85eae0516ebd8b87f378b8af99e0cb91b157e1965907331f2abf452b13`
- Data: `results.json`, `chart-data.csv`, `summary.csv`, `forge.log`.

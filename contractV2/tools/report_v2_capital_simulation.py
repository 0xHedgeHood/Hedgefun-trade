#!/usr/bin/env python3
"""Run the opt-in, in-memory V2 capital fork and save its exact measurements.

No credentials, signing, Anvil transaction submission, or broadcast paths.
"""

import argparse
import json
import os
import pathlib
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[1]
EXPECTED = {
    "default44_32exempt_opening", "default44_noexempt_opening",
    "default44_after_window", "default44_tax10_after_window",
    "sale80_32exempt_opening", "sale80_after_window",
}
PARTICIPATION_EXPECTED = {
    "sale80_baseline", "sale80_cycle1", "sale80_cycle10",
    "sale80_late5000_cycle1", "sale80_late20000_cycle1",
    "sale80_early1_cycle1", "sale80_early2_cycle1",
    "sale80_early1_halfexit_cycle1", "sale80_early3_cycle1",
    "sale90_early5_cycle1", "sale90_early8_cycle1",
    "sale80_opening32_cycle1", "sale90_opening32_cycle1",
    "sale90_opening32_late5000_cycle1",
}
BUDGET32_EXPECTED = {
    "default44_opening32_budget40k", "sale80_opening32_budget40k",
    "sale80_opening32_cycle1_budget40k", "sale80_afterwindow_budget40k",
    "sale80_noexempt_opening_budget40k", "sale90_opening32_budget40k",
}
WALLET_TARGET_EXPECTED = {
    "default44_wallet1_fdv1m", "default44_wallet1_fdv2m",
    "default44_wallet32_fdv1m", "default44_wallet32_fdv2m",
    "default44_wallet32_cycle1_fdv1m", "default44_wallet32_cycle1_fdv2m",
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--block", type=int)
    parser.add_argument("--log", type=pathlib.Path, help="Parse an already completed fork run")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--participation", action="store_true", help="Original supply, outsiders and 1%% strategy scenarios")
    modes.add_argument("--budget32", action="store_true", help="40,000 test USDG total and 32 buyers")
    modes.add_argument("--wallet-targets", action="store_true", help="Compare one and 32 buyers at 1m/2m FDV")
    parser.add_argument("--output", type=pathlib.Path)
    args = parser.parse_args()
    if args.wallet_targets:
        expected, marker, contract = WALLET_TARGET_EXPECTED, "WALLET_TARGET_RESULT ", "V2Budget32ForkTest"
        default_block, output_name, test_filter = 126805242, "v2-wallet-targets.json", "^testWalletTargets_"
    elif args.budget32:
        expected, marker, contract = BUDGET32_EXPECTED, "BUDGET32_RESULT ", "V2Budget32ForkTest"
        default_block, output_name, test_filter = 126805242, "v2-budget32-40k.json", "^testBudget32_"
    elif args.participation:
        expected, marker, contract = PARTICIPATION_EXPECTED, "PARTICIPATION_RESULT ", "V2OriginalSupplyParticipationForkTest"
        default_block, output_name, test_filter = 126779888, "v2-original-supply-participation.json", "^(testParticipants_|testConstraint_)"
    else:
        expected, marker, contract = EXPECTED, "CAPITAL_RESULT ", "V2CapitalSimulationForkTest"
        default_block, output_name, test_filter = 126672507, "v2-capital-simulation.json", None
    if args.block is None:
        args.block = default_block
    if args.block <= 0:
        parser.error("a positive pinned fork block is required")
    expected_tests = len(expected) + (2 if args.participation else 0)
    if args.output is None:
        args.output = ROOT / "deploy" / output_name
    if args.log:
        log = args.log.read_text()
    else:
        env = os.environ.copy()
        env.update(V2_CAPITAL_FORK="1", V2_CAPITAL_BLOCK=str(args.block))
        command = ["forge", "test", "--match-contract", f"^{contract}$", "-vv"]
        if test_filter:
            command += ["--match-test", test_filter]
        result = subprocess.run(command, cwd=ROOT, env=env, text=True, capture_output=True, check=True)
        log = result.stdout + result.stderr
    if f"{expected_tests} passed; 0 failed; 0 skipped" not in log or "[FAIL" in log:
        raise RuntimeError("all opt-in scenarios must pass; refusing incomplete evidence")
    scenarios = {}
    for line in log.splitlines():
        if marker not in line:
            continue
        row = json.loads(line.split(marker, 1)[1])
        name = row["scenario"]
        if name in scenarios:
            raise RuntimeError(f"duplicate scenario: {name}")
        if row["forkBlock"] != args.block:
            raise RuntimeError("log block does not match requested pinned block")
        if args.budget32:
            budget, actual = int(row["totalBudgetUsdgRaw"]), int(row["actualUsdgRaw"])
            if row["buyerCount"] != 32 or budget != 40_000 * 10**6 or not 0 <= budget - actual < 100:
                raise RuntimeError("32-buyer fixed budget was not respected")
            reached = int(row["heldRaw"]) * 100 >= int(row["initialSupplyRaw"]) * 80
            if row["original80Reached"] != reached or int(row["fdvSurvivingE18"]) <= 0:
                raise RuntimeError("ownership flag or measured FDV is invalid")
            scenarios[name] = row
            continue
        if args.wallet_targets:
            target, actual = int(row["targetFdvE18"]), int(row["actualFdvE18"])
            if target not in {1_000_000 * 10**18, 2_000_000 * 10**18} or not 0 <= actual - target < 10**15:
                raise RuntimeError("wallet-comparison FDV must tightly reach its target")
            if row["buyerCount"] not in {1, 32} or row["openingExemptions"] != row["buyerCount"]:
                raise RuntimeError("wallet-comparison exemption count is invalid")
            scenarios[name] = row
            continue
        final_fdv = row["finalFdvE18"] if args.participation else row["fdv2m"]["fdvAfterSweepE18"]
        if int(final_fdv) < 2_000_000 * 10**18:
            raise RuntimeError("FDV target was not reached")
        if args.participation:
            held, denominator = row["operatorHeldRaw"], row["initialSupplyRaw"]
        else:
            eighty = row["ownership80"]
            held, denominator = eighty["heldRaw"], eighty["totalSupplyRaw"]
        if int(held) * 100 < int(denominator) * 80:
            raise RuntimeError("ownership target was not reached")
        scenarios[name] = row
    if set(scenarios) != expected:
        raise RuntimeError("missing or unexpected scenarios")
    report = {
        "environment": "in-memory Robinhood testnet fork; no public transactions",
        "chainId": 46630,
        "forkBlock": args.block,
        "sourceBaseCommit": "1742264",
        "factory": "0x3E95976E2425e63cb2A8d48BBce8976F55627019",
        "stock": "TSLA test token; $358 seeded reference, not a live stock quote",
        "ownershipDefinition": "wallet group / surviving ERC20 totalSupply after hook sweep",
        "fdvDefinition": "V4 marginal token price * surviving totalSupply, at frozen stock/USDG reference",
        "fundingDefinition": "independent exact-output V3 acquisition of cumulative TSLA budget up front",
        "launchFeeIncludedInStageAmounts": False,
        "gasIncluded": False,
        "sequencerBundleSimulated": False,
        "scenarios": scenarios,
    }
    if args.participation:
        report.update(
            ownershipDefinition="40 operator wallets / original minted supply; excludes outsiders and keepers",
            fdvDefinition="V4 marginal token price * surviving totalSupply after settlement, at final moved stock reference",
            fundingDefinition="operator and outsider budgets each quoted independently as initial-state V3 upfront TSLA acquisition; operator includes 25 test USDG launch fee",
            launchFeeIncludedInStageAmounts=True,
            strategyDefinition="kind0: +1% of lot cost takes profit, -1% of last sale price buys dip; lot20%, stop disabled; real V3/feed/TWAP moves",
            strategyCounterfactual="fork-only listing maxSlippage10bps/maxDeviation5bps; current 100bps slippage rejects 1% triggers",
            openingDefinition="openingBundle=false waits180s, all3%base tax; true uses32 exempt recipients +8 ordinary startup callers; sequential EVM calls only",
            outsideDefinition="10 independent wallets; early buys between operator wallets10/11; late buys after the post-strategy original80% checkpoint; exit at >=1m FDV",
            cyclesDefinition="completed price-trigger cycles after graduation and before the original80% checkpoint; exogenous market moves are excluded from investor budgets",
            combinedFundingDefinition="initial-state V3 exact-output acquisition of operatorStockInRaw+outsideStockInRaw, plus launch fee; gross, does not deduct outside sells",
            constraintTests="default44 graduation supply <original80%; current gates reject1% treasury deployment",
            validationTests=expected_tests,
        )
    elif args.budget32:
        report.update(
            ownershipDefinition="32 buyer wallets / original minted supply; fixture wallets32..39 hold zero strategy tokens",
            fdvDefinition="graduated V4 marginal price or active-curve marginal y/x price * surviving totalSupply * stock reference",
            fundingDefinition="initial-state V3 exact-output acquisition of cumulative TSLA up front; 40,000 test USDG includes25 launch fee",
            launchFeeIncludedInStageAmounts=True,
            outsideParticipantsIncluded=False,
            strategyDefinition="one cycle only where specified; fork-only10bp slippage/5bp deviation;20%reserve dip; exogenous V3 market moves excluded",
            validationTests=expected_tests,
        )
    elif args.wallet_targets:
        report.update(
            ownershipDefinition="one or32 associated buyer wallets / original minted and surviving supply, reported separately",
            fdvDefinition="graduated V4 marginal price * surviving totalSupply * final stock reference",
            fundingDefinition="initial-state V3 exact-output acquisition of cumulative TSLA, plus25 test USDG launch fee",
            launchFeeIncludedInStageAmounts=True,
            outsideParticipantsIncluded=False,
            openingDefinition="all buying recipients exempt from opening surcharge;3%base tax applies",
            strategyDefinition="one cycle only where specified; fork-only10bp slippage/5bp deviation;20%reserve dip; exogenous market moves excluded",
            globalCapitalMinimumClaimed=False,
            validationTests=expected_tests,
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(f"Saved {len(expected)} passing fork scenarios: {args.output}")


if __name__ == "__main__":
    main()

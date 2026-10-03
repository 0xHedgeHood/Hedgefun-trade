"""Reject option cash/stock accounting and synthetic-quote corruption."""
import importlib.util
import json
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'tools'))
SPEC=importlib.util.spec_from_file_location('options_history_report',ROOT/'tools/options_history_report.py')
TOOL=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(TOOL)


class OptionsEvidenceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        evidence=ROOT/'deploy/all-strategy-income-2026-10-03/options/forge.log.gz'
        if not evidence.exists(): raise unittest.SkipTest('options replay archive absent')
        cls.raw=TOOL.read_log(evidence);cls.events=TOOL.parse(cls.raw)
        cls.data=json.loads((ROOT/'data/equity-history-2025.json').read_text())

    def changed(self,event,field,value,index=0):
        events=dict(self.events);events[event]=list(events[event])
        events[event][index]={**events[event][index],field:value}
        return events

    def test_all_years_actual_collateral_redemptions_and_closed_cash_validate(self):
        groups,meta,options=TOOL.validate(self.events,self.data)
        self.assertEqual((len(groups),sum(map(len,groups.values())),sum(map(len,options.values()))),(20,5000,760))
        row=TOOL.summarize(('TSLA','wheel'),groups[('TSLA','wheel')],meta[('TSLA','wheel')],options[('TSLA','wheel')])
        self.assertGreater(TOOL.D(row['premiumReceivedUsdg']),2000)
        self.assertLess(TOOL.D(row['settledOptionPnlUsdg']),0)
        self.assertLess(TOOL.D(row['terminalSettledAssetsChangePercent']),0)

    def test_missing_date_and_failed_suite_cannot_be_accepted(self):
        with self.assertRaises(ValueError):TOOL.parse(self.raw.replace('27 passed; 0 failed; 0 skipped','26 passed; 1 failed; 0 skipped'))
        e=dict(self.events);e['ROW']=e['ROW'][1:]
        with self.assertRaises(ValueError):TOOL.validate(e,self.data)

    def test_premium_is_calendar_model_not_free_income(self):
        for field in ('premiumUsdgRaw','offerTimestamp','signedSettledOptionPnlUsdgRaw','payoutAtExpiryUsdgRaw'):
            with self.subTest(field=field), self.assertRaises(ValueError):
                TOOL.validate(self.changed('CONTRACT',field,self.events['CONTRACT'][0][field]+1),self.data)

    def test_daily_cash_and_stock_cannot_be_minted_to_make_results_pass(self):
        for field in ('mmUsdgRaw','escrowStockRaw','grossAssetsUsdgRaw','optionIntrinsicLowerBoundUsdgRaw'):
            with self.subTest(field=field),self.assertRaises(ValueError):
                TOOL.validate(self.changed('ROW',field,self.events['ROW'][7][field]+1,7),self.data)

    def test_principal_redemption_and_staking_cannot_exceed_actual_transfers(self):
        with self.assertRaises(ValueError):
            TOOL.validate(self.changed('REDEEM','usdgOutRaw',self.events['REDEEM'][0]['usdgOutRaw']+1),self.data)
        with self.assertRaises(ValueError):
            TOOL.validate(self.changed('STAKING_BRIDGE','actualStakingFundingRaw',self.events['STAKING_BRIDGE'][0]['actualStakingFundingRaw']+1),self.data)

    def test_swap_fee_and_stated_integration_boundaries_are_enforced(self):
        with self.assertRaises(ValueError):
            TOOL.validate(self.changed('REINVEST','executionCostAtTradeMarkUsdgRaw',self.events['REINVEST'][0]['executionCostAtTradeMarkUsdgRaw']+1),self.data)
        with self.assertRaises(ValueError):
            TOOL.validate(self.changed('PROFILE','funV4Connected',True),self.data)


if __name__=='__main__':unittest.main()

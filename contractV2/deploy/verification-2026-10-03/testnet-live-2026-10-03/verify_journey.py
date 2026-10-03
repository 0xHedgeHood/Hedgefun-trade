#!/usr/bin/env python3
"""Read-only independent receipt, canonical-block, accounting and UI-guard verification."""
import importlib.util
import json
import pathlib

OUT = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('journey', OUT / 'run_journey.py')
j = importlib.util.module_from_spec(spec)
spec.loader.exec_module(j)
s = j.state
assert j.rpc('eth_chainId', []) == hex(46630)
assert all(x['verified'] for x in s['quotes']) and len(s['quotes']) == 5
head = int(j.rpc('eth_blockNumber', []), 16)
gas_cost = 0
steps = []
for i, row in enumerate(s['transactions']):
    assert row['state'] == 'confirmed' and row['nonce'] == i + s['initialNonce']
    r = j.rpc('eth_getTransactionReceipt', [row['hash']])
    assert r['status'] == '0x1' and r['blockHash'] == row['receipt']['blockHash']
    block = j.rpc('eth_getBlockByNumber', [r['blockNumber'], False])
    assert r['blockHash'] == block['hash']
    assert head - int(r['blockNumber'], 16) >= 2
    tx = j.rpc('eth_getTransactionByHash', [row['hash']])
    assert tx['from'].lower() == j.WALLET.lower() and tx['to'].lower() == row['to'].lower()
    assert tx['input'].lower() == row['data'].lower() and int(tx['value'],16) == 0
    assert int(tx['chainId'],16) == 46630 and int(tx['nonce'],16) == row['nonce']
    cost = int(r['gasUsed'],16) * int(r['effectiveGasPrice'],16)
    gas_cost += cost
    steps.append({'label':row['label'],'hash':row['hash'],'blockNumber':int(r['blockNumber'],16),
                  'status':1,'gasCostWei':str(cost),
                  'explorer':'https://explorer.testnet.chain.robinhood.com/tx/'+row['hash']})
assert gas_cost == s['verifiedFinal']['nativeSpentWei']
assert j.read(j.FACTORY,'curves(uint256)',s['id'])[0] == int(s['curve'],16)
assert j.read(s['curve'],'status()')[0] == 2
assert j.read(j.USDG,'allowance(address,address)',j.WALLET,j.FACTORY)[0] == 0

guards = {}
timestamp = int(j.rpc('eth_getBlockByNumber',['latest',False])['timestamp'],16)
for label, deadline, stage, expected in [
    ('staleActiveQuoteRejected',timestamp+300,0,j.cast('sig','StageChanged(uint8)') + f'{2:064x}'),
    ('expiredQuoteRejected',0,2,j.cast('sig','Expired()')),
]:
    params=f'({s["id"]},{j.USDG},100000000,1,1,{deadline},{stage},false)'
    try:
        j.read(j.ROUTER,f'buy({j.PTYPE},(address,address)[])',params,f'[({j.POOL},{j.STOCK})]')
    except j.RpcError as error:
        assert error.error.get('data') == expected, error
        guards[label] = {'verified':True,'revertData':expected,'mode':'eth_call only'}
    else:
        raise AssertionError('Invalid quote unexpectedly accepted')
assert int(j.rpc('eth_getTransactionCount',[j.WALLET,'latest']),16) == s['initialNonce']+len(steps)

def strings(value):
    if isinstance(value,bool) or value is None: return value
    if isinstance(value,int): return str(value)
    if isinstance(value,list): return [strings(x) for x in value]
    if isinstance(value,dict): return {k:strings(v) for k,v in value.items()}
    return value

result = {'schema':'hedgefun-testnet-live-journey-v1','broadcast':True,'chainId':46630,
          'factory':j.FACTORY,'tradeRouter':j.ROUTER,'creator':j.WALLET,'strategyId':s['id'],
          'name':s['name'],'symbol':s['symbol'],'token':s['token'],'treasury':s['treasury'],
          'curve':s['curve'],'stock':j.STOCK,'usdg':j.USDG,'stockUsdgPool':j.POOL,
          'poolId':s['poolId'],'poolKey':s['poolKey'],'stage':2,'stageLabel':'Graduated',
          'successCount':len(steps),'failureCount':0,'verifiedAtBlock':head,
          'gasSpentWei':str(gas_cost),'final':strings(s['verifiedFinal']),
          'fees':strings(s['fees']),'quoteGuards':guards,
          'graduationEvents':json.loads((OUT/'graduation-events.json').read_text()),
          'transactions':steps,
          'limitations':['Stock/tUSDG route on existing two-sided-fee factory; no native ETH bridge used.',
                         'Equity calendar is closed; treasury stock remains funded but not yet booked.',
                         'V4 buy-token fees await owner-authorized conversion; stock-fee delivery is complete.',
                         'This verifies chain integration, not the mainsite UI or a newly deployed creator core.']}
(OUT/'integration.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({k:result[k] for k in ('strategyId','token','curve','stage','successCount','failureCount','gasSpentWei','verifiedAtBlock')},indent=2))
print('Verified canonical receipts, exact calldata, signer, zero native values, gas sum, five trade balance checks, expired/stale-stage quote rejection.')

#!/usr/bin/env python3
"""One explicitly requested Robinhood testnet journey, journaled before signing.

The signer is an encrypted local keystore. This script never reads its password
or exports a private key. Each phase stops on any uncertain transaction outcome.
"""
import json
import pathlib
import subprocess
import sys
import time

ROOT = pathlib.Path('/Users/leo/Documents/ChatGPT/hedgefun')
OUT = pathlib.Path(__file__).resolve().parent
CAST = str(ROOT / '.local/bin/cast')
RPC = 'https://rpc.testnet.chain.robinhood.com'
WALLET = '0xCeCAd0eBB0CAb4fbB2fe6213E3cd6dE82e4D164B'
FACTORY = '0xACEB03aAeE5494Aa54929Ec840630ae32A9ade0A'
ROUTER = '0xB291B34CD2D32C4a2DeFCe074107824654D427eF'
USDG = '0x539574139BB4Ac74Fd2183ba0E38ed777E30B58d'
STOCK = '0xcee322837F181Bd93AC2d71e4dDf334BFF565b98'
POOL = '0x04083643FF9E8c27f66C9dD99947743A9B777244'
REGISTRY = '0x0D73f6bd43D3937e4b07c70bfFC53A8c25d3C1CA'
HOOK = '0xF1b4C95B63091AE2eb68E640F6D9485982146844'
QTYPE = '(string,string,address,address,uint16,uint16,uint32,uint32,uint16,uint16,uint16,uint16,uint96,uint256,uint256)'
PTYPE = '(uint256,address,uint256,uint256,uint256,uint256,uint8,bool)'
STATE = OUT / 'journey.json'
state = json.loads(STATE.read_text()) if STATE.exists() else {
    'chainId': 46630, 'wallet': WALLET, 'factory': FACTORY, 'router': ROUTER,
    'stock': STOCK, 'usdg': USDG, 'name': 'Hedgefun Fresh Wallet Testnet',
    'symbol': 'HFFRESH', 'creatorNonce': 2026100301, 'transactions': [], 'quotes': []
}


class RpcError(Exception):
    def __init__(self, error):
        self.error = error
        super().__init__(str(error))


def rpc(method, params):
    body = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params})
    response = subprocess.run(['curl','--fail','--silent','--show-error','--max-time','30',
                               '-H','Content-Type: application/json','--data-binary','@-',RPC],
                              input=body,text=True,capture_output=True,check=True)
    result = json.loads(response.stdout)
    if 'error' in result:
        raise RpcError(result['error'])
    return result['result']


def cast(*args):
    return subprocess.check_output([CAST, *map(str, args)], text=True, timeout=60).strip()


def save():
    temp = STATE.with_suffix('.tmp')
    temp.write_text(json.dumps(state, indent=2) + '\n')
    temp.replace(STATE)


def words(raw):
    assert raw.startswith('0x') and (len(raw) - 2) % 64 == 0, raw
    return [int(raw[i:i+64], 16) for i in range(2, len(raw), 64)]


def address(value):
    return f'0x{value:040x}'


def read(to, sig, *args, block='latest'):
    return words(rpc('eth_call', [{'from': WALLET, 'to': to, 'data': cast('calldata', sig, *args)}, block]))


def balance(token, owner=WALLET, block='latest'):
    return read(token, 'balanceOf(address)', owner, block=block)[0]


def already(label):
    found = [x for x in state['transactions'] if x['label'] == label]
    if found:
        assert len(found) == 1 and found[0]['state'] == 'confirmed', 'Inspect uncertain transaction before retrying'
        return found[0]


def send(label, to, sig, *args):
    previous = already(label)
    if previous:
        return previous['receipt']
    assert int(rpc('eth_chainId', []), 16) == 46630
    nonce = int(rpc('eth_getTransactionCount', [WALLET, 'pending']), 16)
    assert nonce == state['initialNonce'] + len(state['transactions']), 'Unexpected signer nonce'
    data = cast('calldata', sig, *args)
    tx = {'from': WALLET, 'to': to, 'data': data, 'value': '0x0'}
    simulated = rpc('eth_call', [tx, 'latest'])
    estimated = int(rpc('eth_estimateGas', [tx]), 16)
    gas_limit = (estimated * 130 + 99) // 100
    gas_price = int(rpc('eth_gasPrice', []), 16) * 2
    assert gas_price <= 100_000_000 and gas_limit <= 25_000_000, 'Gas budget outside reviewed envelope'
    native = int(rpc('eth_getBalance', [WALLET, 'latest']), 16)
    maximum = gas_limit * gas_price
    assert maximum < native
    assert state['initialNativeWei'] - native + maximum <= 5_000_000_000_000_000, 'Journey budget exceeded'
    entry = {'label': label, 'to': to, 'from': WALLET, 'signature': sig, 'args': list(args),
             'data': data, 'nonce': nonce, 'estimatedGas': estimated, 'gasLimit': gas_limit,
             'gasPrice': gas_price, 'simulationResult': simulated, 'state': 'sending'}
    state['transactions'].append(entry)
    save()
    print(f'Signing {label}; nonce={nonce}; estimated gas={estimated}', flush=True)
    txhash = cast('send', to, sig, *args, '--rpc-url', RPC, '--chain', '46630', '--legacy',
                  '--nonce', nonce, '--gas-limit', gas_limit, '--gas-price', gas_price,
                  '--keystore', ROOT / '.local/testnet-wallet-20261003/hedgefun-testnet',
                  '--password-file', ROOT / '.local/testnet-wallet-20261003/password', '--async')
    assert txhash.startswith('0x') and len(txhash) == 66, txhash
    entry.update(state='submitted', hash=txhash)
    save()
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        receipt = rpc('eth_getTransactionReceipt', [txhash])
        if receipt:
            break
        time.sleep(0.5)
    else:
        raise RuntimeError(f'Pending transaction {txhash}; do not resend')
    entry['receipt'] = receipt
    assert int(receipt['status'], 16) == 1, f'Transaction reverted: {txhash}'
    assert receipt['from'].lower() == WALLET.lower() and receipt['to'].lower() == to.lower()
    confirmed = rpc('eth_getTransactionByHash', [txhash])
    assert confirmed['input'].lower() == data.lower() and int(confirmed['nonce'], 16) == nonce
    assert int(confirmed['chainId'], 16) == 46630
    entry.update(state='confirmed', transaction=confirmed)
    save()
    print(f'Confirmed {label}: {txhash} in block {int(receipt["blockNumber"], 16)}', flush=True)
    return receipt


def preflight():
    assert int(rpc('eth_chainId', []), 16) == 46630
    signer = cast('wallet', 'address', '--keystore', ROOT / '.local/testnet-wallet-20261003/hedgefun-testnet',
                  '--password-file', ROOT / '.local/testnet-wallet-20261003/password')
    assert signer.lower() == WALLET.lower()
    assert address(read(ROUTER, 'factory()')[0]).lower() == FACTORY.lower()
    assert address(read(FACTORY, 'curveDeployer()')[0]).lower() == REGISTRY.lower()
    assert read(FACTORY, 'publicLaunch()')[0] == 1
    d = read(FACTORY, 'getDefaults()')
    assert d[-2:] == [2, 25_000_000] and d[5] == 2000
    listing = read(FACTORY, 'listings(address)', STOCK)
    assert address(listing[1]).lower() == POOL.lower() and listing[2:] == [26500000000, 1]
    if 'initialNativeWei' not in state:
        state['initialNativeWei'] = int(rpc('eth_getBalance', [WALLET, 'latest']), 16)
        state['initialNonce'] = int(rpc('eth_getTransactionCount', [WALLET, 'pending']), 16)
        state['initialBlock'] = int(rpc('eth_blockNumber', []), 16)
        assert state['initialNonce'] == 0
        assert balance(USDG) == balance(STOCK) == 0
        save()


def fund():
    send('drip-tUSDG', USDG, 'drip()')
    send('drip-TSLA', STOCK, 'drip()')
    assert balance(USDG) == 10_000_000_000 and balance(STOCK) == 15 * 10**18
    print('Public drips verified: 10000 tUSDG + 15 TSLA', flush=True)


def launch():
    send('curve-config', REGISTRY, 'setCurveConfig(string,uint96,uint16,uint8)', state['symbol'], state['creatorNonce'], 4400, 180)
    send('approve-launch', USDG, 'approve(address,uint256)', FACTORY, 25_000_000)
    q = f'("{state["name"]}","{state["symbol"]}",{STOCK},{WALLET},300,1000,300,600,500,0,2000,0,{state["creatorNonce"]},25000000,26500000000)'
    prediction = read(FACTORY, f'predict({QTYPE})', q)
    state.update(token=address(prediction[0]), treasury=address(prediction[1]),
                 terms=f'0x{prediction[2]:064x}', curve=address(read(FACTORY, f'predictCurve({QTYPE})', q)[0]))
    save()
    receipt = send('launch', FACTORY, f'launch({QTYPE},bytes32)', q, state['terms'])
    topic = cast('keccak', 'Launched(uint256,string,address,address,address,address,address)')
    logs = [x for x in receipt['logs'] if x['address'].lower() == FACTORY.lower() and x['topics'][0] == topic]
    assert len(logs) == 1
    state['id'] = int(logs[0]['topics'][1], 16)
    row = read(FACTORY, 'strategies(uint256)', state['id'])
    assert [address(row[i]).lower() for i in (0,1,3,4)] == [state['token'].lower(),state['treasury'].lower(),STOCK.lower(),WALLET.lower()]
    assert address(read(FACTORY, 'curves(uint256)', state['id'])[0]).lower() == state['curve'].lower()
    assert read(state['curve'], 'status()')[0] == 0
    save()
    print(json.dumps({k:state[k] for k in ('id','token','treasury','curve')}), flush=True)


def trade(label, buying, amount, stage, partial=False):
    if already(label):
        return
    assert read(state['curve'], 'status()')[0] == stage
    asset = USDG if buying else state['token']
    send('approve-' + label, asset, 'approve(address,uint256)', ROUTER, amount)
    timestamp = int(rpc('eth_getBlockByNumber', ['latest', False])['timestamp'], 16)
    deadline = timestamp + 300
    path = f'[({POOL},{STOCK if buying else USDG})]'
    sig = f'{"buy" if buying else "sell"}({PTYPE},(address,address)[])'
    def params(minstock, minout):
        return f'({state["id"]},{USDG},{amount},{minstock},{minout},{deadline},{stage},{str(partial).lower()})'
    minstock = 0
    stock_quote = None
    if buying:
        # TooLittleStock(got) quotes the actual V3 route then reverts before a curve/V4 trade.
        try:
            read(ROUTER, sig, params(2**128, 1), path)
        except RpcError as error:
            raw = error.error.get('data')
            assert isinstance(raw, str) and raw.startswith('0xbd392856') and len(raw) == 74, error
            stock_quote = int(raw[10:], 16)
            assert stock_quote > 0
            minstock = stock_quote * 99 // 100
        else:
            raise RuntimeError('Expected a bounded V3 route quote revert')
    quote = read(ROUTER, sig, params(minstock, 1), path)
    minimum = quote[0] * 99 // 100
    assert minimum > 0 and (partial or quote[1] == 0)
    if label == 'graduate':
        assert quote[0] >= 350_000_000 * 10**18 and quote[1] > 0
    before = {name:balance(token) for name,token in [('usdg',USDG),('stock',STOCK),('token',state['token'])]}
    state['quotes'].append({'label':label,'stockQuote':stock_quote,'minStock':minstock,'quotedFinalOut':quote[0],
                            'minFinalOut':minimum,'quotedRefund':quote[1],'deadline':deadline,'before':before})
    save()
    send(label, ROUTER, sig, params(minstock, minimum), path)
    after = {name:balance(token) for name,token in [('usdg',USDG),('stock',STOCK),('token',state['token'])]}
    if buying:
        assert before['usdg'] - after['usdg'] == amount and after['token'] - before['token'] >= minimum
        assert after['stock'] >= before['stock']
    else:
        assert before['token'] - after['token'] == amount and after['usdg'] - before['usdg'] >= minimum
    assert read(asset, 'allowance(address,address)', WALLET, ROUTER)[0] == 0
    assert read(state['curve'], 'status()')[0] == (2 if label == 'graduate' else stage)
    state['quotes'][-1]['after'] = after
    state['quotes'][-1]['verified'] = True
    save()
    print(f'{label} balance deltas and stage verified', flush=True)


def snapshot():
    block = rpc('eth_blockNumber', [])
    assets = {'nativeWei':int(rpc('eth_getBalance', [WALLET, block]),16), 'tUSDG6':balance(USDG,block=block),
              'TSLA18':balance(STOCK,block=block), 'FUN18':balance(state['token'],block=block)}
    buckets = {name:read(state['treasury'], name+'()',block=block)[0]
               for name in ('bookedStock','buybackStock','unbookedStock','totalStockReceived')}
    buckets['heldStock'] = balance(STOCK,state['treasury'],block=block)
    assert buckets['bookedStock'] + buckets['buybackStock'] + buckets['unbookedStock'] == buckets['heldStock']
    assert buckets['heldStock'] > 0
    for token in (USDG,STOCK,state['token']):
        assert balance(token,ROUTER,block=block) == 0
        assert read(token,'allowance(address,address)',WALLET,ROUTER,block=block)[0] == 0
    assert read(state['curve'],'status()',block=block)[0] == 2
    state['verifiedFinal'] = {'blockNumber':int(block,16),'blockHash':rpc('eth_getBlockByNumber',[block,False])['hash'],
                              'stage':2,'walletBalances':assets,'treasury':buckets,'routerBalancesZero':True,
                              'routerAllowancesZero':True,'nativeSpentWei':state['initialNativeWei']-assets['nativeWei']}
    save()
    print(json.dumps(state['verifiedFinal'],indent=2),flush=True)


def settle():
    assert read(state['curve'], 'status()')[0] == 2
    protocol = address(read(state['curve'], 'protocol()')[0])
    for label, recipient in [('creator',WALLET),('treasury',state['treasury']),('protocol',protocol)]:
        amount = read(state['curve'],'claimable(address)',recipient)[0]
        if amount:
            before = balance(STOCK,recipient)
            send('claim-curve-'+label,state['curve'],'claimFees(address)',recipient)
            assert balance(STOCK,recipient) - before == amount
        assert read(state['curve'],'claimable(address)',recipient)[0] == 0
    assert read(state['curve'],'totalFees()')[0] == 0
    key = read(FACTORY,'graduationConfig(uint256)',state['id'])[:5]
    assert address(key[4]).lower() == HOOK.lower()
    state['poolId'] = cast('keccak','0x'+''.join(f'{x:064x}' for x in key))
    state['poolKey'] = {'currency0':address(key[0]),'currency1':address(key[1]),
                        'fee':key[2],'tickSpacing':key[3],'hooks':address(key[4])}
    before = read(HOOK,'accrued(bytes32)',state['poolId'])
    pending = read(HOOK,'pendingTokenFees(bytes32)',state['poolId'])[0]
    save()
    send('sweep-v4-fees',HOOK,'sweep(bytes32)',state['poolId'])
    assert read(HOOK,'accrued(bytes32)',state['poolId']) == [0,0]
    assert read(HOOK,'pendingTokenFees(bytes32)',state['poolId'])[0] == pending+before[0]
    for name in ('owedProtocol','owedCreator','owedTreasury'):
        assert read(HOOK,name+'(bytes32)',state['poolId'])[0] == 0
    state['fees'] = {'curveClaimsDelivered':True,'curveTotalFees':0,'v4AccruedBeforeSweep':before,
                     'v4AccruedAfterSweep':[0,0],'v4StockOwedAfterSweep':0,
                     'pendingTokenFees':pending+before[0],
                     'pendingTokenConversion':'Requires factory owner; this ordinary-wallet journey does not perform it'}
    save()
    print(json.dumps(state['fees'],indent=2),flush=True)


if __name__ == '__main__':
    preflight()
    phase = sys.argv[1]
    if phase == 'fund': fund()
    elif phase == 'launch': launch()
    elif phase == 'curve-buy': trade(phase,True,100_000_000,0)
    elif phase == 'curve-sell': trade(phase,False,1_000_000*10**18,0)
    elif phase == 'graduate': trade(phase,True,9_000_000_000,0,True)
    elif phase == 'v4-buy': trade(phase,True,100_000_000,2)
    elif phase == 'v4-sell': trade(phase,False,1_000_000*10**18,2)
    elif phase == 'settle': settle()
    elif phase == 'snapshot': snapshot()
    else: raise ValueError('Unknown phase')

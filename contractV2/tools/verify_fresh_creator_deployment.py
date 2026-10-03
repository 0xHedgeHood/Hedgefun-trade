#!/usr/bin/env python3
"""Verify a fresh-wallet creator core and promote its candidate using read-only RPC."""
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
SIGNER = '0xcecad0ebb0cab4fbb2fe6213e3cd6de82e4d164b'
LEGACY = '0x75cee941b0ef3a83fea0397bbf903c12c1d7e96d'
FEATURE = 'v2-creator-selected-fresh-wallet-v1'
CORE = {'treasuryDeployer': ('V2TreasuryDeployer', 'V2TreasuryDeployer'),
        'tokenDeployer': ('HedgeFunDeployers', 'TokenDeployer'), 'curveDeployer': ('CurveDeployer', 'CurveDeployer'),
        'hook': ('HedgeFunV2Hook', 'HedgeFunV2Hook'), 'factory': ('HedgeFunV2Factory', 'HedgeFunV2Factory'),
        'tradeRouter': ('HedgeFunV2TradeRouter', 'HedgeFunV2TradeRouter'),
        'nativeRouter': ('HedgeFunV2NativeRouter', 'HedgeFunV2NativeRouter'),
        'rebalancePolicy': ('strategy/V2RebalancePolicy', 'V2RebalancePolicy')}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--candidate', type=Path, required=True)
    p.add_argument('--broadcast-log', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--rpc', default='https://rpc.testnet.chain.robinhood.com')
    p.add_argument('--cast', default='cast')
    a = p.parse_args()
    book, run = json.loads(a.candidate.read_text()), json.loads(a.broadcast_log.read_text())

    def rpc(method, params):
        assert method in {'eth_chainId','eth_getBlockByNumber','eth_getTransactionReceipt',
                          'eth_getTransactionByHash','eth_getCode','eth_call'}
        payload = json.dumps({'jsonrpc':'2.0','id':1,'method':method,'params':params})
        r = subprocess.run(['curl','--fail','--silent','--show-error','--max-time','30',
                            '-H','Content-Type: application/json','--data-binary','@-',a.rpc],
                           input=payload,text=True,capture_output=True,check=True)
        result = json.loads(r.stdout)
        assert 'error' not in result, result
        return result['result']

    def cast(*args):
        return subprocess.check_output([a.cast,*map(str,args)],text=True).strip()

    def code(file, name=None):
        name = name or file
        artifact = ROOT/'out'/(file.split('/')[-1]+'.sol')/(name+'.json')
        return json.loads(artifact.read_text())['bytecode']['object']

    assert int(rpc('eth_chainId',[]),16) == book['chainId'] == run['chain'] == 46630
    assert book['featureVersion'] == FEATURE and book['broadcast'] is False and book['broadcastRequested'] is True
    assert book['operator'].lower() == book['owner'].lower() == SIGNER
    assert book['protocol'].lower() == book['venueOperator'].lower() == LEGACY
    assert len(run['transactions']) == book['plannedTransactionCount'] == 40
    subprocess.run(['git','diff','--exit-code',book['commit'],'--','src'],cwd=ROOT,check=True,capture_output=True)
    head = rpc('eth_getBlockByNumber',['latest',False]); tag = head['number']
    receipts, hashes, nonces = [], [], []
    core_creates = {}
    for row in run['transactions']:
        h, expected = row['hash'], row['transaction']
        tx, receipt = rpc('eth_getTransactionByHash',[h]), rpc('eth_getTransactionReceipt',[h])
        assert receipt and int(receipt['status'],16)==1
        assert receipt['transactionHash'].lower()==h.lower() and tx['hash'].lower()==h.lower()
        assert tx['from'].lower()==receipt['from'].lower()==SIGNER
        assert int(tx['chainId'],16)==46630 and int(tx['value'],16)==0
        assert tx['input'].lower()==expected['input'].lower()
        assert (tx.get('to') or '').lower()==(expected.get('to') or '').lower()
        assert int(tx['nonce'],16)==int(expected['nonce'],16)
        block=rpc('eth_getBlockByNumber',[receipt['blockNumber'],False])
        assert block['hash']==receipt['blockHash'] and h.lower() in [v.lower() for v in block['transactions']]
        assert int(tag,16)-int(receipt['blockNumber'],16)>=2
        if row['contractName'] and row['transactionType']=='CREATE':
            core_creates[row['contractName']]=(receipt['contractAddress'],tx['input'])
        hashes.append(h); nonces.append(int(tx['nonce'],16)); receipts.append(receipt)
    assert len(set(hashes))==40 and nonces==list(range(nonces[0],nonces[0]+40))
    assert nonces[0]==19, 'This deployment must follow the reviewed 19-transaction wallet journey'
    for key,(file,name) in CORE.items():
        if key=='hook': continue
        target,init=core_creates[name]
        assert target.lower()==book[key].lower() and init.startswith(code(file,name))
    hook_rows=[x for x in run['transactions'] if x['transactionType']=='CREATE2' and x['contractName']=='HedgeFunV2Hook']
    assert len(hook_rows)==1 and hook_rows[0]['contractAddress'].lower()==book['hook'].lower()
    hook_input=hook_rows[0]['transaction']['input']
    assert hook_input[2:66].lower()==book['hookSalt'][2:].lower()
    assert ('0x'+hook_input[66:]).startswith(code('HedgeFunV2Hook'))
    assert int(book['hook'],16)&0x3fff==0x2844

    def read(target,sig,*args):
        raw=rpc('eth_call',[{'to':target,'data':cast('calldata',sig,*args)},tag])
        assert (len(raw)-2)%64==0 and len(raw)>2
        return [int(raw[i:i+64],16) for i in range(2,len(raw),64)]

    def eq(target,sig,expected,*args):
        wanted=[int(x,16) if isinstance(x,str) and x.startswith('0x') else int(x) for x in expected]
        assert read(target,sig,*args)==wanted, (sig,target)

    for signature,key in [('owner()','owner'),('protocol()','protocol'),('poolManager()','poolManager'),
                          ('v3Factory()','v3Factory'),('usdg()','usdg'),('hook()','hook'),
                          ('treasuryDeployer()','treasuryDeployer'),('tokenDeployer()','tokenDeployer'),('curveDeployer()','curveDeployer')]:
        eq(book['factory'],signature,[book[key]])
    eq(book['factory'],'publicLaunch()',[1])
    for key in ('treasuryDeployer','tokenDeployer','curveDeployer','hook','tradeRouter'):
        eq(book[key],'factory()',[book['factory']])
    eq(book['nativeRouter'],'router()',[book['tradeRouter']]);eq(book['nativeRouter'],'wrappedNative()',[book['weth']])
    eq(book['hook'],'version()',[2]);eq(book['treasuryDeployer'],'kindCount()',[3])
    for i,name in enumerate(('HedgeFunV2AllInTreasury','HedgeFunV2BuybackTreasury','HedgeFunV2EngineTreasury')):
        manifest=read(book['treasuryDeployer'],'kindManifest(uint8)',i)
        assert manifest[2]==int(cast('keccak',code(name)),16)
        assert manifest[:2]==([1,1] if i==2 else [0,0]) and manifest[3]==(3 if i==2 else 0)
        chunks=read(book['treasuryDeployer'],'kinds(uint8)',i)
        raw='0x'+''.join(rpc('eth_getCode',[f'0x{x:040x}',tag])[2:] for x in chunks)
        assert raw==code(name)
    eq(book['treasuryDeployer'],'allInTriggerCodeHash()',[cast('keccak',code('HedgeFunV2AllInTreasury'))])
    policy=read(book['treasuryDeployer'],'policy(bytes32)',book['rebalancePolicyKey'])
    assert policy[0]==int(book['rebalancePolicy'],16) and policy[2:]==[1,1,150000,160,3,1]
    assert policy[1]==int(cast('keccak',rpc('eth_getCode',[book['rebalancePolicy'],tag])),16)
    defaults=read(book['factory'],'getDefaults()'); base=read(book['baseFactory'],'getDefaults()');base[9]=0
    assert defaults==base and defaults[-2:]==[2,25000000]
    book['expectedDefaults']='0x'+''.join(f'{x:064x}' for x in defaults)
    assert set(book['stocks'])=={'AAPL','GME','NVDA','TSLA','MSFT','AMZN','GOOGL','META'}
    for key in ('market','calendar','usdg','usdgFeed','baseFactory'):
        eq(book[key],'owner()',[LEGACY])
    for stock in book['stocks'].values():
        token,oracle,pool=stock['token'],stock['oracle'],stock['pool']
        eq(book['factory'],'listings(address)',[oracle,pool,stock['openPriceE18'],1],token)
        eq(book['factory'],'listingGates(address)',[stock['maxDeviationBps'],stock['maxSlippageBps'],stock['sellChunkUsdg']],token)
        eq(book['treasuryDeployer'],'lpBps(address)',[stock['lpBps']],token)
        for target in (token,stock['feed']): eq(target,'owner()',[LEGACY])
        for signature,key in [('stock()','token'),('stockFeed()','feed')]:eq(oracle,signature,[stock[key]])
        for signature,key in [('usdgFeed()','usdgFeed'),('calendar()','calendar')]:eq(oracle,signature,[book[key]])
        eq(book['v3Factory'],'getPool(address,address,uint24)',[pool],token,book['usdg'],stock['fee'])
        eq(pool,'liquidity()',[stock['liquidity']])
        slot=read(pool,'slot0()');assert slot[3]>=720 and slot[4]>=720
    book['codeHashes']={}
    for key in (*CORE,'poolManager','weth','usdg','usdgFeed','calendar','v3Factory','market'):
        runtime=rpc('eth_getCode',[book[key],tag]);assert runtime!='0x'
        book['codeHashes'][key]=cast('keccak',runtime)
    assert rpc('eth_getBlockByNumber',[tag,False])['hash']==head['hash']
    book['broadcast']=True
    book['verification']={'blockNumber':int(tag,16),'blockHash':head['hash'],'transactionHashes':hashes,
                          'nonceStart':nonces[0],'nonceEnd':nonces[-1],'receipts':receipts,
                          'gasPaidWei':str(sum(int(x['gasUsed'],16)*int(x['effectiveGasPrice'],16) for x in receipts)),
                          'verifiedAt':datetime.datetime.now(datetime.timezone.utc).isoformat(),
                          'candidateSha256':hashlib.sha256(a.candidate.read_bytes()).hexdigest()}
    a.out.parent.mkdir(parents=True,exist_ok=True)
    a.out.write_text(json.dumps(book,indent=2)+'\n')
    print(json.dumps({'verified':True,'transactions':40,'factory':book['factory'],'owner':book['owner'],
                      'gasPaidWei':book['verification']['gasPaidWei'],'output':str(a.out)}))


if __name__=='__main__':
    main()

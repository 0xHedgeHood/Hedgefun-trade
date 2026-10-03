#!/usr/bin/env python3
"""Journaled, bounded testnet persona campaign. Encrypted keystores stay in .local/."""
import argparse
import hashlib
from decimal import Decimal, getcontext
import json
import os
from pathlib import Path
import secrets
import subprocess
import time

getcontext().prec = 70
ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'contractV2/deploy/persona-2026-10-03'
KEYS = ROOT / '.local/persona-wallets-20261003'
CAST = str(ROOT / '.local/bin/cast')
RPC = 'https://rpc.testnet.chain.robinhood.com'
BOOK = json.loads((ROOT / 'contractV2/deploy/testnet-v2-fresh-creator.json').read_text())
FACTORY, ROUTER, HOOK = [BOOK[k] for k in ('factory','tradeRouter','hook')]
USDG, STOCK, POOL = BOOK['usdg'], BOOK['stocks']['TSLA']['token'], BOOK['stocks']['TSLA']['pool']
OWNER = BOOK['owner']
QTYPE = '(string,string,address,address,uint16,uint16,uint32,uint32,uint16,uint16,uint16,uint16,uint96,uint256,uint256)'
PTYPE = '(uint256,address,uint256,uint256,uint256,uint256,uint8,bool)'
ROLES = ['sniper','opening_buyer','diamond_hands','paper_hands','kol','follower_1','follower_2','late_fomo']
STATE = OUT / 'journal.json'
OUT.mkdir(parents=True, exist_ok=True)
s = json.loads(STATE.read_text()) if STATE.exists() else {'chainId':46630,'factory':FACTORY,'router':ROUTER,
    'creator':OWNER,'symbol':'HFPERSONA','name':'Hedgefun Persona TSLA Test','creatorNonce':2026100303,
    'transactions':[],'actions':[],'samples':[],'wallets':{}}


def save():
    tmp = STATE.with_suffix('.tmp')
    tmp.write_text(json.dumps(s,indent=2)+'\n'); tmp.replace(STATE)


class RpcError(Exception):
    def __init__(self,error):
        self.error=error
        super().__init__(str(error))


def rpc(method,params):
    body=json.dumps({'jsonrpc':'2.0','id':1,'method':method,'params':params})
    for attempt in range(5):
        out=subprocess.run(['curl','--fail','--silent','--show-error','--max-time','30',
            '-H','Content-Type: application/json','--data-binary','@-',RPC],input=body,text=True,capture_output=True,check=True)
        value=json.loads(out.stdout)
        if 'error' not in value:return value['result']
        error=value['error']
        # The public load balancer can briefly route a pinned-head read to a lagging node.
        # Retry reads only; never resend signed transactions or suppress contract reverts.
        if method in ('eth_call','eth_getBlockByNumber','eth_getBalance','eth_getCode') and 'unsupported block number' in error.get('message','') and attempt<4:
            time.sleep(.5);continue
        raise RpcError(error)


def cast(*args):
    return subprocess.check_output([CAST,*map(str,args)],text=True,timeout=90).strip()


def words(raw):
    assert raw.startswith('0x') and (len(raw)-2)%64==0
    return [int(raw[i:i+64],16) for i in range(2,len(raw),64)]


def addr(value): return f'0x{value:040x}'
def who(role): return OWNER if role=='owner' else s['wallets'][role]['address']
def read(target,sig,*args,role='owner',block='latest'):
    return words(rpc('eth_call',[{'from':who(role),'to':target,'data':cast('calldata',sig,*args)},block]))
def balance(token,role,block='latest'): return read(token,'balanceOf(address)',who(role),block=block)[0]
def done(label):
    rows=[x for x in s['transactions'] if x['label']==label]
    if rows:
        assert len(rows)==1 and rows[0]['state']=='confirmed', 'Uncertain transaction; inspect before retrying'
        return rows[0]


def signer(role):
    if role=='owner': return ROOT/'.local/testnet-wallet-20261003/hedgefun-testnet',ROOT/'.local/testnet-wallet-20261003/password'
    return KEYS/role,KEYS/'password'


def send(label,role,target,sig=None,args=(),value=0):
    previous=done(label)
    if previous: return previous['receipt']
    assert int(rpc('eth_chainId',[]),16)==46630
    wallet=who(role)
    data=cast('calldata',sig,*args) if sig else '0x'
    nonce=int(rpc('eth_getTransactionCount',[wallet,'pending']),16)
    key='owner' if role=='owner' else role
    expected=s['initialNonces'][key]+sum(x['role']==role for x in s['transactions'])
    assert nonce==expected, f'Unexpected {role} nonce: {nonce}, expected {expected}'
    tx={'from':wallet,'to':target,'data':data,'value':hex(value)}
    result=rpc('eth_call',[tx,'latest'])
    gas=int(rpc('eth_estimateGas',[tx]),16)
    limit=(gas*130+99)//100
    price=int(rpc('eth_gasPrice',[]),16)*2
    assert price<=100_000_000 and limit<=25_000_000
    native=int(rpc('eth_getBalance',[wallet,'latest']),16)
    assert native>value+limit*price, f'{role} gas balance too low'
    if role=='owner':
        assert s['ownerInitialNativeWei']-native+value+limit*price<=1_500_000_000_000_000
    assert value==0 or (role=='owner' and label.startswith('fund-') and 0<value<=150_000_000_000_000)
    row={'label':label,'role':role,'from':wallet,'to':target,'data':data,'valueWei':value,
         'nonce':nonce,'gasLimit':limit,'gasPriceWei':price,'simulationResult':result,'state':'sending'}
    s['transactions'].append(row);save()
    print(f'Signing {label}: {role} nonce={nonce}',flush=True)
    keyfile,password=signer(role)
    call_args=[sig,*args] if sig else []
    h=cast('send',target,*call_args,'--value',value,'--rpc-url',RPC,'--chain','46630',
           '--legacy','--nonce',nonce,'--gas-limit',limit,'--gas-price',price,
           '--keystore',keyfile,'--password-file',password,'--async')
    assert h.startswith('0x') and len(h)==66
    row.update(hash=h,state='submitted');save()
    end=time.monotonic()+100
    while time.monotonic()<end:
        receipt=rpc('eth_getTransactionReceipt',[h])
        if receipt: break
        time.sleep(.5)
    else: raise RuntimeError(f'Pending {h}; do not resend')
    row['receipt']=receipt;save()
    assert int(receipt['status'],16)==1, f'Reverted: {h}'
    row.update(state='confirmed');save()
    print(f'Confirmed {label}: {h}',flush=True)
    return receipt


def prepare_keys():
    KEYS.mkdir(parents=True,exist_ok=True,mode=0o700);KEYS.chmod(0o700)
    password=KEYS/'password'
    if not password.exists():
        fd=os.open(password,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'w') as f:f.write(secrets.token_urlsafe(40))
    password.chmod(0o600)
    for role in ROLES:
        path=KEYS/role
        if not path.exists():
            # Secret is confined to this child environment, never CLI arguments or logs.
            env=os.environ.copy();env['CAST_PASSWORD']=password.read_text()
            subprocess.run([CAST,'wallet','new',str(KEYS),role],env=env,capture_output=True,text=True,check=True)
            env.pop('CAST_PASSWORD',None)
        path.chmod(0o600)
        address=cast('wallet','address','--keystore',path,'--password-file',password)
        assert role not in s['wallets'] or s['wallets'][role]['address'].lower()==address.lower()
        s['wallets'][role]={'address':address,'role':role};save()
    assert len({who(r).lower() for r in ROLES})==8 and OWNER.lower() not in {who(r).lower() for r in ROLES}
    if 'initialNonces' not in s:
        s['initialNonces']={r:int(rpc('eth_getTransactionCount',[who(r),'pending']),16) for r in ['owner']+ROLES}
        assert all(s['initialNonces'][r]==0 for r in ROLES)
        s['ownerInitialNativeWei']=int(rpc('eth_getBalance',[OWNER,'latest']),16)
        s['startBlock']=int(rpc('eth_blockNumber',[]),16);save()
    (OUT/'wallets.json').write_text(json.dumps(s['wallets'],indent=2)+'\n')
    print('8 encrypted persona wallets ready; public addresses saved.',flush=True)


def setup(plan):
    assert BOOK['broadcast'] and read(FACTORY,'owner()')[0]==int(OWNER,16)
    assert read(ROUTER,'factory()')[0]==int(FACTORY,16)
    prepare_keys()
    for role in ROLES:
        actor=next(x for x in plan['actors'] if x['id']==role)
        send('fund-'+role,'owner',who(role),value=int(actor['native_funding_wei']))
        send('drip-stock-'+role,role,STOCK,'drip()')
        send('approve-stock-'+role,role,STOCK,'approve(address,uint256)',(ROUTER,15*10**18))
        usdg=sum(int(x.get('amount_in',0)) for x in plan['actions'] if x.get('actor')==role and x.get('asset')=='USDG' and x['action']=='buy')
        if usdg:
            send('drip-usdg-'+role,role,USDG,'drip()')
            send('approve-usdg-'+role,role,USDG,'approve(address,uint256)',(ROUTER,usdg))
    if 'fundedBalances' not in s:
        block=rpc('eth_blockNumber',[])
        s['fundedBalances']={r:{'stock':balance(STOCK,r,block),'usdg':balance(USDG,r,block),
             'nativeWei':int(rpc('eth_getBalance',[who(r),block]),16)} for r in ROLES}
        s['fundingBlock']=int(block,16);save()
    print('All actor funding, stock drips and finite approvals confirmed.',flush=True)


def launch(plan):
    if 'curve' in s and done('launch'): return
    defaults=read(FACTORY,'getDefaults()')
    listing=read(FACTORY,'listings(address)',STOCK)
    assert read(FACTORY,'publicLaunch()')[0] and defaults[-2:]==[2,25_000_000] and listing[-1]==1
    p=plan['launch']
    assert p['creator'].lower()==OWNER.lower() and p['stock'].lower()==STOCK.lower()
    s.update(name=p['name'],symbol=p['symbol'],creatorNonce=int(p['nonce']),launchProfile=p);save()
    assert p['curve']=={'saleBps':4000,'snipeSeconds':180} and defaults[10]==9900
    send('curve-config','owner',BOOK['curveDeployer'],'setCurveConfig(string,uint96,uint16,uint8)',(s['symbol'],s['creatorNonce'],p['curve']['saleBps'],p['curve']['snipeSeconds']))
    send('approve-launch','owner',USDG,'approve(address,uint256)',(FACTORY,25_000_000))
    fields=','.join(str(p[k]) for k in ('taxBps','creatorBps','tp1Bps','tp2Bps','dipBps','stopBps','lotBps','bandBpsPerHour'))
    q=f'("{s["name"]}","{s["symbol"]}",{STOCK},{OWNER},{fields},{s["creatorNonce"]},25000000,{listing[2]})'
    token,treasury,terms=read(FACTORY,f'predict({QTYPE})',q)
    s.update(token=addr(token),treasury=addr(treasury),curve=addr(read(FACTORY,f'predictCurve({QTYPE})',q)[0]),
             request=q,terms=f'0x{terms:064x}')
    save()
    receipt=send('launch','owner',FACTORY,f'launch({QTYPE},bytes32)',(q,s['terms']))
    topic=cast('keccak','Launched(uint256,string,address,address,address,address,address)')
    event=next(x for x in receipt['logs'] if x['address'].lower()==FACTORY.lower() and x['topics'][0]==topic)
    s['strategyId']=int(event['topics'][1],16)
    s['launchedAt']=read(s['curve'],'launchedAt()')[0]
    assert read(s['curve'],'snipeSeconds()')[0]==180
    assert all(read(s['curve'],'isOpeningTaxExempt(address)',who(r))[0]==0 for r in ROLES)
    key=read(FACTORY,'graduationConfig(uint256)',s['strategyId'])[:5]
    s['poolKey']={'currency0':addr(key[0]),'currency1':addr(key[1]),'fee':key[2],'tickSpacing':key[3],'hooks':addr(key[4])}
    s['poolId']=cast('keccak','0x'+''.join(f'{v:064x}' for v in key));save()
    print(f'Launched {s["symbol"]} id={s["strategyId"]}; opening window ends {s["launchedAt"]+180}',flush=True)


def timestamp(): return int(rpc('eth_getBlockByNumber',['latest',False])['timestamp'],16)


def record_price(label,block='latest'):
    blockrow=rpc('eth_getBlockByNumber',[block,False]);block=blockrow['number']
    stage=read(s['curve'],'status()',block=block)[0]
    sqrt=read(POOL,'slot0()',block=block)[0]
    ratio=Decimal(sqrt)**2/Decimal(2**192)
    stock0=read(POOL,'token0()',block=block)[0]==int(STOCK,16)
    stock_usdg=(ratio if stock0 else 1/ratio)*Decimal(10**12)
    if stage==0:
        real=read(s['curve'],'realStockReserve()',block=block)[0]
        virtual=read(s['curve'],'virtualStock()',block=block)[0]
        tokenreserve=read(s['curve'],'tokenReserve()',block=block)[0]
        stock_fun=Decimal(real+virtual)/Decimal(tokenreserve)
    else:
        slot=cast('keccak',s['poolId']+f'{6:064x}')
        v4sqrt=read(BOOK['poolManager'],'extsload(bytes32)',slot,block=block)[0]&((1<<160)-1)
        ratio4=Decimal(v4sqrt)**2/Decimal(2**192)
        stock_fun=ratio4 if s['poolKey']['currency0'].lower()==s['token'].lower() else 1/ratio4
    row={'label':label,'blockNumber':int(block,16),'timestamp':int(blockrow['timestamp'],16),
         'stage':stage,'stockPerFun':str(stock_fun),'usdgPerStock':str(stock_usdg),'usdgPerFun':str(stock_fun*stock_usdg),
         'tokenSupplyWei':str(read(s['token'],'totalSupply()',block=block)[0])}
    s['samples'].append(row);save();return row


def action_done(label): return next((x for x in s['actions'] if x['id']==label and x.get('verified')),None)


def trade(action):
    label,role,verb=action['id'],action['actor'],action['action']
    if action_done(label):return
    if done(label):raise RuntimeError('Confirmed trade needs postcondition recovery; do not repeat')
    stage=read(s['curve'],'status()')[0]
    assert stage==action['expected_stage'],(label,stage)
    elapsed=timestamp()-s['launchedAt']
    latest=action.get('latest_elapsed_seconds')
    if latest is not None: assert elapsed<=latest, f'{label}: opening window missed; do not relabel a late buy'
    buying=verb=='buy'
    asset=USDG if action.get('asset')=='USDG' else STOCK
    amount=int(action['amount_in']) if buying else balance(s['token'],role)*action['sell_fraction_bps']//10000
    assert amount>0
    if not buying:send('approve-'+label,role,s['token'],'approve(address,uint256)',(ROUTER,amount))
    deadline=timestamp()+300
    path=f'[({POOL},{STOCK})]' if buying and asset==USDG else '[]'
    sig=f'{verb}({PTYPE},(address,address)[])'
    def params(ms,mo):return f'({s["strategyId"]},{asset if buying else STOCK},{amount},{ms},{mo},{deadline},{stage},{str(action.get("allow_partial_fill",False)).lower()})'
    minstock=amount if buying else 0
    if buying and asset==USDG:
        try:read(ROUTER,sig,params(2**128,1),path,role=role)
        except RpcError as e:
            raw=e.error.get('data','');assert raw.startswith('0xbd392856') and len(raw)==74,e
            minstock=int(raw[10:],16)*99//100
        else:raise AssertionError('Missing stock minimum revert')
    quote=read(ROUTER,sig,params(minstock,1),path,role=role)
    minimum=quote[0]*99//100;assert minimum>0
    before_block=rpc('eth_blockNumber',[])
    before={name:balance(token,role,before_block) for name,token in [('stock',STOCK),('usdg',USDG),('fun',s['token'])]}
    row={'id':label,'role':role,'phase':action['phase'],'action':verb,'amountIn':str(amount),
         'expectedStage':stage,'quoteOut':str(quote[0]),'quoteRefund':str(quote[1]),'minimumOut':str(minimum),'before':before,
         'beforeBlock':int(before_block,16)}
    if buying and stage==0:
        row['quoteRateBps']=read(s['curve'],'buyRateBpsFor(address)',who(role))[0]
        row['creatorRateBps']=read(s['curve'],'buyRateBpsFor(address)',OWNER)[0]
    s['actions'].append(row);save()
    receipt=send(label,role,ROUTER,sig,(params(minstock,minimum),path))
    block=receipt['blockNumber']
    after={name:balance(token,role,block) for name,token in [('stock',STOCK),('usdg',USDG),('fun',s['token'])]}
    if buying:
        assert after['fun']-before['fun']>=minimum
        if asset==STOCK:assert 0<before['stock']-after['stock']<=amount and before['usdg']==after['usdg']
        else:assert before['usdg']-after['usdg']==amount and after['stock']>=before['stock']
    else:assert before['fun']-after['fun']==amount and after['stock']-before['stock']>=minimum
    actual_timestamp=int(rpc('eth_getBlockByNumber',[block,False])['timestamp'],16)
    row.update(after=after,receiptHash=receipt['transactionHash'],timestamp=actual_timestamp)
    actual_elapsed=actual_timestamp-s['launchedAt'];row['elapsedSeconds']=actual_elapsed
    if latest is not None and actual_elapsed>latest:
        row.update(verified=False,openingDeadlineMissed=True);save()
        raise RuntimeError(f'{label} confirmed after opening deadline; preserved actual result')
    earliest=action.get('earliest_elapsed_seconds')
    assert earliest is None or actual_elapsed>=earliest
    if buying and stage==0:
        row['actualRateBps']=read(s['curve'],'buyRateBpsFor(address)',who(role),block=block)[0]
        assert row['actualRateBps']>=300
        if action['phase']=='opening':assert row['actualRateBps']>300
    if action['phase']=='graduate':
        assert read(s['curve'],'status()',block=block)[0]==2 and quote[1]>0
        assert after['stock']>before['stock']-amount
    row['verified']=True
    save();record_price(label,block)
    print(f'Verified {label}; stage={read(s["curve"],"status()")[0]}',flush=True)


def phase(plan,name):
    if name=='opening':launch(plan)
    for action in plan['actions']:
        if action['phase']!=name or action_done(action['id']):continue
        earliest=action.get('earliest_elapsed_seconds')
        if earliest is not None and timestamp()-s['launchedAt']<earliest:
            print(f'WAIT {action["id"]}: earliest elapsed {earliest}s',flush=True)
            while timestamp()-s['launchedAt']<earliest:time.sleep(1)
        if action['action'] in ('buy','sell'):trade(action)
        elif action['action'] in ('signal','hold'):
            s['actions'].append({'id':action['id'],'role':action['actor'],'phase':name,'action':action['action'],
                'timestamp':timestamp(),'note':'Internal scenario record only; no external social post','verified':True});save()
        elif action['action']=='probe':
            probe=action['probe'];stage=read(s['curve'],'status()')[0]
            quoted_stage=0 if probe=='stale_curve_stage' else stage
            deadline=0 if probe=='expired_deadline' else timestamp()+300
            minimum=2**255 if probe=='impossible_min_final_out' else 1
            params=f'({s["strategyId"]},{STOCK},{10**15},0,{minimum},{deadline},{quoted_stage},true)'
            try:read(ROUTER,f'buy({PTYPE},(address,address)[])',params,'[]',role=action['actor'])
            except RpcError as e:
                raw=e.error.get('data','');assert isinstance(raw,str) and raw.startswith('0x')
                if probe=='expired_deadline':assert raw==cast('sig','Expired()')
                if probe=='stale_curve_stage':assert raw==cast('sig','StageChanged(uint8)')+f'{2:064x}'
                if probe=='impossible_min_final_out':assert raw[:10] in (cast('sig','Slippage()'),cast('sig','TooLittle(uint256)'))
                s['actions'].append({'id':action['id'],'role':action['actor'],'phase':name,'action':'probe',
                    'probe':probe,'mode':'eth_call only','revertData':raw,'verified':True});save()
                print(f'Verified quote-only rejection {probe}',flush=True)
            else:raise AssertionError('Invalid quote accepted')
        else:raise ValueError(action['action'])


def finish():
    plan=json.loads((OUT/'plan.json').read_text())
    assert all(action_done(x['id']) for x in plan['actions']), 'Finish requires every planned trade/probe/signal/hold'
    assert read(s['curve'],'status()')[0]==2
    s.setdefault('liquidationQuotes',{})
    for role in ROLES:
        if role in s['liquidationQuotes']:continue
        amount=balance(s['token'],role)
        if amount:
            send('quote-approve-'+role,role,s['token'],'approve(address,uint256)',(ROUTER,amount))
            block=rpc('eth_blockNumber',[])
            deadline=int(rpc('eth_getBlockByNumber',[block,False])['timestamp'],16)+300
            params=f'({s["strategyId"]},{STOCK},{amount},0,1,{deadline},2,true)'
            quote=read(ROUTER,f'sell({PTYPE},(address,address)[])',params,'[]',role=role,block=block)
            s['liquidationQuotes'][role]={'block':int(block,16),'funInput':str(amount),'stockOut':str(quote[0]),
                'funRefund':str(quote[1]),'mode':'eth_call only; independent liquidation, not simultaneously executable'}
        else:s['liquidationQuotes'][role]={'funInput':'0','stockOut':'0','funRefund':'0'}
        save()
    for role in ROLES:
        for token,name in [(STOCK,'stock'),(USDG,'usdg'),(s['token'],'fun')]:
            if read(token,'allowance(address,address)',who(role),ROUTER)[0]:
                send('clear-'+name+'-'+role,role,token,'approve(address,uint256)',(ROUTER,0))
    block=rpc('eth_blockNumber',[])
    s['finalBalances']={r:{'stock':balance(STOCK,r,block),'usdg':balance(USDG,r,block),
        'fun':balance(s['token'],r,block),'nativeWei':int(rpc('eth_getBalance',[who(r),block]),16)} for r in ROLES}
    s['finalBlock']=int(block,16)
    s['treasuryBalances']={k:read(s['treasury'],k+'()',block=block)[0] for k in ('bookedStock','buybackStock','unbookedStock')}
    s['treasuryBalances']['heldStock']=read(STOCK,'balanceOf(address)',s['treasury'],block=block)[0]
    s['feesAtFinal']={'curveStockClaimableTotal':str(read(s['curve'],'totalFees()',block=block)[0]),
        'hookAccrued':list(map(str,read(HOOK,'accrued(bytes32)',s['poolId'],block=block))),
        'hookPendingTokenFees':str(read(HOOK,'pendingTokenFees(bytes32)',s['poolId'],block=block)[0]),
        'note':'Fee settlement not executed in persona campaign; preserves post-trader price observation.'}
    for token in (STOCK,USDG,s['token']):assert read(token,'balanceOf(address)',ROUTER,block=block)[0]==0
    save();record_price('final',block)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('phase',choices=['keys','setup','opening','after_window','graduate','post_graduation','finish'])
    a=p.parse_args()
    from persona_scenarios import build_plan
    plan=build_plan(seed=20261003)
    assert plan['chain_id']==46630 and plan['test_only']
    assert plan['deployment']['factory'].lower()==FACTORY.lower() and plan['deployment']['tradeRouter'].lower()==ROUTER.lower()
    assert plan['assets']['STOCK']['address'].lower()==STOCK.lower() and plan['assets']['USDG']['address'].lower()==USDG.lower()
    plan_hash=hashlib.sha256(json.dumps(plan,sort_keys=True).encode()).hexdigest()
    if a.phase!='keys':
        assert s.get('planSha256',plan_hash)==plan_hash,'Plan changed after campaign started'
        s['planSha256']=plan_hash;save()
    (OUT/'plan.json').write_text(json.dumps(plan,indent=2)+'\n')
    if a.phase=='keys':prepare_keys()
    elif a.phase=='setup':setup(plan)
    elif a.phase=='finish':finish()
    else:phase(plan,a.phase)


if __name__=='__main__':main()

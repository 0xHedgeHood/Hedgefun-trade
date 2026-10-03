#!/usr/bin/env python3
"""Independently audit public persona receipts and pinned-block balances/prices.

Read-only RPC only: no broadcaster import, keystore access, signing, or state
overrides. The final liquidation quotes are hypothetical at the final block;
they do not model all holders liquidating into the same pool simultaneously.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal, getcontext, localcontext
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parents[2]
CAST = ROOT / '.local/bin/cast'
DEFAULT_OUT = ROOT / 'contractV2/deploy/persona-2026-10-03'
RPC = 'https://rpc.testnet.chain.robinhood.com'
LOGS_RPC = None
PTYPE = '(uint256,address,uint256,uint256,uint256,uint256,uint8,bool)'
ZERO = '0x' + '0' * 40
getcontext().prec = 90


class RpcError(RuntimeError):
    def __init__(self, error):
        self.error = error
        super().__init__(str(error))


def history_unavailable(error):
    message = str(error.error.get('message', '')).lower()
    return 'historical state' in message and 'not available' in message


def rpc(method, params, *, url=None):
    request = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params})
    for attempt in range(6):
        result = subprocess.run(
            ['curl', '--fail', '--silent', '--show-error', '--max-time', '30',
             '-H', 'Content-Type: application/json', '--data-binary', '@-', url or RPC],
            input=request, text=True, capture_output=True)
        if result.returncode:
            if result.returncode in (6, 7, 18, 28, 35, 52, 55, 56) and attempt < 5:
                time.sleep((attempt + 1) * .3)
                continue
            raise subprocess.CalledProcessError(result.returncode, result.args, result.stdout, result.stderr)
        data = json.loads(result.stdout)
        if 'error' not in data:
            return data['result']
        message = str(data['error'].get('message', '')).lower()
        # The public RPC occasionally routes a recent historical read to a node
        # that has not made that state available. Retry the identical request;
        # never replace a pinned historical block with latest.
        if 'historical state' in message and 'not available' in message and attempt < 5:
            time.sleep((attempt + 1) * .3)
            continue
        raise RpcError(data['error'])
    raise AssertionError('Unreachable RPC retry state')


@lru_cache(maxsize=None)
def cast(*args):
    return subprocess.check_output([str(CAST), *map(str, args)], text=True, timeout=60).strip()


def words(raw):
    assert raw.startswith('0x') and (len(raw) - 2) % 64 == 0
    return [int(raw[i:i + 64], 16) for i in range(2, len(raw), 64)]


def address(value):
    return f'0x{value:040x}'


def tag(block):
    return hex(block) if isinstance(block, int) else block


@lru_cache(maxsize=None)
def block_at(block):
    value = rpc('eth_getBlockByNumber', [tag(block), False])
    assert value is not None
    return value


@lru_cache(maxsize=None)
def read(target, signature, args=(), block='latest', sender=None):
    tx = {'to': target, 'data': cast('calldata', signature, *args)}
    if sender:
        tx['from'] = sender
    return words(rpc('eth_call', [tx, tag(block)]))


def uint(target, signature, args=(), block='latest'):
    return read(target, signature, args, block)[0]


def balance(token, wallet, block):
    return uint(token, 'balanceOf(address)', (wallet,), block)


def event(receipt, emitter, signature, *, count=1):
    topic = cast('keccak', signature).lower()
    found = [x for x in receipt['logs'] if x['address'].lower() == emitter.lower()
             and x['topics'][0].lower() == topic]
    if count is not None:
        assert len(found) == count, (signature, len(found), receipt['transactionHash'])
    return found


def transfer_delta(receipt, token, wallet):
    result = 0
    for log in event(receipt, token, 'Transfer(address,address,uint256)', count=None):
        amount = words(log['data'])[0]
        sender, recipient = [address(int(x, 16)) for x in log['topics'][1:3]]
        if sender.lower() == wallet.lower():
            result -= amount
        if recipient.lower() == wallet.lower():
            result += amount
    return result


def opening_rate(elapsed, base, start, window):
    if not window or start <= base or elapsed >= window:
        return base
    assert elapsed >= 0
    return base + ((start - base) * (window - elapsed) + window - 1) // window


def ceildiv(a, b):
    return (a + b - 1) // b


def decimal_close(a, b):
    a, b = Decimal(a), Decimal(b)
    assert abs(a - b) <= max(abs(a), abs(b), Decimal(1)) * Decimal('1e-60'), (a, b)


def reject_call(to, signature, args, sender, block, prefix):
    try:
        read(to, signature, args, block, sender)
    except RpcError as error:
        if history_unavailable(error):
            raise
        raw = error.error.get('data', '')
        assert isinstance(raw, str) and raw.lower().startswith(prefix.lower()), error
        return {'mode': 'historical eth_call only', 'blockNumber': block,
                'revertData': raw, 'verified': True}
    raise AssertionError('Invalid quote unexpectedly accepted')


def verify(directory):
    state_path, plan_path = directory / 'journal.json', directory / 'plan.json'
    state, plan = json.loads(state_path.read_text()), json.loads(plan_path.read_text())
    book = json.loads((ROOT / 'contractV2/deploy/testnet-v2-fresh-creator.json').read_text())
    assert state['chainId'] == plan['chain_id'] == book['chainId'] == 46630
    assert int(rpc('eth_chainId', []), 16) == 46630
    assert plan['test_only'] and book['broadcast']
    assert state['planSha256'] == hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()
    assert book['featureVersion'] == 'v2-creator-selected-fresh-wallet-v1'
    factory, router, hook = [book[k] for k in ('factory', 'tradeRouter', 'hook')]
    stock, usdg, v3 = book['stocks']['TSLA']['token'], book['usdg'], book['stocks']['TSLA']['pool']
    token, curve = state['token'], state['curve']
    treasury = state.get('treasuryAddress', state['treasury'])
    assert isinstance(treasury, str), 'Treasury address must remain distinct from its balance snapshot'
    owner = book['owner']
    assert state['factory'].lower() == factory.lower() and state['router'].lower() == router.lower()
    assert state['creator'].lower() == owner.lower()
    assert state['name'] == plan['launch']['name'] and state['symbol'] == plan['launch']['symbol']
    assert state['creatorNonce'] == int(plan['launch']['nonce'])
    actors = {x['id']: x for x in plan['actors']}
    wallets = {role: x['address'] for role, x in state['wallets'].items()}
    assert set(wallets) == set(actors) and len({x.lower() for x in wallets.values()}) == 8
    assert owner.lower() not in {x.lower() for x in wallets.values()}
    assert plan['assets']['STOCK']['address'].lower() == stock.lower()
    assert plan['assets']['USDG']['address'].lower() == usdg.lower()
    final_block, funded_block = state['finalBlock'], state['fundingBlock']
    assert final_block >= funded_block and int(rpc('eth_blockNumber', []), 16) >= final_block + 2
    if LOGS_RPC:
        assert int(rpc('eth_chainId', [], url=LOGS_RPC), 16) == 46630
        assert rpc('eth_getBlockByNumber', [tag(final_block), False], url=LOGS_RPC)['hash'] == block_at(final_block)['hash']
    identity = read(factory, 'strategies(uint256)', (state['strategyId'],), final_block)
    assert [x.lower() for x in (token, treasury, hook, stock, owner)] == [address(x).lower() for x in identity]
    assert uint(factory, 'curves(uint256)', (state['strategyId'],), final_block) == int(curve, 16)
    assert uint(curve, 'status()', block=final_block) == 2
    assert uint(stock, 'decimals()', block=final_block) == 18
    assert uint(token, 'decimals()', block=final_block) == 18
    assert uint(usdg, 'decimals()', block=final_block) == 6
    assert uint(curve, 'isOpeningTaxExempt(address)', (owner,), final_block) == 1
    assert all(uint(curve, 'isOpeningTaxExempt(address)', (wallet,), final_block) == 0
               for wallet in wallets.values())
    launch_at = uint(curve, 'launchedAt()', block=final_block)
    assert launch_at == state['launchedAt']
    base, snipe, window, split_protocol, split_creator = [uint(curve, name + '()', block=final_block)
        for name in ('taxBps', 'snipeBps', 'snipeSeconds', 'protocolBps', 'creatorBps')]
    assert base == plan['launch']['taxBps'] == 300 and snipe == plan['execution']['expected_snipe_bps']
    assert window == plan['launch']['curve']['snipeSeconds'] == 180
    assert split_creator == plan['launch']['creatorBps']
    treasury_abi = json.loads((ROOT / 'contractV2/out/HedgeFunV2Treasury.sol/HedgeFunV2Treasury.json').read_text())['abi']
    components = next(x for x in treasury_abi if x.get('name') == 'params')['outputs'][0]['components']
    params = dict(zip((c['name'] for c in components), read(treasury, 'params()', block=final_block)))
    for name in ('tp1Bps', 'tp2Bps', 'dipBps', 'stopBps', 'lotBps', 'bandBpsPerHour'):
        assert params[name] == plan['launch'][name]

    transactions = state['transactions']
    assert len({x['label'] for x in transactions}) == len(transactions)
    assert len({x['hash'].lower() for x in transactions}) == len(transactions)
    expected_nonce = dict(state['initialNonces'])
    all_receipts, gas_by_role, gas_after_funding = {}, {}, {}
    transaction_rows = []

    def canonical(row):
        assert row['state'] == 'confirmed'
        receipt = rpc('eth_getTransactionReceipt', [row['hash']])
        tx = rpc('eth_getTransactionByHash', [row['hash']])
        assert receipt and tx and int(receipt['status'], 16) == 1
        block = block_at(receipt['blockNumber'])
        assert receipt['blockHash'] == tx['blockHash'] == block['hash'] == row['receipt']['blockHash']
        assert receipt['transactionHash'].lower() == tx['hash'].lower() == row['hash'].lower()
        assert receipt['transactionIndex'] == tx['transactionIndex']
        assert int(receipt['blockNumber'], 16) <= final_block
        wallet = owner if row['role'] == 'owner' else wallets[row['role']]
        assert tx['from'].lower() == row['from'].lower() == wallet.lower()
        assert tx['to'].lower() == row['to'].lower()
        assert tx['input'].lower() == row['data'].lower()
        assert int(tx['nonce'], 16) == row['nonce'] and int(tx['chainId'], 16) == 46630
        assert int(tx['value'], 16) == int(row['valueWei'])
        assert int(tx['gas'], 16) == row['gasLimit']
        if int(tx['value'], 16):
            actor = row['label'].removeprefix('fund-')
            assert row['role'] == 'owner' and row['label'].startswith('fund-')
            assert tx['to'].lower() == wallets[actor].lower()
            assert int(tx['value'], 16) == int(actors[actor]['native_funding_wei'])
        return row, receipt, block

    with ThreadPoolExecutor(max_workers=6) as executor:
        verified_transactions = list(executor.map(canonical, transactions))
    for row, receipt, block in verified_transactions:
        role = row['role']
        assert row['nonce'] == expected_nonce[role]
        expected_nonce[role] += 1
        all_receipts[row['label']] = receipt
        paid = int(receipt['gasUsed'], 16) * int(receipt['effectiveGasPrice'], 16)
        gas_by_role[role] = gas_by_role.get(role, 0) + paid
        if int(receipt['blockNumber'], 16) > funded_block:
            gas_after_funding[role] = gas_after_funding.get(role, 0) + paid
        transaction_rows.append({'label': row['label'], 'role': role, 'hash': row['hash'],
            'blockNumber': int(receipt['blockNumber'], 16), 'timestamp': int(block['timestamp'], 16),
            'nonce': row['nonce'], 'gasPaidWei': str(paid), 'status': 1})
    for role, nonce in expected_nonce.items():
        wallet = owner if role == 'owner' else wallets[role]
        assert int(rpc('eth_getTransactionCount', [wallet, tag(final_block)]), 16) == nonce

    abandoned_launches = []
    for abandoned in state.get('abandonedLaunches', []):
        old_curve, old_token = abandoned['curve'], abandoned['token']
        assert old_curve.lower() != curve.lower() and old_token.lower() != token.lower()
        old_rows = [r for r in transactions if r['hash'].lower() == abandoned['launchHash'].lower()]
        assert len(old_rows) == 1 and old_rows[0]['label'].startswith('aborted-initial-')
        old_receipt = all_receipts[old_rows[0]['label']]
        old_block = int(old_receipt['blockNumber'], 16)
        old_id = abandoned['strategyId']
        old_identity = read(factory, 'strategies(uint256)', (old_id,), final_block)
        assert old_identity[0] == int(old_token, 16) and old_identity[1] == int(abandoned['treasury'], 16)
        assert old_identity[3] == int(stock, 16) and old_identity[4] == int(owner, 16)
        assert uint(factory, 'curves(uint256)', (old_id,), final_block) == int(old_curve, 16)
        assert uint(old_curve, 'launchedAt()', block=final_block) == abandoned['launchedAt']
        assert uint(old_curve, 'status()', block=final_block) == 0
        assert uint(old_curve, 'realStockReserve()', block=final_block) == 0
        assert uint(old_curve, 'totalFees()', block=final_block) == 0
        old_supply = uint(old_curve, 'initialSupply()', block=final_block)
        assert uint(old_curve, 'tokenReserve()', block=final_block) == old_supply
        assert uint(old_token, 'totalSupply()', block=final_block) == old_supply
        assert balance(old_token, old_curve, final_block) == old_supply
        trade_topics = [cast('keccak', signature) for signature in (
            'Bought(address,address,uint256,uint256,uint256)',
            'Sold(address,address,uint256,uint256,uint256)')]
        for start in range(old_block, final_block + 1, 1000):
            assert rpc('eth_getLogs', [{'address': old_curve, 'fromBlock': hex(start),
                'toBlock': hex(min(start + 999, final_block)), 'topics': [trade_topics]}], url=LOGS_RPC) == []
        reason = abandoned['reason']
        assert reason['broadcast'] is False
        old_input = int(reason['inputStockWei'])
        old_deadline = int(block_at(old_block)['timestamp'], 16) + 300
        old_params = f'({old_id},{stock},{old_input},0,1,{old_deadline},0,false)'
        refusal = reject_call(router, f'buy({PTYPE},(address,address)[])',
            (old_params, '[]'), wallets['sniper'], old_block, cast('sig', 'PartialFill(uint256)'))
        accepted_params = f'({old_id},{stock},{old_input},0,1,{old_deadline},0,true)'
        accepted = read(router, f'buy({PTYPE},(address,address)[])',
                        (accepted_params, '[]'), old_block, wallets['sniper'])
        assert accepted[0] > 0 and accepted[1] == int(reason['quoteWithPartialFill'][1]) == 1
        abandoned_launches.append({**abandoned, 'independentVerification': {
            'launchTransactionStatus': 1, 'launchBlock': old_block, 'checkedThroughBlock': final_block,
            'curveStatus': 0, 'noCurveTradeEvents': True, 'fullSupplyStillInCurve': True,
            'roundingRefundWei': str(accepted[1]), 'quoteOnlyRefusal': refusal,
            'classification': 'Successful launch retained on chain; no persona trades; superseded by a fresh opening window.'}})

    assets = {'stock': stock, 'usdg': usdg, 'fun': token}
    ledger = {}
    cost_basis = {role: Decimal(0) for role in actors}
    realized = {role: Decimal(0) for role in actors}
    for role, actor in actors.items():
        wallet = wallets[role]
        ledger[role] = {'stock': int(actor['funding']['stock_amount']),
                        'usdg': int(actor['funding']['usdg_amount']), 'fun': 0}
        for name in ('stock', 'usdg'):
            initial = balance(assets[name], wallet, funded_block)
            assert initial == state['fundedBalances'][role][name] == ledger[role][name]
            if initial:
                drip = all_receipts['drip-' + name + '-' + role]
                assert transfer_delta(drip, assets[name], wallet) == initial
                minted = event(drip, assets[name], 'Transfer(address,address,uint256)')
                assert address(int(minted[0]['topics'][1], 16)) == ZERO
        initial_native = int(rpc('eth_getBalance', [wallet, tag(funded_block)]), 16)
        assert initial_native == state['fundedBalances'][role]['nativeWei']
        assert initial_native == int(actor['native_funding_wei']) - gas_by_role.get(role, 0) + gas_after_funding.get(role, 0)

    initial_supply = uint(curve, 'initialSupply()', block=final_block)
    virtual = uint(curve, 'virtualStock()', block=final_block)
    assert virtual == ceildiv(initial_supply * int(plan['launch']['expectedOpenPriceE18']), 10 ** 18)
    minimum_reserve = uint(curve, 'minTokenReserve()', block=final_block)
    terminal = uint(curve, 'terminalStock()', block=final_block)
    assert minimum_reserve == initial_supply * (10000 - plan['launch']['curve']['saleBps']) // 10000
    invariant = initial_supply * virtual
    assert terminal == ceildiv(invariant, minimum_reserve)
    token_reserve, stock_reserve = initial_supply, 0
    curve_fees, opening_burn, graduation_burn = 0, 0, 0
    details, graduation = [], None
    rows_by_id = {row['id']: row for row in state['actions']}
    assert len(rows_by_id) == len(state['actions'])
    plan_trades = [a for a in plan['actions'] if a['execution'] == 'broadcast']
    actual_trades = [r['id'] for r in state['actions'] if r['action'] in ('buy', 'sell')]
    assert actual_trades == [a['id'] for a in plan_trades]
    for action in plan_trades:
        label, role, verb = action['id'], action['actor'], action['action']
        row, receipt = rows_by_id[label], all_receipts[label]
        assert row['verified'] and row['role'] == role and row['action'] == verb
        block = int(receipt['blockNumber'], 16)
        elapsed = int(block_at(block)['timestamp'], 16) - launch_at
        for bound, lower in ((action.get('earliest_elapsed_seconds'), True),
                             (action.get('latest_elapsed_seconds'), False)):
            if bound is not None:
                assert elapsed >= bound if lower else elapsed <= bound, (label, elapsed, bound)
        txrow = next(r for r in transactions if r['label'] == label)
        assert txrow['to'].lower() == router.lower()
        data = txrow['data']
        assert data[:10].lower() == cast('sig', f'{verb}({PTYPE},(address,address)[])').lower()
        p = words('0x' + data[10:10 + 8 * 64])
        assert p[0] == state['strategyId'] and p[6] == action['expected_stage']
        assert p[7] == int(action['allow_partial_fill'])
        assert p[5] >= int(block_at(block)['timestamp'], 16)
        asset = usdg if verb == 'buy' and action['asset'] == 'USDG' else stock
        assert p[1] == int(asset, 16)
        amount = int(action['amount_in']) if verb == 'buy' else ledger[role]['fun'] * action['sell_fraction_bps'] // 10000
        assert p[2] == amount == int(row['amountIn']) and amount > 0
        assert p[4] == int(row['minimumOut']) and p[4] > 0
        assert row['before'] == ledger[role], (label, 'pretrade ledger mismatch')
        prior_fun = ledger[role]['fun']
        for name, asset_address in assets.items():
            ledger[role][name] += transfer_delta(receipt, asset_address, wallets[role])
            assert ledger[role][name] == row['after'][name]
        record = {'id': label, 'role': role, 'action': verb, 'phase': action['phase'],
                  'blockNumber': block, 'elapsedSeconds': elapsed, 'amountIn': str(amount)}
        if verb == 'buy':
            log = event(receipt, router, 'Bought(uint256,address,address,address,uint256,uint256,uint256)')[0]
            recipient, payment, out, refund = words(log['data'])
            assert recipient == int(wallets[role], 16) and payment == amount and out >= p[4]
            assert int(log['topics'][1], 16) == state['strategyId']
            assert int(log['topics'][2], 16) == int(wallets[role], 16)
            assert int(log['topics'][3], 16) == int(asset, 16)
            assert transfer_delta(receipt, token, wallets[role]) == out
            if asset.lower() == stock.lower():
                assert transfer_delta(receipt, stock, wallets[role]) == refund - amount
                assert transfer_delta(receipt, usdg, wallets[role]) == 0
            else:
                assert transfer_delta(receipt, usdg, wallets[role]) == -amount
                assert transfer_delta(receipt, stock, wallets[role]) == refund
            if not action['allow_partial_fill']:
                assert refund == 0
            record.update(tokensOut=str(out), stockRefund=str(refund))
            if asset.lower() == stock.lower():
                stock_payment = amount - refund
            else:
                received_stock = 0
                for transfer in event(receipt, stock, 'Transfer(address,address,uint256)', count=None):
                    if (int(transfer['topics'][1], 16) == int(v3, 16)
                            and int(transfer['topics'][2], 16) == int(router, 16)):
                        received_stock += words(transfer['data'])[0]
                assert received_stock > 0
                stock_payment = received_stock - refund
            assert stock_payment > 0
            cost_basis[role] += Decimal(stock_payment)
            record['acquisitionCostStockWei'] = str(stock_payment)
        else:
            log = event(receipt, router, 'Sold(uint256,address,address,uint256,uint256,uint256)')[0]
            spent, out, refund = words(log['data'])
            assert spent + refund == amount and out >= p[4]
            assert int(log['topics'][1], 16) == state['strategyId']
            assert int(log['topics'][2], 16) == int(wallets[role], 16)
            assert int(log['topics'][3], 16) == int(stock, 16)
            assert transfer_delta(receipt, token, wallets[role]) == -spent
            assert transfer_delta(receipt, stock, wallets[role]) == out
            assert transfer_delta(receipt, usdg, wallets[role]) == 0
            if not action['allow_partial_fill']:
                assert refund == 0
            record.update(stockOut=str(out), tokenRefund=str(refund), fractionOfCurrentBalanceBps=action['sell_fraction_bps'])
            released_cost = cost_basis[role] * Decimal(spent) / Decimal(prior_fun)
            cost_basis[role] -= released_cost
            realized[role] += Decimal(out) - released_cost
            record.update(averageCostReleasedStockWei=str(released_cost),
                          realizedPnlStockWei=str(Decimal(out) - released_cost))
        if action['expected_stage'] == 0:
            if verb == 'buy':
                log = event(receipt, curve, 'Bought(address,address,uint256,uint256,uint256)')[0]
                paid, received, burned = words(log['data'])
                assert int(log['topics'][1], 16) == int(router, 16)
                assert int(log['topics'][2], 16) == int(wallets[role], 16)
                assert received == out
                rate = opening_rate(elapsed, base, snipe, window)
                fee = paid * base // 10000
                gross = received + burned
                assert burned == gross * (rate - base) // (10000 - base)
                new_reserve = minimum_reserve if paid - fee == terminal - virtual - stock_reserve else ceildiv(invariant, virtual + stock_reserve + paid - fee)
                assert token_reserve - gross == new_reserve
                principal = ceildiv(invariant, new_reserve) - virtual - stock_reserve
                canonical_payment = (principal - 1) * 10000 // (10000 - base) + 1 if principal else 0
                assert paid == canonical_payment
                token_reserve, stock_reserve = new_reserve, stock_reserve + paid - fee
                opening_burn += burned
                expected_burn = action.get('expected', {}).get('opening_burn')
                if expected_burn == 'positive':
                    assert burned > 0 and elapsed < window
                elif expected_burn == 'zero':
                    assert burned == 0 and elapsed >= window
                record.update(stockSpent=str(paid), openingRateBps=rate, openingBurnWei=str(burned))
            else:
                log = event(receipt, curve, 'Sold(address,address,uint256,uint256,uint256)')[0]
                token_in, stock_out, fee = words(log['data'])
                assert token_in == amount and stock_out == out
                gross_stock = virtual + stock_reserve - ceildiv(invariant, token_reserve + token_in)
                assert stock_out + fee == gross_stock and fee == gross_stock * base // 10000
                token_reserve += token_in
                stock_reserve -= gross_stock
            fees = event(receipt, curve, 'TradeFeesAccrued(bool,uint256,uint256,uint256,uint256)')[0]
            total, protocol, creator, treasury_fee = words(fees['data'])
            assert bool(int(fees['topics'][1], 16)) == (verb == 'buy')
            assert total == fee and protocol == fee * split_protocol // 10000
            assert creator == fee * split_creator // 10000 and treasury_fee == fee - protocol - creator
            curve_fees += fee
            record['stockBaseFeeWei'] = str(fee)
        else:
            log = event(receipt, hook, 'Taxed(bytes32,bool,bool,uint256,uint256,uint256)')[0]
            in_token, moved, tax, rate = words(log['data'])
            assert log['topics'][1].lower() == state['poolId'].lower()
            assert bool(int(log['topics'][2], 16)) == (verb == 'sell')
            assert bool(in_token) == (verb == 'buy') and rate == base
            assert tax == moved * rate // 10000 and moved - tax == out
            record.update(hookTaxAsset='FUN' if in_token else 'STOCK', hookTaxWei=str(tax), hookRateBps=rate)
        if action['phase'] == 'graduate':
            assert verb == 'buy' and token_reserve == minimum_reserve and refund > 0
            assert uint(curve, 'status()', block=block) == 2
            values = words(event(receipt, factory, 'Graduated(uint256,uint160,uint128,uint256,uint256,uint256)')[0]['data'])
            split = words(event(receipt, factory, 'GraduationCapitalSplit(uint256,uint256,uint256,bool)')[0]['data'])
            assert values[2] == split[0] and sum(split[:2]) == stock_reserve
            assert values[3] + values[4] == token_reserve
            graduation_burn = values[4]
            graduation = dict(zip(('sqrtPriceX96', 'liquidity', 'stockSeeded', 'tokenSeeded', 'tokenBurned'), map(str, values)))
            graduation.update(treasuryStock=str(split[1]), treasuryBooked=bool(split[2]), transactionHash=receipt['transactionHash'])
        details.append(record)
    assert graduation and uint(curve, 'totalFees()', block=final_block) == curve_fees
    assert uint(token, 'totalSupply()', block=final_block) == initial_supply - opening_burn - graduation_burn

    key = state['poolKey']
    encoded_key = ''.join(f'{int(key[k], 16) if k in ("currency0", "currency1", "hooks") else int(key[k]):064x}'
                          for k in ('currency0', 'currency1', 'fee', 'tickSpacing', 'hooks'))
    assert cast('keccak', '0x' + encoded_key).lower() == state['poolId'].lower()
    storage_slot = cast('keccak', state['poolId'] + f'{6:064x}')
    verified_prices = []
    with localcontext() as context:
        context.prec = 90
        for sample in state['samples']:
            block = sample['blockNumber']
            stage = uint(curve, 'status()', block=block)
            assert stage == sample['stage'] and sample['timestamp'] == int(block_at(block)['timestamp'], 16)
            sqrt_v3 = uint(v3, 'slot0()', block=block)
            ratio = Decimal(sqrt_v3) ** 2 / Decimal(2 ** 192)
            stock_is_zero = uint(v3, 'token0()', block=block) == int(stock, 16)
            stock_price = (ratio if stock_is_zero else 1 / ratio) * Decimal(10 ** 12)
            if stage == 0:
                real = uint(curve, 'realStockReserve()', block=block)
                reserve = uint(curve, 'tokenReserve()', block=block)
                fun_stock = Decimal(virtual + real) / Decimal(reserve)
            else:
                sqrt_v4 = uint(book['poolManager'], 'extsload(bytes32)', (storage_slot,), block) & ((1 << 160) - 1)
                ratio_v4 = Decimal(sqrt_v4) ** 2 / Decimal(2 ** 192)
                fun_stock = ratio_v4 if key['currency0'].lower() == token.lower() else 1 / ratio_v4
            decimal_close(sample['stockPerFun'], fun_stock)
            decimal_close(sample['usdgPerStock'], stock_price)
            decimal_close(sample['usdgPerFun'], fun_stock * stock_price)
            assert int(sample['tokenSupplyWei']) == uint(token, 'totalSupply()', block=block)
            oracle = read(book['stocks']['TSLA']['oracle'], 'tryPrice()', block=block)
            reference = read(book['stocks']['TSLA']['oracle'], 'lastPriceAt()', block=block)
            verified_prices.append({**sample, 'oracleLive': bool(oracle[0]), 'oraclePriceE18': str(oracle[1]),
                                   'referenceAvailable': bool(reference[0]), 'referencePriceE18': str(reference[1]),
                                   'referenceTimestamp': reference[2], 'priceSource': 'same-block pool spots'})
        final_price = next(x for x in reversed(verified_prices) if x['label'] == 'final')
        assert final_price['blockNumber'] == final_block
        final_stock_price = Decimal(final_price['usdgPerStock'])
        final_fun_price = Decimal(final_price['usdgPerFun'])
        final_fun_stock = Decimal(final_price['stockPerFun'])
        funding_sqrt = uint(v3, 'slot0()', block=funded_block)
        funding_ratio = Decimal(funding_sqrt) ** 2 / Decimal(2 ** 192)
        funding_stock0 = uint(v3, 'token0()', block=funded_block) == int(stock, 16)
        funding_price = (funding_ratio if funding_stock0 else 1 / funding_ratio) * Decimal(10 ** 12)
        pnl = {}
        for role, wallet in wallets.items():
            final = state['finalBalances'][role]
            for name, asset_address in assets.items():
                assert balance(asset_address, wallet, final_block) == final[name] == ledger[role][name]
                assert uint(asset_address, 'allowance(address,address)', (wallet, router), final_block) == 0
            actual_native = int(rpc('eth_getBalance', [wallet, tag(final_block)]), 16)
            assert actual_native == final['nativeWei']
            assert actual_native == state['fundedBalances'][role]['nativeWei'] - gas_after_funding.get(role, 0)
            start = state['fundedBalances'][role]
            start_value = Decimal(start['stock']) / 10 ** 18 * funding_price + Decimal(start['usdg']) / 10 ** 6
            end_value = Decimal(final['stock']) / 10 ** 18 * final_stock_price + Decimal(final['usdg']) / 10 ** 6 + Decimal(final['fun']) / 10 ** 18 * final_fun_price
            trading_stock_pnl = Decimal(final['stock'] - start['stock']) / 10 ** 18 + Decimal(final['fun']) / 10 ** 18 * final_fun_stock + Decimal(final['usdg'] - start['usdg']) / 10 ** 6 / final_stock_price
            liquidation = state['liquidationQuotes'][role]
            assert int(liquidation['funInput']) == final['fun']
            liquidation_proof = dict(liquidation)
            if final['fun']:
                quote_block = liquidation['block']
                quote_time = int(block_at(quote_block)['timestamp'], 16)
                assert funded_block < quote_block <= final_block
                trade = f'({state["strategyId"]},{stock},{final["fun"]},0,1,{quote_time+300},2,true)'
                actual_quote = read(router, f'sell({PTYPE},(address,address)[])',
                                    (trade, '[]'), quote_block, wallet)
                assert actual_quote == [int(liquidation['stockOut']), int(liquidation['funRefund'])]
                assert balance(token, wallet, quote_block) == final['fun']
                quote_sqrt = uint(v3, 'slot0()', block=quote_block)
                quote_ratio = Decimal(quote_sqrt) ** 2 / Decimal(2 ** 192)
                quote_stock0 = uint(v3, 'token0()', block=quote_block) == int(stock, 16)
                quote_price = (quote_ratio if quote_stock0 else 1 / quote_ratio) * Decimal(10 ** 12)
                quote_stock_cash = balance(stock, wallet, quote_block)
                quote_usdg_cash = balance(usdg, wallet, quote_block)
                executable_value = (Decimal(quote_stock_cash + actual_quote[0]) / 10 ** 18 * quote_price
                                    + Decimal(quote_usdg_cash) / 10 ** 6)
                liquidation_proof.update(verified=True, stockUsdgPriceAtQuoteBlock=str(quote_price),
                    executableCashUsdg=str(executable_value), unliquidatedFunWei=str(actual_quote[1]),
                    portfolioMarkUsdgAfterFunToStockQuote=str(executable_value),
                    valuationBasis='The executable eth_call sells FUN into STOCK. The resulting STOCK and existing STOCK cash are marked in tUSDG at the same-block pool spot; no subsequent STOCK-to-tUSDG swap or its fees/slippage is included. This is not guaranteed tUSDG proceeds.',
                    fullFunExitPossible=actual_quote[1] == 0,
                    profitVersusFundingUsdg=str(executable_value-start_value) if actual_quote[1] == 0 else None)
            else:
                assert int(liquidation['stockOut']) == int(liquidation['funRefund']) == 0
                liquidation_proof.update(verified=True, fullFunExitPossible=True, noFunRemaining=True)
            pnl[role] = {'address': wallet, 'fundedBaseline': {k: str(v) for k, v in start.items()},
                         'finalBalances': {k: str(v) for k, v in final.items()},
                         'initialMarkUsdg': str(start_value), 'finalMarkUsdg': str(end_value),
                         'markToMarketPnlUsdg': str(end_value - start_value),
                         'tradingPnlInStockAtFinalMark': str(trading_stock_pnl),
                         'gasPaidWei': str(gas_by_role.get(role, 0)),
                         'postFundingGasPaidWei': str(gas_after_funding.get(role, 0)),
                         'fullyExitedFun': final['fun'] == 0,
                         'remainingAverageCostStockWei': str(cost_basis[role]),
                         'realizedTradingPnlStockWei': str(realized[role]),
                         'unrealizedTradingPnlStockWei': str(Decimal(final['fun'])*final_fun_stock-cost_basis[role]),
                         'costBasisMethod': 'Weighted average in stock units; USDG entries use stock actually routed, including trading fees. Gas remains separate.',
                         'liquidationQuote': liquidation_proof}
        treasury_stock = balance(stock, treasury, final_block)
        treasury_usdg = balance(usdg, treasury, final_block)
        treasury_nav = Decimal(treasury_stock) / 10 ** 18 * final_stock_price + Decimal(treasury_usdg) / 10 ** 6
    assert ledger['sniper']['fun'] == ledger['paper_hands']['fun'] == 0
    assert ledger['diamond_hands']['fun'] > 0 and ledger['follower_2']['fun'] > 0
    for role in ('diamond_hands', 'follower_2'):
        assert not any(d['role'] == role and d['action'] == 'sell' for d in details)
    for asset in assets.values():
        assert balance(asset, router, final_block) == 0
    for action in plan['actions']:
        if action['action'] in ('signal', 'hold'):
            assert rows_by_id[action['id']]['verified']
    signal = next(a for a in state['actions'] if a['action'] == 'signal')
    for d in details:
        if d['role'].startswith('follower_') and d['action'] == 'buy':
            assert int(block_at(d['blockNumber'])['timestamp'], 16) >= signal['timestamp']

    guards = {}
    final_time = int(block_at(final_block)['timestamp'], 16)
    sender = wallets['sniper']
    for label, deadline, stage, expected in (
        ('expired_deadline', 0, 2, cast('sig', 'Expired()')),
        ('stale_curve_stage', final_time + 300, 0, cast('sig', 'StageChanged(uint8)') + f'{2:064x}'),
    ):
        trade = f'({state["strategyId"]},{stock},{10**16},0,1,{deadline},{stage},false)'
        guards[label] = reject_call(router, f'buy({PTYPE},(address,address)[])',
                                   (trade, '[]'), sender, final_block, expected)
    before_graduate = next(d['blockNumber'] for d in details if d['role'] == 'paper_hands' and d['phase'] == 'after_window')
    deadline = int(block_at(before_graduate)['timestamp'], 16) + 300
    trade = f'({state["strategyId"]},{stock},{10**16},0,{2**255},{deadline},0,true)'
    guards['impossible_min_final_out'] = reject_call(router, f'buy({PTYPE},(address,address)[])',
        (trade, '[]'), wallets['follower_1'], before_graduate, cast('sig', 'TooLittle(uint256)'))
    launch_block = int(all_receipts['launch']['blockNumber'], 16)
    amount = int(plan_trades[0]['amount_in'])
    normal = read(curve, 'quoteBuyFor(uint256,address)', (amount, wallets['sniper']), launch_block)
    exempt = read(curve, 'quoteBuyFor(uint256,address)', (amount, owner), launch_block)
    assert normal[0] == exempt[0] and normal[1] + normal[2] == exempt[1]
    assert normal[2] > 0 and exempt[2] == 0
    result = {'schema': 'hedgefun-persona-independent-verification-v1', 'chainId': 46630,
        'verificationMode': 'strict historical state and canonical receipts', 'rpcSource': RPC,
        'rpcSources': {'historicalStateAndReceipts': RPC, 'eventHistory': LOGS_RPC or RPC},
        'sourceJournalSha256': hashlib.sha256(state_path.read_bytes()).hexdigest(),
        'sourcePlanSha256': hashlib.sha256(plan_path.read_bytes()).hexdigest(),
        'factory': factory, 'router': router, 'token': token, 'treasury': treasury, 'curve': curve,
        'strategyId': state['strategyId'], 'finalBlock': final_block,
        'finalBlockHash': block_at(final_block)['hash'], 'successCount': len(transactions),
        'tradeCount': len(details), 'totalGasPaidWei': str(sum(gas_by_role.values())),
        'gasPaidByRoleWei': {k: str(v) for k, v in gas_by_role.items()}, 'openingBurnWei': str(opening_burn),
        'curveBaseFeesStockWei': str(curve_fees), 'graduation': graduation, 'treasuryParams': params,
        'treasuryNavUsdgAtFinalPoolSpot': str(treasury_nav), 'openingControl': {
            'blockNumber': launch_block, 'sameGrossPayment': str(normal[0]),
            'nonexemptTokens': str(normal[1]), 'nonexemptBurn': str(normal[2]),
            'creatorTokens': str(exempt[1]), 'creatorBurn': str(exempt[2])},
        'guards': guards, 'actors': pnl, 'trades': details, 'prices': verified_prices,
        'transactions': transaction_rows,
        'preBroadcastErrors': state.get('preBroadcastErrors', []),
        'abandonedLaunches': abandoned_launches,
        'abandonedLaunchTransactionCount': sum(r['label'].startswith('aborted-initial-') for r in transactions),
        'abandonedLaunchGasPaidWei': str(sum(int(r['gasPaidWei']) for r in transaction_rows
                                           if r['label'].startswith('aborted-initial-'))),
        'limitations': ['Sequential public testnet transactions; this is not evidence of mempool ordering or a same-block MEV guarantee.',
            'KOL and follower timing are internal simulation events; no social posts were published.',
            'tUSDG and TSLA are test assets. Gas is reported in test ETH without inventing a USD conversion.',
            'Funded faucet balances are the external-capital baseline, not trading profit.',
            'Remaining FUN is marked at marginal pool spot, not counted as realized sale proceeds.',
            'Liquidation quotes are separately replayed at their own historical blocks before approval cleanup; they cannot all execute at once at those prices.',
            'Each stock/FUN/tUSDG valuation uses one pinned historical block. No live oracle execution is inferred from the display mark.',
            'Treasury NAV is an accounting mark and is not a token redemption price.']}
    target = directory / 'independent-verification.json'
    target.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({key: result[key] for key in ('strategyId', 'finalBlock', 'successCount', 'tradeCount', 'totalGasPaidWei')}, indent=2))
    print(f'Independent receipt, actual tax, faucet-excluded balance, stage and same-block price audit passed: {target}')
    return result


def main():
    global RPC, LOGS_RPC
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, default=DEFAULT_OUT)
    parser.add_argument('--rpc', default=RPC, help='Read-only verification RPC; all chain IDs, block hashes and receipts remain strictly verified')
    parser.add_argument('--logs-rpc', help='Optional separate event-history RPC; its chain ID and final block hash must agree')
    args = parser.parse_args()
    RPC = args.rpc
    LOGS_RPC = args.logs_rpc
    verify(args.directory.resolve())


if __name__ == '__main__':
    main()

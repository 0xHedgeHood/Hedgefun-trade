#!/usr/bin/env python3
"""Validate and freeze public TSLA daily history for a local contract replay.

Reads a saved Yahoo chart response; never signs transactions or changes prices.
The provider's daily timestamp labels a session OPEN. Its close is not known then.
Replay inputs use session dates and synthetic calendar-day offsets, not that timestamp.
"""
from __future__ import annotations

import argparse
import csv
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_EVEN
import hashlib
import json
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
SOURCE_URL = ('https://query1.finance.yahoo.com/v8/finance/chart/TSLA?'
              'period1=1640995200&period2=1767225600&interval=1d&'
              'events=div%2Csplits&includeAdjustedClose=true')
YEARS = (2022, 2023, 2024, 2025)
E18 = Decimal(10) ** 18
PRICE_QUANTUM = Decimal('0.00000001')


def normalize(raw: bytes) -> dict:
    data = json.loads(raw, parse_float=Decimal)
    if data['chart'].get('error'):
        raise ValueError('Provider returned a chart error')
    series = data['chart']['result'][0]
    meta = series['meta']
    if (meta['symbol'], meta['currency'], meta['exchangeTimezoneName']) != (
            'TSLA', 'USD', 'America/New_York'):
        raise ValueError('Wrong instrument, currency or timezone')
    times = series['timestamp']
    if not times or times != sorted(set(times)):
        raise ValueError('Timestamps must be unique and strictly increasing')
    q = series['indicators']['quote'][0]
    adj = series['indicators']['adjclose'][0]['adjclose']
    if any(len(q[k]) != len(times) for k in ('open', 'high', 'low', 'close', 'volume')) or len(adj) != len(times):
        raise ValueError('Inconsistent OHLCV lengths')
    rows = []
    for i, ts in enumerate(times):
        session = datetime.fromtimestamp(ts, ZoneInfo('America/New_York')).date()
        if session.year not in YEARS or session.weekday() >= 5:
            raise ValueError('Session outside requested years or on a weekend')
        values = [q[k][i] for k in ('open', 'high', 'low', 'close')]
        if any(v is None or not Decimal(v).is_finite() or v <= 0 for v in values):
            raise ValueError('Missing, nonfinite or nonpositive OHLC price')
        o, h, low, close = map(Decimal, values)
        if low > min(o, close) or h < max(o, close) or low > h:
            raise ValueError('OHLC range is inconsistent')
        if adj[i] is None or Decimal(adj[i]) != close:
            raise ValueError('Close/adjusted close differ; corporate-action policy needs review')
        volume = q['volume'][i]
        if not isinstance(volume, int) or volume < 0:
            raise ValueError('Invalid volume')
        replay_price = close.quantize(PRICE_QUANTUM, rounding=ROUND_HALF_EVEN)
        rows.append({'date': session.isoformat(), 'providerTimestamp': ts,
                     'openUsd': str(o), 'highUsd': str(h), 'lowUsd': str(low),
                     'closeUsd': str(close), 'adjustedCloseUsd': str(adj[i]),
                     'volume': volume, 'replayCloseUsd': str(replay_price),
                     'replayPriceE18': int(replay_price * E18)})
    if len({r['date'] for r in rows}) != len(rows):
        raise ValueError('Duplicate session date')
    windows = {}
    for year in YEARS:
        selected = [r for r in rows if r['date'].startswith(str(year))]
        if not selected:
            raise ValueError(f'Missing year {year}')
        first = date.fromisoformat(selected[0]['date'])
        windows[str(year)] = {
            'dates': [r['date'] for r in selected],
            'pricesE18': [r['replayPriceE18'] for r in selected],
            'elapsedSeconds': [(date.fromisoformat(r['date']) - first).days * 86400 for r in selected],
        }
    events = json.loads(json.dumps(series.get('events', {}), default=str))
    return {
        'schema': 'hedgefun-tsla-daily-history-v1', 'symbol': 'TSLA', 'currency': 'USD',
        'years': list(YEARS), 'rowCount': len(rows), 'sourceUrl': SOURCE_URL,
        'sourcePage': 'https://finance.yahoo.com/quote/TSLA/history/',
        'sourceSha256': hashlib.sha256(raw).hexdigest(),
        'priceBasis': 'Yahoo Close, already split-adjusted; equals Adj Close for every saved bar',
        'replayPrecision': '8 decimal places, nearest half-even, matching test feed precision',
        'timestampPolicy': 'Provider timestamps label session open, NOT close availability. Use dates only; synthetic EVM day offsets preserve calendar gaps and omit real intraday/DST/early-close timing.',
        'corporateActionPolicy': 'Do not divide prices or multiply holdings again on split dates.',
        'events': events, 'windows': windows, 'rows': rows,
    }


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, default=ROOT/'contractV2/data/tsla-history-2022-2025-source.json')
    p.add_argument('--output', type=Path, default=ROOT/'contractV2/data/tsla-history-2022-2025.json')
    a = p.parse_args()
    normalized = normalize(a.source.read_bytes())
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(normalized, indent=2) + '\n')
    csv_path = a.output.with_suffix('.csv')
    with csv_path.open('w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=normalized['rows'][0].keys(), lineterminator='\n')
        w.writeheader(); w.writerows(normalized['rows'])
    print(json.dumps({'rows': normalized['rowCount'], 'sourceSha256': normalized['sourceSha256'],
                      'windows': {y: {'count': len(w['dates']), 'first': w['dates'][0], 'last': w['dates'][-1]}
                                  for y, w in normalized['windows'].items()}}, indent=2))


if __name__ == '__main__':
    main()

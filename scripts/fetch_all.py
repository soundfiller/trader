#!/usr/bin/env python3
"""Fetch all OHLCV data for full analysis and save to cache."""
import json, os, time, urllib.request, ssl

ctx = ssl.create_default_context()
CACHE_DIR = os.path.join(os.path.dirname(__file__), '..', 'data', 'cache')
BINANCE_DIR = os.path.join(CACHE_DIR, 'binance')
HL_DIR = os.path.join(CACHE_DIR, 'hyperliquid')
os.makedirs(BINANCE_DIR, exist_ok=True)
os.makedirs(HL_DIR, exist_ok=True)

now = int(time.time() * 1000)

def fetch_binance(symbol, interval, limit=200):
    url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
    req = urllib.request.Request(url, headers={'User-Agent': 'Trader/1.0'})
    with urllib.request.urlopen(req, context=ctx, timeout=10) as r:
        raw = json.loads(r.read().decode())
    candles = []
    for k in raw:
        candles.append({
            'open_time': k[0], 'open': float(k[1]), 'high': float(k[2]),
            'low': float(k[3]), 'close': float(k[4]), 'volume': float(k[5]),
            'close_time': k[6]
        })
    path = os.path.join(BINANCE_DIR, f'{symbol}_{interval}.json')
    with open(path, 'w') as f:
        json.dump({'fetched_at': now, 'candles': candles}, f)
    return {'symbol': symbol, 'interval': interval, 'count': len(candles)}

def fetch_hl(symbol, interval, limit=200):
    body = json.dumps({"type":"candleSnapshot","req":{"coin":symbol,"interval":interval,"limit":limit}})
    req = urllib.request.Request('https://api.hyperliquid.xyz/info',
        data=body.encode(), headers={'Content-Type':'application/json','User-Agent':'Trader/1.0'})
    with urllib.request.urlopen(req, context=ctx, timeout=10) as r:
        raw = json.loads(r.read().decode())
    candles = []
    for k in raw:
        candles.append({
            'open_time': k['t'], 'open': float(k['o']), 'high': float(k['h']),
            'low': float(k['l']), 'close': float(k['c']), 'volume': float(k['v']),
            'close_time': k['T']
        })
    path = os.path.join(HL_DIR, f'{symbol}_{interval}.json')
    with open(path, 'w') as f:
        json.dump({'fetched_at': now, 'candles': candles}, f)
    return {'symbol': symbol, 'interval': interval, 'count': len(candles)}

results = []
# BTC all TFs
for tf in ['1h','4h','1d']:
    try: results.append(fetch_binance('BTCUSDT', tf))
    except Exception as e: results.append({'error': f'BTC {tf}: {e}'})

# ETH 1h/4h
for tf in ['1h','4h']:
    try: results.append(fetch_binance('ETHUSDT', tf))
    except Exception as e: results.append({'error': f'ETH {tf}: {e}'})

# SOL 1h/4h
for tf in ['1h','4h']:
    try: results.append(fetch_binance('SOLUSDT', tf))
    except Exception as e: results.append({'error': f'SOL {tf}: {e}'})

# HYPE all TFs (via HL)
for tf in ['1h','4h','1d']:
    try: results.append(fetch_hl('HYPE', tf))
    except Exception as e: results.append({'error': f'HYPE {tf}: {e}'})

print(json.dumps(results))

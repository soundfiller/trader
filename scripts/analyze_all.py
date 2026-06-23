#!/usr/bin/env python3
"""Full analysis: compute indicators, bias, levels from cached OHLCV data."""
import json, os

CACHE_DIR = os.path.join(os.path.dirname(__file__), '..', 'data', 'cache')

def load_cached(source, symbol, tf):
    if source == 'binance':
        path = os.path.join(CACHE_DIR, 'binance', f'{symbol}_{tf}.json')
    else:
        path = os.path.join(CACHE_DIR, 'hyperliquid', f'{symbol}_{tf}.json')
    with open(path) as f:
        return json.load(f)

def sma(values, period):
    if len(values) < period:
        return None
    return sum(values[-period:]) / period

def ema(values, period):
    if len(values) < 2:
        return values[-1] if values else None
    k = 2 / (period + 1)
    result = values[0]
    for v in values[1:]:
        result = v * k + result * (1 - k)
    return result

def atr(candles, period=14):
    if len(candles) < period + 1:
        return None
    trs = []
    for i in range(1, len(candles)):
        h, l, pc = candles[i]['high'], candles[i]['low'], candles[i-1]['close']
        tr = max(h - l, abs(h - pc), abs(l - pc))
        trs.append(tr)
    # EMA of TR
    if len(trs) < period:
        return None
    atr_val = sum(trs[:period]) / period
    for t in trs[period:]:
        atr_val = (atr_val * (period - 1) + t) / period
    return atr_val

def rsi(closes, period=14):
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i-1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def find_levels(candles, tolerance_pct=0.005):
    """Find support/resistance levels from swing highs/lows."""
    highs = [(c['high'], c['open_time']) for c in candles]
    lows = [(c['low'], c['open_time']) for c in candles]
    # Find swing highs (higher than both neighbors)
    swings_high = []
    for i in range(2, len(candles) - 2):
        h = candles[i]['high']
        if h > max(candles[i-1]['high'], candles[i-2]['high']) and h > max(candles[i+1]['high'], candles[i+2]['high']):
            swings_high.append(h)
    swings_low = []
    for i in range(2, len(candles) - 2):
        l = candles[i]['low']
        if l < min(candles[i-1]['low'], candles[i-2]['low']) and l < min(candles[i+1]['low'], candles[i+2]['low']):
            swings_low.append(l)
    # Cluster nearby levels
    def cluster(levels):
        if not levels:
            return []
        sorted_levels = sorted(levels)
        clusters = []
        current = [sorted_levels[0]]
        for l in sorted_levels[1:]:
            if l / current[-1] - 1 < tolerance_pct:
                current.append(l)
            else:
                clusters.append(sum(current) / len(current))
                current = [l]
        clusters.append(sum(current) / len(current))
        return clusters
    return {'resistance': cluster(swings_high)[-5:], 'support': cluster(swings_low)[-5:]}

def analyze(symbol, source='binance'):
    results = {}
    for tf in ['1h', '4h', '1d']:
        try:
            data = load_cached(source, symbol, tf)
            candles = data['candles']
            closes = [c['close'] for c in candles]
            volumes = [c['volume'] for c in candles]
            current = closes[-1]
            
            # Trend: compare EMAs and structure
            ema_short = ema(closes, 9)
            ema_mid = ema(closes, 21)
            ema_long = ema(closes, 50)
            ema_200 = ema(closes, 200) if len(closes) >= 200 else None
            
            rsi_val = rsi(closes, 14)
            atr_val = atr(candles, 14)
            vol_sma = sma(volumes, 20)
            vol_ratio = volumes[-1] / vol_sma if vol_sma else 1.0
            
            levels = find_levels(candles)
            
            # Structure: higher highs / lower lows
            recent = candles[-24:]
            highs_list = [c['high'] for c in recent]
            lows_list = [c['low'] for c in recent]
            
            # Determine structure
            hh = all(recent[i]['high'] > recent[i-1]['high'] for i in range(-6, 0)) if len(recent) >= 7 else False
            ll = all(recent[i]['low'] < recent[i-1]['low'] for i in range(-6, 0)) if len(recent) >= 7 else False
            
            results[tf] = {
                'current': current,
                'open': candles[-1]['open'],
                'high': candles[-1]['high'],
                'low': candles[-1]['low'],
                'volume': volumes[-1],
                'vol_ratio': round(vol_ratio, 2),
                'ema9': round(ema_short, 2) if ema_short else None,
                'ema21': round(ema_mid, 2) if ema_mid else None,
                'ema50': round(ema_long, 2) if ema_long else None,
                'ema200': round(ema_200, 2) if ema_200 else None,
                'rsi14': round(rsi_val, 1) if rsi_val else None,
                'atr14': round(atr_val, 4) if atr_val else None,
                'support': [round(x, 2) for x in levels['support']],
                'resistance': [round(x, 2) for x in levels['resistance']],
                'candles': len(candles)
            }
        except Exception as e:
            results[tf] = {'error': str(e)}
    return results

# Run for all symbols
for sym, src in [('BTCUSDT','binance'), ('ETHUSDT','binance'), ('SOLUSDT','binance'), ('HYPE','hyperliquid')]:
    result = analyze(sym, src)
    print(f"\n=== {sym.replace('USDT','')} ===")
    for tf, data in result.items():
        if 'error' in data:
            print(f"  {tf}: ERROR — {data['error']}")
        else:
            print(f"  {tf}: close={data['current']} | ema9={data['ema9']} ema21={data['ema21']} ema50={data['ema50']} | rsi14={data['rsi14']} | atr={data['atr14']} | vol_ratio={data['vol_ratio']}")
            print(f"       S:{data['support']} | R:{data['resistance']}")

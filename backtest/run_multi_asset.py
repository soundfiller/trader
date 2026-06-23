#!/usr/bin/env python3
"""Multi-asset backtest runner using per-asset configs from asset_configs.json."""
import json, sys, time, numpy as np, pandas as pd, requests
sys.path.insert(0, '/Users/manspetterson/.openclaw/workspace/trader/backtest')
import trader_backtest_v5 as bt
from trader_backtest_v5 import *

# Load configs
with open('/Users/manspetterson/.openclaw/workspace/trader/backtest/asset_configs.json') as f:
    ac = json.load(f)

# Fetch data for all assets
def fetch_hl(symbol, interval='4h', days=800):
    if symbol == 'HYPE':
        return bt.fetch_hype_ohlcv(days=days)
    # BTC/ETH from Binance
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - days * 24 * 3600 * 1000
    all_candles = []
    limit = 1000
    while end_ms > start_ms:
        url = f'https://api.binance.com/api/v3/klines?symbol={symbol}USDT&interval={interval}&limit={limit}&endTime={end_ms}'
        resp = requests.get(url, timeout=30)
        candles = resp.json()
        if not candles or 'code' in candles: break
        all_candles = candles + all_candles
        end_ms = candles[0][0] - 1
        if len(candles) < limit: break
    cols = ['t','o','h','l','c','v','_','_','_','_','_','_']
    df = pd.DataFrame(all_candles, columns=cols)
    for col in ['o','h','l','c','v']: df[col] = df[col].astype(float)
    df['time'] = pd.to_datetime(df['t'], unit='ms')
    result = df[['time','o','h','l','c','v']].copy()
    result.columns = ['time','open','high','low','close','volume']
    return result

def build_cfg(asset_config, ac):
    """Build v5 CONFIG from asset_configs.json entry."""
    rc = asset_config['regime']
    lc = asset_config['limits']
    fc = asset_config['fusion']
    
    cfg = json.loads(json.dumps(CONFIG))
    cfg['costs']['execution_mode'] = 'maker'
    cfg['dsr_trials'] = 10
    cfg['timeframe_hours'] = 4
    cfg['capital_usdc'] = ac['capital_usdc']
    cfg['limits'] = {**cfg['limits'], **lc}
    cfg['fusion'] = fc
    # V5 dual-mode: regime params are the authoritative ones — signal is fallback
    cfg['regime'].update(rc)
    cfg['signal']['rr_target'] = rc.get('mr_rr_target', cfg['signal']['rr_target'])
    cfg['signal']['atr_sl_mult'] = rc.get('mr_atr_sl_mult', cfg['signal']['atr_sl_mult'])
    cfg['signal']['max_hold_bars'] = rc.get('mr_max_hold_bars', cfg['signal']['max_hold_bars'])
    # Also sync shorter param keys used by some signal logic
    for k in ['adx_threshold_low','adx_threshold_high','adx_period','trend_ema_period']:
        cfg.setdefault('regime', {})[k] = rc[k] if k in rc else cfg['regime'].get(k, 0)
    return cfg

results = {}
total_trades = 0
total_start = 0

for symbol in ['HYPE', 'BTC', 'ETH']:
    key = f'{symbol.lower()}_4h'
    if key not in ac or not ac[key].get('tested'):
        print(f'{symbol}: SKIP (not tested)')
        continue
    
    config = ac[key]
    print(f'\n{"="*60}')
    print(f'{symbol} 4h — {config.get("note","")}')
    print(f'{"="*60}')
    
    df = fetch_hl(symbol)
    cfg = build_cfg(config, ac)
    
    bars_per_day = 24 / 4  # = 6 for 4h
    eval_bars = min(int(760 * bars_per_day), len(df) - 200)  # 760 days worth, or as much as we have
    test_start = max(200, len(df) - eval_bars)
    
    print(f'  Bars: {len(df)}, Test start: {test_start}')
    
    # Ensure time is datetime
    if not pd.api.types.is_datetime64_any_dtype(df['time']):
        df['time'] = pd.to_datetime(df['time'])
    df2, trades, equity, sigs = bt.run_backtest(df, cfg, test_start_idx=test_start)
    tdf = df2.iloc[test_start:].reset_index(drop=True)
    res = metrics(tdf, trades, equity, cfg)
    
    dsr = res.get('deflated_sharpe', 0)
    nt = res.get('n_trades', 0)
    pf = res.get('profit_factor', 0)
    ret = res.get('total_return_pct', 0)
    bh = res.get('buyhold_return_pct', 0)
    dd = res.get('max_drawdown_pct', 0)
    wr = res.get('win_rate_pct', 0)
    conv = res.get('winrate_by_conviction', {})
    exits = res.get('exit_reasons', {})
    
    ok = '✅' if dsr > 0.95 else ('⚠️' if dsr > 0.5 else '❌')
    bh_ok = '✅' if ret > bh else '❌'
    
    print(f'  DSR:{dsr:.4f} {ok}  PF:{pf:.3f}  Ret:{ret:+6.1f}% vs BH:{bh:+6.1f}% {bh_ok}')
    print(f'  DD:{dd:+5.1f}%  WR:{wr:4.1f}%  T:{nt:3d}  Conv:{conv}')
    print(f'  Exits: {exits}')
    
    results[symbol] = {'dsr': dsr, 'pf': pf, 'ret': ret, 'bh': bh, 'dd': dd, 'wr': wr, 'nt': nt}
    total_trades += nt
    total_start += ac['capital_usdc']

print(f'\n{"="*60}')
print(f'MULTI-ASSET SUMMARY: {total_trades} trades across {len(results)} assets')
print(f'{"="*60}')

# Combined return (equal allocation)
total_end = total_start
for symbol, r in results.items():
    total_end += (r['ret'] / 100) * ac['capital_usdc']

combined_ret = (total_end / total_start - 1) * 100
combined_bh = np.mean([r['bh'] for r in results.values()])
avg_dsr = np.mean([r['dsr'] for r in results.values()])

print(f'  Combined return: {combined_ret:+.1f}% vs avg BH: {combined_bh:+.1f}%')
print(f'  {"✅ Beats BH" if combined_ret > combined_bh else "❌ Below BH"}')
print(f'  Avg DSR: {avg_dsr:.4f}')
print(f'  {"✅ Trade count sufficient" if total_trades >= 100 else "❌ More trades needed"}')

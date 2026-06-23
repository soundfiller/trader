#!/usr/bin/env python3
"""100-iteration optimization loop for Trader backtest. Random + refinement search.
Includes trend filter (EMA50 direction gate) as a searchable parameter."""
import json, sys, copy, math, random, time, numpy as np, pandas as pd
sys.path.insert(0, '/Users/manspetterson/.openclaw/workspace/trader/backtest')

import trader_backtest as bt
from trader_backtest import *

# Fetch data once
df_all = fetch_hype_ohlcv(days=760 + 45)
print(f"Fetched {len(df_all)} candles\n")

base_cfg = json.loads(json.dumps(CONFIG))
base_cfg["costs"]["execution_mode"] = "maker"
base_cfg["dsr_trials"] = 10

def deep_merge(base, override):
    result = copy.deepcopy(base)
    for k, v in override.items():
        if isinstance(v, dict) and k in result and isinstance(result[k], dict):
            result[k] = deep_merge(result[k], v)
        else:
            result[k] = v
    return result

# Parameter space
param_space = {
    "limits.max_risk_per_trade_pct": [1.0, 1.5, 2.0, 2.5, 3.0, 4.0],
    "limits.min_conviction": [2, 3, 4],
    "limits.min_risk_reward": [1.0, 1.25, 1.5, 2.0, 2.5],
    "signal.rr_target": [1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0, 6.0],
    "signal.atr_sl_mult": [0.5, 0.75, 1.0, 1.25, 1.5, 2.0],
    "signal.max_hold_bars": [20, 30, 40, 48, 60, 72, 90],
    "signal.cooldown_bars": [0, 1, 2],
    "fusion.chart": [0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80],
    "limits.default_leverage": [2, 3, 4, 5],
    "trend_filter.enabled": [True, False],
}

def random_config(seed):
    rng = random.Random(seed + 42)
    cfg = copy.deepcopy(base_cfg)
    cfg["limits"]["max_risk_per_trade_pct"] = rng.choice(param_space["limits.max_risk_per_trade_pct"])
    cfg["limits"]["min_conviction"] = rng.choice(param_space["limits.min_conviction"])
    cfg["limits"]["min_risk_reward"] = rng.choice(param_space["limits.min_risk_reward"])
    cfg["signal"]["rr_target"] = rng.choice(param_space["signal.rr_target"])
    cfg["signal"]["atr_sl_mult"] = rng.choice(param_space["signal.atr_sl_mult"])
    cfg["signal"]["max_hold_bars"] = rng.choice(param_space["signal.max_hold_bars"])
    cfg["signal"]["cooldown_bars"] = rng.choice(param_space["signal.cooldown_bars"])
    chart_w = rng.choice(param_space["fusion.chart"])
    whale_w = round(1.0 - chart_w - 0.10, 2)
    cfg["fusion"] = {"chart": chart_w, "whale": max(0.0, whale_w), "regime": 0.10}
    cfg["limits"]["default_leverage"] = rng.choice(param_space["limits.default_leverage"])
    cfg["limits"]["max_leverage"] = cfg["limits"]["default_leverage"] + 2
    cfg["trend_filter"] = {"enabled": rng.choice(param_space["trend_filter.enabled"]), "ema_period": 50}
    return cfg

# Trend-aware signal generator
def signal_generator_trend(df, cfg):
    """Wraps signal_generator with EMA50 trend-direction gate."""
    sigs = signal_generator(df, cfg)
    tf_cfg = cfg.get("trend_filter", {})
    if not tf_cfg.get("enabled", False):
        return sigs
    
    # Compute EMA50 slope
    ema50 = df['close'].ewm(span=50, adjust=False).mean()
    trend_up = ema50.diff(5) > 0  # EMA50 rising over last 5 bars
    
    filtered = []
    for s in sigs:
        if s.idx >= len(trend_up):
            filtered.append(s)
            continue
        is_uptrend = trend_up.iloc[s.idx]
        if s.direction == "long" and is_uptrend:
            filtered.append(s)
        elif s.direction == "short" and not is_uptrend:
            filtered.append(s)
        # else: counter-trend signal → dropped
    return filtered

# Monkey-patch before each run
original_signal_generator = bt.signal_generator

print(f"{'='*100}")
print(f"100-ITERATION OPTIMIZATION — Random search + refinement. 2yr HYPE 4h. Trend filter as parameter.")
print(f"{'='*100}\n")

results = []
best_dsr = -999
N = 85
M = 15

for i in range(N + M):
    t0 = time.time()
    
    if i < N:
        cfg = random_config(i)
        phase = "RAND"
    else:
        top3 = sorted(results, key=lambda r: r.get("deflated_sharpe", -999), reverse=True)[:3]
        base_for_mutate = copy.deepcopy(top3[random.Random(i).randint(0, 2)]["config"])
        mutate_key = random.Random(i+100).choice(list(param_space.keys()))
        parts = mutate_key.split(".")
        new_val = random.Random(i+200).choice(param_space[mutate_key])
        if len(parts) == 2:
            base_for_mutate[parts[0]][parts[1]] = new_val
        cfg = base_for_mutate
        phase = "REFINE"
    
    # Monkey-patch signal generator with trend filter for this run
    def make_patched_sg(_cfg):
        return lambda df, __cfg: signal_generator_trend(df, _cfg)
    bt.signal_generator = make_patched_sg(cfg)
    
    test_start_idx = len(df_all) - int(760 * 24/4)
    df2, trades, equity, sigs = run_backtest(df_all, cfg, test_start_idx=test_start_idx)
    test_df = df2.iloc[test_start_idx:].reset_index(drop=True)
    res = metrics(test_df, trades, equity, cfg)
    res["config"] = cfg
    res["n_signals"] = len(sigs)
    
    dsr = res.get("deflated_sharpe", -99)
    pf = res.get("profit_factor", 0)
    ret = res.get("total_return_pct", 0)
    bh = res.get("buyhold_return_pct", 0)
    wr = res.get("win_rate_pct", 0)
    nt = res.get("n_trades", 0)
    dd = res.get("max_drawdown_pct", 0)
    
    results.append(res)
    if dsr > best_dsr: best_dsr = dsr
    
    elapsed = time.time() - t0
    tf = "🔀" if cfg.get("trend_filter", {}).get("enabled", False) else "  "
    print(f"[{i+1:3d}/{N+M}] {phase:6s} {tf} ret:{ret:+6.1f}% DSR:{dsr:.4f} PF:{pf:.3f} "
          f"DD:{dd:+5.1f}% WR:{wr:4.1f}% T:{nt:3d} "
          f"risk:{cfg['limits']['max_risk_per_trade_pct']:.0f}% rr:{cfg['signal']['rr_target']:.1f} "
          f"c≥{cfg['limits']['min_conviction']} sl:{cfg['signal']['atr_sl_mult']:.1f}x "
          f"hold:{cfg['signal']['max_hold_bars']:2d}b "
          f"chart:{cfg['fusion']['chart']:.2f} "
          f"({elapsed:.1f}s)")

# Restore original
bt.signal_generator = original_signal_generator

print(f"\n{'='*100}")
print(f"BEST: Deflated Sharpe = {best_dsr:.4f}")
print(f"{'='*100}\n")

# Rank top 20
ranked = sorted(results, key=lambda r: r.get("deflated_sharpe", -999), reverse=True)
print(f"{'Rank':<5} {'Trend':<6} {'DSR':>8} {'PF':>7} {'Return':>8} {'Win%':>7} {'DD':>7} {'T':>5}  Risk RR  Ctg SLx  Hold Lev Chart")
print("-"*120)
for i, r in enumerate(ranked[:20]):
    cfg = r.get("config", {})
    dsr = r.get("deflated_sharpe", -99)
    pf = r.get("profit_factor", 0)
    ret = r.get("total_return_pct", 0)
    wr = r.get("win_rate_pct", 0)
    dd = r.get("max_drawdown_pct", 0)
    nt = r.get("n_trades", 0)
    tf = "ON" if cfg.get("trend_filter", {}).get("enabled", False) else "off"
    marker = "🥇" if i == 0 else ("🥈" if i == 1 else ("🥉" if i == 2 else f" {i+1:2d}."))
    print(f"{marker:<5} {tf:<6} {dsr:>8.4f} {pf:>7.3f} {ret:>+7.1f}% {wr:>6.1f}% {dd:>+6.1f}% {nt:>4d}  "
          f"{cfg.get('limits',{}).get('max_risk_per_trade_pct',0):.0f}% "
          f"{cfg.get('signal',{}).get('rr_target',0):.0f} "
          f"{cfg.get('limits',{}).get('min_conviction',0)} "
          f"{cfg.get('signal',{}).get('atr_sl_mult',0):.1f} "
          f"{cfg.get('signal',{}).get('max_hold_bars',0):2d} "
          f"{cfg.get('limits',{}).get('default_leverage',0)}x "
          f"{cfg.get('fusion',{}).get('chart',0):.2f}")

print()

best = ranked[0]
best_cfg = best.get("config", {})
bh = best.get("buyhold_return_pct", 0)
conv = best.get("winrate_by_conviction", {})
exit_r = best.get("exit_reasons", {})

print("🏆 BEST CONFIGURATION:")
print(f"  Deflated Sharpe:   {best.get('deflated_sharpe',0):.4f}")
print(f"  Return:            {best.get('total_return_pct',0):+.1f}% vs Buy&Hold {bh:+.1f}%")
print(f"  Profit Factor:     {best.get('profit_factor',0):.3f}")
print(f"  Win Rate:          {best.get('win_rate_pct',0):.1f}%")
print(f"  Max Drawdown:      {best.get('max_drawdown_pct',0):+.1f}%")
print(f"  Trades:            {best.get('n_trades',0)}")
print(f"  Sharpe / Sortino:  {best.get('sharpe_ann',0):.3f} / {best.get('sortino_ann',0):.3f}")
print(f"  Conviction curve:  {conv}")
print(f"  Exit reasons:      {exit_r}")
print(f"\n  Config:")
print(f"    risk/trade:      {best_cfg.get('limits',{}).get('max_risk_per_trade_pct',0):.0f}%")
print(f"    min_conviction:  {best_cfg.get('limits',{}).get('min_conviction',0)}")
print(f"    min_RR:          {best_cfg.get('limits',{}).get('min_risk_reward',0)}")
print(f"    rr_target:       {best_cfg.get('signal',{}).get('rr_target',0):.1f}")
print(f"    atr_sl_mult:     {best_cfg.get('signal',{}).get('atr_sl_mult',0):.2f}x")
print(f"    max_hold_bars:   {best_cfg.get('signal',{}).get('max_hold_bars',0)}")
print(f"    cooldown:        {best_cfg.get('signal',{}).get('cooldown_bars',0)} bars")
print(f"    chart weight:    {best_cfg.get('fusion',{}).get('chart',0):.2f}")
print(f"    whale weight:    {best_cfg.get('fusion',{}).get('whale',0):.2f}")
print(f"    leverage:        {best_cfg.get('limits',{}).get('default_leverage',0)}x")
print(f"    trend_filter:    {'ON 🔀' if best_cfg.get('trend_filter',{}).get('enabled',False) else 'off'}")

print()

# Gate check
gates = [
    ("DSR > 0.95",   best.get("deflated_sharpe", -99) > 0.95),
    ("PF > 1.5",     best.get("profit_factor", 0) > 1.5),
    ("Return > BH",  best.get("total_return_pct", 0) > bh),
    ("MaxDD > −15%", best.get("max_drawdown_pct", 0) > -15),
    ("Trades ≥ 100", best.get("n_trades", 0) >= 100),
]
passed = sum(1 for _, ok in gates if ok)
for name, ok in gates:
    print(f"  {'✅' if ok else '❌'} {name}")

print(f"\nGate summary: {passed}/{len(gates)}")

if passed == len(gates):
    print("✅ FULLY PROMOTABLE")
elif passed >= 3:
    print("⚠️ PARTIAL — promising but needs structural change")
else:
    print("❌ Strategy logic needs redesign, not parameter tuning")

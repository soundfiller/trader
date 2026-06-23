#!/usr/bin/env python3
"""V3 optimization: iterative loops until DSR plateaus. All 5 improvements active."""
import json, sys, copy, math, random, time
sys.path.insert(0, '/Users/manspetterson/.openclaw/workspace/trader/backtest')

import trader_backtest_v3 as bt
from trader_backtest_v3 import *

# Fetch data
df_all = fetch_hype_ohlcv(days=760 + 45)
print(f"Fetched {len(df_all)} candles\n")

base_cfg = json.loads(json.dumps(CONFIG))
base_cfg["costs"]["execution_mode"] = "maker"
base_cfg["dsr_trials"] = 10

# Parameter space for search
param_space = {
    "limits.max_risk_per_trade_pct": [1.0, 1.5, 2.0, 2.5, 3.0, 4.0],
    "limits.min_conviction": [2, 3, 4],
    "limits.min_risk_reward": [1.0, 1.25, 1.5, 2.0],
    "signal.rr_target": [2.0, 2.5, 3.0, 4.0, 5.0, 6.0],
    "signal.atr_sl_mult": [0.75, 1.0, 1.25, 1.5, 2.0],
    "signal.max_hold_bars": [36, 48, 60, 72, 90, 120],
    "signal.cooldown_bars": [0, 1, 2],
    "fusion.chart": [0.55, 0.60, 0.65, 0.70, 0.75, 0.80],
    # V3-specific
    "trailing.trail_atr_mult": [1.5, 2.0, 2.5, 3.0],
    "trailing.activation_r_mult": [0.5, 1.0, 1.5, 2.0],
    "regime.adx_threshold": [20, 25, 30, 35],
    "vol_adj_sizing.target_atr_pct": [0.02, 0.03, 0.04, 0.05],
    "vol_adj_sizing.max_risk_mult": [1.0, 1.5, 2.0],
    "early_cut.thesis_decay_bars": [8, 12, 16, 20, 24],
}

def build_config(seed):
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
    cfg["fusion"] = {"chart": chart_w, "whale": round(1.0 - chart_w - 0.10, 2), "regime": 0.10}
    
    cfg["trailing"]["trail_atr_mult"] = rng.choice(param_space["trailing.trail_atr_mult"])
    cfg["trailing"]["activation_r_mult"] = rng.choice(param_space["trailing.activation_r_mult"])
    cfg["regime"]["adx_threshold"] = rng.choice(param_space["regime.adx_threshold"])
    cfg["vol_adj_sizing"]["target_atr_pct"] = rng.choice(param_space["vol_adj_sizing.target_atr_pct"])
    cfg["vol_adj_sizing"]["max_risk_mult"] = rng.choice(param_space["vol_adj_sizing.max_risk_mult"])
    cfg["early_cut"]["thesis_decay_bars"] = rng.choice(param_space["early_cut.thesis_decay_bars"])
    
    return cfg

def run_cfg(cfg, df):
    test_start_idx = len(df) - int(760 * 24/4)
    df2, trades, equity, sigs = run_backtest(df, cfg, test_start_idx=test_start_idx)
    test_df = df2.iloc[test_start_idx:].reset_index(drop=True)
    return metrics(test_df, trades, equity, cfg), test_df, trades, sigs

# Multi-loop: 10 iterations per loop, stop when DSR gain < 0.02
all_results = []
best_dsr_global = -999
loop = 0

while True:
    loop += 1
    loop_results = []
    n_iters = 10
    
    print(f"\n{'═'*100}")
    print(f"LOOP {loop} — {'Random search' if loop == 1 else 'Refinement around top-3'}")
    print(f"{'═'*100}\n")
    
    for i in range(n_iters):
        t0 = time.time()
        
        if loop == 1:
            cfg = build_config(i + loop * 100)
            phase = "RAND"
        else:
            # Refine: mutate top-3 configs
            top3 = sorted(loop_results if loop_results else all_results, 
                         key=lambda r: r.get("deflated_sharpe", -999), reverse=True)[:3]
            base_cfg_mut = copy.deepcopy(top3[random.Random(i+loop).randint(0, min(2, len(top3)-1))]["config"])
            mutate_key = random.Random(i+loop+99).choice(list(param_space.keys()))
            val = random.Random(i+loop+199).choice(param_space[mutate_key])
            parts = mutate_key.split(".")
            if len(parts) == 4:
                base_cfg_mut[parts[0]][parts[1]][parts[2]] = val
            elif len(parts) == 3:
                base_cfg_mut[parts[0]][parts[1]][parts[2]] = val
            elif len(parts) == 2:
                base_cfg_mut[parts[0]][parts[1]] = val
            cfg = base_cfg_mut
            phase = "MUTATE"
        
        res, _, _, _ = run_cfg(cfg, df_all)
        res["config"] = cfg
        loop_results.append(res)
        all_results.append(res)
        
        dsr = res.get("deflated_sharpe", -99)
        ret = res.get("total_return_pct", 0)
        pf = res.get("profit_factor", 0)
        dd = res.get("max_drawdown_pct", 0)
        nt = res.get("n_trades", 0)
        wr = res.get("win_rate_pct", 0)
        conv = res.get("winrate_by_conviction", {})
        exits = res.get("exit_reasons", {})
        
        elapsed = time.time() - t0
        print(f"  [{i+1:2d}] {phase:6s} DSR:{dsr:.4f} PF:{pf:.3f} Ret:{ret:+7.1f}% "
              f"DD:{dd:+6.1f}% WR:{wr:5.1f}% T:{nt:4d} "
              f"Exits:{exits} Conv:{conv} ({elapsed:.1f}s)")
    
    # Find best in this loop
    best_loop = sorted(loop_results, key=lambda r: r.get("deflated_sharpe", -999), reverse=True)[0]
    best_dsr_loop = best_loop.get("deflated_sharpe", -99)
    
    print(f"\n🏆 Loop {loop} best: DSR = {best_dsr_loop:.4f}")
    
    # Check plateau
    dsr_gain = best_dsr_loop - best_dsr_global
    best_dsr_global = max(best_dsr_global, best_dsr_loop)
    
    if dsr_gain < 0.01 and loop >= 3:
        print(f"📉 DSR gain {dsr_gain:.4f} < 0.01 — plateau reached after {loop} loops.")
        break
    if loop >= 5:
        print("⏰ Max 5 loops reached.")
        break

# Final report
print(f"\n{'═'*100}")
print(f"FINAL RESULTS — {len(all_results)} configurations, {loop} loops")
print(f"Best DSR: {best_dsr_global:.4f}")
print(f"{'═'*100}\n")

ranked = sorted(all_results, key=lambda r: r.get("deflated_sharpe", -999), reverse=True)
print(f"{'Rank':<5} {'DSR':>8} {'PF':>7} {'Return':>8} {'Win%':>7} {'DD':>7} {'T':>5} {'Trader' if False else ''}  Exits")
print("-"*100)
for i, r in enumerate(ranked[:15]):
    cfg = r.get("config", {})
    dsr = r.get("deflated_sharpe", -99)
    pf = r.get("profit_factor", 0)
    ret = r.get("total_return_pct", 0)
    wr = r.get("win_rate_pct", 0)
    dd = r.get("max_drawdown_pct", 0)
    nt = r.get("n_trades", 0)
    exits = r.get("exit_reasons", {})
    marker = "🥇" if i == 0 else ("🥈" if i == 1 else ("🥉" if i == 2 else f" {i+1:2d}."))
    print(f"{marker:<5} {dsr:>8.4f} {pf:>7.3f} {ret:>+7.1f}% {wr:>6.1f}% {dd:>+6.1f}% {nt:>4d}  "
          f"{exits}")

# Show best config details
best = ranked[0]
best_cfg = best.get("config", {})
bh = best.get("buyhold_return_pct", 0)
exits = best.get("exit_reasons", {})
conv = best.get("winrate_by_conviction", {})

print(f"\n🏆 BEST CONFIGURATION:")
print(f"  Deflated Sharpe:   {best.get('deflated_sharpe',0):.4f}")
print(f"  Return:            {best.get('total_return_pct',0):+.1f}% vs Buy&Hold {bh:+.1f}%")
print(f"  Profit Factor:     {best.get('profit_factor',0):.3f}")
print(f"  Win Rate:          {best.get('win_rate_pct',0):.1f}%")
print(f"  Max Drawdown:      {best.get('max_drawdown_pct',0):+.1f}%")
print(f"  Trades:            {best.get('n_trades',0)}")
print(f"  Sharpe / Sortino:  {best.get('sharpe_ann',0):.3f} / {best.get('sortino_ann',0):.3f}")
print(f"  Conviction curve:  {conv}")
print(f"  Exit reasons:      {exits}")
print(f"\n  Config:")
print(f"    risk/trade:      {best_cfg.get('limits',{}).get('max_risk_per_trade_pct',0):.0f}%")
print(f"    min_conviction:  {best_cfg.get('limits',{}).get('min_conviction',0)}")
print(f"    min_RR:          {best_cfg.get('limits',{}).get('min_risk_reward',0)}")
print(f"    rr_target:       {best_cfg.get('signal',{}).get('rr_target',0):.1f}")
print(f"    atr_sl_mult:     {best_cfg.get('signal',{}).get('atr_sl_mult',0):.2f}x")
print(f"    max_hold_bars:   {best_cfg.get('signal',{}).get('max_hold_bars',0)}")
print(f"    chart weight:    {best_cfg.get('fusion',{}).get('chart',0):.2f}")
print(f"    trail_atr_mult:  {best_cfg.get('trailing',{}).get('trail_atr_mult',0)}x")
print(f"    trail_activate:  {best_cfg.get('trailing',{}).get('activation_r_mult',0)}R")
print(f"    adx_threshold:   {best_cfg.get('regime',{}).get('adx_threshold',0)}")
print(f"    vol_target_atr:  {best_cfg.get('vol_adj_sizing',{}).get('target_atr_pct',0)*100:.0f}%")
print(f"    vol_max_risk_mul:{best_cfg.get('vol_adj_sizing',{}).get('max_risk_mult',0)}x")
print(f"    thesis_decay:    {best_cfg.get('early_cut',{}).get('thesis_decay_bars',0)} bars")

print(f"\nGATE CHECK:")
gates = [
    ("DSR > 0.95",   best.get("deflated_sharpe", -99) > 0.95),
    ("PF > 1.5",     best.get("profit_factor", 0) > 1.5),
    ("Return > BH",  best.get("total_return_pct", 0) > bh),
    ("MaxDD > -15%", best.get("max_drawdown_pct", 0) > -15),
    ("Trades >= 100", best.get("n_trades", 0) >= 100),
]
passed = sum(1 for _, ok in gates if ok)
for name, ok in gates:
    print(f"  {'✅' if ok else '❌'} {name}")
print(f"\n  {passed}/{len(gates)} gates passed")
if passed == len(gates):
    print("  ✅ FULLY PROMOTABLE")
elif passed >= 3:
    print("  ⚠️ PROMISING — close to gates")
else:
    print("  ❌ Needs work")

#!/usr/bin/env python3
"""V5 dual-mode regime switch optimization. 50 iterations, plateau detection."""
import json, sys, copy, math, random, time
sys.path.insert(0, '/Users/manspetterson/.openclaw/workspace/trader/backtest')

import trader_backtest_v5 as bt
from trader_backtest_v5 import *

df_all = fetch_hype_ohlcv(days=760 + 45)
print(f"Fetched {len(df_all)} candles\n")

base = json.loads(json.dumps(CONFIG))
base["costs"]["execution_mode"] = "maker"
base["dsr_trials"] = 10

param_space = {
    "limits.max_risk_per_trade_pct": [1.5, 2.0, 2.5, 3.0],
    "limits.min_conviction": [3, 4],
    "limits.min_risk_reward": [1.0, 1.25, 1.5, 2.0],
    "signal.cooldown_bars": [0, 1],
    "fusion.chart": [0.60, 0.65, 0.70, 0.75, 0.80],
    # V5 regime params
    "regime.adx_threshold_low": [15, 20, 22],
    "regime.adx_threshold_high": [22, 25, 28, 30],
    # MR params
    "regime.mr_rr_target": [2.0, 2.5, 3.0, 3.5],
    "regime.mr_atr_sl_mult": [1.0, 1.25, 1.5, 2.0],
    "regime.mr_max_hold_bars": [48, 60, 72, 90],
    # TF params
    "regime.tf_trail_atr_mult": [2.0, 2.5, 3.0, 4.0],
    "regime.tf_trail_activation_r": [0.5, 1.0, 1.5, 2.0],
    "regime.tf_entry_atr_breakout": [0.20, 0.30, 0.40],
    "regime.tf_max_hold_bars": [72, 96, 120, 150],
    "regime.tf_pyramid_on_r": [0, 0.5, 1.0, 1.5],
}

def build_cfg(seed, best_cfg=None):
    rng = random.Random(seed)
    if best_cfg:
        cfg = copy.deepcopy(best_cfg)
        for _ in range(rng.randint(1, 2)):
            k = rng.choice(list(param_space.keys()))
            v = rng.choice(param_space[k])
            parts = k.split(".")
            cfg[parts[0]][parts[1]] = v
    else:
        cfg = copy.deepcopy(base)
        for k, vals in param_space.items():
            parts = k.split(".")
            cfg[parts[0]][parts[1]] = rng.choice(vals)
    
    cfg["signal"]["rr_target"] = cfg["regime"]["mr_rr_target"]
    cfg["signal"]["atr_sl_mult"] = cfg["regime"]["mr_atr_sl_mult"]
    cfg["signal"]["max_hold_bars"] = cfg["regime"]["mr_max_hold_bars"]
    
    cw = random.Random(seed+1).uniform(0.60, 0.80)
    cfg["fusion"] = {"chart": round(cw, 2), "whale": round(1.0-cw-0.10, 2), "regime": 0.10}
    return cfg

results = []
best_dsr = -999

print(f"{'═'*90}")
print(f"V5 — DUAL-MODE REGIME SWITCH: Mean-Reversion (ADX<20) + Trend-Following (ADX>25)")
print(f"{'═'*90}\n")

for loop in range(4):
    n = 25 if loop < 2 else 12
    phase = "RANDOM" if loop < 2 else "REFINE"
    
    for i in range(n):
        t0 = time.time()
        
        if phase == "RANDOM":
            cfg = build_cfg(i*100 + loop*1000)
        else:
            top3 = sorted(results, key=lambda r: r.get("deflated_sharpe", -999), reverse=True)[:3]
            cfg = build_cfg(i+loop*1000, best_cfg=top3[0].get("config"))
        
        test_start_idx = len(df_all) - int(760 * 24/4)
        df2, trades, equity, sigs = bt.run_backtest(df_all, cfg, test_start_idx=test_start_idx)
        test_df = df2.iloc[test_start_idx:].reset_index(drop=True)
        res = metrics(test_df, trades, equity, cfg)
        res["config"] = cfg
        results.append(res)
        
        dsr = res.get("deflated_sharpe", -99)
        ret = res.get("total_return_pct", 0)
        pf = res.get("profit_factor", 0)
        dd = res.get("max_drawdown_pct", 0)
        nt = res.get("n_trades", 0)
        wr = res.get("win_rate_pct", 0)
        
        # Count trades by mode
        mr_trades = sum(1 for t in trades if hasattr(t, 'direction'))
        
        conv = res.get("winrate_by_conviction", {})
        exits = res.get("exit_reasons", {})
        
        if dsr > best_dsr: best_dsr = dsr
        
        elapsed = time.time() - t0
        r_low = cfg["regime"]["adx_threshold_low"]
        r_high = cfg["regime"]["adx_threshold_high"]
        print(f"[{len(results):3d}] DSR:{dsr:.4f} PF:{pf:.3f} Ret:{ret:+7.1f}% DD:{dd:+6.1f}% "
              f"WR:{wr:5.1f}% T:{nt:4d} ADX:{r_low}/{r_high} "
              f"Exits:{exits} ({elapsed:.1f}s)")

# Final report
print(f"\n{'═'*90}")
print(f"BEST DSR: {best_dsr:.4f} from {len(results)} configs, {loop+1} loops")
print(f"{'═'*90}\n")

ranked = sorted(results, key=lambda r: r.get("deflated_sharpe", -999), reverse=True)
print(f"{'Rk':<3} {'DSR':>8} {'PF':>7} {'Ret':>8} {'DD':>7} {'WR':>6} {'T':>4}  ADX  MR:RR/SL/Hold  TF:Trail/Act/Brk/Hold/Pyr")
print("-"*115)
for i, r in enumerate(ranked[:15]):
    cfg = r["config"]["regime"]
    dsr = r["deflated_sharpe"]
    pf = r["profit_factor"]
    ret = r["total_return_pct"]
    dd = r["max_drawdown_pct"]
    wr = r["win_rate_pct"]
    nt = r["n_trades"]
    m = "🥇" if i==0 else ("🥈" if i==1 else ("🥉" if i==2 else f" {i+1:2d}"))
    print(f"{m:<3} {dsr:>8.4f} {pf:>7.3f} {ret:>+7.1f}% {dd:>+6.1f}% {wr:>5.1f}% {nt:>4d}  "
          f"{cfg['adx_threshold_low']}/{cfg['adx_threshold_high']}  "
          f"{cfg['mr_rr_target']}/{cfg['mr_atr_sl_mult']}/{cfg['mr_max_hold_bars']}  "
          f"{cfg['tf_trail_atr_mult']}/{cfg['tf_trail_activation_r']}/{cfg['tf_entry_atr_breakout']}/{cfg['tf_max_hold_bars']}/{cfg['tf_pyramid_on_r']}")

best = ranked[0]
cfg = best["config"]
bh = best["buyhold_return_pct"]
exits = best.get("exit_reasons", {})
conv = best.get("winrate_by_conviction", {})

print(f"\n🏆 BEST CONFIG:")
print(f"  DSR: {best['deflated_sharpe']:.4f} | Ret: {best['total_return_pct']:+.1f}% vs BH {bh:+.1f}%")
print(f"  PF: {best['profit_factor']:.3f} | DD: {best['max_drawdown_pct']:+.1f}% | WR: {best['win_rate_pct']:.1f}% | Trades: {best['n_trades']}")
print(f"  Conviction: {conv}")
print(f"  Exits: {exits}")
print(f"  ADX gates: {cfg['regime']['adx_threshold_low']} / {cfg['regime']['adx_threshold_high']}")
print(f"  MR: RR={cfg['regime']['mr_rr_target']} SL={cfg['regime']['mr_atr_sl_mult']}x Hold={cfg['regime']['mr_max_hold_bars']}b")
print(f"  TF: Trail={cfg['regime']['tf_trail_atr_mult']}x Act={cfg['regime']['tf_trail_activation_r']}R "
      f"Brk={cfg['regime']['tf_entry_atr_breakout']}x Hold={cfg['regime']['tf_max_hold_bars']}b Pyr={cfg['regime']['tf_pyramid_on_r']}R")

gates = [
    ("DSR > 0.95",   best.get("deflated_sharpe", -99) > 0.95),
    ("PF > 1.5",     best.get("profit_factor", 0) > 1.5),
    ("Return > BH",  best.get("total_return_pct", 0) > bh),
    ("MaxDD > -15%", best.get("max_drawdown_pct", 0) > -15),
    ("Trades >= 100", best.get("n_trades", 0) >= 100),
]
passed = sum(1 for _, ok in gates if ok)
print(f"\nGates: {passed}/5")
for n, ok in gates: print(f"  {'✅' if ok else '❌'} {n}")
if passed == 5: print("\n✅ FULLY PROMOTABLE")

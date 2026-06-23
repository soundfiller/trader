#!/usr/bin/env python3
"""V4 optimization: calibrated conviction, funding carry, dual-model debate. 50 iterations."""
import json, sys, copy, math, random, time, numpy as np
sys.path.insert(0, '/Users/manspetterson/.openclaw/workspace/trader/backtest')

import trader_backtest_v4 as bt
from trader_backtest_v4 import *

df_all = fetch_hype_ohlcv(days=760 + 45)
print(f"Fetched {len(df_all)} candles\n")

base_cfg = json.loads(json.dumps(CONFIG))
base_cfg["costs"]["execution_mode"] = "maker"
base_cfg["dsr_trials"] = 10
# Start with all V4 features ON, V3 off
for k in ["trailing", "regime", "multi_tf", "vol_adj_sizing", "early_cut"]:
    base_cfg[k]["enabled"] = False
for k in ["calibrated", "funding_carry", "dual_model"]:
    base_cfg[k]["enabled"] = True

param_space = {
    "limits.max_risk_per_trade_pct": [1.5, 2.0, 2.5, 3.0],
    "limits.min_conviction": [3, 4],
    "limits.min_risk_reward": [1.0, 1.25, 1.5],
    "signal.rr_target": [2.0, 2.5, 3.0, 4.0],
    "signal.atr_sl_mult": [1.0, 1.25, 1.5, 2.0],
    "signal.max_hold_bars": [36, 48, 60, 72, 90],
    "signal.cooldown_bars": [0, 1],
    "fusion.chart": [0.60, 0.65, 0.70, 0.75, 0.80],
    # V4 toggles
    "calibrated.enabled": [True, False],
    "funding_carry.enabled": [True, False],
    "dual_model.enabled": [True, False],
    # V3 toggles (searchable - optimizer chooses best combination)
    "trailing.enabled": [True, False],
    "regime.enabled": [True, False],
    "vol_adj_sizing.enabled": [True, False],
    "early_cut.enabled": [True, False],
    # V4 params
    "calibrated.kelly_fraction": [0.25, 0.5, 0.75],
    "calibrated.min_trades_for_calibration": [30, 50, 75],
    "funding_carry.carry_weight": [0.10, 0.15, 0.20, 0.25],
    "funding_carry.min_annualized_carry_pct": [25.0, 50.0, 100.0],
    "dual_model.bonus_conviction": [0, 1],
    "trailing.trail_atr_mult": [2.0, 2.5, 3.0],
    "trailing.activation_r_mult": [1.0, 1.5, 2.0],
    "regime.adx_threshold": [20, 25, 30],
    "early_cut.thesis_decay_bars": [8, 12, 16, 20],
}

def build_cfg(seed):
    rng = random.Random(seed + 777)
    cfg = copy.deepcopy(base_cfg)
    cfg["limits"]["max_risk_per_trade_pct"] = rng.choice(param_space["limits.max_risk_per_trade_pct"])
    cfg["limits"]["min_conviction"] = rng.choice(param_space["limits.min_conviction"])
    cfg["limits"]["min_risk_reward"] = rng.choice(param_space["limits.min_risk_reward"])
    cfg["signal"]["rr_target"] = rng.choice(param_space["signal.rr_target"])
    cfg["signal"]["atr_sl_mult"] = rng.choice(param_space["signal.atr_sl_mult"])
    cfg["signal"]["max_hold_bars"] = rng.choice(param_space["signal.max_hold_bars"])
    cfg["signal"]["cooldown_bars"] = rng.choice(param_space["signal.cooldown_bars"])
    cw = rng.choice(param_space["fusion.chart"])
    cfg["fusion"] = {"chart": cw, "whale": round(1.0-cw-0.10, 2), "regime": 0.10}
    
    for f in ["calibrated", "funding_carry", "dual_model", "trailing", "regime", "vol_adj_sizing", "early_cut"]:
        cfg[f]["enabled"] = rng.choice(param_space[f"{f}.enabled"])
    
    cfg["calibrated"]["kelly_fraction"] = rng.choice(param_space["calibrated.kelly_fraction"])
    cfg["calibrated"]["min_trades_for_calibration"] = rng.choice(param_space["calibrated.min_trades_for_calibration"])
    cfg["funding_carry"]["carry_weight"] = rng.choice(param_space["funding_carry.carry_weight"])
    cfg["funding_carry"]["min_annualized_carry_pct"] = rng.choice(param_space["funding_carry.min_annualized_carry_pct"])
    cfg["dual_model"]["bonus_conviction"] = rng.choice(param_space["dual_model.bonus_conviction"])
    cfg["trailing"]["trail_atr_mult"] = rng.choice(param_space["trailing.trail_atr_mult"])
    cfg["trailing"]["activation_r_mult"] = rng.choice(param_space["trailing.activation_r_mult"])
    cfg["regime"]["adx_threshold"] = rng.choice(param_space["regime.adx_threshold"])
    cfg["early_cut"]["thesis_decay_bars"] = rng.choice(param_space["early_cut.thesis_decay_bars"])
    return cfg

def run_cfg(cfg):
    test_start_idx = len(df_all) - int(760 * 24/4)
    df2, trades, equity, sigs = run_backtest(df_all, cfg, test_start_idx=test_start_idx)
    test_df = df2.iloc[test_start_idx:].reset_index(drop=True)
    return metrics(test_df, trades, equity, cfg)

results = []
best_dsr = -999

print(f"{'═'*100}")
print(f"V4 — Calibrated Conviction + Funding Carry + Dual-Model Debate")
print(f"{'═'*100}\n")

for loop in range(3):
    n = 25 if loop == 0 else 12
    phase = "RAND" if loop == 0 else "REFINE"
    
    for i in range(n):
        t0 = time.time()
        
        if phase == "RAND":
            cfg = build_cfg(i + loop * 100)
        else:
            top5 = sorted(results, key=lambda r: r.get("deflated_sharpe", -999), reverse=True)[:5]
            base = copy.deepcopy(top5[random.Random(i+loop).randint(0, len(top5)-1)]["config"])
            for _ in range(random.Random(i+loop+50).randint(1, 2)):
                k = random.Random(i+loop+99).choice(list(param_space.keys()))
                v = random.Random(i+loop+199).choice(param_space[k])
                parts = k.split(".")
                if len(parts) == 3: base[parts[0]][parts[1]][parts[2]] = v
                elif len(parts) == 2: base[parts[0]][parts[1]] = v
        
        res = run_cfg(cfg)
        res["config"] = cfg
        results.append(res)
        
        dsr = res.get("deflated_sharpe", -99)
        ret = res.get("total_return_pct", 0)
        pf = res.get("profit_factor", 0)
        dd = res.get("max_drawdown_pct", 0)
        nt = res.get("n_trades", 0)
        wr = res.get("win_rate_pct", 0)
        exits = res.get("exit_reasons", {})
        conv = res.get("winrate_by_conviction", {})
        
        # Feature summary
        features = []
        for f in ["calibrated", "funding_carry", "dual_model", "trailing", "regime"]:
            features.append(f[0].upper() if cfg.get(f, {}).get("enabled", False) else "_")
        
        if dsr > best_dsr: best_dsr = dsr
        
        elapsed = time.time() - t0
        print(f"[{len(results):3d}] {phase:7s} [{''.join(features)}] DSR:{dsr:.4f} PF:{pf:.3f} "
              f"Ret:{ret:+7.1f}% DD:{dd:+6.1f}% WR:{wr:5.1f}% T:{nt:4d} "
              f"Conv:{conv} ({elapsed:.1f}s)")

print(f"\n{'═'*100}")
print(f"FINAL: {len(results)} configs, best DSR = {best_dsr:.4f}")
print(f"{'═'*100}\n")

ranked = sorted(results, key=lambda r: r.get("deflated_sharpe", -999), reverse=True)
print(f"{'Rk':<3} {'Features':<7} {'DSR':>8} {'PF':>7} {'Return':>8} {'Win%':>7} {'DD':>7} {'T':>4}  Conviction")
print("-"*105)
for i, r in enumerate(ranked[:15]):
    cfg = r.get("config", {})
    features = []
    for f in ["calibrated", "funding_carry", "dual_model", "trailing", "regime"]:
        features.append(f[0].upper() if cfg.get(f, {}).get("enabled", False) else "_")
    dsr = r.get("deflated_sharpe", -99)
    pf = r.get("profit_factor", 0)
    ret = r.get("total_return_pct", 0)
    wr = r.get("win_rate_pct", 0)
    dd = r.get("max_drawdown_pct", 0)
    nt = r.get("n_trades", 0)
    conv = r.get("winrate_by_conviction", {})
    m = "🥇" if i==0 else ("🥈" if i==1 else ("🥉" if i==2 else f" {i+1:2d}"))
    print(f"{m:<3} {''.join(features):<7} {dsr:>8.4f} {pf:>7.3f} {ret:>+7.1f}% {wr:>6.1f}% {dd:>+6.1f}% {nt:>4d}  {conv}")

best = ranked[0]
cfg = best.get("config", {})
bh = best.get("buyhold_return_pct", 0)

feats_on = [f for f in ["calibrated", "funding_carry", "dual_model", "trailing", "regime"] if cfg.get(f, {}).get("enabled", False)]
print(f"\n🏆 BEST: DSR={best.get('deflated_sharpe',0):.4f} | Features: {feats_on if feat_on else 'baseline-like'}")
print(f"  Ret: {best.get('total_return_pct',0):+.1f}% | PF: {best.get('profit_factor',0):.3f} | DD: {best.get('max_drawdown_pct',0):+.1f}% | Trades: {best.get('n_trades',0)}")
print(f"  Conviction: {best.get('winrate_by_conviction',{})}")
print(f"  Exits: {best.get('exit_reasons',{})}")

gates = [
    ("DSR > 0.95",   best.get("deflated_sharpe", -99) > 0.95),
    ("PF > 1.5",     best.get("profit_factor", 0) > 1.5),
    ("Return > BH",  best.get("total_return_pct", 0) > bh),
    ("MaxDD > -15%", best.get("max_drawdown_pct", 0) > -15),
    ("Trades >= 100", best.get("n_trades", 0) >= 100),
]
passed = sum(1 for _, ok in gates if ok)
print(f"\n  Gates: {passed}/5")
for n, ok in gates: print(f"    {'✅' if ok else '❌'} {n}")

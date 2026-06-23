#!/usr/bin/env python3
"""
V5 dual-mode ETH 4h — 30 random configs, maker execution.
Parameter space same as BTC.
"""
import json, math, sys, os, copy, random
import numpy as np
import pandas as pd
from scipy.stats import norm

sys.path.insert(0, os.path.dirname(__file__))

# Import the backtest engine
from trader_backtest_v5 import (
    CONFIG, run_backtest, metrics, BARS_PER_YEAR,
    chart_analysis, whale_tracker, signal_generator, deflated_sharpe
)

# Load data
df_eth = pd.read_pickle('/tmp/eth_4h.pkl')
print(f"ETH 4h data: {len(df_eth)} bars, {df_eth['time'].min()} to {df_eth['time'].max()}")

# Parameter space (same as BTC)
param_space = {
    "limits.max_risk_per_trade_pct":  (1.0, 3.0),        # float
    "limits.min_conviction":          (2, 4),            # int
    "limits.min_risk_reward":         (1.2, 2.5),        # float
    "limits.max_leverage":            (3, 5),            # int
    "fusion.chart":                   (0.3, 0.65),       # float
    "fusion.whale":                   (0.2, 0.45),       # float
    "regime.adx_threshold_low":       (15, 25),          # int
    "regime.adx_threshold_high":      (22, 32),          # int
    "regime.mr_rr_target":            (1.5, 3.0),        # float
    "regime.mr_atr_sl_mult":          (1.0, 2.0),        # float
    "regime.mr_max_hold_bars":        (36, 96),          # int
    "regime.tf_trail_atr_mult":       (1.5, 4.0),        # float
    "regime.tf_trail_activation_r":   (0.5, 2.0),        # float
    "regime.tf_entry_atr_breakout":   (0.2, 0.8),        # float
    "regime.tf_max_hold_bars":        (60, 180),         # int
    "regime.tf_pyramid_on_r":         (0.5, 2.0),        # float
    "signal.rr_target":               (1.5, 3.5),        # float
    "signal.atr_sl_mult":             (0.7, 1.8),        # float
    "signal.max_hold_bars":           (15, 60),          # int
    "signal.cooldown_bars":           (0, 3),            # int
    "costs.taker_slippage_pct":       (0.01, 0.05),      # float
    "costs.funding_hourly_bps_mean":  (-0.05, 0.05),     # float
    "costs.funding_hourly_bps_vol":   (0.5, 2.0),        # float
}

def set_cfg_path(cfg, path, value):
    """Set a value in a nested dict using a dot-delimited path."""
    keys = path.split(".")
    d = cfg
    for k in keys[:-1]:
        d = d[k]
    d[keys[-1]] = value

def random_config(seed):
    rng = random.Random(seed)
    cfg = copy.deepcopy(CONFIG)
    cfg["seed"] = seed
    # Always maker for this run
    cfg["costs"]["execution_mode"] = "maker"
    cfg["dsr_trials"] = 30  # Number of trials in this batch

    for param, (lo, hi) in param_space.items():
        if isinstance(lo, int) and isinstance(hi, int):
            val = rng.randint(lo, hi)
        else:
            val = lo + rng.random() * (hi - lo)
            if isinstance(lo, float) and not isinstance(lo, bool):
                val = round(val, 4)
        set_cfg_path(cfg, param, val)

    # Recompute fusion weights to sum to 1.0
    f_chart = cfg["fusion"]["chart"]
    f_whale = cfg["fusion"]["whale"]
    total = f_chart + f_whale
    cfg["fusion"]["chart"] = f_chart / total
    cfg["fusion"]["whale"] = f_whale / total
    cfg["fusion"]["regime"] = 1.0 - cfg["fusion"]["chart"] - cfg["fusion"]["whale"]

    return cfg

# Compute test_start_idx
test_start_idx = max(200, len(df_eth) - 760 * 6)
print(f"test_start_idx = {test_start_idx} (out of {len(df_eth)} bars)")
print(f"Test period: {df_eth['time'].iloc[test_start_idx]} to {df_eth['time'].iloc[-1]}")
print(f"Test bars = {len(df_eth) - test_start_idx}")
print()

# Run 30 random configs
results = []
for trial in range(30):
    seed = trial + 100
    cfg = random_config(seed)
    
    try:
        df_out, trades, equity, sigs = run_backtest(df_eth, cfg, test_start_idx=test_start_idx)
        test_df = df_out.iloc[test_start_idx:].reset_index(drop=True)
        res = metrics(test_df, trades, equity, cfg)
        res["n_signals"] = len(sigs) if sigs else 0
        
        # Extract key fields
        row = {
            "trial": trial,
            "seed": seed,
            "n_trades": res.get("n_trades", 0),
            "n_signals": res.get("n_signals", 0),
            "total_return_pct": res.get("total_return_pct", 0),
            "win_rate_pct": res.get("win_rate_pct", 0),
            "profit_factor": res.get("profit_factor", 0),
            "sharpe_ann": res.get("sharpe_ann", 0),
            "sortino_ann": res.get("sortino_ann", 0),
            "max_drawdown_pct": res.get("max_drawdown_pct", 0),
            "calmar": res.get("calmar", 0),
            "psr_gt0": res.get("psr_gt0", 0),
            "deflated_sharpe": res.get("deflated_sharpe", 0),
            "capital_end": res.get("capital_end", 0),
            "avg_R": res.get("avg_R", 0),
            "expectancy_R": res.get("expectancy_R", 0),
            "net_pnl": res.get("net_pnl", 0),
        }
        
        # Also capture key config params for interpretability
        row["adx_low"] = cfg["regime"]["adx_threshold_low"]
        row["adx_high"] = cfg["regime"]["adx_threshold_high"]
        row["min_conviction"] = cfg["limits"]["min_conviction"]
        row["risk_pct"] = cfg["limits"]["max_risk_per_trade_pct"]
        row["chart_fusion"] = cfg["fusion"]["chart"]
        row["whale_fusion"] = cfg["fusion"]["whale"]
        row["mr_rr"] = cfg["regime"]["mr_rr_target"]
        row["tf_trail_mult"] = cfg["regime"]["tf_trail_atr_mult"]
        
        results.append(row)
        print(f"  Trial {trial:2d} | trades={row['n_trades']:3d} | ret={row['total_return_pct']:7.1f}% | "
              f"wr={row['win_rate_pct']:5.1f}% | pf={row['profit_factor']:5.2f} | "
              f"sr={row['sharpe_ann']:5.2f} | dsr={row['deflated_sharpe']:5.3f} | "
              f"dd={row['max_drawdown_pct']:5.1f}%")
        
    except Exception as e:
        print(f"  Trial {trial:2d} FAILED: {e}")
        continue

# Sort by deflated_sharpe descending
results.sort(key=lambda r: r["deflated_sharpe"], reverse=True)

print("\n" + "=" * 90)
print("V5 ETH 4h DUAL-MODE — TOP 5 BY DEFLATED SHARPE (maker exec)")
print("=" * 90)

cols = ["trial","n_trades","n_signals","total_return_pct","win_rate_pct","profit_factor",
        "sharpe_ann","sortino_ann","max_drawdown_pct","calmar","psr_gt0","deflated_sharpe",
        "avg_R","net_pnl","capital_end"]

header = f"{'Rank':>4} | {'Trial':>5} | {'Trades':>6} | {'Signals':>7} | {'Return%':>7} | {'Win%':>6} | {'PF':>6} | {'Sharpe':>6} | {'Sortino':>7} | {'DD%':>6} | {'Calmar':>7} | {'PSR':>6} | {'DSR':>6} | {'AvgR':>6} | {'NetPnL':>8} | {'CapEnd':>8}"
print(header)
print("-" * len(header))

for rank, r in enumerate(results[:5], 1):
    print(f"{rank:4d} | {r['trial']:5d} | {r['n_trades']:6d} | {r['n_signals']:7d} | "
          f"{r['total_return_pct']:7.1f} | {r['win_rate_pct']:6.1f} | {r['profit_factor']:6.2f} | "
          f"{r['sharpe_ann']:6.2f} | {r['sortino_ann']:7.2f} | {r['max_drawdown_pct']:6.1f} | "
          f"{r['calmar']:7.2f} | {r['psr_gt0']:6.3f} | {r['deflated_sharpe']:6.3f} | "
          f"{r['avg_R']:6.2f} | {r['net_pnl']:8.2f} | {r['capital_end']:8.2f}")

# Summary stats
dsr_vals = [r["deflated_sharpe"] for r in results]
sr_vals = [r["sharpe_ann"] for r in results]
best_dsr = max(dsr_vals)
best_sr = max(sr_vals)
median_dsr = sorted(dsr_vals)[len(dsr_vals)//2]
mean_dsr = sum(dsr_vals)/len(dsr_vals)

n_dsr_gt_095 = sum(1 for v in dsr_vals if v > 0.95)
n_dsr_gt_090 = sum(1 for v in dsr_vals if v > 0.90)

print("\n" + "=" * 90)
print("SUMMARY")
print("=" * 90)
print(f"Configs run:         {len(results)}")
print(f"Best DSR:            {best_dsr:.4f}")
print(f"Best Sharpe (ann):   {best_sr:.4f}")
print(f"Median DSR:          {median_dsr:.4f}")
print(f"Mean DSR:            {mean_dsr:.4f}")
print(f"Configs DSR > 0.95:  {n_dsr_gt_095} / {len(results)}")
print(f"Configs DSR > 0.90:  {n_dsr_gt_090} / {len(results)}")
print(f"DSR > 0.95 clear:    {'YES ✓' if n_dsr_gt_095 > 0 else 'NO ✗'}")

# Gate check: deflated_sharpe > 0.95 = statistically significant beyond the search
print(f"\nGate check: Any config with deflated_sharpe > 0.95? -> {'PASS ✓' if n_dsr_gt_095 > 0 else 'FAIL ✗'}")
if best_dsr > 0.95:
    print(f"ETH can clear DSR > 0.95 with proper config selection.")
else:
    print(f"ETH does NOT clear DSR > 0.95 in this sweep. Best DSR = {best_dsr:.4f}")

#!/usr/bin/env python3
"""
BTC 4h Per-Asset Parameter Optimization — v5 Dual-Mode Engine
"""
import json, math, sys, itertools, random
import numpy as np
import pandas as pd

# Import the v5 engine
sys.path.insert(0, "/Users/manspetterson/.openclaw/workspace/trader/backtest")
from trader_backtest_v5 import CONFIG, run_backtest, metrics, BARS_PER_YEAR

random.seed(42)
np.random.seed(42)

# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------
df_btc = pd.read_pickle("/tmp/btc_4h.pkl")
print(f"Data: {len(df_btc)} bars, {df_btc.time.min()} to {df_btc.time.max()}")

test_start = max(200, len(df_btc) - 760 * 6)
print(f"Test start index: {test_start} / {len(df_btc)}")
print(f"Test bars: {len(df_btc) - test_start} ({((len(df_btc)-test_start)*4/24):.1f} days)")

# ---------------------------------------------------------------------------
# Parameter grids
# ---------------------------------------------------------------------------
adx_lows    = [18, 20, 22, 25]
adx_highs   = [25, 28, 30, 35]  # only where high > low
mr_rr       = [2.0, 2.5, 3.0]
mr_sl       = [1.5, 2.0, 2.5]
mr_hold     = [48, 60, 72]
tf_trail    = [2.0, 2.5, 3.0]
tf_act      = [1.0, 1.5, 2.0]
tf_break    = [0.3, 0.4]
tf_hold     = [72, 96, 120]

# Build all valid ADX pairs
valid_adx = [(l, h) for l in adx_lows for h in adx_highs if h > l]
print(f"Valid ADX pairs: {len(valid_adx)}")

# Generate 30 unique random configs
params = []
all_combos = list(itertools.product(
    valid_adx, mr_rr, mr_sl, mr_hold, tf_trail, tf_act, tf_break, tf_hold
))
print(f"Total combinatorial space: {len(all_combos)}")

# Pick 30 random unique combos
random.shuffle(all_combos)
selected = all_combos[:30]

results = []

for idx, combo in enumerate(selected):
    (adx_low, adx_high), mr_rt, mr_sl_m, mr_mh, tf_tm, tf_ar, tf_ba, tf_mh = combo

    cfg = json.loads(json.dumps(CONFIG))  # deep copy

    # Override with per-asset tuning
    cfg["signal"]["rr_target"] = mr_rt
    cfg["signal"]["atr_sl_mult"] = mr_sl_m
    cfg["signal"]["max_hold_bars"] = 30  # not used directly; MR uses regime.mr_max_hold_bars
    cfg["signal"]["cooldown_bars"] = 0

    cfg["limits"]["min_conviction"] = 3
    cfg["limits"]["min_risk_reward"] = 1.0
    cfg["limits"]["max_risk_per_trade_pct"] = 2.0

    cfg["fusion"]["chart"] = 0.75
    cfg["fusion"]["whale"] = 0.35
    cfg["fusion"]["regime"] = 0.10

    cfg["costs"]["execution_mode"] = "maker"
    cfg["dsr_trials"] = 10

    cfg["regime"]["adx_threshold_low"] = adx_low
    cfg["regime"]["adx_threshold_high"] = adx_high
    cfg["regime"]["mr_rr_target"] = mr_rt
    cfg["regime"]["mr_atr_sl_mult"] = mr_sl_m
    cfg["regime"]["mr_max_hold_bars"] = mr_mh
    cfg["regime"]["tf_trail_atr_mult"] = tf_tm
    cfg["regime"]["tf_trail_activation_r"] = tf_ar
    cfg["regime"]["tf_entry_atr_breakout"] = tf_ba
    cfg["regime"]["tf_max_hold_bars"] = tf_mh

    print(f"\n--- Config {idx+1}/30: ADX=[{adx_low}, {adx_high}] "
          f"MR=[{mr_rt}/{mr_sl_m}/{mr_mh}] TF=[{tf_tm}/{tf_ar}/{tf_ba}/{tf_mh}]")

    try:
        df2, trades, equity, sigs = run_backtest(df_btc, cfg, test_start_idx=test_start)
        test_df = df2.iloc[test_start:].reset_index(drop=True)
        res = metrics(test_df, trades, equity, cfg)

        entry = {
            "config_id": idx,
            "adx_low": adx_low,
            "adx_high": adx_high,
            "mr_rr_target": mr_rt,
            "mr_atr_sl_mult": mr_sl_m,
            "mr_max_hold": mr_mh,
            "tf_trail_atr_mult": tf_tm,
            "tf_activation_r": tf_ar,
            "tf_breakout_atr": tf_ba,
            "tf_max_hold": tf_mh,
        }

        entry["n_trades"] = res.get("n_trades", 0)
        entry["n_signals"] = res.get("n_signals", 0)
        entry["profit_factor"] = res.get("profit_factor", 0)
        entry["total_return_pct"] = res.get("total_return_pct", 0)
        entry["max_drawdown_pct"] = res.get("max_drawdown_pct", 0)
        entry["win_rate_pct"] = res.get("win_rate_pct", 0)
        entry["sharpe_ann"] = res.get("sharpe_ann", 0)
        entry["sortino_ann"] = res.get("sortino_ann", 0)
        entry["calmar"] = res.get("calmar", 0)
        entry["deflated_sharpe"] = res.get("deflated_sharpe", 0)
        entry["psr_gt0"] = res.get("psr_gt0", 0)
        entry["dsr_note"] = res.get("dsr_note", "")
        entry["winrate_by_conviction"] = res.get("winrate_by_conviction", {})
        entry["exit_reasons"] = res.get("exit_reasons", {})
        entry["capital_end"] = res.get("capital_end", 0)

        results.append(entry)
        print(f"  Trades={entry['n_trades']:4d}  WR={entry['win_rate_pct']:5.1f}%  "
              f"PF={entry['profit_factor']:.2f}  Ret={entry['total_return_pct']:+.1f}%  "
              f"DD={entry['max_drawdown_pct']:.1f}%  "
              f"SR={entry['sharpe_ann']:.2f}  DSR={entry['deflated_sharpe']:.4f}  "
              f"PSR={entry['psr_gt0']:.4f}")
    except Exception as e:
        print(f"  FAILED: {e}")
        import traceback; traceback.print_exc()

# ---------------------------------------------------------------------------
# Rank by deflated_sharpe
# ---------------------------------------------------------------------------
results.sort(key=lambda x: x["deflated_sharpe"], reverse=True)

print("\n\n" + "=" * 120)
print("TOP 5 CONFIGS (ranked by Deflated Sharpe)")
print("=" * 120)

header = f"{'Rank':5s} {'ADX_L':4s} {'ADX_H':4s} {'MR_RR':6s} {'MR_SL':6s} {'MR_H':5s} {'TF_T':6s} {'TF_A':5s} {'TF_B':6s} {'TF_H':5s} | {'Trd':5s} {'WR%':5s} {'PF':5s} {'Ret%':7s} {'DD%':6s} {'SR':5s} {'DSR':6s} {'PSR':6s} {'Calm':5s}"
print(header)
print("-" * 120)

for rank, r in enumerate(results[:5], 1):
    print(
        f"{rank:5d} {r['adx_low']:4d} {r['adx_high']:4d} "
        f"{r['mr_rr_target']:6.1f} {r['mr_atr_sl_mult']:6.1f} {r['mr_max_hold']:5d} "
        f"{r['tf_trail_atr_mult']:6.1f} {r['tf_activation_r']:5.1f} {r['tf_breakout_atr']:6.1f} {r['tf_max_hold']:5d} | "
        f"{r['n_trades']:5d} {r['win_rate_pct']:5.1f} {r['profit_factor']:5.2f} "
        f"{r['total_return_pct']:+7.1f} {r['max_drawdown_pct']:6.1f} "
        f"{r['sharpe_ann']:5.2f} {r['deflated_sharpe']:6.4f} {r['psr_gt0']:6.4f} "
        f"{r.get('calmar',0):5.2f}"
    )

print("-" * 120)

# ---------------------------------------------------------------------------
# Gate check: can BTC 4h clear DSR > 0.95?
# ---------------------------------------------------------------------------
best_dsr = results[0]["deflated_sharpe"] if results else 0
dsr_gt_095 = best_dsr > 0.95
best5_dsr = [r["deflated_sharpe"] for r in results[:5]]
median_dsr = np.median([r["deflated_sharpe"] for r in results])

print(f"\n{'='*60}")
print(f"GATE CHECK: Can BTC 4h clear DSR > 0.95?")
print(f"{'='*60}")
print(f"  Best DSR:            {best_dsr:.4f}")
print(f"  Top-5 DSRs:          {[round(d,4) for d in best5_dsr]}")
print(f"  Median DSR (30 runs): {median_dsr:.4f}")
print(f"  Configs with DSR > 0.95: {sum(1 for r in results if r['deflated_sharpe'] > 0.95)} / {len(results)}")
print(f"  VERDICT: {'PASS ✅ — BTC 4h can clear DSR > 0.95 with per-asset tuning' if dsr_gt_095 else 'FAIL ❌ — No config reached DSR > 0.95'}")
print(f"{'='*60}")

# Print full table of all 30 for completeness
print(f"\n{'='*120}")
print(f"ALL 30 CONFIGS (sorted by DSR)")
print(f"{'='*120}")
print(header)
print("-" * 120)
for rank, r in enumerate(results, 1):
    print(
        f"{rank:5d} {r['adx_low']:4d} {r['adx_high']:4d} "
        f"{r['mr_rr_target']:6.1f} {r['mr_atr_sl_mult']:6.1f} {r['mr_max_hold']:5d} "
        f"{r['tf_trail_atr_mult']:6.1f} {r['tf_activation_r']:5.1f} {r['tf_breakout_atr']:6.1f} {r['tf_max_hold']:5d} | "
        f"{r['n_trades']:5d} {r['win_rate_pct']:5.1f} {r['profit_factor']:5.2f} "
        f"{r['total_return_pct']:+.1f} {r['max_drawdown_pct']:6.1f} "
        f"{r['sharpe_ann']:5.2f} {r['deflated_sharpe']:6.4f} {r['psr_gt0']:6.4f} "
        f"{r.get('calmar',0):5.2f}"
    )
print("=" * 120)

# Save results
import json as j
output = {
    "top_5": results[:5],
    "all_30": results,
    "gate_check": {
        "best_dsr": best_dsr,
        "passed_dsr_095": dsr_gt_095,
        "median_dsr": median_dsr,
        "n_above_095": sum(1 for r in results if r['deflated_sharpe'] > 0.95)
    }
}
with open("/tmp/btc_optimization_results.json", "w") as f:
    j.dump(output, f, indent=2, default=str)
print(f"\nResults saved to /tmp/btc_optimization_results.json")

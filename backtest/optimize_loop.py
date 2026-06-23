#!/usr/bin/env python3
"""Optimization loop: re-run Trader backtest with iterative parameter adjustments.
10 loops max, tracks best Deflated Sharpe. Safe — only reads public HL candles."""
import json, sys, copy, math
sys.path.insert(0, '/Users/manspetterson/.openclaw/workspace/trader/backtest')
from trader_backtest import *

# Fetch HYPE data once
df_all = fetch_hype_ohlcv(days=760 + 45)
print(f"Fetched {len(df_all)} candles")

# Base config
base_cfg = json.loads(json.dumps(CONFIG))
base_cfg["costs"]["execution_mode"] = "maker"
base_cfg["dsr_trials"] = 10

# Iterations — each adjusts ONE thing from the base
variations = [
    {"name": "Baseline (current strategy)", "override": {}},
    
    # 1-3: Widen TP, let winners run
    {"name": "R:R target 3:1 (wider TP)", "override": {
        "signal": {"rr_target": 3.0}
    }},
    {"name": "R:R target 4:1 + expiry 60 bars", "override": {
        "signal": {"rr_target": 4.0, "max_hold_bars": 60}
    }},
    {"name": "R:R target 2.5 + tighter SL (0.75x ATR)", "override": {
        "signal": {"rr_target": 2.5, "atr_sl_mult": 0.75}
    }},
    
    # 4-6: Conviction filtering
    {"name": "Only conviction ≥ 4", "override": {
        "limits": {"min_conviction": 4}
    }},
    {"name": "Conv ≥ 4 + R:R ≥ 2.0", "override": {
        "limits": {"min_conviction": 4, "min_risk_reward": 2.0}
    }},
    {"name": "Conv ≥ 4 + longer expiry (72 bars)", "override": {
        "limits": {"min_conviction": 4},
        "signal": {"max_hold_bars": 72}
    }},
    
    # 7-8: Trend-following — heavier chart, tighter SL, wider TP
    {"name": "Trend: chart 0.75 + whale 0.15 + R:R 3:1", "override": {
        "fusion": {"chart": 0.75, "whale": 0.15, "regime": 0.10},
        "signal": {"rr_target": 3.0, "atr_sl_mult": 1.25}
    }},
    {"name": "Trend: chart 0.80 + SL 1.5xATR + R:R 4:1", "override": {
        "fusion": {"chart": 0.80, "whale": 0.10, "regime": 0.10},
        "signal": {"rr_target": 4.0, "atr_sl_mult": 1.5, "max_hold_bars": 48}
    }},
    
    # 9-10: Risk-adjusted sizing
    {"name": "Risk 1% + wider SL + R:R 3:1", "override": {
        "limits": {"max_risk_per_trade_pct": 1.0},
        "signal": {"rr_target": 3.0, "atr_sl_mult": 1.5}
    }},
    {"name": "Risk 3% + tight SL (0.75xATR) + R:R 2:1", "override": {
        "limits": {"max_risk_per_trade_pct": 3.0},
        "signal": {"rr_target": 2.0, "atr_sl_mult": 0.75}
    }},
]

def deep_merge(base, override):
    """Merge override dict into base, recursively."""
    result = copy.deepcopy(base)
    for k, v in override.items():
        if isinstance(v, dict) and k in result and isinstance(result[k], dict):
            result[k] = deep_merge(result[k], v)
        else:
            result[k] = v
    return result

results = []
best_dsr = -999

print(f"\n{'='*80}")
print(f"OPTIMIZATION LOOP — 11 configs, 2yr HYPE 4h, 760 days evaluated")
print(f"{'='*80}\n")

for i, var in enumerate(variations):
    cfg = deep_merge(base_cfg, var["override"])
    
    # Run backtest
    test_start_idx = len(df_all) - int(760 * 24/4)
    df2, trades, equity, sigs = run_backtest(df_all, cfg, test_start_idx=test_start_idx)
    test_df = df2.iloc[test_start_idx:].reset_index(drop=True)
    res = metrics(test_df, trades, equity, cfg)
    res["name"] = var["name"]
    res["n_signals"] = len(sigs)
    
    results.append(res)
    
    dsr = res.get("deflated_sharpe", -99)
    pf = res.get("profit_factor", 0)
    ret = res.get("total_return_pct", 0)
    bh = res.get("buyhold_return_pct", 0)
    wr = res.get("win_rate_pct", 0)
    nt = res.get("n_trades", 0)
    dd = res.get("max_drawdown_pct", 0)
    conv = res.get("winrate_by_conviction", {})
    
    # Status indicators
    bh_beat = "✅" if ret > bh else "❌"
    dsr_ok = "✅" if dsr > 0.95 else ("⚠️" if dsr > 0.5 else "❌")
    pf_ok = "✅" if pf > 1.5 else "❌"
    
    if dsr > best_dsr:
        best_dsr = dsr
        best_name = var["name"]
    
    print(f"[{i:2d}] {var['name'][:50]:50s} | DSR:{dsr:7.3f} {dsr_ok} | PF:{pf:6.2f} {pf_ok} | "
          f"Ret:{ret:+6.1f}% vs BH:{bh:+6.1f}% {bh_beat} | Win:{wr:5.1f}% | "
          f"DD:{dd:+6.1f}% | Trades:{nt:3d} | Conv:{conv}")
    print()

# Summary
print("="*80)
print(f"BEST: {best_name} — Deflated Sharpe: {best_dsr:.4f}")
print("="*80)
print()

# Rank by deflated sharpe
ranked = sorted(results, key=lambda r: r.get("deflated_sharpe", -999), reverse=True)
print("RANKED BY DEFLATED SHARPE:")
print(f"{'Rank':<5} {'DSR':>8} {'PF':>7} {'Return':>8} {'Win%':>7} {'DD':>7} {'Trades':>7}  Name")
print("-"*85)
for i, r in enumerate(ranked):
    dsr = r.get("deflated_sharpe", -99)
    pf = r.get("profit_factor", 0)
    ret = r.get("total_return_pct", 0)
    wr = r.get("win_rate_pct", 0)
    dd = r.get("max_drawdown_pct", 0)
    nt = r.get("n_trades", 0)
    marker = "🥇" if i == 0 else ("🥈" if i == 1 else ("🥉" if i == 2 else f" {i+1}."))
    print(f"{marker:<5} {dsr:>8.4f} {pf:>7.3f} {ret:>+7.1f}% {wr:>6.1f}% {dd:>+6.1f}% {nt:>6d}  {r['name']}")

print()
if best_dsr > 0.95:
    print("✅ BEST RESULT PROMOTABLE (DSR > 0.95)")
elif best_dsr > 0.5:
    print("⚠️ BEST RESULT PROMISING BUT NOT PROMOTABLE (DSR < 0.95)")
else:
    print("❌ NO CONFIGURATION PASSES GATES. Strategy logic needs structural change, not parameter tuning.")

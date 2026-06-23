# Future Projects — Trader Agent

> Created: 2026-06-23 | Maintained by Oscar

## SOL Investigation

**Status:** Completed — 2026-06-23  
**Result:** CLOSE MISS (DSR 0.473 vs 0.50 gate)  
**Priority:** Can revisit with finer tuning

### Results
- **Best DSR:** 0.473 (gate: 0.50) — ❌
- **Best params:** ADX 22/28, MR RR=3.0/SL=2.5x/hold=48, TF trail=3.0/act=1.5/bo=0.4/hold=72
- **Performance:** +21.1% vs BH -58.8%, 73 trades, 1.23 PF, -11.2% DD, 38% WR
- **Key finding:** All 30 configs had positive DSR — the engine DOES extract alpha from SOL
- **Sweet spot:** ADX low=22 across all top-5 configs (narrower MR window than BTC)

### Why it Failed the Gate
DSR haircut for 10 trials + moderate trade count (73) limits statistical power. The raw Sharpe is higher — DSR_trials=1 would remove the multiple-testing penalty. PSR=77% (77% chance true Sharpe > 0) suggests a real edge that needs more data or finer tuning to prove.

### Next Steps (if revisited)
1. DSR_trials=1 sweep to assess raw SR
2. Finer grid around ADX 22/28 with RR 2.5-3.5, SL 2.0-3.0x
3. Cross-validate on different time windows

Configs: `/workspace/trader/backtest/asset_configs.json` — sol_4h section

---

## HYPE Momentum/Breakout Engine

**Status:** Concept  
**Priority:** Low  
**Dependency:** SOL investigation provides more data on asset behavior patterns

### Rationale
HYPE trends relentlessly — the v5 dual-mode (MR + TF) doesn't work because mean-reversion zones are structurally scarce. A pure momentum/breakout strategy might capture HYPE's price action better.

### Concept
- Entry: ATR breakout only (no MR leg)
- Exit: trailing stop only (no fixed TP)
- Trend filter: ADX > 25 (stay in trend-following mode permanently)
- Position management: pyramid on pullbacks to EMA20

---

## Freqtrade Integration

**Status:** Blocked  
**Blocker:** Exchange pair validation — Freqtrade requires a live exchange connection that supports the trading pair. Hyperliquid pairs aren't on Binance, and CCXT Hyperliquid doesn't support `fetch_ohlcv`.

### What Exists
- `freqtrade/strategies/TraderV5Hyperliquid.py` — v5 dual-mode ported to Freqtrade format
- `freqtrade/config_backtest.json` — backtest config
- HYPE 4h data imported to local format (3391 candles)

### Next Step
Custom Hyperliquid exchange plugin for Freqtrade, or use a different backtesting framework (e.g., VectorBT Pro) that supports custom data feeds.

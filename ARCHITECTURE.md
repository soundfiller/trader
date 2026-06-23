# Trader Agent — Reference Architecture

> **Version:** 2.0 | **Date:** 2026-06-23 | **Phase:** 4 (Live Trading)
> 
> A comprehensive reference for algorithm research, improvement proposals, and system understanding.

---

## 1. SYSTEM OVERVIEW

Trader is a single AI agent running 6 analytical skills as a pipeline on a 3-tier heartbeat. It trades HYPE perpetuals on Hyperliquid L1 with real USDC from a unified account (spot + perps share one pool).

### 1.1 Platform Architecture

```
EUR (fiat)
  │
  ▼
Binance ── buy USDC with EUR
  │
  ▼
Arbitrum ── withdraw USDC (transfer layer)
  │
  ▼
Hyperliquid L1 ── unified account (spot + perps)
  │
  ▼
hl_trader.py ── execution engine (Python, REST-only)
  │
  ▼
Exchange-native TP/SL orders (reduceOnly trigger orders)
```

- **Wallet:** `0x...` (Robocop V3, address stored in creds/, not in public repo)
- **Private key:** macOS Keychain — zero secrets on disk. Read at runtime via `security` CLI.
- **Account type:** Unified (USDC pool shared between spot and perps)

### 1.2 Capital Detection

The agent uses two capital fields from `hl_trader.py balance`:

| Field | What it represents | Use for |
|-------|-------------------|----------|
| `total_value_usdc` | spot + perps account value (stable) | Risk calculations, position sizing |
| `trading_capital_usdc` | perps margin when in positions, spot USDC when flat | Checking deployable margin availability |

**Do not use `trading_capital_usdc` for risk %.** It swings 4× (e.g., $440 → $108) when a position opens, making the same trade go from 2% → 8% risk retroactively. Risk is always measured against total account value.

| State | total_value_usdc returns | trading_capital_usdc returns |
|-------|--------------------------|------------------------------|
| Flat (no positions) | spot + perps USDC (~$440) | spot USDC (~$440) |
| Positions open | spot + perps USDC (~$440) | perps margin only (~$108) |

### 1.3 Agent Identity

| Attribute | Value |
|-----------|-------|
| Name | Trader |
| Role | Personal AI trading agent |
| Model | `deepseek/deepseek-v4-pro` |
| Fallback chain | DeepSeek Pro → DeepSeek Flash → Ollama (STOP-CHECK) / Opus (FULL) |
| Execution | Autonomous within risk fence |
| Escalation | Oscar → Måns (human-only gates: risk, strategy, funding) |

---

## 2. TIERED HEARTBEAT

### 2.1 STOP-CHECK (every 5 minutes)
**Purpose:** Safety only. No analysis, no execution.

- HALT check (`settings.json` + `.HALT` file)
- Balance sync (`hl_trader.py balance`)
- Stops watchdog (`hl_trader.py stops_watchdog`)
- Daily loss check (≥5% → HALT)
- SL proximity alert (<0.5% from current price)
- Silent unless emergency. Token budget <5K output.
- Fallback: Ollama qwen3.5 (local, free) — stop verification only, no execution

### 2.2 LIGHT SIGNAL (every 15 minutes)
**Purpose:** Market awareness + trade detection + fast execution.

- All STOP-CHECK steps
- Price refresh (allMids for all watchlist symbols)
- Quick 4h structure check (higher highs/lows, S/R)
- Signal scan: conviction (1-5), R:R calculation
- **Execution gate:** conviction≥3 + R:R≥1.5 → execute
- **⚡ FAST EXECUTION PATH:** conviction≥4 + R:R≥2.0 → skip analysis, execute immediately
- Telegram only on execution, SL proximity, emergency
- Token budget <15K output. Fallback: DeepSeek Flash → Ollama

### 2.3 FULL ANALYSIS (every 60 minutes)
**Purpose:** Complete 6-skill pipeline. Deep analysis + behavioral review.

- All LIGHT SIGNAL steps
- Full OHLCV data: 1h, 4h, 1d for all watchlist symbols
- Whale/OI/funding data from Hyperliquid
- Skill pipeline: chart → whale → signal → risk → portfolio → journal
- Briefings at 07:00 and 19:00 UTC
- Drift detection (current positions vs original thesis)
- Behavioral patterns (revenge trading, size escalation, overtrading)
- Token budget: normal. Fallback: DeepSeek Flash → Claude Opus

---

## 3. SKILL PIPELINE

```
OHLCV data ──→ chart-analysis ──→ bias, conviction, S/R, invalidation
                    │
OI/funding ──→ whale-tracker ──→ sentiment, net flow, unusual volume
                    │
                    ▼
              signal-generator ──→ direction, entry, SL, TP, R:R, fused conviction
                    │
                    ▼
              risk-manager ──→ position size, risk check, verdict (gate)
                    │
                    ▼
              hl_trader.py ──→ exchange order + auto TP/SL placement
                    │
                    ▼
              portfolio-manager ──→ P&L, exposure, concentration warnings
              journal-analyzer ──→ win rate, profit factor, behavioral flags
```

### 3.1 chart-analysis

**Algorithm:** EMA stack (20/50/200), RSI(14), MACD(12/26/9), ATR(14), fractal pivots, pattern detection.
**Output:** bias (bullish/bearish/neutral), conviction (1-5), trend direction + strength, S/R levels with touch counts, invalidation price.
**Conviction rubric:** 5 = textbook aligned (EMA + pattern + volume + oscillators all agree), 1 = chop.

### 3.2 whale-tracker

**Algorithm:** Net flow (Σbuys − Σsells), volume z-score vs trailing 20-period baseline, OI change decomposition, funding rate classification, wallet clustering.
**Output:** sentiment (accumulation/distribution/neutral), net_flow_usd, buy_sell_ratio, unusual_volume flag (z≥2), OI signal, funding signal.
**Conviction rubric:** 5 = strong net flow + z≥3 + cluster agreement + OI corroboration. Capped at 4 without clusters.

### 3.3 signal-generator

**Algorithm:** Fuses chart + whale outputs with market context. Agreement check: conflict → cap conviction, no_trade if both weak. Entry: pullback-to-structure (S/R + EMA), never market-chase. SL: beyond invalidation AND ≥1×ATR. TP: nearest S/R + measured-move target. Weighted conviction: chart 0.55, whale 0.35, regime 0.10. Penalize conflict.

**Output:** direction, entry zone, stop-loss, take-profit(s), fused conviction, R:R, thesis, expiration (60 min).

**Rejection conditions:** R:R<1.5, conviction<3, chart+whale conflict with weak conviction, market-chase >0.5×ATR above breakout.

### 3.4 risk-manager ⚠️ ENFORCEMENT LAYER

**Algorithm (fail-fast):**
1. `trading_capital_usdc` = $0 → rejected (no deployable margin)
2. No SL → rejected (hard_block)
3. Conviction < min (3) → rejected
4. R:R < min (1.5) → rejected
5. Daily loss ≥ 5% → rejected (hard_block)
6. **Position size:** `qty = (trading_capital × 0.02) / (entry − SL)` — risk-based, 2% cap
7. Notional > 40% of capital → shrink or reject
8. Gross exposure > 80% → reject
9. Correlation group cap breach → reject

**Output:** verdict (approved_with_size / rejected / approved_warn), position_size {qty, notional, risk_usd, risk_pct}, per-rule check results.

**Immutable guardrails:** risk ≤2%, no trade without SL, daily loss → hard block, exposure ≤80%, limits from config file never from prompt.

### 3.5 portfolio-manager

**Algorithm:** Mark all positions. Compute unrealized P&L, exposure per position, open risk (Σ distance-to-SL × qty), concentration warnings, correlation group clustering.
**Output:** Equity, P&L, gross/net exposure, open risk, position-level metrics, limit status (OK/WARN/BREACH).

### 3.6 journal-analyzer

**Algorithm:** Win rate, profit factor, avg R, expectancy, drawdown curve. Segment by conviction bucket and symbol. Behavioral detectors: revenge trading (low-conviction trade within T minutes of loss), overtrading (count/day > μ+2σ), size escalation (risk_usd rising after consecutive losses), conviction drift, plan abandonment.
**Output:** Performance metrics, behavioral flags (type + evidence + severity), plan adherence score, recommendations.

---

## 4. RISK MODEL

### 4.1 Core Parameters (from config/risk.json)

| Parameter | Value | Enforcement |
|-----------|-------|-------------|
| Max risk per trade | 2% of total_value_usdc | risk-manager Step 5 |
| Max position size | 40% of capital | risk-manager Step 6 |
| Max gross exposure | 80% of capital | risk-manager Step 7 |
| Daily loss limit | 5% of capital | STOP-CHECK + risk-manager Step 4 |
| Max correlated positions | 2 | risk-manager Step 8 |
| Min conviction to trade | 3/5 | risk-manager Step 2 |
| Min R:R | 1.5:1 | risk-manager Step 3 |
| Max leverage | 5x | hl_trader.py order validation |
| Default leverage | 3x | Rule 5 |
| Allow 5x leverage | Conviction 5/5 + R:R ≥ 2.5:1 | Rule 17 |

### 4.2 Position Sizing Formula

```
risk_usd = total_value_usdc × 0.02
distance = abs(entry_px − stop_loss_px)
qty = risk_usd / distance
notional_usd = qty × entry_px
leverage = min(max_leverage, notional_usd / total_value_usdc)
```

### 4.3 HALT Triggers

| Trigger | Action | Resolution |
|---------|--------|------------|
| Daily loss ≥ 5% | Self-HALT: halt=true in settings.json + .HALT file | Human /resume |
| Stop-loss placement failure | Self-HALT | Human /resume |
| Post-trade verification mismatch | .HALT file created by hl_trader.py | Human investigation |
| Human command | halt flag set manually | Human /resume |

### 4.4 Stops Watchdog

Every 5 minutes (STOP-CHECK): verify all positions have TP/SL resting on exchange via `hl_trader.py stops_watchdog`. If missing → re-place immediately via `set_stop`. If re-placement fails → HALT.

**Known issue:** Hyperliquid has shown stops can vanish from the exchange between heartbeats (4× on June 22). Root cause unknown — exchange behavior, not script bug. The watchdog is the defense.

---

## 5. EXECUTION PROTOCOL

### 5.1 Trade Flow

```
signal-generator output
  → risk-manager gate (capital, SL, conviction, R:R, size, exposure)
  → hl_trader.py order HYPE <direction> <size> <lev> <tp> <sl>
  → hl_trader.py stops_watchdog (verify TP/SL on exchange)
  → journal entry
  → Telegram report
```

### 5.2 hl_trader.py Commands

```bash
# Read-only
python3 scripts/hl_trader.py balance          # Account + positions + trading_capital_usdc
python3 scripts/hl_trader.py market HYPE       # Current mid price
python3 scripts/hl_trader.py verify_stops HYPE # Check TP/SL orders exist
python3 scripts/hl_trader.py stops_watchdog    # Verify stops on ALL positions

# Mutating (guarded by .HALT check)
python3 scripts/hl_trader.py order HYPE long <usd> <lev> <tp> <sl>
python3 scripts/hl_trader.py order HYPE short <usd> <lev> <tp> <sl>
python3 scripts/hl_trader.py set_stop HYPE <tp> <sl>
python3 scripts/hl_trader.py close HYPE [pct]
python3 scripts/hl_trader.py leverage HYPE <lev>
```

### 5.3 Post-Trade Verification

After every execution:
1. Verify TP/SL orders exist on exchange
2. Check position size matches intended size
3. Check leverage matches intended leverage
4. If mismatch → hl_trader.py auto-flattens + creates .HALT file

### 5.4 Fast Execution Path

When conviction ≥ 4/5 AND R:R ≥ 2.0:1 AND HALT clear AND capital available:
- **Skip full analysis pipeline**
- Calculate size from `total_value_usdc`
- Execute immediately via `hl_trader.py order`
- Verify stops
- Report to Telegram
- Do not escalate. Do not wait.

---

## 6. WATCHLIST & DATA SOURCES

### 6.1 Monitored Symbols

| Symbol | Role | Correlation Group |
|--------|------|-------------------|
| HYPE | **Traded live** | HL ecosystem |
| BTC | **Traded live** (v5 dual-mode) | Majors |
| ETH | Major alt | Majors |
| SOL | High-beta proxy | Majors |

### 6.2 Data Sources

| Source | Data | Cache TTL |
|--------|------|-----------|
| Hyperliquid `/info` — `allMids` | Real-time mid prices | None (live) |
| Hyperliquid `/info` — `candleSnapshot` | OHLCV 1h/4h/1d for HYPE | 5 min |
| Hyperliquid `/info` — `clearinghouseState` | Account balance, positions, margin | None (live) |
| Hyperliquid `/info` — `openInterest`, `funding` | OI change, funding rate | 10 min |
| Binance `/api/v3/klines` | OHLCV for BTC/ETH/SOL | 5 min |
| `config/risk.json` | Risk parameters | Human-updated |
| `config/settings.json` | Phase, HALT, features | Agent/human-updated |
| `config/watchlist.json` | Symbol roster | Human-updated |
| macOS Keychain | Wallet private key | Runtime only |

### 6.3 Known API Issues

- Hyperliquid candleSnapshot, recentTrades, openInterest, funding endpoints periodically return deserialization errors or empty responses
- Only allMids and meta endpoints are reliably available during degraded periods
- When candles unavailable: agent uses allMids price + journal context for thesis
- When OI/funding unavailable: whale-tracker outputs neutral, conv≤2

---

## 7. DECISION AUTHORITY

| Decision | Authority | Constraint |
|----------|-----------|------------|
| Entry/exit | Trader | Within conviction + R:R gates |
| Position sizing | Trader | ≤2% risk, ≤40% position, ≤80% exposure |
| SL/TP placement | Trader | SL ≥1×ATR, R:R ≥1.5 |
| Cut losers for better setups | Trader | Conviction≥4 opposite + current thesis≤2 |
| Leverage selection | Trader | 3x default, 5x only if conv=5 + R:R≥2.5 |
| Risk parameter changes | **Human only** | config/risk.json |
| Strategy changes | **Human only** | Signal logic, new markets |
| Deposits/funding | **Human only** | Binance EUR→USDC |
| HALT / resume | Human or agent (5% daily loss) | settings.json + .HALT file |
| Disabling guardrails | **Human only** | Never from chat |

---

## 8. ESCALATION PATH

```
Trader (autonomous within risk fence)
  │
  ├── Oscar (routine escalations, tool failures, execution edge cases)
  │     │
  │     └── Måns (risk params, strategy, funding only)
  │
  └── Direct to Måns (HALT conditions only)
```

Oscar speaks for Måns on trading operations within the risk fence.

---

## 9. KNOWN EDGE CASES & FAILURE MODES

### 9.1 Capital Detection (FIXED — v2 2026-06-23)
- **Problem v1:** `perps_account_value_usdc` returns $0 when flat on unified accounts, causing agent to believe it has no trading capital despite spot USDC being available
- **Fix v1:** `trading_capital_usdc` field added to `hl_trader.py balance` — returns spot when flat, perps when in positions
- **Problem v2 (whiplash):** `trading_capital_usdc` swings 4× when positions open/close, making risk % unstable
- **Fix v2:** `total_value_usdc` added as stable denominator for risk calculations. `trading_capital_usdc` still used for checking deployable margin availability.

### 9.2 Stop-Loss Vanishing (MONITORED)
- **Problem:** Hyperliquid trigger orders disappear between heartbeats (4× observed June 22)
- **Defense:** 5-min STOP-CHECK with stops_watchdog → re-place immediately → HALT on failure
- **Gap:** Watchdog can't auto-repair without TP/SL prices — needs caller context

### 9.3 API Degradation (MITIGATED)
- **Problem:** HL info API endpoints return deserialization errors sporadically
- **Defense:** Agent uses allMids for price, journal context for thesis when candles unavailable
- **Gap:** Whale-tracker and chart-analysis degrade to neutral during API outages

### 9.4 SL Widening Drift (FIXED)
- **Problem:** Agent widened SL from $66.20→$66.50→$66.04→$64.85 on June 22, increasing risk from ~2% to 11.8%
- **Fix:** Rule 18 — SL widening must reduce position size to keep risk ≤2%

### 9.5 LLM Failures
- **Rate:** 2.6% of heartbeat runs (3/112 over 36 hours)
- **Impact:** Silent skip — no stop verification for that cycle
- **Mitigation:** Ollama fallback for STOP-CHECK (basic verification, no execution)

---

## 10. CONFIGURATION FILES

### 10.1 config/risk.json
```json
{
  "limits": {
    "max_risk_per_trade_pct": 2.0,
    "max_position_pct": 40.0,
    "max_gross_exposure_pct": 80.0,
    "daily_loss_limit_pct": 5.0,
    "max_correlated_positions": 2,
    "min_conviction": 3,
    "min_risk_reward": 1.5,
    "max_leverage": 5,
    "default_leverage": 3,
    "allow_5x_conviction": 5,
    "allow_5x_rr_min": 2.5
  },
  "account": {
    "type": "unified",
    "trading_capital_source": "perps_account_value_usdc"
  },
  "signal": {
    "expiry_minutes": 60,
    "anti_overtrade_cooldown_minutes": 15
  },
  "timeframes": {
    "primary": "4h",
    "context": "1d",
    "alert": "1h"
  }
}
```

### 10.2 config/settings.json
```json
{
  "halt": false,
  "phase": "4",
  "exchange": "hyperliquid",
  "live_trading": {
    "enabled": true,
    "max_risk_per_trade_pct": 2.0,
    "daily_loss_limit_pct": 5.0,
    "max_positions": 2,
    "max_leverage": 5,
    "symbols": ["HYPE", "BTC"]
  },
  "features": {
    "signal_generation": true,
    "auto_position_sync": true,
    "auto_execution": true
  }
}
```

### 10.3 config/watchlist.json
```json
{
  "symbols": [
    {"symbol": "BTCUSDT", "role": "traded", "correlation_group": "majors", "note": "V5 dual-mode"},
    {"symbol": "ETHUSDT", "role": "major", "correlation_group": "majors"},
    {"symbol": "SOLUSDT", "role": "high_beta", "correlation_group": "majors"},
    {"symbol": "HYPEUSDT", "role": "ecosystem_native", "correlation_group": "hl_ecosystem"}
  ],
  "correlation_groups": {
    "majors": {"max_same_direction": 2},
    "hl_ecosystem": {"max_same_direction": 1}
  }
}
```

---

### 10.4 config/risk_btc.json — BTC V5 Dual-Mode Parameters

Optimal BTC parameters from 30-random sweep (2026-06-23). Beats buy-and-hold by 40.7 percentage points.

```json
{
  "asset": "BTC",
  "pair": "BTC/USDC:USDC",
  "timeframe": "4h",
  "engine_version": "v5_dual_mode",
  "limits": {
    "max_risk_per_trade_pct": 2.0,
    "min_risk_reward": 1.0,
    "max_leverage": 5,
    "default_leverage": 3
  },
  "regime": {
    "adx_threshold_low": 20,
    "adx_threshold_high": 30,
    "mr_rr_target": 3.0,
    "mr_atr_sl_mult": 2.5,
    "mr_max_hold_bars": 60,
    "tf_trail_atr_mult": 2.5,
    "tf_trail_activation_r": 1.5,
    "tf_entry_atr_breakout": 0.3,
    "tf_max_hold_bars": 72
  },
  "fusion": {"chart": 0.75, "whale": 0.15, "regime": 0.10},
  "backtest_performance": {
    "dsr": 0.654,
    "profit_factor": 1.422,
    "total_return_pct": 31.8,
    "buyhold_return_pct": -8.9,
    "max_drawdown_pct": -14.7,
    "win_rate_pct": 44.3,
    "trades": 61,
    "test_period": "2024-05-24 to 2026-06-23"
  }
}
```

Key differences vs HYPE: wider stops (2.5x ATR), higher RR target (3.0), lower min_risk_reward gate (1.0). BTC's higher nominal price and different volatility structure require these adjustments.

---

## 11. MULTI-ASSET BACKTEST RESULTS

### 11.1 Per-Asset OOS Results

323 backtest runs across 5 engine versions. Final v5 dual-mode honest OOS results:

| Asset | Trades | DSR | PF | Return | vs BH | MaxDD | WR | Verdict |
|-------|--------|-----|-----|--------|-------|-------|-----|---------|
| **BTC** | 61 | **0.654** | 1.42 | **+31.8%** | −8.9% | −14.7% | 44.3% | ✅ Live candidate |
| ETH | 69 | 0.076 | 0.82 | −14.6% | −55.3% | −35.4% | 30.4% | ❌ Excluded |
| HYPE | 46 | 0.257 | 1.05 | +2.8% | +171.0% | −13.3% | 32.6% | ❌ No MR edge |

### 11.2 HYPE In-Sample Artifact

Initial sweep reported DSR 0.937 for HYPE — this was **in-sample**. HYPE only has 3,391 4h bars (565 days), but the test window requires 4,560 bars. The sweep's negative test_start_idx processed the entire dataset as test data.

Honest OOS DSR: **0.257**. The dual-mode engine works when an asset alternates between trending and ranging. HYPE trends relentlessly — ADX stays elevated, mean-reversion zones are structurally scarce.

### 11.3 Why BTC Works

BTC spent 2024-2026 in a bear/choppy cycle (−8.9% buy-and-hold). The mean-reversion mode had clean range-bound conditions to exploit. When BTC trended, the ADX gate switched to trend-following mode. The oscillation between the two modes is what the engine was designed for.

### 11.4 SOL Investigation (Planned)

SOL is high-beta to BTC. Likely responds to similar MR parameters. Planned as a standalone sweep: 30 random configs on SOL 4h, same parameter space as BTC sweep. Gate: DSR > 0.50, trades > 40, beats BH. If it clears, the basket extends to 2 live assets.

---

## 12. SESSION HISTORY (June 20-23, 2026)

| Date | Event | P&L |
|------|-------|-----|
| Jun 20 | Agent scaffolded, config created | — |
| Jun 21 | Phase 4 live activated, hl_trader.py deployed | $0.00 |
| Jun 22 | SHORT HYPE entered at $66.86, stopped at $68.46 | −$3.08 |
| Jun 22 | LONG HYPE entered at $67.39 (3x) | — |
| Jun 23 07:46 | LONG stopped out at $65.50 SL | −$3.64 |
| Jun 23 07:46 | Self-HALT (daily loss −8.5% > 5%) | — |
| Jun 23 08:21 | Måns deposited ~€99 (~$112 USDC) | — |
| Jun 23 08:42 | SHORT HYPE 3.69 @ $64.13 (3x) | Active |
| Jun 23 08:52 | Trader v2 build: tiered heartbeat, fast path, capital fix | — |

---

## 13. IMPROVEMENT DIRECTIONS

Areas identified for algorithm research:

1. **Signal fusion model:** Current equal-weight chart(0.55) + whale(0.35) + regime(0.10). Could backtest alternative weightings, add momentum factors, or incorporate volatility regime detection.

2. **Stop-loss optimization:** Fixed ATR-based stops (≥1×ATR). Could explore volatility-adjusted trailing stops, time-based exits, or Kelly-criterion position sizing.

3. **Active trade management:** Currently "set and forget" with TP/SL. Rule 16 adds active cut for better setups, but no partial profit-taking, trailing stop logic, or scale-in/scale-out patterns.

4. **Correlation-aware sizing:** Majors group limits same-direction trades to 2. Could incorporate dynamic correlation matrices or regime-dependent correlation caps.

5. **Confidence calibration:** Conviction rubric is deterministic (agreement score → 1-5). Could track actual win rate per conviction bucket and auto-calibrate.

6. **Market regime detection:** Currently uses simple trend structure (higher highs/lows). Could add volatility regime (GARCH, ATR percentile), volume profile, or market microstructure metrics.

7. **Execution quality:** Market orders only. Could evaluate limit order execution with spread capture, TWAP for larger positions.

8. **Drift detection:** Weekly review of decision quality vs outcomes. Currently manual — no automated win-rate-by-rule tracking.

---

## 14. FILE INDEX

| File | Purpose |
|------|---------|
| `AGENTS.md` | Agent instructions, rules (18), execution protocol, silence rules |
| `SOUL.md` | Identity, phase, architecture, style |
| `IDENTITY.md` | Name, role, scope |
| `USER.md` | Måns profile, preferences, pronouns |
| `SKILLS.md` | 6 skills: schemas, algorithms, conviction rubrics, guardrails, test fixtures |
| `HEARTBEAT.md` | 3-tier heartbeat spec with fast execution path |
| `MEMORY.md` | Long-term memory, timeline, configuration snapshots |
| `TOOLS.md` | API endpoints, cache policy, file permissions |
| `DREAMS.md` | Nightly memory consolidation logs |
| `config/risk.json` | Risk parameters (immutable from chat) |
| `config/settings.json` | Phase, HALT, feature toggles |
| `config/watchlist.json` | Symbol roster |
| `creds/hyperliquid.json` | Wallet address (key in macOS Keychain) |
| `scripts/hl_trader.py` | Execution engine (order, balance, stops, leverage) |
| `scripts/live_dashboard.py` | Live dashboard script |
| `journal/YYYY-MM-DD.md` | Daily trade log |
| `dashboard.html` | Static dashboard snapshot |
| `FUTURE.md` | Future projects: SOL investigation, HYPE momentum, Freqtrade |

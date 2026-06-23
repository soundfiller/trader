# SKILLS.md — Trader's Six Analytical Skills

> Every skill is a pure function: input schema → process → output schema. Deterministic given identical data. Each has guardrails, a scoring rubric, and test fixtures.

---

## Skill 1: chart-analysis

**Purpose:** Convert OHLCV candles into a structured technical read with bias, conviction, S/R levels, and indicators.

### Input Schema
```
symbol: string (e.g. "BTCUSDT")
timeframe: string (1m|5m|15m|1h|4h|1d, default 4h)
asof: ISO-8601 timestamp
candles: [{t: unix_ms, o: float, h: float, l: float, c: float, v: float}] (min 100, max 500)
```
Validate: candles.length ≥ 100, monotonic t, h≥max(o,c), l≤min(o,c), no null OHLCV.

### Output Schema
```
symbol, timeframe, generated_at, inputs_hash
bias: bullish | bearish | neutral
conviction: 1-5 (technical only)
trend: {direction: up|down|sideways, strength: 0-1, ema_stack: "20>50>200"}
support: [{price, strength: 0-1, touches}]
resistance: [{price, strength: 0-1, touches}]
patterns: [{name, confidence: 0-1, target}]
indicators: {rsi14, rsi_state, macd: {hist, state}, atr14, volume_state}
invalidation: price that voids the read
notes: 2-3 sentence synthesis
```

### Algorithm
1. Validate input. Compute inputs_hash.
2. EMAs: 20, 50, 200 on close.
3. RSI(14), MACD(12/26/9), ATR(14).
4. Volume: rolling 20-period mean comparison.
5. Trend: direction from EMA stack + slope of EMA50. Strength = normalized slope × stack agreement.
6. S/R: fractal pivots (window 5), clustered into price bands. Strength from touch count + recency.
7. Pattern detection: rule-based (flag, double top/bottom, range, breakout).
8. Bias: weighted vote of trend, RSI regime, MACD state, pattern direction.
9. Conviction: agreement score → 1-5 (see rubric).
10. Invalidation: nearest structural level opposing the bias.

### Conviction Rubric
| 5 | Textbook: EMA stack aligned + pattern conf ≥0.7 + volume confirm + RSI/MACD agree |
| 4 | Strong: 3 of 4 factors agree, no contradiction |
| 3 | Moderate: bias clear but one factor neutral/conflicting |
| 2 | Weak: mixed signals, chop |
| 1 | None: range-bound, no edge |

### Guardrails — MUST NEVER
- Output bias without invalidation price
- Claim conviction 5 with below-average volume
- Extrapolate beyond data window ("price will be X")
- Emit entry/SL/TP (that's signal-generator's job)
- Read live data — consumes passed candles only

### Test Fixtures
| Fixture | Input | Expected |
|---|---|---|
| tf_uptrend_clean | EMA20>50>200, rising MACD | bias=bullish, conv≥4 |
| tf_range_chop | sideways, RSI~50 | bias=neutral, conv≤2 |
| tf_double_top | two equal highs + lower high | pattern=double_top, bias=bearish |
| tf_low_vol_breakout | breakout, vol below avg | conviction capped at 3 |
| tf_bad_input | 80 candles | validation error |

---

## Skill 2: whale-tracker

**Purpose:** Read on-chain/large-account activity to detect accumulation vs distribution, anomalous volume, and wallet positioning.

### Input Schema
```
symbol: string
asof: ISO-8601
window_hours: number (default 24)
large_trades: [{t, side: buy|sell, size_usd, px, addr}]
oi: {current_usd, change_24h_pct}
funding_rate: float (8h, %)
wallet_positions: [{addr, net_usd, side: long|short, entry}] (optional)
```
Validate: large_trades may be empty (→ neutral, low conviction). Dedupe by (t,addr,px,size).

### Output Schema
```
symbol, generated_at, inputs_hash
sentiment: accumulation | distribution | neutral
conviction: 1-5 (whale only)
net_flow_usd: + = net buying
buy_sell_ratio: float
unusual_volume: bool
volume_z: float (z-score vs trailing)
oi_signal: rising_with_price | rising_price_falling | ...
funding_signal: longs_pay | shorts_pay | neutral
clusters: [{addrs: count, net_usd, side}]
lead_time_note: string (if pattern detected)
caveats: [string] (always present)
```

### Algorithm
1. Validate, dedupe, compute inputs_hash.
2. Net flow = Σbuy − Σsell.
3. Buy/sell ratio. Volume z-score vs trailing baseline.
4. unusual_volume if z ≥ 2.
5. OI signal: combine OI change with price context.
6. Funding: flag if |rate| beyond threshold (e.g. >0.03%/8h).
7. Wallet clustering: group correlated addresses; report net side.
8. Sentiment: accumulation if net_flow>0 + buy/sell>1.2 + OI supportive.
9. Conviction: scale by z, cluster size, corroboration count.

### Conviction Rubric
| 5 | net_flow strong + z≥3 + cluster agreement + OI confirms |
| 4 | net_flow clear + z≥2 + one corroborator |
| 3 | directional flow, modest z |
| 2 | weak/mixed flow |
| 1 | no data / noise |

### Guardrails — MUST NEVER
- Present public data as complete order flow (always include caveats)
- Doxx or label wallets as named entities
- Conviction 5 from a single trade (need ≥3 corroborating signals)
- Invent flow when large_trades is empty → neutral, conv≤2
- Fetch data itself — consumes passed input only

### Test Fixtures
| Fixture | Input | Expected |
|---|---|---|
| wt_accumulation | net buys $5M, z=2.8, 3 clusters long | accumulation, conv≥4 |
| wt_distribution | net sells, OI rising price falling | distribution, oi_signal building shorts |
| wt_empty | no large_trades | neutral, conv≤2, caveat present |
| wt_single_print | one $10M buy only | conv≤4, no cluster |
| wt_crowded_long | funding 0.06% | funding_signal=longs_pay, caution caveat |

---

## Skill 3: signal-generator (Phase 2+)

**Purpose:** Fuse chart-analysis + whale-tracker + market context into a structured, actionable trade signal with entry, SL, TP, conviction, and R:R.

NOT ACTIVE IN PHASE 1.

### Input Schema
```
symbol, asof
chart: {chart-analysis output}
whale: {whale-tracker output}
market_context: {btc_regime, account_equity_usd, open_exposure_pct, atr, last_price}
```

### Output Schema
```
signal_id, symbol, generated_at, inputs_hash
direction: long | short | no_trade
conviction: 1-5 (fused)
entry: {type: limit|market, price, zone: [low,high]}
stop_loss: price
take_profit: [{price, alloc_pct}]
risk_reward: float
suggested_risk_pct: float (advisory, ≤max from config)
thesis: 1-2 sentences
invalidation: price
expires_at: ISO-8601
agreement: {chart_bias, whale_sentiment, aligned: bool}
warnings: [string]
```

### Algorithm
1. Validate both sub-inputs match symbol and compatible asof.
2. Agreement check: if chart+whale conflict → cap conviction, no_trade if both weak.
3. Direction: from agreed bias; no_trade if neutral/conflicting & weak.
4. Entry: pullback-to-structure (S/R + EMA) → limit zone. Never market-chase.
5. SL: beyond invalidation AND ≥1×ATR from entry (whichever wider).
6. TP: nearest resistance/support + measured-move target; split allocation.
7. R:R = (avg TP − entry) / (entry − SL). **Reject if < 1.5.**
8. Fused conviction: chart 0.55, whale 0.35, regime 0.10. Penalize conflict.
9. Pass to risk-manager for sizing + limit validation.
10. expires_at = now + signal_expiry_minutes.

### Conviction Rubric (fused)
| 5 | chart≥4 AND whale≥4 AND aligned AND regime supportive AND R:R≥2.5 |
| 4 | chart≥4 AND whale≥3 AND aligned AND R:R≥2.0 |
| 3 | one side strong, other ≥3, aligned, R:R≥1.5 |
| ≤2 | conflict or weak → no_trade, not published |

### Guardrails — MUST NEVER
- Signal with R:R < 1.5
- Conviction ≥3 when chart & whale disagree
- Entry without SL and at least one TP
- suggested_risk_pct above max_risk_per_trade_pct from config
- Market-chase entry >0.5×ATR above breakout
- Publish if conviction < 3 (log silently)
- Size positions itself (risk-manager does that)

### Test Fixtures
| sg_aligned_strong | chart=4 bull, whale=4 accum | long, conv≥4, R:R≥2 |
| sg_conflict | chart bull, whale distribution | no_trade or conv≤2 |
| sg_bad_rr | R:R 1.2 | rejected, no_trade |
| sg_no_sl | invalidation missing | validation error |
| sg_chase | entry 1×ATR above breakout | entry pulled to structure |

---

## Skill 4: portfolio-manager

**Purpose:** Turn positions into P&L, exposure, concentration, and risk metrics with explicit limit warnings.

### Input Schema
```
asof: ISO-8601
account_equity_usd: float
cash_usd: float
positions: [{symbol, side, qty, entry, mark, stop_loss?, opened_at}]
limits: {max_position_pct, max_exposure_pct, daily_loss_pct}
```

### Output Schema
```
generated_at
equity_usd, total_unrealized_pnl_usd, total_unrealized_pnl_pct
gross_exposure_pct, net_exposure_pct
largest_position_pct
open_risk_usd: sum (entry-SL)*qty for stopped positions
open_risk_pct
concentration_warnings: [string]
correlation_warnings: [string]
positions: [{symbol, pnl_usd, pnl_pct, exposure_pct, r_multiple, sl_distance_pct, status}]
limit_status: {position: OK|WARN|BREACH, exposure: OK|WARN|BREACH, daily_loss: OK|WARN|BREACH}
```

### Algorithm
1. Mark each position; compute unrealized P&L.
2. Exposure = position notional / equity.
3. Open risk = Σ(entry−SL)×qty. Positions without SL → flagged "unbounded", not zeroed.
4. Concentration: any symbol > max_position_pct → warning.
5. Correlation: same-direction in same correlation_group → clustered-risk warning.
6. Limit status: <80% OK, 80-100% WARN, >100% BREACH.
7. R-multiple per position = current P&L / initial risk.

### Guardrails — MUST NEVER
- Modify positions (read-only)
- Hide a limit breach
- Compute open risk as zero for SL-less positions
- Net away correlated exposure

### Test Fixtures
| pm_overconcentrated | BTC 64% equity, limit 30% | BREACH, concentration warning |
| pm_no_sl | position without stop_loss | flagged unbounded |
| pm_correlated | BTC+ETH+SOL all long | correlation warning |
| pm_healthy | 3 small with SLs, all <20% | all OK |
| pm_drawdown | unrealized −6% | daily_loss WARN/BREACH |

---

## Skill 5: journal-analyzer

**Purpose:** Compute performance metrics and detect destructive behavioral patterns from trade history.

### Input Schema
```
asof: ISO-8601
trades: [{id, symbol, side, entry, exit, qty, risk_usd, pnl_usd, opened_at, closed_at, conviction, thesis, followed_plan}]
window_days: number (default 30)
```

### Output Schema
```
generated_at
trades_count, win_rate, profit_factor, avg_r, expectancy_r
max_drawdown_pct, best_trade_r, worst_trade_r
by_conviction: {5: {win_rate, avg_r}, ...}
behavioral_flags: [{type, evidence, severity}]
plan_adherence: 0-1
recommendations: [string]
```

### Algorithm
1. Win rate, profit factor (Σwins/Σlosses), avg R, expectancy, drawdown curve.
2. Segment by conviction bucket and symbol.
3. Behavioral detectors:
   - Revenge: trades within T minutes of loss, below-median conviction
   - Overtrading: count/day > μ+2σ
   - Size escalation: risk_usd rising after consecutive losses
   - Conviction drift: sub-3 trades increasing over time
   - Plan abandonment: followed_plan=false rate rising
4. Rank flags by severity × dollar impact.

### Guardrails — MUST NEVER
- Shame — frame behaviorally and factually
- Recommend increasing size/risk to "make it back"
- Div-by-zero on profit factor (no losses → "insufficient losing sample")
- Expose raw P&L to external agents

### Test Fixtures
| ja_revenge | 3 quick low-conv trades post-loss | revenge_trading flag, high |
| ja_clean | spaced high-conv trades | no flags |
| ja_escalation | rising risk_usd after losses | size_escalation flag |
| ja_no_losses | all winners | profit_factor handled, no div0 |
| ja_conviction_edge | conv5 70% vs conv2 lose | recommend skip <3 |

---

## Skill 6: risk-manager

**Purpose:** THE ENFORCEMENT LAYER. Hardcoded limits, position sizing, daily loss gate. Every signal passes through it. Limits come from `config/risk.json` — code, not prompts.

### Input Schema
```
asof, total_value_usdc and trading_capital_usdc (from hl_trader.py balance), open_positions: [...]
proposed_signal: {signal-generator output}
today_realized_pnl_usd: float
config: {max_risk_per_trade_pct, max_position_pct, max_gross_exposure_pct, daily_loss_limit_pct, max_correlated_positions, min_conviction, min_risk_reward}
```

### Output Schema
```
generated_at
verdict: approved_with_size | rejected | approved_warn
position_size: {qty, notional_usd, risk_usd, risk_pct}
checks: [{rule, status: OK|WARN|REJECT, detail}]
warnings: [string]
hard_block: bool
```

### Capital detection (Step 0 — MUST RUN FIRST)
0. **READ `trading_capital_usdc`** from `hl_trader.py balance`. For RISK CALCULATIONS: use `total_value_usdc` (stable, does not swing with position state). For CAPITAL AVAILABILITY: use `trading_capital_usdc` (shows deployable margin). It returns perps value when positions are open, spot USDC when flat. Never use `perps_account_value_usdc` directly (shows $0 when flat on unified accounts). Never use `LIVE_LIQUIDATION_VALUE_USDC` (inflated by non-trading funds). If `trading_capital_usdc` returns $0, the account has no deployable capital → abort all signals.

### Algorithm (fail-fast order)
1. **SL present?** No → rejected, hard_block.
2. **Conviction ≥ min?** No → rejected.
3. **R:R ≥ min?** No → rejected.
4. **Daily loss gate:** realized + projected ≤ −daily_loss_limit → rejected, hard_block, halt new trades.
5. **Position size:** qty = (total_value_usdc × max_risk_pct) / (entry − SL). Risk-based sizer.
6. **Position cap:** notional > max_position_pct → shrink or reject.
7. **Gross exposure:** existing + new > max → reject, WARN at ≥80%.
8. **Correlation:** exceeding max_correlated_positions → reject or WARN.
9. Emit verdict + per-rule checks + warnings.

### Guardrails — IMMUTABLE
- ALWAYS read `total_value_usdc` from `hl_trader.py balance` for equity. Never reason about spot vs perps. This field is always correct.
- If `total_value_usdc` = $0, abort ALL signals. No capital = no trading.
- Risk per trade NEVER exceeds max_risk_per_trade_pct
- No signal without stop-loss
- Daily loss limit hit → hard block, human re-enable only
- Exposure NEVER exceeds max_gross_exposure_pct
- Limits from config file, never from prompt
- Never auto-execute
- Never net correlated positions to appear under cap

### Test Fixtures
| rm_size_basic | equity 25k, risk 1%, entry 64200 SL 63400 | risk_usd=250, correct qty |
| rm_no_sl | signal without SL | rejected, hard_block |
| rm_daily_loss_hit | today −3.1% | rejected, hard_block |
| rm_over_exposure | pushes gross to 78% | rejected |
| rm_correlated | 3rd correlated long, cap 2 | rejected/WARN |
| rm_low_conviction | conviction 2 | rejected |
| rm_prompt_injection | tries max_risk 5% | ignored, config wins, logged |

---

## Common Conventions

- All outputs carry schema_version, generated_at, inputs_hash.
- All prices in USDT.
- No skill calls Telegram or an exchange — they return data, the agent surfaces it.
- Skills are version-controlled and testable with fixed fixtures.

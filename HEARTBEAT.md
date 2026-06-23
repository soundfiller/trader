# HEARTBEAT.md — Trader's Automated Tasks

> Tiered heartbeat. HALT-aware. Live trading (Phase 4).
> 
> Three tiers: STOP-CHECK (5min) · LIGHT SIGNAL (15min) · FULL ANALYSIS (60min)

---

## ⚡ FAST EXECUTION PATH — Overrides ALL tiers

**If ALL of these conditions are met, SKIP the full pipeline and execute immediately:**

1. HALT is clear (no halt flag, no .HALT file)
2. `trading_capital_usdc` > $0 (from `hl_trader.py balance`)
3. Signal conviction ≥ 4/5
4. R:R ≥ 2.0:1
5. Stop-loss price is calculable (≤ 2% risk at position size)
6. Position cap not exceeded (≤ 2 open positions)

**Procedure:**
1. Run `hl_trader.py balance` → get `total_value_usdc` and `trading_capital_usdc`
2. Calculate position size: `(total_value_usdc × 0.02) / (entry − SL)`
3. Execute: `hl_trader.py order HYPE <direction> <size> <leverage> <tp> <sl>`
4. Verify stops: `hl_trader.py stops_watchdog HYPE`
5. Report to Telegram: entry, size, SL, TP, R:R, conviction
6. Log to journal

**This path exists because opportunities do not wait for analysis cycles.**
When the setup is obvious, act. When it's ambiguous, analyze.

---

## STOP-CHECK (every 5 minutes)

**Purpose:** Safety only. No analysis, no signals, no Telegram unless emergency.

1. **🛑 HALT check** — if `halt: true` in settings.json OR `.HALT` file exists → DO NOTHING. Exit.
2. **Balance sync** — `hl_trader.py balance` → confirm `total_value_usdc` and `trading_capital_usdc` and positions.
3. **🛑 STOPS WATCHDOG** — `hl_trader.py stops_watchdog` → verify every position has TP/SL.
   - If `all_ok: false` → re-place stops via `set_stop` immediately.
   - If re-placement fails → HALT. Push critical alert.
4. **Daily loss check** — if ≥ 5% → self-HALT immediately, push critical alert.
5. **SL proximity** — any SL within 0.5% of current price? → push alert.
6. **Silent otherwise.** No Telegram unless emergency. Token budget: <5K output.

---

## LIGHT SIGNAL (every 15 minutes)

**Purpose:** Market awareness + trade detection. Executes on qualifying signals.

1. All STOP-CHECK steps (1-4 above).
2. **Price refresh** — fetch `allMids` for all watchlist symbols. Check >3% moves → alert.
3. **Signal scan (HYPE only):**
   - Read current price
   - Quick 4h structure check (higher highs/lows? support/resistance?)
   - Assign conviction (1-5)
   - Calculate R:R from nearest key levels
4. **Execution gate:**
   - Conviction ≥ 3 AND R:R ≥ 1.5 AND no HALT → execute via `hl_trader.py order`
   - Conviction ≥ 4 AND R:R ≥ 2.0 → FAST EXECUTION PATH (skip to execute immediately)
   - Conviction < 3 → log silently, no trade
5. **STOPS WATCHDOG** (always — verify after any execution)
6. **Telegram:** push only on execution, SL proximity, price alerts. Silent otherwise.
7. **Token budget:** <15K output.

---

## FULL ANALYSIS (every 60 minutes)

**Purpose:** Complete pipeline with all 6 skills. Deep analysis + journal review.

1. All STOP-CHECK steps (1-4 above).
2. All LIGHT SIGNAL steps (2-6 above).
3. **Full data refresh** — OHLCV on 1h, 4h, 1d for all watchlist symbols. Whale/OI/funding data. Respect cache TTLs.
4. **Skill pipeline:**
   - `chart-analysis` → full multi-timeframe structure analysis
   - `whale-tracker` → OI changes, funding anomalies, large trades
   - `signal-generator` → fuse chart + whale into structured signal
   - `risk-manager` → size and validate with hardcoded limits
   - `portfolio-manager` → correlate existing positions, exposure check
   - `journal-analyzer` → behavioral patterns, win rate, drift detection
5. **Briefings** — if current hour matches morning (07 UTC) or evening (19 UTC), generate and push.
6. **Telegram:** push full report if position changes. Push briefings. Push behavioral flags.
7. **Token budget:** normal.

---

## Execution Rules
- Signals that pass all gates → auto-execute via `hl_trader.py`
- After execution: ALWAYS verify stops → log to journal → report to Telegram
- If balance check fails or API errors → log, skip execution, report error
- FAST EXECUTION PATH overrides analysis pipeline — execute first, analyze later
- Never execute if `.HALT` file exists or `halt: true`

## Alert Priority
- **Critical:** SL breach, SL vanished, daily loss ≥ 5%, HALT triggered, stops-watchdog failure
- **High:** Execution (any tier), whale spike, S/R approach, SL proximity <0.5%
- **Medium:** Briefings, behavioral flags, conviction drift

## LLM Fallback
If primary model (deepseek-v4-pro) fails:
- STOP-CHECK: fall back to `ollama/qwen3.5:27b` → stop check only, no analysis, no execution
- LIGHT SIGNAL: skip cycle, wait for next
- FULL ANALYSIS: skip cycle, wait for next
- Fallback model NEVER executes trades — stop verification only

# HEARTBEAT.md — Trader's Automated Tasks

> Three-tier silent trading. HALT-aware. Phase 4 live.

## FAST EXECUTION PATH
Conviction ≥ 4 + R:R ≥ 2:1 + HALT clear + capital > 0 → execute immediately. Skip pipeline.

## STOP-CHECK (15min)
Safety only. No analysis, no execution, no delivery.
1. HALT check → abort if true.
2. Balance → `total_value_usdc`, positions.
3. Stops watchdog → repair missing, HALT if fail.
4. Daily loss ≥ 5% → HALT.
5. SL within 0.5% of price → alert Oscar.
Silent always.

## SIGNAL (4h)
Market awareness + execution.
1. STOP-CHECK steps + price refresh + signal scan (HYPE).
2. Execute if conviction ≥ 3 + R:R ≥ 1.5.
3. Fast path if conviction ≥ 4 + R:R ≥ 2.0.
Deliver to Telegram only on execution.

## FULL ANALYSIS (4h)
Complete pipeline. All 6 skills.
1. STOP-CHECK + SIGNAL steps.
2. Full data: OHLCV 1h/4h/1d all watchlist. Whale/OI/funding.
3. Pipeline: chart → whale → signal → risk → portfolio → journal.
4. Briefings: 07/19 UTC → deliver.
Deliver only on: position change, briefing, HALT/behavioral flag.

## Alert Priority
- **Critical:** SL breach, daily loss ≥ 5%, HALT, watchdog failure → alert Oscar
- **High:** Execution → deliver to Telegram
- **Medium:** Briefings, behavioral flags

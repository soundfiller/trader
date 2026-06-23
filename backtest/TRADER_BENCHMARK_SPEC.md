# Trader Agent — Benchmark Exercise Specification

> **Version:** 1.0 · **Date:** 2026-06-23 · **Owner:** Måns / Oscar
> Companion engine: `trader_backtest.py` (runnable, ports the six skills)

---

## 0. Why this exists

Trader is validating strategy logic **live, with real USDC**, which is the most
expensive possible way to learn. This exercise moves validation *off* live capital:
backtest the six-skill pipeline against history, measure it honestly, and only
promote parameters to live once they clear statistical gates.

Two questions are deliberately kept separate:

| Question | How it's answered | Verdict location |
|---|---|---|
| **Is the logic sound?** | Backtest on 2+ yrs at realistic notional | Metric battery + DSR |
| **Is it profitable *now* at ~$112?** | Cost model on actual trade sizes | §5 — mostly a scale question, not a logic question |

---

## 1. Comparison set (benchmark against these, in order)

1. **Buy-and-hold HYPE** — the honest baseline. Most active strategies lose to it in a bull leg.
2. **Naive rules bot** — Chainstack grid / RSI bot, no LLM. Answers: *does the LLM add alpha over a dumb rule?*
3. **Open-source multi-agent** — TradingAgents (LangGraph, DeepSeek-compatible) or virattt/AI-Hedge-Fund. The "state-of-the-art architecture" yardstick.
4. **Hyperliquid vault leaderboard** — real money, same venue, same asset universe.

The engine computes #1 automatically. #2–#4 are external runs you point at the same
date windows for an apples-to-apples comparison.

---

## 2. Metric battery

Report **all** of these every run — never optimise one in isolation.

| Metric | Formula / definition | Good threshold | Why |
|---|---|---|---|
| Total return % | end/start − 1 | > buy-and-hold | Beat the baseline |
| Win rate % | wins / trades | context-dependent | Pair with avg R |
| Profit factor | Σwins / \|Σlosses\| | > 1.75 | Edge per $ lost |
| Avg R / Expectancy | mean(net_pnl / risk_usd) | > 0.2 | Edge per unit risk |
| Max drawdown % | min(equity/cummax − 1) | > −15% | Survivability |
| Sharpe (ann.) | μ/σ × √(bars/yr) | > 2.0 | Risk-adjusted, industry standard |
| Sortino (ann.) | μ/σ_downside × √(bars/yr) | > 2.0, ≥ Sharpe | Rewards upside vol |
| Calmar | ann. return / \|maxDD\| | > 2.0 | Leverage/concentration risk |
| **PSR(>0)** | P(true Sharpe > 0) given skew, kurtosis, T | > 0.95 | Significance w/ non-normal returns |
| **Deflated Sharpe** | PSR vs expected-max Sharpe over N config trials | > 0.95 | Corrects for multiple-testing / overfitting |

**Calibration metric (the one your fast-execution path depends on):**

- **Win-rate-by-conviction-bucket** — does conviction-5 actually win more than conviction-3?
  If the curve is flat or *inverted*, your conviction score is not predictive and the
  "skip analysis at conviction ≥ 4" fast path is firing on noise.

**Sanity coupling:** if Sortino ≫ Sharpe → downside is controlled (good). If Sortino ≤
Sharpe → red flag. If Calmar is enormous on a short window → small-sample distortion,
not skill (annualising a 1-month return over a tiny drawdown — discount it).

---

## 3. Test protocol — walk-forward

Walk-forward is the validation standard: re-fit on a rolling in-sample window, test
on the next out-of-sample window, roll forward, repeat. A strategy must prove itself
*repeatedly* across regimes, not in one lucky backtest.

```
|---- IS train 6mo ----|-- OOS test 2mo --|
        roll 2mo →   |---- IS train 6mo ----|-- OOS test 2mo --|
                              roll 2mo →   |---- IS ----|-- OOS --|
```

- **Report OOS metrics only.** In-sample is for fitting; OOS is the truth.
- **The in-sample / OOS gap is your overfitting gauge.** Big gap = data-mined.
- **Deflate.** Every parameter you try is a "trial." Track N_trials and feed it to the
  Deflated Sharpe. Trying 10 fusion-weight combos and reporting the best raw Sharpe is
  how backtests lie. Expect live Sharpe **30–50% below** backtest.

### Data window & the 5000-candle cap

| Timeframe | 5000 candles ≈ | Walk-forward usable? |
|---|---|---|
| **4h (primary)** | **~833 days (2.3 yr)** | ✅ ample — this is your decision frame |
| 1h (alert) | ~208 days (~7 mo) | partial |
| 15m | ~52 days | no — not enough for WF |

The cap is **not binding** on your 4h decision timeframe. Backtest the 4h logic over
the full ~2.3 yr; use 1h only for intrabar stop/alert realism, not for the WF study.

### Look-ahead discipline (enforced in the engine)

- Indicators computed on **closed bar t**; entry fills at **open of t+1**.
- Exits check intrabar high/low against SL/TP within the bar.
- This is the conservative "next-open" execution assumption. Same-close fills inflate
  results and do not survive live — never report them.

---

## 4. Cost model (your real Hyperliquid numbers)

| Component | Value | Engine knob |
|---|---|---|
| Maker fee | 0.0150% | `costs.maker_fee_pct` |
| Taker fee (base T0) | 0.0450% | `costs.taker_fee_pct` |
| Builder code | **up to +0.10%** | `costs.builder_code_pct` |
| Modelled taker slippage | 0.030%/side | `costs.taker_slippage_pct` |
| Funding | **hourly** cadence, premium-index based | `costs.funding_hourly_*` |
| Gas | 0 | — |

**Two structural facts that drive the cost model:**

1. **Builder code is the biggest avoidable fee.** At +0.10% it is **>2× the base taker
   fee** and the dominant lever in the sensitivity sweep (§6). Audit `hl_trader.py` order
   params and any referral/skill (e.g. Perp-Lobster ships with a referral link) for an
   attached builder code. Remove it unless it is paying for itself.

2. **Hourly funding is a risk control, not a footnote.** A 1–3 day directional hold pays
   or earns funding **24–72 times**. In a hot trending regime the hourly bleed against a
   held position can exceed the entire 2% per-trade risk budget *before price reaches the
   stop*. Model it (`funding_hourly_bps_*`) and add a funding-regime gate on held positions.

**Fees are not the constraint at your size** — round trip on $237 notional is ~$0.21.
The constraints are **variance** (too few trades to tell signal from noise) and **the two
leaks above**.

---

## 5. Conviction calibration procedure

1. Log every decision: `predicted_conviction`, `realized_R`, timestamp, regime.
2. After ≥ 100 trades, compute win-rate and avg-R **per conviction bucket**.
3. Fit calibrated P(win) = f(conviction, regime). 
4. **Replace the fast-execution trigger** ("conviction ≥ 4 → skip analysis") with
   "calibrated P(win) ≥ X, where X clears costs." Until the data justifies it, the
   analysis-skip stays **off**.
5. Re-check calibration monthly (drift detection).

---

## 6. Promotion gates (live-deploy criteria)

A parameter set is promoted from backtest → live **only if all hold on OOS data:**

- [ ] Profit factor > 1.5 **and** beats buy-and-hold over the same window
- [ ] Max drawdown > −15%
- [ ] **Deflated Sharpe > 0.95** (this is the gate that kills most overfit configs)
- [ ] Win-rate-by-conviction is **monotonic** (higher conviction → higher win rate)
- [ ] ≥ 100 OOS trades (statistical power) — *fewer = not promotable, regardless of Sharpe*
- [ ] Cost-adjusted (maker, no builder code, funding modelled) still positive

---

## 7. How to run

```bash
# 1-month synthetic demo (validates plumbing — no HL access needed)
python3 trader_backtest.py

# REAL HYPE 4h data (run locally where api.hyperliquid.xyz is reachable)
python3 trader_backtest.py --real --days 30 --warmup 45

# full walk-forward window
python3 trader_backtest.py --real --days 760 --warmup 45

# cost sensitivity
python3 trader_backtest.py --mode taker            # market orders + slippage
python3 trader_backtest.py --mode maker --builder 0.10   # builder-code leak
```

**Freqtrade path (parallel, recommended for production WF):** Freqtrade v2026.5.1
supports Hyperliquid via CCXT with unified accounts, funding (Bybit-model), and
stop-loss-on-exchange. Run it **backtest-only / offline** as a validation harness to
avoid unified-account collision with live Trader; port validated params back. Note its
"no market orders → limit + 5% slippage sim" actually *matches* your pullback-to-structure
entry logic, so it pushes you onto cheaper maker fills by design.

---

## 8. Demo results — 1-month SYNTHETIC run

> ⚠️ **Synthetic data.** This validates the engine + the six-skill wiring end-to-end.
> It is **NOT** evidence about HYPE's real edge. Re-run with `--real` for that.

| Metric | Value | Read |
|---|---|---|
| Trades (1 mo) | 11 | Tiny sample — by design, shows the power problem |
| Win rate | 54.5% | — |
| Profit factor | 1.71 | Above 1.5 |
| Total return | +7.4% | vs buy-and-hold −19% (synthetic down-regime) |
| Max drawdown | −3.9% | — |
| Sharpe (ann.) | 2.84 | Looks great… |
| Sortino (ann.) | 3.69 | > Sharpe ✓ |
| Calmar | 35.3 | **Absurd — small-sample distortion, ignore** |
| **PSR(>0)** | 0.77 | only 77% sure true Sharpe > 0 |
| **Deflated Sharpe** | **0.49** | **coin-flip after honest correction** |
| Win-rate by conviction | conv-3: 62.5% · conv-4: 33.3% | **INVERTED** |

**Two findings the engine surfaces immediately (even on synthetic data):**

1. **Headline Sharpe 2.84 collapses to a 0.49 Deflated Sharpe.** With 11 trades and 10
   config trials, there is essentially a coin-flip chance the edge is real. This is the
   entire argument for ≥ 100 OOS trades before promotion — and exactly why 1 month of
   data proves *plumbing*, not *edge*.

2. **Conviction is inverted** (conv-3 wins more than conv-4). On synthetic data that's
   noise — but it's precisely the diagnostic that, on *real* data, tells you whether the
   fast-execution path is firing on your least reliable signals. Watch this curve.

### Cost sensitivity (same run, 11 trades)

| Execution | Total fees | Net P&L | vs base |
|---|---|---|---|
| Maker, no builder | $0.13 | $8.22 | — |
| Taker (market) | $0.38 | $7.76 | 3.0× fees |
| Maker + builder 0.10% | $0.97 | $7.35 | **7.6× fees** |
| Taker + builder 0.10% | $1.22 | $6.89 | 9.6× fees |

The builder code, not the maker/taker choice, is the dominant fee lever.

---

## 9. Limitations (honest)

- **whale-tracker is a proxy** here (volume z-score + funding). The real skill needs HL
  `openInterest`/`funding` + wallet clustering — wire those in the `--real` path.
- **Funding is synthetic** in the demo (mean 0). Feed real HL hourly funding for the live edge.
- **Single asset (HYPE), single venue.** No correlation-group logic exercised with one symbol.
- **The 40%-of-capital position cap is ambiguous** vs the leverage model: with 2% risk
  sizing on typical SL distances the leverage cap (3×/5×) binds first. The engine treats
  leverage as the binding constraint and flags this — worth reconciling in `risk.json`.
- **Synthetic ≠ HYPE.** Every number in §8 is illustrative of the *method*, not the asset.

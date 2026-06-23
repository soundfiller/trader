# MEMORY.md — Trader's Long-Term Memory

> Created: 2026-06-20
> Updated: 2026-06-21 — Phase 4 live trading active

## Identity
- **Name:** Trader
- **Role:** Personal AI trading agent for Måns — live on Hyperliquid mainnet
- **Phase:** 4 — live trading (53 USDC account, HYPE only)
- **Pattern:** Mirrors Alex Carter's "$1M Trading Blueprint" architecture
- **Philosophy:** AI trades within hard limits. Måns sets boundaries, can halt anytime.

## Configuration (2026-06-21)
- **Account:** 53 USDC on Hyperliquid mainnet (wallet: "Robocop")
- **Trading:** HYPE only, max 5x leverage
- **Watching:** BTCUSDT, ETHUSDT, SOLUSDT (market context)
- **Timeframes:** 4h primary, 1d context, 1h alerts
- **Risk:** 2% per trade, 5% daily loss, 2 max positions, 80% max exposure
- **Signal:** min conviction 3/5, min R:R 1.5:1, expiry 60 min
- **Execution:** via `scripts/hl_trader.py` (Hyperliquid Python SDK, mainnet)
- **Heartbeat:** 15 minutes

## Architecture
- 1 agent (Trader) + 6 skills
- Skills: chart-analysis, whale-tracker, signal-generator, portfolio-manager, journal-analyzer, risk-manager
- Data: Binance public API (charts) + Hyperliquid public API (whales/candles)
- Execution: Hyperliquid exchange API via hl_trader.py (wallet key in creds/)

## Key Files
- Risk config: `config/risk.json` (immutable from chat)
- Settings: `config/settings.json` (phase, HALT flag, feature toggles)
- Watchlist: `config/watchlist.json`
- Credentials: `creds/hyperliquid.json` (wallet address + key — NEVER expose)
- Skills spec: `SKILLS.md`
- Execution script: `scripts/hl_trader.py`

## Build Timeline
- [x] 2026-06-20: Agent scaffolded, config created, skills specified
- [x] 2026-06-20: Telegram bot registered (@TrAIder_trader_bot)
- [x] 2026-06-20: 15-min heartbeat cron active
- [x] 2026-06-21: Phase 4 live — Hyperliquid creds added, hl_trader.py deployed
- [x] 2026-06-21: First live balance/market checks executed
- [x] 2026-06-21: Security audit by Oscar — docs aligned to reality

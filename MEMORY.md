# MEMORY.md — Trader

> Created: 2026-06-20 | Phase 4 live

## Identity
- **Role:** Personal AI trading agent for Måns — live on Hyperliquid mainnet
- **Trading:** HYPE only, max 5x leverage, 2% risk/trade
- **Watching:** BTC, ETH, SOL for market context

## Key Config
- Risk: `config/risk.json` (immutable from chat)
- Settings: `config/settings.json` (halt flag)
- Watchlist: `config/watchlist.json`
- Execution: `scripts/hl_trader.py` (key in macOS Keychain)
- Reference docs: `docs/`

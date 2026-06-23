# TOOLS.md — Trader's Tool Setup

## APIs

### Binance Public (no auth)
- OHLCV: `GET https://api.binance.com/api/v3/klines?symbol=SYMBOL&interval=TF&limit=200`
- 24h ticker: `GET https://api.binance.com/api/v3/ticker/24hr?symbol=SYMBOL`
- Rate limit: 1200 weight/min. Backoff on 429.
- Symbols: BTCUSDT, ETHUSDT, SOLUSDT

### Hyperliquid Public Info (no auth)
- Recent trades: `POST https://api.hyperliquid.xyz/info` body `{"type":"recentTrades",...}`
- Open interest: `POST https://api.hyperliquid.xyz/info` body `{"type":"openInterest",...}`
- Funding: `POST https://api.hyperliquid.xyz/info` body `{"type":"funding",...}`
- Candles: `POST https://api.hyperliquid.xyz/info` body `{"type":"candleSnapshot","req":{"coin":"HYPE","interval":"4h","limit":200}}`

### Hyperliquid Exchange (AUTHENTICATED — LIVE TRADING)
- **Script:** `python3 scripts/hl_trader.py <command> [args...]`
- **Commands:**
  - `balance` — Get USDC balance, account value, open positions
  - `market HYPE` — Get current mid price and metadata
  - `order HYPE long|short <size_usd> <leverage>` — **PLACE LIVE MARKET ORDER**
  - `close HYPE [pct]` — **CLOSE POSITION** (default 100%)
  - `leverage HYPE <lev>` — Update leverage
- **Security:** Script reads wallet key from `creds/hyperliquid.json` internally. Key is NEVER exposed in output.
- **HALT GUARD:** Script refuses execution if `config/settings.json` has `halt: true` AND the `HALT` flag file exists at `~/.openclaw/workspace/trader/.HALT`. Check before every order.

## Cache Policy

| Data | TTL | Location |
|---|---|---|
| OHLCV | min(tf, 5m) | data/cache/binance/{symbol}_{tf}.json |
| Whale/OI | 10 min | data/cache/hyperliquid/{symbol}.json |

Every cache record has `fetched_at`. Never analyze stale data silently.

## Files I Read
- `config/risk.json` — limits (immutable source of truth)
- `config/watchlist.json` — symbols to track
- `config/settings.json` — phase, HALT flag, features, timings
- `creds/hyperliquid.json` — wallet address (key accessed by script only)
- `journal/YYYY-MM-DD.md` — daily log
- `data/cache/` — cached API data
- `.HALT` — hard kill switch file (if present → no execution)

## Files I Write
- `data/cache/` — fresh API data
- `signals/active/` — active signals
- `signals/history/` — closed signals with outcomes
- `journal/YYYY-MM-DD.md` — daily trade entries and execution logs

## Telegram
- Interface: Telegram bot `@TrAIder_trader_bot`
- Commands: /scan, /chart, /whale, /portfolio, /signals, /positions, /risk, /journal, /weekly, /watch, /status, /halt, /resume

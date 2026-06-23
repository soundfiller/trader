#!/usr/bin/env python3
"""Heartbeat analysis script — fetch data and output structured analysis."""
import json, sys, os
from datetime import datetime, timezone
import subprocess

def fetch_json(url, body=None):
    """Fetch JSON from API."""
    if body:
        cmd = ['curl', '-s', '-X', 'POST', url, '-H', 'Content-Type: application/json', '-d', json.dumps(body)]
    else:
        cmd = ['curl', '-s', url]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    return json.loads(result.stdout)

# Get timestamps
now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

# Fetch 1h candles
print("Fetching 1h candles...", file=sys.stderr)
c1h = fetch_json("https://api.hyperliquid.xyz/info", {
    "type": "candleSnapshot",
    "req": {"coin": "HYPE", "interval": "1h", "startTime": now_ms - 48*3600*1000, "endTime": now_ms}
})

# Fetch 4h candles
print("Fetching 4h candles...", file=sys.stderr)
c4h = fetch_json("https://api.hyperliquid.xyz/info", {
    "type": "candleSnapshot",
    "req": {"coin": "HYPE", "interval": "4h", "startTime": now_ms - 200*3600*1000, "endTime": now_ms}
})

print("\n=== RECENT 1h CANDLES (last 12) ===")
for c in c1h[-12:]:
    dt = datetime.fromtimestamp(c['t']/1000, tz=timezone.utc)
    print(f"{dt.strftime('%m-%d %H:%M')} | O:{c['o']} H:{c['h']} L:{c['l']} C:{c['c']} V:{float(c['v']):,.0f}")

print("\n=== RECENT 4h CANDLES (last 12) ===")
for c in c4h[-12:]:
    dt = datetime.fromtimestamp(c['t']/1000, tz=timezone.utc)
    print(f"{dt.strftime('%m-%d %H:%M')} | O:{c['o']} H:{c['h']} L:{c['l']} C:{c['c']} V:{float(c['v']):,.0f}")

# Price analysis
latest = c1h[-1]
print(f"\n=== CURRENT PRICE ===")
print(f"Last 1h close: {latest['c']}")
print(f"Last 1h OHLC: O:{latest['o']} H:{latest['h']} L:{latest['l']} C:{latest['c']}")

# Compute key levels from 4h data
print("\n=== 4H ANALYSIS ===")
closes_4h = [float(c['c']) for c in c4h]
highs_4h = [float(c['h']) for c in c4h]
lows_4h = [float(c['l']) for c in c4h]
volumes_4h = [float(c['v']) for c in c4h]

# Recent trend
recent_closes = closes_4h[-20:]
print(f"20-period closes: {[round(x,2) for x in recent_closes]}")

# Simple MA
ma20_4h = sum(closes_4h[-20:]) / 20
ma50_4h = sum(closes_4h[-50:]) / min(50, len(closes_4h))
ma200_4h = sum(closes_4h) / len(closes_4h) if len(closes_4h) >= 200 else sum(closes_4h) / len(closes_4h)

print(f"MA20: {ma20_4h:.3f}, MA50: {ma50_4h:.3f}")
print(f"Latest close: {closes_4h[-1]}")

# Support/Resistance
recent_high = max(highs_4h[-50:])
recent_low = min(lows_4h[-50:])
print(f"50-period range: {recent_low:.3f} - {recent_high:.3f}")

# Current price relative to MAs
current = float(latest['c'])
print(f"\n=== BIAS ===")
if current > ma20_4h > ma50_4h:
    print("Structure: Bullish (price > MA20 > MA50)")
elif current < ma20_4h < ma50_4h:
    print("Structure: Bearish (price < MA20 < MA50)")
else:
    print(f"Structure: Mixed (price={current:.3f} vs MA20={ma20_4h:.3f} MA50={ma50_4h:.3f})")

# Volume analysis
avg_vol_20 = sum(volumes_4h[-20:]) / 20
last_vol = volumes_4h[-1]
print(f"\nVolume last 4h: {last_vol:,.0f} vs 20-period avg: {avg_vol_20:,.0f} ({last_vol/avg_vol_20*100:.0f}%)")

# Recent price change
pct_24h = ((closes_4h[-1] - closes_4h[-7]) / closes_4h[-7]) * 100 if len(closes_4h) >= 7 else 0
print(f"24h change: {pct_24h:.2f}%")

# Save to cache
cache_dir = "data/cache"
os.makedirs(cache_dir, exist_ok=True)
cache_1h = {"fetched_at": datetime.now(timezone.utc).isoformat(), "data": c1h}
cache_4h = {"fetched_at": datetime.now(timezone.utc).isoformat(), "data": c4h}
with open(f"{cache_dir}/hype_1h.json", "w") as f:
    json.dump(cache_1h, f)
with open(f"{cache_dir}/hype_4h.json", "w") as f:
    json.dump(cache_4h, f)
print("\nCached to data/cache/hype_1h.json and data/cache/hype_4h.json")

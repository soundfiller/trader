#!/usr/bin/env python3
"""Trader Live Dashboard — queries exchange + state in real-time."""
import json, subprocess, sys, time, urllib.request, os

def api_post(endpoint, body):
    data = json.dumps(body).encode()
    req = urllib.request.Request(f'https://api.hyperliquid.xyz/{endpoint}', data=data, headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())

def run_trader(cmd):
    r = subprocess.run([sys.executable, '/Users/manspetterson/.openclaw/workspace/trader/scripts/hl_trader.py'] + cmd.split(), capture_output=True, text=True, timeout=30)
    return json.loads(r.stdout)

# === LIVE DATA ===
ts = time.strftime('%Y-%m-%d %H:%M:%S %Z')

# Prices (API returns strings — cast to float)
mids = api_post('info', {'type': 'allMids'})
hype = float(mids.get('HYPE', 0))
btc = float(mids.get('BTC', 0))
eth = float(mids.get('ETH', 0))
sol = float(mids.get('SOL', 0))

# Balance
bal = run_trader('balance')
tc = float(bal['trading_capital_usdc'])
spot = float(bal['spot_usdc'])
perps = float(bal['perps_account_value_usdc'])
total_eq = float(bal['LIVE_LIQUIDATION_VALUE_USDC'])

# Position
positions = bal.get('positions', [])
pos = positions[0] if positions else None

# Stops
wd = run_trader('stops_watchdog HYPE')
stops_ok = wd.get('all_ok', False)
stops_checked = wd.get('positions_checked', 0)

# HALT
halt_file = os.path.exists('/Users/manspetterson/.openclaw/workspace/trader/.HALT')
with open('/Users/manspetterson/.openclaw/workspace/trader/config/settings.json') as f:
    settings = json.load(f)
halt_flag = settings.get('halt', False)
halted = halt_flag or halt_file

# Position details
if pos and tc > 0:
    size = abs(float(pos['size']))
    entry = float(pos['entry_px'])
    direction = '🔴 SHORT' if float(pos['size']) < 0 else '🟢 LONG'
    lev = float(pos.get('leverage', 3))
    upnl = float(pos.get('unrealized_pnl', 0))
    notional = size * hype
    margin = notional / lev
    risk_dist = float(pos.get('stop_loss', 66)) - hype if direction == '🔴 SHORT' else hype - float(pos.get('stop_loss', 0))
    risk_usd = risk_dist * size
    risk_pct = (risk_usd / tc * 100) if tc > 0 else 0
else:
    size = entry = upnl = notional = margin = risk_usd = risk_pct = 0
    direction = '—'
    lev = 0

# Journal P&L
journal_dir = '/Users/manspetterson/.openclaw/workspace/trader/journal'
total_realized = 0
for fname in sorted(os.listdir(journal_dir)):
    if not fname.endswith('.md'): continue
    with open(os.path.join(journal_dir, fname)) as f:
        content = f.read()
    for line in content.split('\n'):
        if 'Realized loss' in line or 'Realized PnL' in line:
            import re
            nums = re.findall(r'-\$?[\d.]+', line)
            if nums:
                try: total_realized += float(nums[0].replace('$',''))
                except: pass

# === RENDER ===
bar = '─' * 56
print(f'''
╔{'═'*56}╗
║  🦾 TRADER LIVE DASHBOARD  │  {ts}  ║
║  Phase 4 · Hyperliquid L1 · HYPE Only · Wallet: Robocop V3  ║
╚{'═'*56}╝''')

print(f'''
┌{bar}┐
│ 💰 ACCOUNT                                  │
├{bar}┤
│  Total Equity:      \${total_eq:>10,.2f}                     │
│  Trading Capital:   \${tc:>10,.2f}  (perps deployed)          │
│  Spot USDC:         \${spot:>10,.2f}  (idle)                  │
│  Perps Value:       \${perps:>10,.2f}                         │
│  Realized P&L:      \${total_realized:>10,.2f}  (all sessions) │
│  Unrealized P&L:    \${upnl:>10,.2f}                          │
│  Net P&L:           \${total_realized+upnl:>10,.2f}           │
│  HALT:              {'🔴 HALTED' if halted else '🟢 CLEAR':>10}                    │
└{bar}┘''')

if pos:
    sl_px = 66.00
    tp_px = 59.60
    dist_sl = abs(hype - sl_px)
    dist_tp = abs(hype - tp_px)
    rr = dist_tp / dist_sl if dist_sl > 0 else 0
    print(f'''
┌{bar}┐
│ 📉 POSITION: {direction:<38} │
├{bar}┤
│  Entry:    \${entry:<10.4f}   Size:    {size:<10.4f} HYPE       │
│  Current:  \${hype:<10.4f}   Lev:     {lev:<10.1f}x             │
│  Notional: \${notional:<10.2f}  Margin:  \${margin:<10.2f}       │
│  SL:       \${sl_px:<10.2f}   TP:      \${tp_px:<10.2f}         │
│  Dist SL:  \${dist_sl:<10.4f}   Dist TP: \${dist_tp:<10.4f}     │
│  R:R:      {rr:<10.2f}   Risk:    {risk_pct:<9.2f}%           │
│  Stops:    {'✅ VERIFIED' if stops_ok else '❌ MISSING':<10}                      │
│  uPNL:     \${upnl:<10.4f}                                    │
└{bar}┘''')

print(f'''
┌{bar}┐
│ 📊 MARKET                                   │
├{bar}┤
│  HYPE:  \${hype:<10.2f}  │  BTC:   \${btc:<10.2f}            │
│  ETH:   \${eth:<10.2f}  │  SOL:   \${sol:<10.2f}            │
├{bar}┤
│  All majors tracking together — {'bearish' if hype < 66 else 'mixed'}           │
└{bar}┘''')

print(f'''
┌{bar}┐
│ ⚙️ TIERED HEARTBEAT                          │
├{bar}┤
│  🛡️  STOP-CHECK   (5m)   Balance + stops + SL proximity    │
│  ⚡  LIGHT SIGNAL (15m)  Price + signal + ⚡FAST PATH        │
│  🔬  FULL ANALYSIS(60m)  6 skills + briefings + drift       │
├{bar}┤
│  💾  dashboard.py — run anytime for live snapshot           │
└{bar}┘''')

print(f'''
┌{bar}┐
│ 🔑 RULES ACTIVE                              │
├{bar}┤
│  #2b  ⚡ FAST PATH: conv≥4 + R:R≥2 → execute immediately   │
│  #10  🪙 Use trading_capital_usdc only (never freezes)      │
│  #16  ✂️  Cut losers for higher-conviction opposite trades  │
│  #18  📏 SL widening → reduce size, never increase risk     │
└{bar}┘

     🟢 LIVE  ·  {total_eq:.0f} USDC  ·  {'🔴 HALTED' if halted else '🟢 ARMED'}
''')

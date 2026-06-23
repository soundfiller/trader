#!/usr/bin/env python3
"""Bulk close a HYPE position — single connection, multiple attempts."""
import subprocess, time, json, sys
from hyperliquid.info import Info
from hyperliquid.exchange import Exchange
from hyperliquid.utils import constants
from eth_account import Account

# Get key from keychain
result = subprocess.run(
    ['security','find-generic-password','-a','trader','-s','hyperliquid-mainnet','-w'],
    capture_output=True, text=True, timeout=10)
key = result.stdout.strip()
account = Account.from_key(key)
exchange = Exchange(account, constants.MAINNET_API_URL)
info = Info(constants.MAINNET_API_URL)
query_addr = account.address

max_attempts = 20
for i in range(max_attempts):
    user_state = info.user_state(query_addr)
    positions = user_state.get('assetPositions', [])
    target = None
    for pos in positions:
        p = pos.get('position', {})
        if p.get('coin') == 'HYPE':
            target = p
            break
    
    if not target:
        print(f"✅ Position fully closed after {i} attempts")
        break
    
    size = abs(float(target.get('szi', '0')))
    px = float(target.get('entryPx', '0'))
    upnl = float(target.get('unrealizedPnl', '0'))
    print(f"Attempt {i+1}: {size} HYPE remaining, uPnL={round(upnl,2)}", flush=True)
    
    result = exchange.market_close('HYPE', 1.0)
    statuses = result.get('response', {}).get('data', {}).get('statuses', [])
    if statuses:
        fill = statuses[0].get('filled', statuses[0].get('resting', {}))
        avg_px = fill.get('avgPx', 'N/A')
        total_sz = fill.get('totalSz', '0')
        print(f"  → filled {total_sz} @ {avg_px}", flush=True)
    
    time.sleep(0.5)

# Final balance
info2 = Info(constants.MAINNET_API_URL)
user_state = info2.user_state(query_addr)
balance = float(user_state.get('marginSummary', {}).get('accountValue', '0'))
cash = float(user_state.get('withdrawable', '0'))
pos_after = user_state.get('assetPositions', [])
print(f"\nFinal: perps_value={round(balance,2)}, available={round(cash,2)}, positions={len(pos_after)}")

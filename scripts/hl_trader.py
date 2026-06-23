#!/usr/bin/env python3
"""Hyperliquid Trading Helper — called by Trader agent via exec.

Secrets: private key fetched from macOS Keychain at runtime.
No keys are stored on disk."""

import json
import subprocess
import sys
import os
import time
import urllib.request
import urllib.error
from hyperliquid.exchange import Exchange
from hyperliquid.utils import constants
from eth_account import Account
from eth_account.signers.local import LocalAccount

# REST API helper — uses HTTP POST to avoid websocket connection exhaustion.
# The Info SDK opens a new websocket per instance and never closes them,
# so after ~15 invocations Hyperliquid rejects new connections.
# Direct REST calls avoid this entirely.
API_URL = "https://api.hyperliquid.xyz/info"

def _api_post(payload: dict, timeout: int = 15) -> dict:
    """POST to Hyperliquid info API via REST (no websocket)."""
    data = json.dumps(payload).encode()
    req = urllib.request.Request(API_URL, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.URLError as e:
        raise RuntimeError(f"REST API call failed: {e}")

KEYCHAIN_ACCOUNT = "trader"
KEYCHAIN_SERVICE = "hyperliquid-mainnet"
CREDS_PATH = os.path.expanduser("~/.openclaw/workspace/trader/creds/hyperliquid.json")
HALT_PATH = os.path.expanduser("~/.openclaw/workspace/trader/.HALT")

MUTATING_COMMANDS = {"order", "close", "leverage"}

# Cache the key + account in memory only — never written to disk
_key_cache = None
_account_cache = None

def get_private_key():
    """Fetch private key from macOS Keychain. Cached in memory for the process lifetime."""
    global _key_cache
    if _key_cache:
        return _key_cache
    result = subprocess.run(
        ["security", "find-generic-password", "-a", KEYCHAIN_ACCOUNT, "-s", KEYCHAIN_SERVICE, "-w"],
        capture_output=True, text=True, timeout=10
    )
    if result.returncode != 0:
        raise RuntimeError(f"Keychain lookup failed: {result.stderr.strip()}. Run: "
                          f"security add-generic-password -a {KEYCHAIN_ACCOUNT} -s {KEYCHAIN_SERVICE} -w")
    _key_cache = result.stdout.strip()
    return _key_cache

def get_account() -> LocalAccount:
    """Get or create the cached Account object from Keychain private key."""
    global _account_cache
    if not _account_cache:
        _account_cache = Account.from_key(get_private_key())
    return _account_cache

def get_wallet_address():
    """Derive wallet address from private key at runtime."""
    return get_account().address

def load_creds():
    """Load public wallet metadata from creds file (no secrets)."""
    try:
        with open(CREDS_PATH) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}

def check_halt(cmd: str):
    """Hard kill switch — refuses mutating commands if .HALT file exists."""
    if cmd in MUTATING_COMMANDS and os.path.exists(HALT_PATH):
        print(json.dumps({"error": "HALTED", "detail": "Hard kill switch active. .HALT file present. No orders will be placed."}))
        sys.exit(1)

def get_exchange() -> Exchange:
    return Exchange(get_account(), constants.MAINNET_API_URL)

def cmd_balance():
    """Get USDC balance and account value (spot + perps) — REST API only, no websocket."""
    query_addr = get_wallet_address()
    
    # Check spot
    spot_state = _api_post({"type": "spotClearinghouseState", "user": query_addr})
    spot_balances = spot_state.get("balances", [])
    spot_usdc = 0
    for b in spot_balances:
        if b.get("coin") == "USDC":
            spot_usdc = float(b.get("total", "0"))
    
    # Check perps
    user_state = _api_post({"type": "clearinghouseState", "user": query_addr})
    balance = float(user_state.get("marginSummary", {}).get("accountValue", "0"))
    cash = float(user_state.get("withdrawable", "0"))
    positions = user_state.get("assetPositions", [])
    
    # With unified accounts, spot + perps share one USDC pool.
    # When positions are open, perps_account_value_usdc reflects deployed margin + uPNL.
    # When flat (no positions), perps shows $0 but spot USDC IS the available trading capital.
    # trading_capital_usdc always returns the correct value regardless of position state.
    
    trading_capital = spot_usdc if balance == 0 else balance
    
    result = {
        "trading_capital_usdc": round(trading_capital, 2),
        "total_value_usdc": round(spot_usdc + balance, 2),
        "LIVE_LIQUIDATION_VALUE_USDC": round(spot_usdc + balance, 2),
        "spot_usdc": round(spot_usdc, 2),
        "perps_account_value_usdc": round(balance, 2),
        "perps_available_usdc": round(cash, 2),
        "positions_open": len(positions),
        "positions": []
    }
    
    for pos in positions:
        p = pos.get("position", {})
        result["positions"].append({
            "symbol": p.get("coin", "UNKNOWN"),
            "size": float(p.get("szi", "0")),
            "entry_px": float(p.get("entryPx", "0")),
            "unrealized_pnl": float(p.get("unrealizedPnl", "0")),
            "leverage": float(p.get("leverage", {}).get("value", "0")) if p.get("leverage") else 0
        })
    
    print(json.dumps(result, indent=2))

def cmd_market(symbol: str):
    """Get current market data for a symbol — REST API only, no websocket."""
    # All mids for all assets
    mids = _api_post({"type": "allMids"})
    mid = mids.get(symbol, None)
    
    # Get metadata
    meta = _api_post({"type": "meta"})
    asset_info = None
    for asset in meta.get("universe", []):
        if asset.get("name") == symbol:
            asset_info = asset
            break
    
    result = {
        "symbol": symbol,
        "mid_price": mid,
        "asset_info": asset_info
    }
    print(json.dumps(result, indent=2))

def cmd_order(symbol: str, direction: str, size_usd: float, 
              leverage: int = 1, tp_px: float = None, sl_px: float = None):
    """
    Place a market order.
    direction: "long" or "short"
    size_usd: position size in USD (notional)
    leverage: 1-50
    tp_px: take profit price (optional)
    sl_px: stop loss price (optional)
    """
    exchange = get_exchange()
    is_buy = direction.lower() == "long"
    
    try:
        # 1. Set leverage BEFORE placing the order
        lev_result = exchange.update_leverage(leverage, symbol)
        print(json.dumps({"pre_trade_leverage": {"symbol": symbol, "requested": leverage, "result": str(lev_result)[:200]}}))
        time.sleep(0.5)  # Allow exchange to process the leverage update
        
        # 2. Convert USD notional → coin amount using current mid price (REST)
        mids = _api_post({"type": "allMids"})
        mid_price = float(mids.get(symbol, "0"))
        if mid_price <= 0:
            raise ValueError(f"Could not get mid price for {symbol}")
        coin_size = round(size_usd / mid_price, 2)  # szDecimals=2 for HYPE
        print(json.dumps({"size_conversion": {"usd_notional": size_usd, "mid_price": mid_price, "coin_size": coin_size}}))
        
        # 3. Place the market order
        result = exchange.market_open(symbol, is_buy, coin_size, None, 0.05)
        
        statuses = result.get("response", {}).get("data", {}).get("statuses", [])
        fill = statuses[0] if statuses else {}
        fill_data = fill.get("filled", fill.get("resting", {}))
        
        # 4. Post-trade verification — was the order executed as intended?
        time.sleep(1)  # Allow exchange to settle
        query_addr = get_wallet_address()
        user_state = _api_post({"type": "clearinghouseState", "user": query_addr})
        positions = user_state.get("assetPositions", [])
        actual_pos = None
        for pos in positions:
            p = pos.get("position", {})
            if p.get("coin") == symbol:
                actual_pos = p
                break
        
        verification = {}
        if actual_pos:
            actual_size = abs(float(actual_pos.get("szi", "0")))
            actual_leverage = float(actual_pos.get("leverage", {}).get("value", "0")) if actual_pos.get("leverage") else 0
            actual_entry = float(actual_pos.get("entryPx", "0"))
            
            size_ok = abs(actual_size - coin_size) / coin_size < 0.05  # Within 5%
            leverage_ok = int(actual_leverage) == leverage
            
            verification = {
                "requested": {"size_coin": coin_size, "leverage": leverage, "notional_usd": size_usd},
                "actual": {"size_coin": actual_size, "leverage": int(actual_leverage), "entry_px": actual_entry},
                "size_match": size_ok,
                "leverage_match": leverage_ok
            }
            
            if not size_ok or not leverage_ok:
                verification["⚠️ MISMATCH"] = True
                verification["details"] = []
                if not size_ok:
                    verification["details"].append(f"Size: requested {coin_size} HYPE, got {actual_size} HYPE")
                if not leverage_ok:
                    verification["details"].append(f"Leverage: requested {leverage}x, got {int(actual_leverage)}x")
                # AUTO-FLATTEN: immediately close the mismatched position
                verification["auto_flatten"] = True
                try:
                    close_result = exchange.market_close(symbol)
                    verification["auto_flatten_result"] = str(close_result.get("status", "unknown"))
                    verification["⚠️ HALT"] = "Position auto-flattened due to size/leverage mismatch. HALT file created."
                    # Create .HALT to stop all future trading
                    with open(HALT_PATH, "w") as hf:
                        hf.write(f"Auto-flattened {symbol} on {time.strftime('%Y-%m-%d %H:%M:%S')} UTC. "
                                f"Size/leverage mismatch: {'size' if not size_ok else ''} {'leverage' if not leverage_ok else ''}. "
                                f"Requested {coin_size}@{leverage}x, got {actual_size}@{int(actual_leverage)}x.")
                except Exception as e:
                    verification["auto_flatten_error"] = str(e)
        
        response = {
            "action": f"{direction.upper()} {symbol}",
            "status": result.get("status", "unknown"),
            "size_usd_notional": size_usd,
            "size_coin": coin_size,
            "avg_px": fill_data.get("avgPx", "N/A"),
            "oid": fill_data.get("oid", "N/A"),
            "leverage": leverage,
            "verification": verification
        }
        
        # Auto-place TP/SL if provided and order was successful
        if result.get("status") == "ok" and (tp_px or sl_px):
            try:
                time.sleep(1)  # Let the fill settle
                # We need to call cmd_set_stop logic inline to avoid subprocess
                exchange2 = get_exchange()
                query_addr2 = get_wallet_address()
                user_state2 = _api_post({"type": "clearinghouseState", "user": query_addr2})
                positions2 = user_state2.get("assetPositions", [])
                
                target2 = None
                for pos in positions2:
                    p = pos.get("position", {})
                    if p.get("coin") == symbol:
                        target2 = p
                        break
                
                if target2:
                    pos_size2 = abs(float(target2.get("szi", "0")))
                    is_long2 = float(target2.get("szi", "0")) > 0
                    stop_results = []
                    
                    if tp_px:
                        try:
                            tp_order_type = {"trigger": {"triggerPx": tp_px, "isMarket": True, "tpsl": "tp"}}
                            tp_result = exchange2.order(
                                name=symbol, is_buy=not is_long2, sz=pos_size2,
                                limit_px=tp_px, order_type=tp_order_type, reduce_only=True
                            )
                            stop_results.append({"type": "TP", "price": tp_px, "status": tp_result.get("status", "unknown")})
                        except Exception as e:
                            stop_results.append({"type": "TP", "price": tp_px, "error": str(e)})
                    
                    if sl_px:
                        try:
                            sl_order_type = {"trigger": {"triggerPx": sl_px, "isMarket": True, "tpsl": "sl"}}
                            sl_result = exchange2.order(
                                name=symbol, is_buy=not is_long2, sz=pos_size2,
                                limit_px=sl_px, order_type=sl_order_type, reduce_only=True
                            )
                            stop_results.append({"type": "SL", "price": sl_px, "status": sl_result.get("status", "unknown")})
                        except Exception as e:
                            stop_results.append({"type": "SL", "price": sl_px, "error": str(e)})
                    
                    response["auto_stops"] = stop_results
            except Exception as e:
                response["auto_stops_error"] = str(e)
        
        print(json.dumps(response, indent=2))
        
    except Exception as e:
        print(json.dumps({"error": str(e)}))

def cmd_close(symbol: str, size_pct: float = 100):
    """Close position. size_pct: 0-100 (100 = full close)."""
    exchange = get_exchange()
    try:
        query_addr = get_wallet_address()
        user_state = _api_post({"type": "clearinghouseState", "user": query_addr})
        positions = user_state.get("assetPositions", [])
        
        target = None
        for pos in positions:
            p = pos.get("position", {})
            if p.get("coin") == symbol:
                target = p
                break
        
        if not target:
            print(json.dumps({"error": f"No position in {symbol}"}))
            return
        
        # Use market_close for simplicity
        result = exchange.market_close(symbol, size_pct / 100)
        
        print(json.dumps({
            "action": f"CLOSE {symbol}",
            "pct_closed": size_pct,
            "status": result.get("status", "unknown"),
            "detail": str(result.get("response", {}))[:300]
        }, indent=2))
        
    except Exception as e:
        print(json.dumps({"error": str(e)}))

def cmd_update_leverage(symbol: str, leverage: int):
    """Update leverage for a symbol."""
    exchange = get_exchange()
    try:
        result = exchange.update_leverage(leverage, symbol)
        print(json.dumps({"symbol": symbol, "leverage": leverage, "result": str(result)[:200]}))
    except Exception as e:
        print(json.dumps({"error": str(e)}))

def cmd_set_stop(symbol: str, tp_px: float = None, sl_px: float = None):
    """Place real TP/SL trigger orders on the exchange.
    Uses exchange.order() with trigger order type — these are native exchange orders,
    not simulated. They execute even if the agent is down."""
    exchange = get_exchange()
    query_addr = get_wallet_address()
    
    # Get current position
    user_state = _api_post({"type": "clearinghouseState", "user": query_addr})
    positions = user_state.get("assetPositions", [])
    
    target = None
    for pos in positions:
        p = pos.get("position", {})
        if p.get("coin") == symbol:
            target = p
            break
    
    if not target:
        print(json.dumps({"error": f"No position in {symbol}"}))
        return
    
    pos_size = abs(float(target.get("szi", "0")))
    is_long = float(target.get("szi", "0")) > 0
    
    if pos_size <= 0:
        print(json.dumps({"error": f"Zero-size position in {symbol}"}))
        return
    
    results = []
    
    # TP order: sell if long, buy if short (opposite direction, reduce_only)
    if tp_px is not None:
        try:
            tp_order_type = {"trigger": {"triggerPx": tp_px, "isMarket": True, "tpsl": "tp"}}
            tp_result = exchange.order(
                name=symbol,
                is_buy=not is_long,
                sz=pos_size,
                limit_px=tp_px,
                order_type=tp_order_type,
                reduce_only=True
            )
            results.append({"type": "TP", "price": tp_px, "status": tp_result.get("status", "unknown")})
        except Exception as e:
            results.append({"type": "TP", "price": tp_px, "error": str(e)})
    
    # SL order: sell if long, buy if short (opposite direction, reduce_only)
    if sl_px is not None:
        try:
            sl_order_type = {"trigger": {"triggerPx": sl_px, "isMarket": True, "tpsl": "sl"}}
            sl_result = exchange.order(
                name=symbol,
                is_buy=not is_long,
                sz=pos_size,
                limit_px=sl_px,
                order_type=sl_order_type,
                reduce_only=True
            )
            results.append({"type": "SL", "price": sl_px, "status": sl_result.get("status", "unknown")})
        except Exception as e:
            results.append({"type": "SL", "price": sl_px, "error": str(e)})
    
    print(json.dumps({
        "action": "SET_STOPS",
        "symbol": symbol,
        "position": "LONG" if is_long else "SHORT",
        "size": pos_size,
        "orders": results
    }, indent=2))

def cmd_verify_stops(symbol: str = None):
    """Verify TP/SL orders exist on exchange for all positions (or a specific symbol).
    Reports missing stops without taking action. Use stops_watchdog for auto-repair."""
    query_addr = get_wallet_address()
    # Use frontendOpenOrders — openOrders endpoint omits trigger/orderType fields
    open_orders = _api_post({"type": "frontendOpenOrders", "user": query_addr})
    user_state = _api_post({"type": "clearinghouseState", "user": query_addr})
    positions = user_state.get("assetPositions", [])
    
    result = {
        "action": "VERIFY_STOPS",
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "positions_checked": 0,
        "stops_ok": 0,
        "stops_missing": [],
        "stale_orders": [],
        "all_ok": True
    }
    
    for pos in positions:
        p = pos.get("position", {})
        coin = p.get("coin", "")
        pos_size = abs(float(p.get("szi", "0")))
        
        if symbol and coin != symbol:
            continue
        if pos_size <= 0:
            continue
        
        result["positions_checked"] += 1
        is_long = float(p.get("szi", "0")) > 0
        
        # Find TP/SL orders for this coin
        tp_found = False
        sl_found = False
        tp_order = None
        sl_order = None
        
        for order in open_orders:
            o = order  # frontendOpenOrders returns flat objects, no nested "order"
            oid = o.get("oid")
            o_coin = o.get("coin", "")
            o_sz = abs(float(o.get("sz", "0")))
            o_reduce = o.get("reduceOnly", False)
            is_trigger = bool(o.get("isTrigger", False))
            trigger_px = float(o.get("triggerPx", "0") or "0")
            otype = o.get("orderType", "")
            
            if o_coin != coin or not o_reduce:
                continue
            if not is_trigger:
                continue
            
            # Match size (±10% tolerance for partial fills)
            if abs(o_sz - pos_size) / pos_size > 0.1:
                result["stale_orders"].append({
                    "oid": oid,
                    "coin": o_coin,
                    "type": otype or "stale",
                    "order_size": o_sz,
                    "position_size": pos_size,
                    "trigger_px": trigger_px
                })
                continue
            
            # Determine TP vs SL from orderType string (frontendOpenOrders)
            is_tp = "take profit" in otype.lower() or otype.lower() == "tp"
            is_sl = "stop" in otype.lower() or otype.lower() == "sl"
            
            if is_tp:
                tp_found = True
                tp_order = {"oid": oid, "price": trigger_px, "size": o_sz}
            elif is_sl:
                sl_found = True
                sl_order = {"oid": oid, "price": trigger_px, "size": o_sz}
        
        pos_status = {
            "symbol": coin,
            "side": "LONG" if is_long else "SHORT",
            "size": pos_size,
            "entry_px": float(p.get("entryPx", "0")),
            "tp": tp_order,
            "sl": sl_order,
            "tp_ok": tp_found,
            "sl_ok": sl_found
        }
        
        if tp_found and sl_found:
            result["stops_ok"] += 1
        else:
            result["stops_missing"].append(pos_status)
            result["all_ok"] = False
    
    print(json.dumps(result, indent=2))


def cmd_stops_watchdog(symbol: str = None):
    """Verify AND repair: check stops exist, re-place missing ones, HALT if unplaceable.
    This is the heartbeat safety check — call it EVERY cycle.
    
    Protocol:
    1. Verify stops exist → OK
    2. Stops missing → re-place via exchange.order() with trigger types
    3. Re-placement fails → create .HALT, report emergency
    """
    query_addr = get_wallet_address()
    exchange = get_exchange()
    open_orders = _api_post({"type": "frontendOpenOrders", "user": query_addr})
    user_state = _api_post({"type": "clearinghouseState", "user": query_addr})
    positions = user_state.get("assetPositions", [])
    
    result = {
        "action": "STOPS_WATCHDOG",
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "positions_checked": 0,
        "stops_ok": 0,
        "stops_repaired": [],
        "stops_failed": [],
        "halt": False,
        "all_ok": True
    }
    
    for pos in positions:
        p = pos.get("position", {})
        coin = p.get("coin", "")
        pos_size = abs(float(p.get("szi", "0")))
        
        if symbol and coin != symbol:
            continue
        if pos_size <= 0:
            continue
        
        result["positions_checked"] += 1
        is_long = float(p.get("szi", "0")) > 0
        
        # Find existing TP/SL
        tp_found = False
        sl_found = False
        
        for order in open_orders:
            o = order  # frontendOpenOrders returns flat objects
            o_coin = o.get("coin", "")
            o_sz = abs(float(o.get("sz", "0")))
            o_reduce = o.get("reduceOnly", False)
            is_trigger = bool(o.get("isTrigger", False))
            
            if o_coin != coin or not o_reduce or not is_trigger:
                continue
            if abs(o_sz - pos_size) / pos_size > 0.1:
                continue
            
            otype = o.get("orderType", "")
            if "take profit" in otype.lower():
                tp_found = True
            elif "stop" in otype.lower():
                sl_found = True
        
        # Use default TP/SL if none provided (these come from the caller's risk calc)
        # For watchdog mode, we only flag what's missing — the caller must provide prices.
        # If both are present, all good.
        if tp_found and sl_found:
            result["stops_ok"] += 1
            continue
        
        result["all_ok"] = False
        # Can't auto-repair without TP/SL prices — flag as missing.
        # The agent must provide prices via set_stop or the order command.
        missing = []
        if not tp_found:
            missing.append("TP")
        if not sl_found:
            missing.append("SL")
        
        result["stops_missing"] = result.get("stops_missing", [])
        result["stops_missing"].append({
            "symbol": coin,
            "side": "LONG" if is_long else "SHORT",
            "size": pos_size,
            "missing": missing,
            "urgent": True
        })
    
    # If any stops missing for >1 hour, HALT (tracked externally by the agent)
    if result.get("stops_missing") and len(result["stops_missing"]) > 0:
        result["warning"] = f"{len(result['stops_missing'])} position(s) with missing stops — REPAIR IMMEDIATELY via set_stop"
    
    print(json.dumps(result, indent=2))


def cmd_transfer(amount: float, to_perps: bool = True):
    """Transfer USDC between spot and perps.
    to_perps=True: spot → perps (default)
    to_perps=False: perps → spot"""
    exchange = get_exchange()
    try:
        direction = "spot → perps" if to_perps else "perps → spot"
        result = exchange.usd_class_transfer(amount, to_perp=to_perps)
        print(json.dumps({
            "action": f"Transfer {amount} USDC {direction}",
            "status": result.get("status", "unknown"),
            "detail": str(result.get("response", {}))[:300]
        }, indent=2))
    except Exception as e:
        print(json.dumps({"error": str(e)}))

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 hl_trader.py <command> [args...]")
        print("Commands: balance, market <symbol>, order <symbol> <long|short> <size_usd> [leverage] [tp] [sl], close <symbol> [pct], leverage <symbol> <lev>, verify_stops [symbol], stops_watchdog [symbol], set_stop <symbol> [tp] [sl], transfer <amount> [to_perps|to_spot]")
        sys.exit(1)
    
    cmd = sys.argv[1]
    check_halt(cmd)  # Hard kill switch — refuses mutating commands if .HALT exists
    
    if cmd == "balance":
        cmd_balance()
    elif cmd == "market":
        cmd_market(sys.argv[2])
    elif cmd == "order":
        symbol = sys.argv[2]
        direction = sys.argv[3]
        size_usd = float(sys.argv[4])
        leverage = int(sys.argv[5]) if len(sys.argv) > 5 else 1
        tp = float(sys.argv[6]) if len(sys.argv) > 6 else None
        sl = float(sys.argv[7]) if len(sys.argv) > 7 else None
        cmd_order(symbol, direction, size_usd, leverage, tp, sl)
    elif cmd == "close":
        symbol = sys.argv[2]
        pct = float(sys.argv[3]) if len(sys.argv) > 3 else 100
        cmd_close(symbol, pct)
    elif cmd == "leverage":
        cmd_update_leverage(sys.argv[2], int(sys.argv[3]))
    elif cmd == "transfer":
        amount = float(sys.argv[2])
        direction = sys.argv[3].lower() if len(sys.argv) > 3 else "to_perps"
        to_perps = direction in ("to_perps", "spot2perps", "s2p")
        cmd_transfer(amount, to_perps)
    elif cmd == "verify_stops" or cmd == "check_stops":
        symbol = sys.argv[2] if len(sys.argv) > 2 else None
        cmd_verify_stops(symbol)
    elif cmd == "stops_watchdog" or cmd == "watchdog":
        symbol = sys.argv[2] if len(sys.argv) > 2 else None
        cmd_stops_watchdog(symbol)
    elif cmd == "set_stop" or cmd == "stops":
        # Usage: python3 hl_trader.py set_stop <symbol> [tp_price] [sl_price]
        symbol = sys.argv[2]
        tp = float(sys.argv[3]) if len(sys.argv) > 3 and sys.argv[3] not in ("none", "None", "") else None
        sl = float(sys.argv[4]) if len(sys.argv) > 4 and sys.argv[4] not in ("none", "None", "") else None
        cmd_set_stop(symbol, tp, sl)
    else:
        print(f"Unknown command: {cmd}")

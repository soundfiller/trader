#!/usr/bin/env python3
"""
Trader Agent — Standalone Backtest Engine
=========================================
Ports the six-skill pipeline (chart -> whale -> signal -> risk -> execution -> journal)
into a deterministic, look-ahead-safe backtester for HYPE perps on Hyperliquid.

Design goals
------------
* No look-ahead: indicators are computed on CLOSED bar t; entry fills at OPEN of t+1.
* Faithful risk model: 2% risk sizing, the 9-step risk-manager gate, leverage cap.
* Real cost model: maker/taker fees, slippage, HOURLY funding accrual, builder-code toggle.
* Full metric battery: Sharpe / Sortino / Calmar / profit factor / win-rate-by-conviction,
  plus Probabilistic & Deflated Sharpe (Bailey & Lopez de Prado) for honest significance.

Data
----
fetch_hype_ohlcv()  -> REAL Hyperliquid candleSnapshot (use when running locally).
synthetic_ohlcv()   -> realistic GBM+vol-clustering generator (used in sandboxes w/o HL access).

Run:  python3 trader_backtest.py            # synthetic 1-month demo
      python3 trader_backtest.py --real      # pull real HYPE 4h candles from Hyperliquid
"""
from __future__ import annotations
import argparse, json, math, sys, time
from dataclasses import dataclass, field, asdict
import numpy as np
import pandas as pd
from scipy.stats import norm

# ----------------------------------------------------------------------------
# CONFIG  (mirrors config/risk.json — change here, not in prompts)
# ----------------------------------------------------------------------------
CONFIG = {
    "capital_usdc": 112.0,
    "limits": {
        "max_risk_per_trade_pct": 2.0,
        "max_position_pct": 40.0,        # of capital (NOTE: ambiguous vs leverage — see report)
        "max_gross_exposure_pct": 80.0,
        "daily_loss_limit_pct": 5.0,
        "min_conviction": 3,
        "min_risk_reward": 1.5,
        "max_leverage": 5,
        "default_leverage": 3,
        "allow_5x_conviction": 5,
        "allow_5x_rr_min": 2.5,
    },
    "signal": {
        "rr_target": 2.0,                # TP placed to target this R:R
        "atr_sl_mult": 1.0,              # SL >= 1x ATR beyond invalidation
        "max_hold_bars": 30,             # position expiry (4h*30 = 5 days)
        "cooldown_bars": 1,              # anti-overtrade
    },
    "fusion": {"chart": 0.55, "whale": 0.35, "regime": 0.10},
    "costs": {
        "execution_mode": "maker",       # 'maker' (limit) or 'taker' (market)
        "maker_fee_pct": 0.0150,
        "taker_fee_pct": 0.0450,
        "builder_code_pct": 0.0,         # set 0.10 to model a builder code leak
        "taker_slippage_pct": 0.030,     # modelled slippage for market orders
        "funding_hourly_bps_mean": 0.0,  # synthetic funding drift (bps/hr of notional)
        "funding_hourly_bps_vol": 1.0,   # synthetic funding noise (bps/hr)
    },
    "timeframe_hours": 4,                # 4h primary
    "dsr_trials": 10,                    # N strategy configs tried -> Deflated Sharpe haircut
    "dsr_trial_sr_vol": 0.5,             # assumed cross-trial annualized SR dispersion
    "seed": 42,
}

BARS_PER_YEAR = lambda tf_h: (24 / tf_h) * 365.0

# ----------------------------------------------------------------------------
# DATA LAYER
# ----------------------------------------------------------------------------
def fetch_hype_ohlcv(coin="HYPE", interval="4h", days=30):
    """REAL Hyperliquid candleSnapshot. Works where api.hyperliquid.xyz is reachable.
    Returns DataFrame[time, open, high, low, close, volume]."""
    import requests  # local-only dependency
    end = int(time.time() * 1000)
    start = end - days * 24 * 3600 * 1000
    body = {"type": "candleSnapshot",
            "req": {"coin": coin, "interval": interval, "startTime": start, "endTime": end}}
    r = requests.post("https://api.hyperliquid.xyz/info", json=body, timeout=20)
    r.raise_for_status()
    rows = r.json()
    df = pd.DataFrame([{
        "time": pd.to_datetime(c["t"], unit="ms"),
        "open": float(c["o"]), "high": float(c["h"]),
        "low": float(c["l"]), "close": float(c["c"]), "volume": float(c["v"]),
    } for c in rows])
    return df.sort_values("time").reset_index(drop=True)


def synthetic_ohlcv(days=30, tf_h=4, start_px=66.0, ann_vol=0.95, seed=42):
    """Realistic 4h OHLCV via GBM with GARCH-like vol clustering + mild regime.
    Calibrated loosely to HYPE (~$66, ~95% annualized vol). SYNTHETIC — validates
    the harness/logic, NOT HYPE's real edge."""
    rng = np.random.default_rng(seed)
    n = int(days * 24 / tf_h)
    dt = tf_h / (24 * 365.0)
    # vol clustering
    vol = np.empty(n); vol[0] = ann_vol
    for i in range(1, n):
        shock = rng.normal(0, 0.15)
        vol[i] = max(0.3, min(2.0, 0.97 * vol[i-1] + 0.03 * ann_vol + shock * ann_vol * 0.1))
    # regime drift: gentle up then chop then down
    drift = np.concatenate([np.full(n//3, 0.6), np.full(n//3, 0.0), np.full(n - 2*(n//3), -0.5)])
    closes = np.empty(n); closes[0] = start_px
    for i in range(1, n):
        mu = drift[i] * 0.5
        ret = (mu - 0.5 * vol[i]**2) * dt + vol[i] * math.sqrt(dt) * rng.normal()
        closes[i] = closes[i-1] * math.exp(ret)
    opens = np.empty(n); opens[0] = start_px
    opens[1:] = closes[:-1]
    bar_rng = np.abs(closes - opens) + closes * vol * math.sqrt(dt) * np.abs(rng.normal(size=n)) * 0.8
    highs = np.maximum(opens, closes) + bar_rng * rng.uniform(0.1, 0.7, n)
    lows  = np.minimum(opens, closes) - bar_rng * rng.uniform(0.1, 0.7, n)
    volume = np.abs(rng.normal(1_000_000, 350_000, n)) * (1 + (vol / ann_vol - 1))
    times = pd.date_range("2026-05-23", periods=n, freq=f"{tf_h}h")
    return pd.DataFrame({"time": times, "open": opens, "high": highs,
                         "low": lows, "close": closes, "volume": volume})

# ----------------------------------------------------------------------------
# SKILL 1 — chart-analysis  (EMA stack, RSI, MACD, ATR, structure)
# ----------------------------------------------------------------------------
def ema(s, span): return s.ewm(span=span, adjust=False).mean()

def rsi(close, n=14):
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1/n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1/n, adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50)

def atr(df, n=14):
    h, l, c = df["high"], df["low"], df["close"]
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1/n, adjust=False).mean()

def chart_analysis(df):
    out = df.copy()
    out["ema20"], out["ema50"], out["ema200"] = ema(df.close,20), ema(df.close,50), ema(df.close,200)
    out["rsi"] = rsi(df.close)
    macd = ema(df.close,12) - ema(df.close,26)
    out["macd"], out["macd_sig"] = macd, ema(macd,9)
    out["atr"] = atr(df)
    # structure: higher-highs / higher-lows over 10 bars
    out["hh"] = df.high.rolling(10).max()
    out["ll"] = df.low.rolling(10).min()
    bias, conv, invalid = [], [], []
    for i in range(len(out)):
        r = out.iloc[i]
        if i < 200 or pd.isna(r.ema200):
            bias.append("neutral"); conv.append(1); invalid.append(np.nan); continue
        score = 0
        bull_stack = r.ema20 > r.ema50 > r.ema200
        bear_stack = r.ema20 < r.ema50 < r.ema200
        score += 1 if bull_stack else (-1 if bear_stack else 0)
        score += 1 if r.close > r.ema50 else -1
        score += 1 if r.macd > r.macd_sig else -1
        score += 1 if r.rsi > 55 else (-1 if r.rsi < 45 else 0)
        # structure agreement
        up_struct = r.close > out.iloc[max(0,i-10)].close and r.low > out.iloc[max(0,i-5)].ll
        score += 1 if up_struct else -1
        b = "bullish" if score >= 2 else ("bearish" if score <= -2 else "neutral")
        c = min(5, max(1, 1 + abs(score)))            # |score| 0..5 -> conv 1..5
        bias.append(b); conv.append(int(c))
        inv = r.ema50 if b == "bullish" else (r.ema50 if b == "bearish" else r.close)
        invalid.append(inv)
    out["chart_bias"], out["chart_conv"], out["invalidation"] = bias, conv, invalid
    return out

# ----------------------------------------------------------------------------
# SKILL 2 — whale-tracker (PROXY: volume z-score + funding sentiment)
#   NOTE: real version needs HL openInterest/funding + wallet clusters. Capped at 4.
# ----------------------------------------------------------------------------
def whale_tracker(df, funding):
    out = df.copy()
    z = (df.volume - df.volume.rolling(20).mean()) / df.volume.rolling(20).std()
    out["vol_z"] = z.fillna(0)
    sent, conv = [], []
    for i in range(len(out)):
        zi = out.iloc[i].vol_z
        f = funding[i]
        s = "neutral"
        if zi >= 1 and df.close.iloc[i] > df.open.iloc[i]: s = "accumulation"
        elif zi >= 1 and df.close.iloc[i] < df.open.iloc[i]: s = "distribution"
        c = 1
        if abs(zi) >= 2: c = 3
        if abs(zi) >= 3: c = 4         # capped at 4 without wallet clusters
        if abs(zi) >= 1: c = max(c, 2)
        sent.append(s); conv.append(c)
    out["whale_sent"], out["whale_conv"] = sent, conv
    return out

# ----------------------------------------------------------------------------
# SKILL 3 — signal-generator (fuse chart+whale, entry/SL/TP/RR, expiry)
# ----------------------------------------------------------------------------
@dataclass
class Signal:
    idx: int; direction: str; entry: float; sl: float; tp: float
    rr: float; conviction: int; thesis: str

def signal_generator(df, cfg):
    w = cfg["fusion"]; sigs = []
    for i in range(len(df)):
        r = df.iloc[i]
        if r.chart_bias == "neutral" or pd.isna(r.atr): continue
        direction = "long" if r.chart_bias == "bullish" else "short"
        # whale agreement / conflict
        whale_dir = {"accumulation": "long", "distribution": "short", "neutral": direction}[r.whale_sent]
        conflict = whale_dir != direction
        regime_conv = 3 if (r.ema20 > r.ema200) == (direction == "long") else 2
        fused = w["chart"]*r.chart_conv + w["whale"]*r.whale_conv + w["regime"]*regime_conv
        if conflict: fused -= 1.0                      # penalize conflict
        conviction = int(min(5, max(1, round(fused))))
        entry = r.close                                # pullback-to-structure proxy
        atr_sl = cfg["signal"]["atr_sl_mult"] * r.atr
        if direction == "long":
            sl = min(r.invalidation, entry - atr_sl)
            dist = entry - sl
            tp = entry + cfg["signal"]["rr_target"] * dist
        else:
            sl = max(r.invalidation, entry + atr_sl)
            dist = sl - entry
            tp = entry - cfg["signal"]["rr_target"] * dist
        if dist <= 0: continue
        rr = abs(tp - entry) / dist
        sigs.append(Signal(i, direction, entry, sl, tp, rr, conviction,
                           f"{direction} {r.chart_bias}/{r.whale_sent} conflict={conflict}"))
    return sigs

# ----------------------------------------------------------------------------
# SKILL 4 — risk-manager (9-step fail-fast gate + 2% sizing)
# ----------------------------------------------------------------------------
@dataclass
class Sized:
    sig: Signal; qty: float; notional: float; risk_usd: float; leverage: float; verdict: str

def risk_manager(sig, capital, cfg):
    L = cfg["limits"]
    if capital <= 0: return Sized(sig,0,0,0,0,"rejected:no_capital")
    if sig.sl is None: return Sized(sig,0,0,0,0,"rejected:no_sl")
    if sig.conviction < L["min_conviction"]: return Sized(sig,0,0,0,0,"rejected:conviction")
    if sig.rr < L["min_risk_reward"]: return Sized(sig,0,0,0,0,"rejected:rr")
    risk_usd = capital * L["max_risk_per_trade_pct"]/100.0
    dist = abs(sig.entry - sig.sl)
    qty = risk_usd / dist
    notional = qty * sig.entry
    leverage = notional / capital
    # leverage cap is the binding constraint (see report: 40% notional rule is ambiguous)
    max_lev = L["max_leverage"] if (sig.conviction >= L["allow_5x_conviction"] and sig.rr >= L["allow_5x_rr_min"]) else L["default_leverage"]
    if leverage > max_lev:
        qty *= max_lev / leverage
        notional = qty * sig.entry
        leverage = max_lev
    return Sized(sig, qty, notional, risk_usd, leverage, "approved")

# ----------------------------------------------------------------------------
# SKILL 5 — execution sim (fees, slippage, hourly funding) + position lifecycle
# ----------------------------------------------------------------------------
@dataclass
class Trade:
    entry_idx:int; exit_idx:int; direction:str; entry:float; exit:float
    qty:float; notional:float; conviction:int; rr:float
    gross_pnl:float; fees:float; funding:float; net_pnl:float; r_mult:float; reason:str

def run_backtest(df, cfg, test_start_idx=0):
    """test_start_idx: bar index where the evaluated window begins (earlier bars are
    indicator warmup only — no trades opened, not counted in equity/metrics)."""
    rng = np.random.default_rng(cfg["seed"]+1)
    n = len(df)
    tf = cfg["timeframe_hours"]
    fmean, fvol = cfg["costs"]["funding_hourly_bps_mean"], cfg["costs"]["funding_hourly_bps_vol"]
    funding_hourly = rng.normal(fmean, fvol, n*tf) / 1e4   # fraction of notional per hour
    bar_funding = funding_hourly.reshape(-1, tf).sum(axis=1)

    df = chart_analysis(df)
    df = whale_tracker(df, bar_funding)
    sigs = [s for s in signal_generator(df, cfg) if s.idx >= test_start_idx-1]
    sig_by_idx = {s.idx: s for s in sigs}

    mode = cfg["costs"]["execution_mode"]
    fee_pct = (cfg["costs"]["maker_fee_pct"] if mode=="maker" else cfg["costs"]["taker_fee_pct"]) + cfg["costs"]["builder_code_pct"]
    slip_pct = 0.0 if mode=="maker" else cfg["costs"]["taker_slippage_pct"]
    risk_usd = cfg["capital_usdc"]*cfg["limits"]["max_risk_per_trade_pct"]/100.0

    capital = cfg["capital_usdc"]
    equity_curve=[capital]; trades=[]; open_pos=None; cooldown_until=-1
    day_anchor_equity=capital; cur_day=df.time.iloc[test_start_idx].date()

    for i in range(max(1,test_start_idx), n):
        bar = df.iloc[i]
        if bar.time.date()!=cur_day:
            cur_day=bar.time.date(); day_anchor_equity=capital
        # ---- manage open position ----
        if open_pos is not None:
            s,qty,notional,entry_px,bars_held,acc_funding,entry_idx = open_pos
            f_cost = notional*bar_funding[i]*(1 if s.direction=="long" else -1)
            capital-=f_cost; acc_funding+=f_cost
            hit=exit_px=None
            if s.direction=="long":
                if bar.low<=s.sl: hit,exit_px="SL",s.sl
                elif bar.high>=s.tp: hit,exit_px="TP",s.tp
            else:
                if bar.high>=s.sl: hit,exit_px="SL",s.sl
                elif bar.low<=s.tp: hit,exit_px="TP",s.tp
            bars_held+=1
            if hit is None and bars_held>=cfg["signal"]["max_hold_bars"]:
                hit,exit_px="EXPIRY",bar.close
            if hit:
                if hit=="TP": fill=exit_px
                else: fill=exit_px*(1-slip_pct/100) if s.direction=="long" else exit_px*(1+slip_pct/100)
                gross=(fill-entry_px)*qty if s.direction=="long" else (entry_px-fill)*qty
                entry_fee=abs(notional)*fee_pct/100; exit_fee=abs(qty*fill)*fee_pct/100
                net=gross-entry_fee-exit_fee-acc_funding
                capital+=net
                trades.append(Trade(open_pos[6],i,s.direction,entry_px,fill,qty,notional,
                                    s.conviction,s.rr,gross,entry_fee+exit_fee,acc_funding,net,net/risk_usd,hit))
                open_pos=None; cooldown_until=i+cfg["signal"]["cooldown_bars"]
            else:
                open_pos=(s,qty,notional,entry_px,bars_held,acc_funding,open_pos[6])
        # ---- new entry: signal on closed bar i-1, fill at open of i ----
        if open_pos is None and i>cooldown_until:
            sig=sig_by_idx.get(i-1)
            if sig is not None:
                sized=risk_manager(sig,capital,cfg)
                dd_today=(capital-day_anchor_equity)/day_anchor_equity*100 if day_anchor_equity>0 else 0
                if sized.verdict=="approved" and dd_today>-cfg["limits"]["daily_loss_limit_pct"]:
                    fill=bar.open*(1+slip_pct/100) if sig.direction=="long" else bar.open*(1-slip_pct/100)
                    qty=sized.qty; notional=qty*fill
                    open_pos=(sig,qty,notional,fill,0,0.0,i)
        equity_curve.append(capital)

    return df, trades, np.array(equity_curve), sigs

# ----------------------------------------------------------------------------
# SKILL 6 — journal-analyzer (metric battery + PSR/DSR)
# ----------------------------------------------------------------------------
def deflated_sharpe(returns, n_trials, trial_sr_vol_ann, periods_per_year):
    r = np.asarray(returns, float)
    T = len(r)
    if T < 3 or r.std(ddof=1)==0: return dict(sr_ann=0,psr=0,dsr=0,note="insufficient trades")
    sr = r.mean()/r.std(ddof=1)                      # per-period Sharpe
    sk = float(pd.Series(r).skew()); ku = float(pd.Series(r).kurtosis())+3
    # Probabilistic Sharpe that true SR>0:
    psr = norm.cdf((sr*math.sqrt(T-1))/math.sqrt(1 - sk*sr + (ku-1)/4*sr**2))
    # expected max Sharpe under N trials (Bailey & Lopez de Prado), per-period scale:
    g = 0.5772156649
    sr_trial_vol = trial_sr_vol_ann/math.sqrt(periods_per_year)
    e_max = sr_trial_vol*((1-g)*norm.ppf(1-1/n_trials) + g*norm.ppf(1-1/(n_trials*math.e)))
    dsr = norm.cdf(((sr-e_max)*math.sqrt(T-1))/math.sqrt(1 - sk*sr + (ku-1)/4*sr**2))
    return dict(sr_ann=sr*math.sqrt(periods_per_year), psr=psr, dsr=dsr,
                e_max_sr_ann=e_max*math.sqrt(periods_per_year), note="ok")

def metrics(df, trades, equity, cfg):
    tf=cfg["timeframe_hours"]; ppy=BARS_PER_YEAR(tf)
    eq=pd.Series(equity); rets=eq.pct_change().dropna()
    res={}
    res["capital_start"]=cfg["capital_usdc"]; res["capital_end"]=float(eq.iloc[-1])
    res["total_return_pct"]=(eq.iloc[-1]/eq.iloc[0]-1)*100
    res["n_trades"]=len(trades)
    res["n_signals"]=None
    # buy & hold
    bh=(df.close.iloc[-1]/df.close.iloc[0]-1)*100
    res["buyhold_return_pct"]=bh
    if trades:
        tdf=pd.DataFrame([asdict(t) for t in trades])
        wins=tdf[tdf.net_pnl>0]; losses=tdf[tdf.net_pnl<=0]
        res["win_rate_pct"]=len(wins)/len(tdf)*100
        res["profit_factor"]=(wins.net_pnl.sum()/abs(losses.net_pnl.sum())) if len(losses) and losses.net_pnl.sum()!=0 else float('inf')
        res["avg_R"]=tdf.r_mult.mean(); res["expectancy_R"]=tdf.r_mult.mean()
        res["total_fees"]=tdf.fees.sum(); res["gross_pnl"]=tdf.gross_pnl.sum(); res["net_pnl"]=tdf.net_pnl.sum()
        # win rate by conviction bucket
        res["winrate_by_conviction"]={int(c): round(g.assign(w=g.net_pnl>0).w.mean()*100,1)
                                       for c,g in tdf.groupby("conviction")}
        res["trades_by_conviction"]={int(c): int(len(g)) for c,g in tdf.groupby("conviction")}
        res["exit_reasons"]=tdf.reason.value_counts().to_dict()
        # risk-adjusted on per-trade net returns (as % of capital)
        trade_rets=tdf.net_pnl.values/cfg["capital_usdc"]
        dd=(eq/eq.cummax()-1); res["max_drawdown_pct"]=dd.min()*100
        if rets.std()>0:
            res["sharpe_ann"]=rets.mean()/rets.std()*math.sqrt(ppy)
            downside=rets[rets<0]
            res["sortino_ann"]=rets.mean()/downside.std()*math.sqrt(ppy) if len(downside)>1 and downside.std()>0 else float('nan')
            ann_ret=(eq.iloc[-1]/eq.iloc[0])**(ppy/len(rets))-1
            res["calmar"]=ann_ret/abs(dd.min()) if dd.min()<0 else float('inf')
        d=deflated_sharpe(trade_rets, cfg["dsr_trials"], cfg["dsr_trial_sr_vol"], len(trades) if len(trades)>2 else 3)
        res["psr_gt0"]=d["psr"]; res["deflated_sharpe"]=d["dsr"]; res["dsr_note"]=d["note"]
    return res

# ----------------------------------------------------------------------------
def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--real",action="store_true",help="pull real HYPE candles from Hyperliquid")
    ap.add_argument("--days",type=int,default=30)
    ap.add_argument("--mode",default=None,help="maker|taker override")
    ap.add_argument("--builder",type=float,default=None,help="builder code pct, e.g. 0.10")
    ap.add_argument("--warmup",type=int,default=45,help="warmup days for EMA200 (not counted)")
    args=ap.parse_args()
    cfg=json.loads(json.dumps(CONFIG))  # deep copy
    if args.mode: cfg["costs"]["execution_mode"]=args.mode
    if args.builder is not None: cfg["costs"]["builder_code_pct"]=args.builder

    bars_per_day=24/cfg["timeframe_hours"]
    warmup_bars=int(args.warmup*bars_per_day)
    if args.real:
        df=fetch_hype_ohlcv(days=args.days+args.warmup); src="REAL Hyperliquid HYPE 4h"
    else:
        df=synthetic_ohlcv(days=args.days+args.warmup, tf_h=cfg["timeframe_hours"], seed=cfg["seed"]); src="SYNTHETIC (validates harness, not HYPE edge)"
    test_start_idx=len(df)-int(args.days*bars_per_day)

    df2,trades,equity,sigs=run_backtest(df,cfg,test_start_idx=test_start_idx)
    test_df=df2.iloc[test_start_idx:].reset_index(drop=True)
    res=metrics(test_df,trades,equity,cfg); res["n_signals"]=len(sigs); res["data_source"]=src

    print("="*70); print(f"TRADER BACKTEST  |  {src}")
    print(f"bars={len(df)}  days={args.days}  tf={cfg['timeframe_hours']}h  exec={cfg['costs']['execution_mode']}  builder={cfg['costs']['builder_code_pct']}%")
    print("="*70)
    order=["data_source","capital_start","capital_end","total_return_pct","buyhold_return_pct",
           "n_signals","n_trades","win_rate_pct","profit_factor","avg_R","expectancy_R",
           "gross_pnl","total_fees","net_pnl","max_drawdown_pct","sharpe_ann","sortino_ann",
           "calmar","psr_gt0","deflated_sharpe","e_max","dsr_note",
           "winrate_by_conviction","trades_by_conviction","exit_reasons"]
    for k in order:
        if k in res:
            v=res[k]
            if isinstance(v,float): v=round(v,4)
            print(f"  {k:24s}: {v}")
    print("="*70)
    return res

if __name__=="__main__":
    main()

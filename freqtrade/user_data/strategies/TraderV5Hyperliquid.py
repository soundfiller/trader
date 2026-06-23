# pragma pylint: disable=missing-docstring, invalid-name, pointless-string-statement
# flake8: noqa: F401
# --- Do not remove these libs ---
import numpy as np
import pandas as pd
from pandas import DataFrame
from datetime import datetime
from typing import Optional, Union

from freqtrade.strategy import (BooleanParameter, CategoricalParameter, DecimalParameter,
                                IStrategy, IntParameter, RealParameter)

# --------------------------------
# TraderV5 — Dual-Mode Regime Switch
# Mean-Reversion (ADX < 20) + Trend-Following (ADX > 25)
# Ported from trader_backtest_v5.py
# --------------------------------

class TraderV5Hyperliquid(IStrategy):
    # Strategy interface
    INTERFACE_VERSION = 3
    can_short = False  # spot mode for backtest; set True for futures live
    timeframe = '4h'

    # Risk
    stoploss = -0.10  # Hard 10% SL — overridden by custom_stoploss
    trailing_stop = False  # Dynamic trailing in TF mode handled via custom_exit
    use_custom_stoploss = True

    # ROI — disabled (we use custom exits)
    minimal_roi = {"0": 100}

    # Protection
    @property
    def protections(self):
        return [
            {"method": "CooldownPeriod", "lookback_period_candles": 1, "stop_duration_candles": 0},
            {"method": "MaxDrawdown", "lookback_period_candles": 200, "trade_limit": 5,
             "stop_duration_candles": 20, "max_allowed_drawdown": 0.05},
            {"method": "StoplossGuard", "lookback_period_candles": 48, "trade_limit": 2,
             "stop_duration_candles": 12, "only_per_pair": True},
        ]

    # Position sizing
    stake_amount = "unlimited"
    max_open_trades = 2

    # === V5 TUNABLE PARAMETERS ===
    # ADX regime gates
    adx_low = IntParameter(18, 25, default=20, space="buy")
    adx_high = IntParameter(25, 35, default=25, space="buy")

    # Mean-reversion params
    mr_rr_target = DecimalParameter(2.0, 4.0, default=2.5, decimals=1, space="buy")
    mr_atr_sl_mult = DecimalParameter(1.0, 2.5, default=2.0, decimals=1, space="buy")

    # Trend-following params
    tf_trail_atr_mult = DecimalParameter(2.0, 4.0, default=2.5, decimals=1, space="sell")
    tf_trail_activation_r = DecimalParameter(1.0, 2.5, default=1.5, decimals=1, space="sell")
    tf_entry_atr_breakout = DecimalParameter(0.2, 0.5, default=0.4, decimals=1, space="buy")

    # Fusion
    chart_weight = DecimalParameter(0.60, 0.80, default=0.75, decimals=2, space="buy")

    # Minimum conviction
    min_conviction = IntParameter(3, 4, default=3, space="buy")
    min_rr = DecimalParameter(1.0, 2.0, default=1.0, decimals=2, space="buy")

    # === INDICATORS ===
    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # EMAs
        dataframe['ema20'] = dataframe['close'].ewm(span=20, adjust=False).mean()
        dataframe['ema50'] = dataframe['close'].ewm(span=50, adjust=False).mean()
        dataframe['ema200'] = dataframe['close'].ewm(span=200, adjust=False).mean()

        # RSI
        delta = dataframe['close'].diff()
        up = delta.clip(lower=0)
        down = -delta.clip(upper=0)
        rsi_up = up.ewm(alpha=1/14, adjust=False).mean()
        rsi_down = down.ewm(alpha=1/14, adjust=False).mean()
        dataframe['rsi'] = 100 - 100 / (1 + rsi_up / rsi_down.replace(0, np.nan))

        # MACD
        macd_fast = dataframe['close'].ewm(span=12, adjust=False).mean()
        macd_slow = dataframe['close'].ewm(span=26, adjust=False).mean()
        dataframe['macd'] = macd_fast - macd_slow
        dataframe['macd_sig'] = dataframe['macd'].ewm(span=9, adjust=False).mean()

        # ATR
        h, l, c = dataframe['high'], dataframe['low'], dataframe['close']
        tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
        dataframe['atr'] = tr.ewm(alpha=1/14, adjust=False).mean()

        # ADX
        plus_dm = dataframe['high'].diff().clip(lower=0)
        minus_dm = (-dataframe['low'].diff()).clip(lower=0)
        plus_di = 100 * (plus_dm.ewm(alpha=1/14, adjust=False).mean() / dataframe['atr'].replace(0, np.nan))
        minus_di = 100 * (minus_dm.ewm(alpha=1/14, adjust=False).mean() / dataframe['atr'].replace(0, np.nan))
        dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di).replace(0, np.nan)
        dataframe['adx'] = dx.ewm(alpha=1/14, adjust=False).mean()

        # Volume z-score (whale proxy)
        vol_mean = dataframe['volume'].rolling(20).mean()
        vol_std = dataframe['volume'].rolling(20).std()
        dataframe['vol_z'] = ((dataframe['volume'] - vol_mean) / vol_std.replace(0, 1)).fillna(0)

        # Trend direction
        dataframe['trend_up'] = dataframe['ema50'] > dataframe['ema200']
        dataframe['ema_stack_bull'] = dataframe['ema20'] > dataframe['ema50']

        # Chart bias
        def _bias(r):
            score = 0
            score += 1 if r.ema20 > r.ema50 > r.ema200 else (-1 if r.ema20 < r.ema50 < r.ema200 else 0)
            score += 1 if r.close > r.ema50 else -1
            score += 1 if r.macd > r.macd_sig else -1
            score += 1 if r.rsi > 55 else (-1 if r.rsi < 45 else 0)
            return "bullish" if score >= 2 else ("bearish" if score <= -2 else "neutral")

        dataframe['chart_bias'] = dataframe.apply(_bias, axis=1)

        # Conviction
        def _conv(r):
            if r.chart_bias == "neutral": return 1
            score = abs(1 if r.ema20 > r.ema200 else -1)
            score += (1 if r.macd > r.macd_sig else -1)
            score += (1 if r.rsi > 55 else (-1 if r.rsi < 45 else 0))
            score += (1 if r.vol_z > 0 else -1)
            return min(5, max(1, 1 + abs(score)))

        dataframe['chart_conv'] = dataframe.apply(_conv, axis=1)

        # Whale sentiment
        def _whale_sent(r):
            if r.vol_z >= 1 and r.close > r.open: return "accumulation"
            elif r.vol_z >= 1 and r.close < r.open: return "distribution"
            return "neutral"

        dataframe['whale_sent'] = dataframe.apply(_whale_sent, axis=1)

        # Whale conviction
        def _whale_conv(r):
            z = abs(r.vol_z)
            if z >= 3: return 4
            if z >= 2: return 3
            if z >= 1: return 2
            return 1

        dataframe['whale_conv'] = dataframe.apply(_whale_conv, axis=1)

        # Regime detection
        dataframe['is_trending'] = dataframe['adx'] > self.adx_high.value
        dataframe['is_ranging'] = dataframe['adx'] < self.adx_low.value
        dataframe['transition_zone'] = (dataframe['adx'] >= self.adx_low.value) & (dataframe['adx'] <= self.adx_high.value)

        return dataframe

    # === ENTRY SIGNALS ===
    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # Mean-reversion entries (ADX < low threshold)
        mr_long = (
            (dataframe['is_ranging']) &
            (dataframe['chart_bias'] == 'bullish') &
            (dataframe['chart_conv'] >= self.min_conviction.value) &
            (dataframe['volume'] > 0)
        )
        mr_short = (
            (dataframe['is_ranging']) &
            (dataframe['chart_bias'] == 'bearish') &
            (dataframe['chart_conv'] >= self.min_conviction.value) &
            (dataframe['volume'] > 0)
        )

        # Trend-following entries (ADX > high threshold, with trend)
        tf_long = (
            (dataframe['is_trending']) &
            (dataframe['chart_bias'] == 'bullish') &
            (dataframe['trend_up']) &
            (dataframe['chart_conv'] >= self.min_conviction.value) &
            (dataframe['volume'] > 0)
        )
        tf_short = (
            (dataframe['is_trending']) &
            (dataframe['chart_bias'] == 'bearish') &
            (~dataframe['trend_up']) &
            (dataframe['chart_conv'] >= self.min_conviction.value) &
            (dataframe['volume'] > 0)
        )

        dataframe.loc[mr_long, 'enter_long'] = 1
        dataframe.loc[mr_short, 'enter_short'] = 1
        dataframe.loc[tf_long, 'enter_long'] = 1
        dataframe.loc[tf_short, 'enter_short'] = 1

        return dataframe

    # === EXIT SIGNALS ===
    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # V5 exits are handled via custom_stoploss and custom_exit
        return dataframe

    # === CUSTOM STOPS ===
    def custom_stoploss(self, pair: str, trade: 'Trade', current_time: datetime,
                        current_rate: float, current_profit: float, after_fill: bool,
                        **kwargs) -> Optional[float]:
        """Dynamic SL based on regime mode."""
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if dataframe is None or len(dataframe) == 0:
            return None

        last_candle = dataframe.iloc[-1]
        adx = last_candle.get('adx', 25)
        atr = last_candle.get('atr', 0)

        if adx > self.adx_high.value:
            # Trend-following: trailing stop at tf_trail_atr_mult * ATR
            if current_profit > self.tf_trail_activation_r.value / 100:
                # Activate trailing
                tf_sl = -(self.tf_trail_atr_mult.value * atr / current_rate)
                return tf_sl
            else:
                # Wide initial SL for TF entries
                return -(2.0 * atr / current_rate)
        else:
            # Mean-reversion: fixed ATR-based SL
            return -(self.mr_atr_sl_mult.value * atr / current_rate)

    def custom_exit(self, pair: str, trade: 'Trade', current_time: datetime,
                    current_rate: float, current_profit: float, **kwargs):
        """Custom exit for fixed TP in mean-reversion mode."""
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if dataframe is None or len(dataframe) == 0:
            return None

        last_candle = dataframe.iloc[-1]
        adx = last_candle.get('adx', 25)
        atr = last_candle.get('atr', 0)
        entry = trade.open_rate

        if adx < self.adx_high.value:
            # Mean-reversion: fixed R:R TP
            rr_target = self.mr_rr_target.value
            sl_mult = self.mr_atr_sl_mult.value

            if trade.is_short:
                tp_price = entry - rr_target * sl_mult * atr
                if current_rate <= tp_price:
                    return "mr_tp_hit"
            else:
                tp_price = entry + rr_target * sl_mult * atr
                if current_rate >= tp_price:
                    return "mr_tp_hit"

        return None

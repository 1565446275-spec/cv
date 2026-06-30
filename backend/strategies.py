"""
strategies.py — 交易策略信号生成模块

每个策略函数接收 OHLCV DataFrame 和参数字典，
返回一个 pandas Series（或类似结构），其中：
    +1 → 买入信号
     0 → 持有/无操作
    -1 → 卖出信号

策略列表：
    - ma_cross          ：双均线交叉（短线上穿长线买入，下穿卖出）
    - rsi_reversal      ：RSI 超卖超买反转（< 阈值买入，> 阈值卖出）
    - macd_cross        ：MACD 金叉/死叉
    - bollinger_reversion：布林带上下轨回归
"""

from __future__ import annotations

import pandas as pd
import numpy as np


def compute_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """计算相对强弱指数 RSI。"""
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)

    avg_gain = gain.ewm(span=period, adjust=False).mean()
    avg_loss = loss.ewm(span=period, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi


def compute_macd(
    series: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """计算 MACD 线、信号线和柱状图。"""
    ema_fast = series.ewm(span=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def compute_bollinger(
    series: pd.Series,
    period: int = 20,
    std_dev: float = 2.0,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """计算布林带中轨、上轨、下轨。"""
    sma = series.rolling(window=period).mean()
    std = series.rolling(window=period).std(ddof=0)
    upper = sma + std_dev * std
    lower = sma - std_dev * std
    return sma, upper, lower


# ---------------------------------------------------------------------------
# 策略实现
# ---------------------------------------------------------------------------


def ma_cross(
    df: pd.DataFrame,
    params: dict | None = None,
) -> pd.Series:
    """
    双均线交叉策略。

    params:
        short_window (int)  ：短周期，默认 5
        long_window  (int)  ：长周期，默认 20
    """
    if params is None:
        params = {}
    short_w = int(params.get("short_window", 5))
    long_w = int(params.get("long_window", 20))

    if short_w >= long_w:
        raise ValueError(
            f"short_window ({short_w}) 必须小于 long_window ({long_w})"
        )

    close = df["close"]
    ma_short = close.rolling(window=short_w).mean()
    ma_long = close.rolling(window=long_w).mean()

    signals = pd.Series(0, index=df.index, dtype=float)

    # 产生有效信号的起点
    start_idx = max(short_w, long_w)
    if len(df) <= start_idx:
        return signals

    prev_short = ma_short.shift(1)
    prev_long = ma_long.shift(1)

    # 上穿 → 买入
    buy_cond = (ma_short >= ma_long) & (prev_short < prev_long)
    # 下穿 → 卖出
    sell_cond = (ma_short <= ma_long) & (prev_short > prev_long)

    signals.loc[buy_cond] = 1
    signals.loc[sell_cond] = -1

    return signals


def rsi_reversal(
    df: pd.DataFrame,
    params: dict | None = None,
) -> pd.Series:
    """
    RSI 超卖超买反转策略。

    params:
        rsi_period         (int)  ：RSI 计算周期，默认 14
        oversold_threshold (float)：超卖阈值，默认 30
        overbought_threshold (float)：超买阈值，默认 70
    """
    if params is None:
        params = {}
    period = int(params.get("rsi_period", 14))
    oversold = float(params.get("oversold_threshold", 30))
    overbought = float(params.get("overbought_threshold", 70))

    if oversold >= overbought:
        raise ValueError(
            f"oversold_threshold ({oversold}) 必须小于 overbought_threshold ({overbought})"
        )

    close = df["close"]
    rsi = compute_rsi(close, period=period)

    signals = pd.Series(0, index=df.index, dtype=float)

    # RSI 从超卖区回升 → 买入；从超买区回落 → 卖出
    prev_rsi = rsi.shift(1)

    buy_cond = (prev_rsi <= oversold) & (rsi > oversold)
    sell_cond = (prev_rsi >= overbought) & (rsi < overbought)

    signals.loc[buy_cond] = 1
    signals.loc[sell_cond] = -1

    return signals


def macd_cross(
    df: pd.DataFrame,
    params: dict | None = None,
) -> pd.Series:
    """
    MACD 金叉/死叉策略。

    params:
        fast_period   (int)：快线周期，默认 12
        slow_period   (int)：慢线周期，默认 26
        signal_period (int)：信号线周期，默认 9
    """
    if params is None:
        params = {}
    fast_p = int(params.get("fast_period", 12))
    slow_p = int(params.get("slow_period", 26))
    signal_p = int(params.get("signal_period", 9))

    if fast_p >= slow_p:
        raise ValueError(
            f"fast_period ({fast_p}) 必须小于 slow_period ({slow_p})"
        )

    close = df["close"]
    macd_line, signal_line, _ = compute_macd(close, fast_p, slow_p, signal_p)

    signals = pd.Series(0, index=df.index, dtype=float)

    prev_macd = macd_line.shift(1)
    prev_signal = signal_line.shift(1)

    # MACD 上穿信号线 → 金叉 → 买入
    buy_cond = (macd_line >= signal_line) & (prev_macd < prev_signal)
    # MACD 下穿信号线 → 死叉 → 卖出
    sell_cond = (macd_line <= signal_line) & (prev_macd > prev_signal)

    signals.loc[buy_cond] = 1
    signals.loc[sell_cond] = -1

    return signals


def bollinger_reversion(
    df: pd.DataFrame,
    params: dict | None = None,
) -> pd.Series:
    """
    布林带均值回归策略。

    params:
        period  (int)  ：布林带周期，默认 20
        std_dev (float)：标准差倍数，默认 2.0
    """
    if params is None:
        params = {}
    period = int(params.get("period", 20))
    std_dev = float(params.get("std_dev", 2.0))

    close = df["close"]
    _, upper, lower = compute_bollinger(close, period=period, std_dev=std_dev)

    signals = pd.Series(0, index=df.index, dtype=float)

    # 收盘价触及或跌破下轨 → 买入
    buy_cond = close <= lower
    # 收盘价触及或升穿上轨 → 卖出
    sell_cond = close >= upper

    signals.loc[buy_cond] = 1
    signals.loc[sell_cond] = -1

    return signals


# ---------------------------------------------------------------------------
# 策略注册表 — 方便 main.py 动态调用
# ---------------------------------------------------------------------------
STRATEGY_REGISTRY: dict[str, callable] = {
    "ma_cross": ma_cross,
    "rsi_reversal": rsi_reversal,
    "macd_cross": macd_cross,
    "bollinger_reversion": bollinger_reversion,
}

STRATEGY_DESCRIPTIONS: dict[str, str] = {
    "ma_cross": "双均线交叉",
    "rsi_reversal": "RSI 超卖超买反转",
    "macd_cross": "MACD 金叉死叉",
    "bollinger_reversion": "布林带均值回归",
}

STRATEGY_DEFAULT_PARAMS: dict[str, dict] = {
    "ma_cross": {"short_window": 5, "long_window": 20},
    "rsi_reversal": {"rsi_period": 14, "oversold_threshold": 30, "overbought_threshold": 70},
    "macd_cross": {"fast_period": 12, "slow_period": 26, "signal_period": 9},
    "bollinger_reversion": {"period": 20, "std_dev": 2.0},
}

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

try:
    from .strategies import compute_macd, compute_rsi
except ImportError:
    from strategies import compute_macd, compute_rsi


def predict_etf(symbol: str, df: pd.DataFrame, horizon_days: int = 10) -> dict[str, Any]:
    """Build a short-horizon ETF trend forecast from daily OHLCV data.

    This is a deterministic technical model for demo and monitoring use. It is
    not a price guarantee and should not be treated as investment advice.
    """
    if len(df) < 40:
        return {
            "symbol": symbol,
            "status": "insufficient_data",
            "message": f"{symbol} 有效日 K 只有 {len(df)} 条，ETF 预测至少需要 40 条。",
            "rows": len(df),
        }

    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    volume = df["volume"].astype(float).replace(0, np.nan)

    latest_close = float(close.iloc[-1])
    latest_date = str(df.index[-1].date())

    ret_5 = _period_return(close, 5)
    ret_20 = _period_return(close, 20)
    ret_60 = _period_return(close, 60)
    ma5 = _rolling_last(close, 5)
    ma20 = _rolling_last(close, 20)
    ma60 = _rolling_last(close, 60)
    ma120 = _rolling_last(close, 120)

    macd_line, signal_line, histogram = compute_macd(close)
    macd_hist = _safe_float(histogram.iloc[-1])
    macd_hist_prev = _safe_float(histogram.iloc[-2])
    macd_improving = macd_hist > macd_hist_prev
    macd_score = _clamp(macd_hist / max(latest_close * 0.015, 1e-9), -1.0, 1.0)
    if macd_improving:
        macd_score += 0.15
    macd_score = _clamp(macd_score, -1.0, 1.0)

    rsi = _safe_float(compute_rsi(close, 14).iloc[-1])
    if rsi < 30:
        rsi_score = -0.15
    elif rsi <= 45:
        rsi_score = 0.15
    elif rsi <= 68:
        rsi_score = 0.35
    elif rsi <= 78:
        rsi_score = 0.05
    else:
        rsi_score = -0.35

    trend_score = 0.0
    if latest_close > ma20:
        trend_score += 0.25
    else:
        trend_score -= 0.20
    if ma20 > ma60:
        trend_score += 0.25
    else:
        trend_score -= 0.20
    if len(close) >= 120 and ma60 > ma120:
        trend_score += 0.10

    momentum_score = (
        0.35 * math.tanh(ret_20 * 6)
        + 0.20 * math.tanh(ret_60 * 4)
        + 0.10 * math.tanh(ret_5 * 10)
    )

    vol_20 = _safe_float(close.pct_change().tail(20).std())
    annual_vol = vol_20 * math.sqrt(252)
    max_drawdown_60 = _max_drawdown(close.tail(60))
    volume_ratio = _safe_float(volume.tail(5).mean() / volume.tail(20).mean())
    volume_score = _clamp((volume_ratio - 1.0) * 0.35, -0.15, 0.20)
    risk_penalty = _clamp(annual_vol * 0.55 + abs(max_drawdown_60) * 0.75, 0.0, 0.75)

    raw_score = trend_score + momentum_score + macd_score * 0.25 + rsi_score + volume_score - risk_penalty
    score = _clamp(raw_score, -1.0, 1.0)

    if score >= 0.28:
        direction = "看多"
        action = "买入观察"
    elif score <= -0.25:
        direction = "看空"
        action = "减仓观察"
    else:
        direction = "震荡"
        action = "持有观察"

    confidence = int(round(50 + abs(score) * 42 - min(annual_vol, 0.6) * 15))
    confidence = int(_clamp(confidence, 35, 88))

    expected_return = _clamp(
        0.035 * score
        + 0.20 * ret_20
        + 0.15 * ret_5
        + 0.08 * ret_60
        + 0.02 * macd_score
        - 0.04 * risk_penalty,
        -0.12,
        0.12,
    ) * math.sqrt(max(horizon_days, 1) / 10)
    horizon_vol = max(vol_20, 0.006) * math.sqrt(max(horizon_days, 1))
    expected_low = latest_close * (1 + expected_return - 1.15 * horizon_vol)
    expected_high = latest_close * (1 + expected_return + 1.15 * horizon_vol)

    support = max(_safe_float(low.tail(20).min()), latest_close * 0.90)
    resistance = _safe_float(high.tail(20).max())
    target_price = max(expected_high, resistance) if score > 0 else expected_high
    stop_loss = min(ma20 * 0.975, latest_close * (1 - max(0.035, horizon_vol * 0.65)))

    reasons = _build_reasons(
        latest_close=latest_close,
        ma20=ma20,
        ma60=ma60,
        ret_20=ret_20,
        ret_60=ret_60,
        rsi=rsi,
        macd_hist=macd_hist,
        macd_improving=macd_improving,
        annual_vol=annual_vol,
        max_drawdown_60=max_drawdown_60,
        volume_ratio=volume_ratio,
    )

    return {
        "symbol": symbol,
        "status": "ok",
        "as_of": latest_date,
        "horizon_days": horizon_days,
        "close": round(latest_close, 4),
        "direction": direction,
        "action": action,
        "confidence": confidence,
        "score": round(score, 4),
        "expected_return_pct": round(expected_return * 100, 2),
        "expected_low": round(max(expected_low, 0), 4),
        "expected_high": round(max(expected_high, 0), 4),
        "target_price": round(max(target_price, 0), 4),
        "stop_loss": round(max(stop_loss, 0), 4),
        "support": round(max(support, 0), 4),
        "resistance": round(max(resistance, 0), 4),
        "risk_level": _risk_level(annual_vol, max_drawdown_60),
        "reasons": reasons,
        "indicators": {
            "ret_5_pct": round(ret_5 * 100, 2),
            "ret_20_pct": round(ret_20 * 100, 2),
            "ret_60_pct": round(ret_60 * 100, 2),
            "ma5": round(ma5, 4),
            "ma20": round(ma20, 4),
            "ma60": round(ma60, 4),
            "rsi14": round(rsi, 2),
            "macd": round(_safe_float(macd_line.iloc[-1]), 6),
            "macd_signal": round(_safe_float(signal_line.iloc[-1]), 6),
            "macd_histogram": round(macd_hist, 6),
            "annual_vol_pct": round(annual_vol * 100, 2),
            "max_drawdown_60_pct": round(max_drawdown_60 * 100, 2),
            "volume_ratio_5_20": round(volume_ratio, 3),
        },
    }


def _period_return(close: pd.Series, period: int) -> float:
    if len(close) <= period or float(close.iloc[-period - 1]) <= 0:
        return 0.0
    return float(close.iloc[-1] / close.iloc[-period - 1] - 1)


def _rolling_last(series: pd.Series, window: int) -> float:
    value = series.rolling(window).mean().iloc[-1]
    if pd.isna(value):
        return float(series.tail(min(window, len(series))).mean())
    return float(value)


def _max_drawdown(close: pd.Series) -> float:
    if close.empty:
        return 0.0
    running_max = close.cummax()
    drawdown = close / running_max - 1
    return _safe_float(drawdown.min())


def _risk_level(annual_vol: float, max_drawdown: float) -> str:
    if annual_vol >= 0.35 or max_drawdown <= -0.18:
        return "高"
    if annual_vol >= 0.22 or max_drawdown <= -0.10:
        return "中"
    return "低"


def _build_reasons(**kwargs: float | bool) -> list[str]:
    reasons: list[str] = []
    latest_close = float(kwargs["latest_close"])
    ma20 = float(kwargs["ma20"])
    ma60 = float(kwargs["ma60"])
    ret_20 = float(kwargs["ret_20"])
    ret_60 = float(kwargs["ret_60"])
    rsi = float(kwargs["rsi"])
    macd_hist = float(kwargs["macd_hist"])
    macd_improving = bool(kwargs["macd_improving"])
    annual_vol = float(kwargs["annual_vol"])
    max_drawdown_60 = float(kwargs["max_drawdown_60"])
    volume_ratio = float(kwargs["volume_ratio"])

    if latest_close > ma20 > ma60:
        reasons.append("价格站上 20/60 日均线，趋势结构偏强")
    elif latest_close < ma20:
        reasons.append("价格低于 20 日均线，短线趋势偏弱")
    else:
        reasons.append("价格接近均线区间，趋势仍需确认")

    if ret_20 > 0 and ret_60 > 0:
        reasons.append("20 日与 60 日动量同向为正")
    elif ret_20 < 0 and ret_60 < 0:
        reasons.append("20 日与 60 日动量同向为负")
    else:
        reasons.append("中短期动量分化，适合等待方向确认")

    if macd_hist > 0 and macd_improving:
        reasons.append("MACD 柱体为正且继续改善")
    elif macd_hist < 0 and not macd_improving:
        reasons.append("MACD 柱体为负且动能走弱")

    if 45 <= rsi <= 68:
        reasons.append("RSI 位于健康强势区间")
    elif rsi > 78:
        reasons.append("RSI 偏高，短线有过热风险")
    elif rsi < 35:
        reasons.append("RSI 偏低，仍需观察止跌信号")

    if volume_ratio > 1.15:
        reasons.append("近 5 日成交量高于 20 日均量，资金关注度提升")
    if annual_vol >= 0.35 or max_drawdown_60 <= -0.18:
        reasons.append("近期波动或回撤较大，仓位需要保守")

    return reasons[:6]


def _safe_float(value: Any) -> float:
    try:
        if pd.isna(value) or np.isinf(value):
            return 0.0
        return float(value)
    except Exception:
        return 0.0


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))

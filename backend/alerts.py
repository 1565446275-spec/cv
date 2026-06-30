from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

try:
    from .strategies import compute_macd
except ImportError:
    from strategies import compute_macd


def scan_technical_alerts(
    symbol: str,
    df: pd.DataFrame,
    ma20_tolerance: float = 0.015,
) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []
    if len(df) < 30:
        return [{
            "symbol": symbol,
            "strategy": "data_check",
            "action": "observe",
            "level": "warning",
            "message": f"{symbol} 有效日 K 只有 {len(df)} 条，少于 30 条，暂不生成技术信号。",
            "details": {"rows": len(df)},
        }]

    close = df["close"].astype(float)
    latest_date = str(df.index[-1].date())
    latest_close = float(close.iloc[-1])

    alerts.extend(_macd_zero_axis_alert(symbol, df, latest_date))
    alerts.extend(_ma20_pullback_alert(symbol, df, latest_date, ma20_tolerance))

    if not alerts:
        alerts.append({
            "symbol": symbol,
            "strategy": "technical_summary",
            "action": "hold",
            "level": "info",
            "message": f"{symbol} 暂未触发 MACD 零轴下金叉或 20 日线回踩信号。",
            "details": {"date": latest_date, "close": round(latest_close, 4)},
        })

    return alerts


def _macd_zero_axis_alert(symbol: str, df: pd.DataFrame, latest_date: str) -> list[dict[str, Any]]:
    close = df["close"].astype(float)
    macd_line, signal_line, histogram = compute_macd(close)
    if len(macd_line.dropna()) < 2:
        return []

    prev_macd = float(macd_line.iloc[-2])
    prev_signal = float(signal_line.iloc[-2])
    curr_macd = float(macd_line.iloc[-1])
    curr_signal = float(signal_line.iloc[-1])
    curr_hist = float(histogram.iloc[-1])
    price = float(close.iloc[-1])

    golden_under_zero = (
        prev_macd <= prev_signal
        and curr_macd > curr_signal
        and curr_macd < 0
        and curr_signal < 0
    )
    death_cross = prev_macd >= prev_signal and curr_macd < curr_signal

    if golden_under_zero:
        return [{
            "symbol": symbol,
            "strategy": "macd_zero_golden",
            "action": "buy",
            "level": "strong",
            "message": f"{symbol} 触发零轴下方 MACD 金叉，属于低位动能修复买入观察信号。",
            "details": {
                "date": latest_date,
                "close": round(price, 4),
                "macd": round(curr_macd, 6),
                "signal": round(curr_signal, 6),
                "histogram": round(curr_hist, 6),
            },
        }]

    if death_cross:
        return [{
            "symbol": symbol,
            "strategy": "macd_dead_cross",
            "action": "sell",
            "level": "warning",
            "message": f"{symbol} 触发 MACD 死叉，动能转弱，建议卖出或降低仓位观察。",
            "details": {
                "date": latest_date,
                "close": round(price, 4),
                "macd": round(curr_macd, 6),
                "signal": round(curr_signal, 6),
                "histogram": round(curr_hist, 6),
            },
        }]

    return []


def _ma20_pullback_alert(
    symbol: str,
    df: pd.DataFrame,
    latest_date: str,
    tolerance: float,
) -> list[dict[str, Any]]:
    close = df["close"].astype(float)
    low = df["low"].astype(float)
    ma20 = close.rolling(20).mean()
    if pd.isna(ma20.iloc[-1]):
        return []

    curr_close = float(close.iloc[-1])
    curr_low = float(low.iloc[-1])
    curr_ma20 = float(ma20.iloc[-1])
    prev_close = float(close.iloc[-2])
    prev_ma20 = float(ma20.iloc[-2]) if not pd.isna(ma20.iloc[-2]) else curr_ma20
    ma20_rising = curr_ma20 >= prev_ma20

    touched_ma20 = curr_low <= curr_ma20 * (1 + tolerance)
    reclaimed_ma20 = curr_close >= curr_ma20 * (1 - tolerance)
    prior_above = prev_close >= prev_ma20
    broke_down = curr_close < curr_ma20 * (1 - max(tolerance, 0.02))

    if touched_ma20 and reclaimed_ma20 and prior_above and ma20_rising:
        return [{
            "symbol": symbol,
            "strategy": "ma20_pullback",
            "action": "buy",
            "level": "medium",
            "message": f"{symbol} 回踩 20 日均线后收回，趋势仍在，触发买入观察信号。",
            "details": {
                "date": latest_date,
                "close": round(curr_close, 4),
                "low": round(curr_low, 4),
                "ma20": round(curr_ma20, 4),
                "distance_pct": round((curr_close / curr_ma20 - 1) * 100, 3),
            },
        }]

    if broke_down:
        return [{
            "symbol": symbol,
            "strategy": "ma20_breakdown",
            "action": "sell",
            "level": "warning",
            "message": f"{symbol} 收盘跌破 20 日均线，趋势保护失效，触发卖出/减仓提醒。",
            "details": {
                "date": latest_date,
                "close": round(curr_close, 4),
                "ma20": round(curr_ma20, 4),
                "distance_pct": round((curr_close / curr_ma20 - 1) * 100, 3),
            },
        }]

    return []


def scan_multifactor(
    data_map: dict[str, pd.DataFrame],
    weights: dict[str, float] | None = None,
    top_n: int = 5,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if weights is None:
        weights = {"alpha013": 0.3, "alpha015": 0.4, "quality": 0.3}

    rows: list[dict[str, Any]] = []
    for symbol, df in data_map.items():
        if len(df) < 60:
            continue
        factor_row = _compute_factor_row(symbol, df)
        if factor_row:
            rows.append(factor_row)

    if not rows:
        return [], []

    factor_df = pd.DataFrame(rows).set_index("symbol")
    factor_df["alpha013_score"] = _rank_score(factor_df["alpha013"], ascending=True)
    factor_df["alpha015_score"] = _rank_score(factor_df["alpha015"], ascending=True)
    factor_df["quality_score"] = _rank_score(factor_df["quality"], ascending=False)

    total_weight = sum(abs(v) for v in weights.values()) or 1.0
    factor_df["score"] = (
        weights.get("alpha013", 0.3) * factor_df["alpha013_score"]
        + weights.get("alpha015", 0.4) * factor_df["alpha015_score"]
        + weights.get("quality", 0.3) * factor_df["quality_score"]
    ) / total_weight
    factor_df = factor_df.sort_values("score", ascending=False)

    ranking = []
    for rank, (symbol, row) in enumerate(factor_df.iterrows(), 1):
        ranking.append({
            "rank": rank,
            "symbol": symbol,
            "score": round(float(row["score"]), 4),
            "alpha013": round(float(row["alpha013"]), 6),
            "alpha015": round(float(row["alpha015"]), 6),
            "quality": round(float(row["quality"]), 6),
            "close": round(float(row["close"]), 4),
        })

    alerts: list[dict[str, Any]] = []
    buy_count = max(1, min(top_n, len(ranking)))
    sell_threshold = max(buy_count + 1, math.ceil(len(ranking) * 0.8))

    for item in ranking[:buy_count]:
        alerts.append({
            "symbol": item["symbol"],
            "strategy": "multifactor_rank",
            "action": "buy",
            "level": "medium",
            "message": f"{item['symbol']} 多因子综合排名第 {item['rank']}，进入买入候选池。",
            "details": item,
        })

    for item in ranking[sell_threshold - 1:]:
        alerts.append({
            "symbol": item["symbol"],
            "strategy": "multifactor_rank",
            "action": "sell",
            "level": "info",
            "message": f"{item['symbol']} 多因子综合排名靠后，建议从自选池中剔除或降低关注。",
            "details": item,
        })

    return alerts, ranking


def _compute_factor_row(symbol: str, df: pd.DataFrame) -> dict[str, Any] | None:
    latest = df.iloc[-1]
    prev = df.iloc[-2]
    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)

    latest_close = float(latest["close"])
    if latest_close <= 0 or float(prev["close"]) <= 0:
        return None

    geometric_mid = math.sqrt(max(float(latest["high"]) * float(latest["low"]), 0))
    vwap_proxy = float((latest["high"] + latest["low"] + latest["close"]) / 3)
    alpha013 = (geometric_mid - vwap_proxy) / latest_close
    alpha015 = float(latest["open"] / prev["close"] - 1)

    ret_20 = latest_close / float(close.iloc[-21]) - 1 if len(close) > 21 else 0.0
    volatility_20 = float(close.pct_change().tail(20).std() or 0.0)
    ma60 = float(close.rolling(60).mean().iloc[-1])
    trend_quality = latest_close / ma60 - 1 if ma60 > 0 else 0.0
    range_efficiency = _safe_float((close.iloc[-1] - close.iloc[-20]) / (high.tail(20).max() - low.tail(20).min()))
    quality = 0.45 * ret_20 - 0.25 * volatility_20 + 0.2 * trend_quality + 0.1 * range_efficiency

    return {
        "symbol": symbol,
        "close": latest_close,
        "alpha013": alpha013,
        "alpha015": alpha015,
        "quality": quality,
    }


def _rank_score(series: pd.Series, ascending: bool) -> pd.Series:
    if len(series) <= 1:
        return pd.Series(1.0, index=series.index)
    rank = series.rank(method="average", ascending=ascending)
    return 1 - (rank - 1) / (len(series) - 1)


def _safe_float(value: Any) -> float:
    try:
        if pd.isna(value) or np.isinf(value):
            return 0.0
        return float(value)
    except Exception:
        return 0.0

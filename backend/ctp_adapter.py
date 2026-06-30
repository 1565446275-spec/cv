from __future__ import annotations

import hashlib
import math
import os
from datetime import datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd


_FUTURE_META: dict[str, dict[str, Any]] = {
    "rb": {"name": "螺纹钢", "exchange": "SHFE", "multiplier": 10, "tick": 1.0},
    "au": {"name": "黄金", "exchange": "SHFE", "multiplier": 1000, "tick": 0.02},
    "ag": {"name": "白银", "exchange": "SHFE", "multiplier": 15, "tick": 1.0},
    "cu": {"name": "铜", "exchange": "SHFE", "multiplier": 5, "tick": 10.0},
    "IF": {"name": "沪深300股指", "exchange": "CFFEX", "multiplier": 300, "tick": 0.2},
    "IH": {"name": "上证50股指", "exchange": "CFFEX", "multiplier": 300, "tick": 0.2},
    "IC": {"name": "中证500股指", "exchange": "CFFEX", "multiplier": 200, "tick": 0.2},
    "IM": {"name": "中证1000股指", "exchange": "CFFEX", "multiplier": 200, "tick": 0.2},
    "m": {"name": "豆粕", "exchange": "DCE", "multiplier": 10, "tick": 1.0},
    "i": {"name": "铁矿石", "exchange": "DCE", "multiplier": 100, "tick": 0.5},
    "y": {"name": "豆油", "exchange": "DCE", "multiplier": 10, "tick": 2.0},
    "SR": {"name": "白糖", "exchange": "CZCE", "multiplier": 10, "tick": 1.0},
    "TA": {"name": "PTA", "exchange": "CZCE", "multiplier": 5, "tick": 2.0},
    "MA": {"name": "甲醇", "exchange": "CZCE", "multiplier": 10, "tick": 1.0},
}


def get_ctp_status() -> dict[str, Any]:
    """Return CTP adapter readiness without leaking credentials."""
    import_ready = False
    import_error = ""
    try:
        import XM_CTP  # type: ignore  # noqa: F401

        import_ready = True
    except Exception as exc:
        import_error = str(exc)

    required_env = [
        "CTP_FRONT_MD",
        "CTP_FRONT_TD",
        "CTP_BROKER_ID",
        "CTP_USER_ID",
        "CTP_PASSWORD",
    ]
    optional_env = ["CTP_APP_ID", "CTP_AUTH_CODE"]
    missing = [key for key in required_env if not os.getenv(key)]
    configured = not missing
    mode = "live_ready" if import_ready and configured else "simulation"

    return {
        "mode": mode,
        "adapter": "ctp_python / XM_CTP compatible",
        "source": "https://gitee.com/sea_trade/ctp_python",
        "python_module_ready": import_ready,
        "config_ready": configured,
        "missing_env": missing,
        "optional_missing_env": [key for key in optional_env if not os.getenv(key)],
        "import_error": "" if import_ready else import_error,
        "message": (
            "CTP Python 模块与柜台配置已就绪，可接入真实行情/交易。"
            if mode == "live_ready"
            else "当前使用仿真量化扫描；安装并配置 CTP 后可切换真实接口。"
        ),
    }


def scan_derivatives(
    symbols: list[str],
    strategy: str = "trend_breakout",
    capital: float = 500000.0,
    risk_per_trade: float = 0.01,
    days: int = 120,
) -> dict[str, Any]:
    scans: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    for raw_symbol in symbols:
        symbol = raw_symbol.strip()
        if not symbol:
            continue
        try:
            parsed = parse_contract(symbol)
            bars = _simulate_contract_bars(symbol, max(days, 80), parsed)
            item = _scan_one(symbol, parsed, bars, strategy, capital, risk_per_trade)
            scans.append(item)
        except Exception as exc:
            errors.append({"symbol": symbol, "error": f"扫描失败: {exc}"})

    priority = {"long": 0, "short": 1, "hold": 2, "avoid": 3}
    scans.sort(key=lambda row: (priority.get(row.get("signal"), 9), -abs(row.get("score", 0))))

    return {
        "mode": get_ctp_status()["mode"],
        "strategy": strategy,
        "capital": capital,
        "risk_per_trade": risk_per_trade,
        "count": len(scans),
        "contracts": scans,
        "errors": errors,
        "disclaimer": "期货期权杠杆较高，扫描结果仅用于项目展示和策略研究，不构成交易建议。",
    }


def parse_contract(symbol: str) -> dict[str, Any]:
    s = symbol.strip()
    parts = s.replace("_", "-").split("-")
    option_type = None
    strike = None
    future_symbol = parts[0]
    if len(parts) >= 3 and parts[1].upper() in {"C", "P", "CALL", "PUT"}:
        option_type = "call" if parts[1].upper() in {"C", "CALL"} else "put"
        strike = float(parts[2])

    product = "".join(ch for ch in future_symbol if ch.isalpha())
    month = "".join(ch for ch in future_symbol if ch.isdigit())
    if not product or not month:
        raise ValueError("合约格式应类似 rb2605、IF2606、m2609-C-3200")

    product_key = product if product in _FUTURE_META else product.upper()
    meta = _FUTURE_META.get(product_key) or _FUTURE_META.get(product.lower())
    if not meta:
        meta = {"name": product.upper(), "exchange": "UNKNOWN", "multiplier": 10, "tick": 1.0}

    expiry = _estimate_expiry(month)
    return {
        "symbol": s,
        "underlying": future_symbol,
        "product": product_key,
        "month": month,
        "asset_type": "option" if option_type else "future",
        "option_type": option_type,
        "strike": strike,
        "expiry": expiry.strftime("%Y-%m-%d"),
        "days_to_expiry": max((expiry.date() - datetime.now().date()).days, 1),
        **meta,
    }


def _scan_one(
    symbol: str,
    parsed: dict[str, Any],
    df: pd.DataFrame,
    strategy: str,
    capital: float,
    risk_per_trade: float,
) -> dict[str, Any]:
    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    last = float(close.iloc[-1])
    ma20 = float(close.rolling(20).mean().iloc[-1])
    ma60 = float(close.rolling(60).mean().iloc[-1])
    ret20 = float(close.iloc[-1] / close.iloc[-21] - 1)
    vol20 = float(close.pct_change().tail(20).std() * math.sqrt(252))
    atr14 = _atr(high, low, close, 14)
    breakout_high = float(high.tail(20).max())
    breakout_low = float(low.tail(20).min())
    channel_pos = (last - breakout_low) / max(breakout_high - breakout_low, 1e-9)

    trend_score = 0.0
    trend_score += 0.35 if last > ma20 else -0.25
    trend_score += 0.30 if ma20 > ma60 else -0.25
    trend_score += 0.25 * math.tanh(ret20 * 8)
    trend_score += 0.20 if channel_pos > 0.72 else -0.20 if channel_pos < 0.28 else 0.0

    if strategy == "mean_reversion":
        zscore = (last - ma20) / max(float(close.tail(20).std()), 1e-9)
        score = -math.tanh(zscore / 1.7)
    elif strategy == "vol_breakout":
        score = trend_score + (0.18 if vol20 > 0.22 and channel_pos > 0.65 else 0)
    else:
        score = trend_score
    score = _clamp(score, -1.0, 1.0)

    if score >= 0.28:
        signal = "long"
        action = "开多观察"
        stop = last - max(2.2 * atr14, last * 0.012)
        target = last + max(3.0 * atr14, last * 0.02)
    elif score <= -0.28:
        signal = "short"
        action = "开空观察"
        stop = last + max(2.2 * atr14, last * 0.012)
        target = last - max(3.0 * atr14, last * 0.02)
    else:
        signal = "hold"
        action = "等待观察"
        stop = last - max(2.0 * atr14, last * 0.01)
        target = last + max(2.0 * atr14, last * 0.01)

    multiplier = float(parsed.get("multiplier", 10))
    contract_value = last * multiplier
    margin_rate = 0.12 if parsed["asset_type"] == "future" else 1.0
    margin_per_lot = contract_value * margin_rate
    risk_budget = capital * risk_per_trade
    per_lot_risk = abs(last - stop) * multiplier
    suggested_lots = int(max(0, min(capital / max(margin_per_lot, 1), risk_budget / max(per_lot_risk, 1))))
    suggested_lots = min(suggested_lots, 20)

    risk_checks = _risk_checks(capital, risk_per_trade, margin_per_lot, per_lot_risk, suggested_lots, vol20)
    result = {
        "symbol": symbol,
        "asset_type": parsed["asset_type"],
        "name": parsed["name"],
        "exchange": parsed["exchange"],
        "underlying": parsed["underlying"],
        "last": round(last, 4),
        "signal": signal,
        "action": action,
        "score": round(score, 4),
        "confidence": int(_clamp(52 + abs(score) * 38 - min(vol20, 0.6) * 18, 35, 88)),
        "ma20": round(ma20, 4),
        "ma60": round(ma60, 4),
        "ret20_pct": round(ret20 * 100, 2),
        "annual_vol_pct": round(vol20 * 100, 2),
        "atr14": round(atr14, 4),
        "target": round(max(target, 0), 4),
        "stop": round(max(stop, 0), 4),
        "margin_per_lot": round(margin_per_lot, 2),
        "per_lot_risk": round(per_lot_risk, 2),
        "suggested_lots": suggested_lots,
        "risk_checks": risk_checks,
        "reasons": _derivative_reasons(last, ma20, ma60, ret20, channel_pos, vol20, signal),
    }

    if parsed["asset_type"] == "option":
        result["option"] = _option_metrics(parsed, last, vol20)

    return result


def _option_metrics(parsed: dict[str, Any], underlying_price: float, annual_vol: float) -> dict[str, Any]:
    strike = float(parsed["strike"])
    days = max(int(parsed["days_to_expiry"]), 1)
    t = days / 365
    sigma = max(annual_vol, 0.08)
    r = 0.02
    call = parsed["option_type"] == "call"
    d1 = (math.log(max(underlying_price, 1e-9) / strike) + (r + 0.5 * sigma**2) * t) / (sigma * math.sqrt(t))
    d2 = d1 - sigma * math.sqrt(t)
    price = (
        underlying_price * _norm_cdf(d1) - strike * math.exp(-r * t) * _norm_cdf(d2)
        if call
        else strike * math.exp(-r * t) * _norm_cdf(-d2) - underlying_price * _norm_cdf(-d1)
    )
    delta = _norm_cdf(d1) if call else _norm_cdf(d1) - 1
    gamma = _norm_pdf(d1) / max(underlying_price * sigma * math.sqrt(t), 1e-9)
    theta = -underlying_price * _norm_pdf(d1) * sigma / (2 * math.sqrt(t)) / 365
    return {
        "type": "看涨" if call else "看跌",
        "strike": strike,
        "days_to_expiry": days,
        "theoretical_price": round(max(price, 0), 4),
        "delta": round(delta, 4),
        "gamma": round(gamma, 6),
        "theta_per_day": round(theta, 4),
        "implied_vol_used_pct": round(sigma * 100, 2),
    }


def _risk_checks(
    capital: float,
    risk_per_trade: float,
    margin_per_lot: float,
    per_lot_risk: float,
    lots: int,
    vol20: float,
) -> list[dict[str, Any]]:
    checks = []
    margin_usage = margin_per_lot * lots / max(capital, 1)
    trade_risk = per_lot_risk * lots / max(capital, 1)
    checks.append(_check("单笔风险", trade_risk <= risk_per_trade * 1.05, f"{trade_risk * 100:.2f}% / 上限 {risk_per_trade * 100:.2f}%"))
    checks.append(_check("保证金占用", margin_usage <= 0.35, f"{margin_usage * 100:.2f}% / 上限 35.00%"))
    checks.append(_check("波动率过滤", vol20 <= 0.55, f"年化波动 {vol20 * 100:.2f}%"))
    checks.append(_check("手数限制", lots <= 20, f"建议 {lots} 手 / 上限 20 手"))
    return checks


def _check(name: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"name": name, "passed": passed, "detail": detail}


def _derivative_reasons(last: float, ma20: float, ma60: float, ret20: float, channel_pos: float, vol20: float, signal: str) -> list[str]:
    reasons = []
    if last > ma20 > ma60:
        reasons.append("价格位于 20/60 日均线上方，趋势结构偏多")
    elif last < ma20 < ma60:
        reasons.append("价格位于 20/60 日均线下方，趋势结构偏空")
    else:
        reasons.append("均线结构分化，趋势确认度一般")
    if ret20 > 0.03:
        reasons.append("20 日动量较强，顺势策略占优")
    elif ret20 < -0.03:
        reasons.append("20 日动量偏弱，空头或防守信号增强")
    if channel_pos > 0.72:
        reasons.append("价格处于 20 日通道上沿，存在突破观察价值")
    elif channel_pos < 0.28:
        reasons.append("价格处于 20 日通道下沿，需关注下破或反弹")
    if vol20 > 0.35:
        reasons.append("近期波动率较高，建议降低合约手数")
    if signal == "hold":
        reasons.append("综合分数未达开仓阈值，等待更明确触发条件")
    return reasons[:5]


def _simulate_contract_bars(symbol: str, days: int, parsed: dict[str, Any]) -> pd.DataFrame:
    seed = int(hashlib.sha256(symbol.encode("utf-8")).hexdigest()[:8], 16)
    rng = np.random.default_rng(seed)
    base = _base_price(parsed)
    drift = ((seed % 17) - 8) / 10000
    vol = 0.012 + (seed % 9) / 1000
    returns = rng.normal(drift, vol, days)
    close = base * np.exp(np.cumsum(returns))
    high = close * (1 + rng.uniform(0.001, 0.012, days))
    low = close * (1 - rng.uniform(0.001, 0.012, days))
    open_ = np.r_[base, close[:-1]] * (1 + rng.normal(0, 0.003, days))
    volume = rng.integers(12000, 120000, days)
    dates = pd.bdate_range(end=datetime.now().date(), periods=days)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=dates,
    )


def _base_price(parsed: dict[str, Any]) -> float:
    product = parsed["product"]
    defaults = {
        "rb": 3500,
        "au": 560,
        "ag": 7600,
        "cu": 78000,
        "IF": 3900,
        "IH": 2700,
        "IC": 5800,
        "IM": 6200,
        "m": 3200,
        "i": 820,
        "y": 7800,
        "SR": 6200,
        "TA": 5200,
        "MA": 2600,
    }
    return float(defaults.get(product, defaults.get(str(product).lower(), 3000)))


def _estimate_expiry(month: str) -> datetime:
    now = datetime.now()
    if len(month) >= 4:
        year = 2000 + int(month[:2])
        mon = int(month[2:4])
    elif len(month) >= 3:
        year = now.year
        mon = int(month[-2:])
    else:
        year = now.year
        mon = now.month
    mon = min(max(mon, 1), 12)
    return datetime(year, mon, 15) + timedelta(days=7)


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> float:
    prev_close = close.shift(1)
    tr = pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return float(tr.rolling(period).mean().iloc[-1])


def _norm_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))

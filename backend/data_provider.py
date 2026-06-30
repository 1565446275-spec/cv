"""
data_provider.py — 金融数据获取层

优先使用 OpenBB SDK 获取历史行情数据，
如果 OpenBB 不可用或请求失败，自动 fallback 到 yfinance。

输出统一为 pandas DataFrame，列名：
    open, high, low, close, volume
index 为 datetime 类型。

使用 OpenBB 源码相关说明：
本文件仅通过 pip 安装的 openbb 包调用其公开 API，
未复制或修改 OpenBB 内部源码。
OpenBB 基于 AGPLv3 许可证发布，详情见：
https://github.com/OpenBB-finance/OpenBB/blob/main/LICENSE
"""

from __future__ import annotations

import logging
import json
from datetime import datetime
from typing import Optional
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# OpenBB 导入（可能因环境不支持而失败）
# ---------------------------------------------------------------------------
_HAVE_OPENBB = False
try:
    from openbb import obb  # type: ignore[import-untyped]

    _HAVE_OPENBB = True
    logger.info("OpenBB SDK loaded successfully.")
except ImportError:
    logger.warning("OpenBB SDK not installed; will fall back to yfinance.")
except Exception as exc:
    logger.warning("OpenBB SDK import error (%s); will fall back to yfinance.", exc)


def _fetch_openbb(
    symbol: str,
    start: str,
    end: str,
) -> Optional[pd.DataFrame]:
    """尝试用 OpenBB SDK 获取行情数据。"""
    if not _HAVE_OPENBB:
        return None

    try:
        # OpenBB v4+ API: obb.equity.price.historical(...)
        data = obb.equity.price.historical(
            symbol=symbol,
            start_date=start,
            end_date=end,
        )
        # data 是 OBBject 对象，调用 .to_dataframe() 转为 DataFrame
        df: pd.DataFrame = data.to_dataframe()
        if df is None or df.empty:
            logger.warning("OpenBB returned empty DataFrame for %s", symbol)
            return None

        # 统一列名
        df = df.rename(
            columns={
                "open": "open",
                "high": "high",
                "low": "low",
                "close": "close",
                "volume": "volume",
            }
        )
        # 确保必要列存在
        required = {"open", "high", "low", "close", "volume"}
        if not required.issubset(df.columns):
            logger.warning(
                "OpenBB response missing columns %s for %s",
                required - set(df.columns),
                symbol,
            )
            return None

        # 确保 index 为 datetime
        if not isinstance(df.index, pd.DatetimeIndex):
            df.index = pd.to_datetime(df.index)

        df = df.sort_index()
        logger.info(
            "OpenBB fetched %d rows for %s [%s → %s]",
            len(df),
            symbol,
            start,
            end,
        )
        return df[["open", "high", "low", "close", "volume"]]

    except Exception as exc:
        logger.warning("OpenBB fetch failed for %s: %s", symbol, exc)
        return None


def _to_tencent_symbol(symbol: str) -> Optional[str]:
    """Convert common ticker formats to Tencent Finance market symbols."""
    s = symbol.strip().lower()
    if s.startswith(("sh", "sz", "hk", "us")):
        return s

    if s.endswith(".ss") or s.endswith(".sh"):
        return f"sh{s.split('.')[0]}"
    if s.endswith(".sz"):
        return f"sz{s.split('.')[0]}"
    if s.endswith(".hk"):
        code = s.split(".")[0].zfill(5)
        return f"hk{code}"
    if s.isdigit() and len(s) == 5:
        return f"hk{s}"
    if s.isdigit() and len(s) == 6:
        return f"sh{s}" if s.startswith(("5", "6", "9")) else f"sz{s}"

    # Tencent's public endpoint often returns only quotes for US symbols, not
    # historical K-lines. Keep this mapping for future compatibility.
    if s.isalpha():
        return f"us{s.upper()}"

    return None


def _extract_six_digit_code(symbol: str) -> Optional[str]:
    s = symbol.strip().lower()
    if s.startswith(("sh", "sz")) and len(s) >= 8 and s[2:8].isdigit():
        return s[2:8]
    if (s.endswith(".ss") or s.endswith(".sh") or s.endswith(".sz")) and s.split(".")[0].isdigit():
        code = s.split(".")[0]
        return code if len(code) == 6 else None
    if s.isdigit() and len(s) == 6:
        return s
    return None


def _is_shenzhen_symbol(symbol: str) -> bool:
    """Return True for common Shenzhen stock/ETF code formats."""
    s = symbol.strip().lower()
    code = _extract_six_digit_code(symbol)
    if not code:
        return False
    if s.startswith("sz") or s.endswith(".sz"):
        return True
    return not code.startswith(("5", "6", "9"))


def _fetch_szse_realtime(symbol: str) -> Optional[pd.DataFrame]:
    """Fetch one-row realtime quote from the official SZSE endpoint.

    SZSE does not expose a stable public daily K-line endpoint for this use
    case, so this is used only as a final fallback/enrichment source when
    historical providers cannot return data.
    """
    if not _is_shenzhen_symbol(symbol):
        return None

    code = _extract_six_digit_code(symbol)
    if not code:
        return None

    try:
        query = urlencode({"marketId": 1, "code": code})
        url = f"https://www.szse.cn/api/market/ssjjhq/getTimeData?{query}"
        req = Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://www.szse.cn/market/trend/index.html",
                "Accept": "application/json,text/plain,*/*",
            },
        )
        with urlopen(req, timeout=20) as resp:
            payload = json.loads(resp.read().decode("utf-8"))

        data = payload.get("data") or {}
        if not data:
            logger.warning("SZSE returned empty realtime quote for %s", symbol)
            return None

        def as_float(key: str) -> float:
            try:
                value = data.get(key)
                if value in (None, "", "--"):
                    return float("nan")
                return float(str(value).replace(",", ""))
            except Exception:
                return float("nan")

        now_price = as_float("now")
        close_price = now_price if pd.notna(now_price) and now_price > 0 else as_float("close")
        open_price = as_float("open")
        high_price = as_float("high")
        low_price = as_float("low")
        volume = as_float("volume")

        if pd.isna(close_price) or close_price <= 0:
            return None

        if pd.isna(open_price) or open_price <= 0:
            open_price = close_price
        if pd.isna(high_price) or high_price <= 0:
            high_price = max(open_price, close_price)
        if pd.isna(low_price) or low_price <= 0:
            low_price = min(open_price, close_price)
        if pd.isna(volume) or volume < 0:
            volume = 0.0

        market_time = data.get("marketTime") or data.get("time")
        idx = pd.to_datetime(market_time, errors="coerce")
        if pd.isna(idx):
            idx = pd.Timestamp(datetime.now().date())
        else:
            idx = pd.Timestamp(idx).tz_localize(None) if getattr(idx, "tzinfo", None) else pd.Timestamp(idx)

        df = pd.DataFrame(
            [{
                "open": open_price,
                "high": high_price,
                "low": low_price,
                "close": close_price,
                "volume": volume,
            }],
            index=pd.DatetimeIndex([idx.normalize()]),
        )
        logger.info("SZSE realtime fetched quote for %s as %s", symbol, code)
        return df[["open", "high", "low", "close", "volume"]]

    except Exception as exc:
        logger.warning("SZSE realtime fetch failed for %s: %s", symbol, exc)
        return None


def _fetch_tencent(
    symbol: str,
    start: str,
    end: str,
) -> Optional[pd.DataFrame]:
    """Fetch daily K-line data from Tencent Finance."""
    tencent_symbol = _to_tencent_symbol(symbol)
    if not tencent_symbol:
        return None

    try:
        query = urlencode({
            "param": f"{tencent_symbol},day,{start},{end},640,qfq",
        })
        url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?{query}"
        req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(req, timeout=25) as resp:
            payload = json.loads(resp.read().decode("utf-8"))

        item = payload.get("data", {}).get(tencent_symbol)
        if not item:
            logger.warning("Tencent returned no data node for %s", symbol)
            return None

        rows = item.get("qfqday") or item.get("day") or []
        if not rows:
            logger.warning("Tencent returned empty K-line rows for %s", symbol)
            return None

        rows = [row[:6] for row in rows if len(row) >= 6]
        df = pd.DataFrame(
            rows,
            columns=["date", "open", "close", "high", "low", "volume"],
        )
        df["date"] = pd.to_datetime(df["date"])
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna(subset=["open", "high", "low", "close"])
        df = df.set_index("date").sort_index()

        logger.info(
            "Tencent fetched %d rows for %s as %s [%s -> %s]",
            len(df),
            symbol,
            tencent_symbol,
            start,
            end,
        )
        return df[["open", "high", "low", "close", "volume"]]

    except Exception as exc:
        logger.warning("Tencent fetch failed for %s: %s", symbol, exc)
        return None


def _fetch_yfinance(
    symbol: str,
    start: str,
    end: str,
) -> Optional[pd.DataFrame]:
    """用 yfinance 获取行情数据（fallback）。"""
    try:
        import yfinance as yf  # type: ignore[import-untyped]

        ticker = yf.Ticker(symbol)
        df = ticker.history(start=start, end=end)

        if df is None or df.empty:
            logger.warning("yfinance returned empty DataFrame for %s", symbol)
            return None

        # yfinance 列名首字母大写 → 统一小写
        df.columns = [c.lower() for c in df.columns]

        required = {"open", "high", "low", "close", "volume"}
        if not required.issubset(df.columns):
            logger.warning(
                "yfinance response missing columns %s for %s",
                required - set(df.columns),
                symbol,
            )
            return None

        if not isinstance(df.index, pd.DatetimeIndex):
            df.index = pd.to_datetime(df.index)

        # 移除时区信息保持一致性
        if df.index.tz is not None:
            df.index = df.index.tz_localize(None)

        df = df.sort_index()
        logger.info(
            "yfinance fetched %d rows for %s [%s → %s]",
            len(df),
            symbol,
            start,
            end,
        )
        return df[["open", "high", "low", "close", "volume"]]

    except ImportError:
        logger.error("yfinance not installed — no data source available.")
        return None
    except Exception as exc:
        logger.warning("yfinance fetch failed for %s: %s", symbol, exc)
        return None


def _fetch_stooq(
    symbol: str,
    start: str,
    end: str,
) -> Optional[pd.DataFrame]:
    """Use Stooq as a lightweight HTTP fallback when Yahoo is rate-limited."""
    try:
        stooq_symbol = symbol.lower()
        if "." not in stooq_symbol:
            stooq_symbol = f"{stooq_symbol}.us"

        query = urlencode({
            "s": stooq_symbol,
            "d1": start.replace("-", ""),
            "d2": end.replace("-", ""),
            "i": "d",
        })
        url = f"https://stooq.com/q/d/l/?{query}"
        df = pd.read_csv(url)

        if df is None or df.empty or "Date" not in df.columns:
            logger.warning("Stooq returned empty DataFrame for %s", symbol)
            return None

        df = df.rename(columns={
            "Date": "date",
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Volume": "volume",
        })
        required = {"date", "open", "high", "low", "close", "volume"}
        if not required.issubset(df.columns):
            logger.warning(
                "Stooq response missing columns %s for %s",
                required - set(df.columns),
                symbol,
            )
            return None

        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date").sort_index()
        logger.info(
            "Stooq fetched %d rows for %s [%s -> %s]",
            len(df),
            symbol,
            start,
            end,
        )
        return df[["open", "high", "low", "close", "volume"]]

    except Exception as exc:
        logger.warning("Stooq fetch failed for %s: %s", symbol, exc)
        return None


def fetch_historical_data(
    symbol: str,
    start: str,
    end: str,
) -> pd.DataFrame:
    """
    获取历史 OHLCV 行情数据。

    优先级：OpenBB SDK → yfinance

    Parameters
    ----------
    symbol : str
        股票代码，如 "AAPL"、"TSLA"、"600519.SS"。
    start : str
        起始日期，格式 "YYYY-MM-DD"。
    end : str
        结束日期，格式 "YYYY-MM-DD"。

    Returns
    -------
    pd.DataFrame
        列：open, high, low, close, volume；index 为 DatetimeIndex。

    Raises
    ------
    RuntimeError
        所有数据源均无法获取数据时抛出。
    """
    # 1) A 股/港股优先使用腾讯日 K。深交所 6 位 ETF/股票也走这里。
    df = _fetch_tencent(symbol, start, end)
    if df is not None and not df.empty:
        return df

    # 2) OpenBB / yfinance / Stooq 作为海外资产和兜底数据源。
    df = _fetch_openbb(symbol, start, end)
    if df is not None and not df.empty:
        return df

    df = _fetch_yfinance(symbol, start, end)
    if df is None or df.empty:
        df = _fetch_stooq(symbol, start, end)
    if df is not None and not df.empty:
        return df

    # 3) 深交所官方实时行情兜底。它只有最新报价，不能替代历史回测日 K。
    df = _fetch_szse_realtime(symbol)
    if df is not None and not df.empty:
        return df

    # 4) 全部失败 → 清晰报错
    raise RuntimeError(_build_data_error(symbol, start, end))


def _build_data_error(symbol: str, start: str, end: str) -> str:
    s = symbol.strip().upper()
    if s.isdigit() and len(s) == 5:
        return (
            f"无法获取 {symbol} 在 [{start} → {end}] 区间的行情数据。"
            f"系统会把 5 位数字代码按港股处理；如果你要查深交所股票/ETF，"
            f"请使用 6 位代码，例如 000001、300497、159915。"
            f"如果 {symbol} 确认为港股权证或衍生品，可能该时间段没有可用日 K，无法回测。"
        )
    if _is_shenzhen_symbol(symbol):
        return (
            f"无法获取深交所代码 {symbol} 在 [{start} → {end}] 区间的历史日 K。"
            f"已尝试腾讯财经、OpenBB、yfinance、Stooq 和深交所官方实时行情。"
            f"请确认代码为 6 位有效深市股票/ETF，或扩大日期区间。"
        )
    return (
        f"无法获取 {symbol} 在 [{start} → {end}] 区间的行情数据。"
        f"请确认：① 股票代码正确；② openbb 或 yfinance 已安装；"
        f"③ 网络连接正常；④ 该股票在该时间段内有交易数据。"
    )

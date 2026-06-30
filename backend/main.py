"""
main.py — FastAPI 量化交易演示后端

本后端是 cv-vercel 项目的量化交易演示 API 层。
数据来源：OpenBB SDK（优先）→ yfinance（fallback）。
策略逻辑为本项目自行实现，仅调用 OpenBB 包公开 API 获取行情，
未复制或修改 OpenBB 内部源码。

OpenBB 基于 AGPLv3 许可证发布：https://github.com/OpenBB-finance/OpenBB
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator

try:
    from .alerts import scan_multifactor, scan_technical_alerts
    from .backtest import backtest
    from .ctp_adapter import get_ctp_status, scan_derivatives
    from .data_provider import fetch_historical_data
    from .etf_predictor import predict_etf
    from .strategies import STRATEGY_REGISTRY, STRATEGY_DESCRIPTIONS, STRATEGY_DEFAULT_PARAMS
except ImportError:
    from alerts import scan_multifactor, scan_technical_alerts
    from backtest import backtest
    from ctp_adapter import get_ctp_status, scan_derivatives
    from data_provider import fetch_historical_data
    from etf_predictor import predict_etf
    from strategies import STRATEGY_REGISTRY, STRATEGY_DESCRIPTIONS, STRATEGY_DEFAULT_PARAMS

# ---------------------------------------------------------------------------
# 日志
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)-16s | %(levelname)-5s | %(message)s",
)
logger = logging.getLogger("main")

# ---------------------------------------------------------------------------
# FastAPI 应用
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Quant Demo 量化交易演示 API",
    version="1.0.0",
    description="简历展示站 — 量化交易策略回测后端",
)

# CORS — 允许 GitHub Pages 和本地开发
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=(
        r"^https?://("
        r"localhost|127\.0\.0\.1|"
        r"10\.\d+\.\d+\.\d+|"
        r"172\.(1[6-9]|2\d|3[0-1])\.\d+\.\d+|"
        r"192\.168\.\d+\.\d+|"
        r".*\.github\.io|"
        r".*\.vercel\.app|"
        r".*\.loca\.lt|"
        r".*\.trycloudflare\.com"
        r")(:\d+)?$"
    ),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Pydantic 模型
# ---------------------------------------------------------------------------


class BacktestRequest(BaseModel):
    """回测请求体。"""

    symbol: str = Field(
        ..., min_length=1, max_length=20, description="股票代码，如 AAPL"
    )
    start: str = Field(..., description="起始日期 YYYY-MM-DD")
    end: str = Field(..., description="结束日期 YYYY-MM-DD")
    strategy: str = Field(
        ..., description=f"策略名称，可选：{list(STRATEGY_REGISTRY.keys())}"
    )
    initial_cash: float = Field(
        100000.0, ge=1000, description="初始资金"
    )
    commission: float = Field(
        0.0003, ge=0, le=0.01, description="手续费率"
    )
    slippage: float = Field(
        0.0001, ge=0, le=0.01, description="滑点比例"
    )
    params: dict[str, Any] = Field(
        default_factory=dict, description="策略参数"
    )

    @field_validator("start", "end")
    @classmethod
    def validate_date(cls, v: str) -> str:
        try:
            datetime.strptime(v, "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError(f"日期格式错误，应为 YYYY-MM-DD，实际为 {v}") from exc
        return v

    @field_validator("strategy")
    @classmethod
    def validate_strategy(cls, v: str) -> str:
        if v not in STRATEGY_REGISTRY:
            raise ValueError(
                f"不支持的策略 '{v}'，可选：{list(STRATEGY_REGISTRY.keys())}"
            )
        return v

    @field_validator("end")
    @classmethod
    def end_not_before_start(cls, v: str, info: Any) -> str:
        if "start" in info.data:
            start = info.data["start"]
            if v < start:
                raise ValueError(f"end ({v}) 不能早于 start ({start})")
        return v


class HealthResponse(BaseModel):
    status: str


class AlertScanRequest(BaseModel):
    symbols: list[str] = Field(..., min_length=1, max_length=80)
    start: str = Field(..., description="YYYY-MM-DD")
    end: str = Field(..., description="YYYY-MM-DD")
    ma20_tolerance: float = Field(0.015, ge=0, le=0.08)
    factor_top_n: int = Field(5, ge=1, le=50)
    factor_weights: dict[str, float] = Field(
        default_factory=lambda: {"alpha013": 0.3, "alpha015": 0.4, "quality": 0.3}
    )

    @field_validator("symbols")
    @classmethod
    def normalize_symbols(cls, v: list[str]) -> list[str]:
        symbols = []
        for item in v:
            symbol = str(item).strip().upper()
            if symbol and symbol not in symbols:
                symbols.append(symbol)
        if not symbols:
            raise ValueError("symbols 不能为空")
        return symbols

    @field_validator("start", "end")
    @classmethod
    def validate_scan_date(cls, v: str) -> str:
        try:
            datetime.strptime(v, "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError(f"日期格式错误，应为 YYYY-MM-DD，实际为 {v}") from exc
        return v


class ETFPredictRequest(BaseModel):
    symbols: list[str] = Field(..., min_length=1, max_length=30)
    start: str = Field(..., description="YYYY-MM-DD")
    end: str = Field(..., description="YYYY-MM-DD")
    horizon_days: int = Field(10, ge=3, le=60)

    @field_validator("symbols")
    @classmethod
    def normalize_symbols(cls, v: list[str]) -> list[str]:
        symbols = []
        for item in v:
            symbol = str(item).strip().upper()
            if symbol and symbol not in symbols:
                symbols.append(symbol)
        if not symbols:
            raise ValueError("symbols 不能为空")
        return symbols

    @field_validator("start", "end")
    @classmethod
    def validate_predict_date(cls, v: str) -> str:
        try:
            datetime.strptime(v, "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError(f"日期格式错误，应为 YYYY-MM-DD，实际为 {v}") from exc
        return v


class DerivativeScanRequest(BaseModel):
    symbols: list[str] = Field(..., min_length=1, max_length=40)
    strategy: str = Field("trend_breakout", description="trend_breakout / mean_reversion / vol_breakout")
    capital: float = Field(500000.0, ge=10000)
    risk_per_trade: float = Field(0.01, ge=0.001, le=0.05)
    days: int = Field(120, ge=80, le=260)

    @field_validator("symbols")
    @classmethod
    def normalize_symbols(cls, v: list[str]) -> list[str]:
        symbols = []
        for item in v:
            symbol = str(item).strip()
            if symbol and symbol not in symbols:
                symbols.append(symbol)
        if not symbols:
            raise ValueError("symbols 不能为空")
        return symbols

    @field_validator("strategy")
    @classmethod
    def validate_derivative_strategy(cls, v: str) -> str:
        allowed = {"trend_breakout", "mean_reversion", "vol_breakout"}
        if v not in allowed:
            raise ValueError(f"不支持的期货期权策略 {v}，可选：{sorted(allowed)}")
        return v


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------


@app.get("/health", response_model=HealthResponse)
async def health():
    """健康检查。"""
    return HealthResponse(status="ok")


@app.get("/")
async def serve_home():
    return FileResponse(_frontend_file("quant-demo.html"))


@app.get("/quant-demo.html")
async def serve_quant_demo():
    return FileResponse(_frontend_file("quant-demo.html"))


@app.get("/api/quote")
async def get_quote(
    symbol: str = Query(..., min_length=1, max_length=20),
    start: str = Query(..., description="起始日期 YYYY-MM-DD"),
    end: str = Query(..., description="结束日期 YYYY-MM-DD"),
):
    """
    获取历史 OHLCV 行情数据。
    """
    _validate_date_param(start)
    _validate_date_param(end)

    try:
        df = fetch_historical_data(symbol, start, end)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    records = _df_to_ohlcv_list(df)
    return {
        "symbol": symbol,
        "start": start,
        "end": end,
        "count": len(records),
        "data": records,
    }


@app.post("/api/backtest")
async def run_backtest(req: BacktestRequest):
    """
    执行策略回测。

    1. 获取行情数据
    2. 计算策略信号
    3. 执行回测引擎
    4. 返回绩效指标、净值曲线、交易明细
    """
    logger.info(
        "Backtest request: symbol=%s strategy=%s [%s → %s] cash=%.0f",
        req.symbol,
        req.strategy,
        req.start,
        req.end,
        req.initial_cash,
    )

    # 获取行情
    try:
        df = fetch_historical_data(req.symbol, req.start, req.end)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if len(df) < 30:
        raise HTTPException(
            status_code=400,
            detail=(
                f"数据不足：{req.symbol} 在 {req.start} 到 {req.end} 之间只获取到 "
                f"{len(df)} 条有效日 K 数据，至少需要 30 条才能运行回测。"
                "请换成交易更活跃的股票代码，或扩大日期区间。"
            ),
        )

    if len(df) < 30:
        raise HTTPException(
            status_code=400,
            detail=f"数据不足（仅 {len(df)} 行），至少需要 30 行有效行情数据。",
        )

    # 合并参数：默认参数 + 用户自定义参数
    default_params = STRATEGY_DEFAULT_PARAMS.get(req.strategy, {}).copy()
    default_params.update(req.params)

    # 生成信号
    strategy_fn = STRATEGY_REGISTRY[req.strategy]
    try:
        signals = strategy_fn(df, params=default_params)
    except Exception as exc:
        raise HTTPException(
            status_code=400, detail=f"策略计算失败: {exc}"
        )

    # 执行回测
    result = backtest(
        df=df,
        signals=signals,
        strategy_name=req.strategy,
        symbol=req.symbol.upper(),
        initial_cash=req.initial_cash,
        commission=req.commission,
        slippage=req.slippage,
    )

    return {
        "symbol": result.symbol,
        "strategy": result.strategy,
        "params": default_params,
        "metrics": result.metrics,
        "equity_curve": result.equity_curve,
        "price_series": result.price_series,
        "signals": result.signals,
        "trades": result.trades,
    }


@app.post("/api/alerts/scan")
async def scan_alerts(req: AlertScanRequest):
    if req.end < req.start:
        raise HTTPException(status_code=400, detail="结束日期不能早于起始日期")

    alerts: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    data_map: dict[str, pd.DataFrame] = {}

    for symbol in req.symbols:
        try:
            df = fetch_historical_data(symbol, req.start, req.end)
            data_map[symbol] = df
            alerts.extend(scan_technical_alerts(symbol, df, req.ma20_tolerance))
        except RuntimeError as exc:
            errors.append({"symbol": symbol, "error": str(exc)})
        except Exception as exc:
            logger.exception("Alert scan failed for %s", symbol)
            errors.append({"symbol": symbol, "error": f"扫描失败: {exc}"})

    factor_alerts, factor_ranking = scan_multifactor(
        data_map,
        weights=req.factor_weights,
        top_n=req.factor_top_n,
    )
    alerts.extend(factor_alerts)

    priority = {"buy": 0, "sell": 1, "observe": 2, "hold": 3}
    alerts.sort(key=lambda item: (priority.get(item.get("action"), 9), item.get("symbol", "")))

    return {
        "start": req.start,
        "end": req.end,
        "symbols": req.symbols,
        "count": len(alerts),
        "alerts": alerts,
        "factor_ranking": factor_ranking,
        "errors": errors,
    }


@app.post("/api/etf/predict")
async def predict_etfs(req: ETFPredictRequest):
    if req.end < req.start:
        raise HTTPException(status_code=400, detail="结束日期不能早于起始日期")

    predictions: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    for symbol in req.symbols:
        try:
            df = fetch_historical_data(symbol, req.start, req.end)
            predictions.append(predict_etf(symbol, df, req.horizon_days))
        except RuntimeError as exc:
            errors.append({"symbol": symbol, "error": str(exc)})
        except Exception as exc:
            logger.exception("ETF prediction failed for %s", symbol)
            errors.append({"symbol": symbol, "error": f"预测失败: {exc}"})

    return {
        "start": req.start,
        "end": req.end,
        "horizon_days": req.horizon_days,
        "symbols": req.symbols,
        "count": len(predictions),
        "predictions": predictions,
        "errors": errors,
        "disclaimer": "预测基于历史行情与技术指标，仅用于学习和项目展示，不构成投资建议。",
    }


@app.get("/api/ctp/status")
async def ctp_status():
    return get_ctp_status()


@app.post("/api/derivatives/scan")
async def derivative_scan(req: DerivativeScanRequest):
    return scan_derivatives(
        symbols=req.symbols,
        strategy=req.strategy,
        capital=req.capital,
        risk_per_trade=req.risk_per_trade,
        days=req.days,
    )


@app.get("/api/strategies")
async def list_strategies():
    """返回支持的策略列表。"""
    return {
        "strategies": [
            {
                "id": sid,
                "name": STRATEGY_DESCRIPTIONS.get(sid, sid),
                "default_params": STRATEGY_DEFAULT_PARAMS.get(sid, {}),
            }
            for sid in STRATEGY_REGISTRY
        ]
    }


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _validate_date_param(v: str) -> None:
    try:
        datetime.strptime(v, "%Y-%m-%d")
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"日期格式错误，应为 YYYY-MM-DD，实际为 {v}",
        ) from exc


def _frontend_file(filename: str) -> Path:
    path = Path(__file__).resolve().parents[1] / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"前端文件不存在: {filename}")
    return path


def _df_to_ohlcv_list(df: pd.DataFrame) -> list[dict]:
    """将 DataFrame 转为 OHLCV JSON 列表。"""
    records = []
    for idx, row in df.iterrows():
        records.append(
            {
                "date": str(idx.date()) if hasattr(idx, "date") else str(idx),
                "open": round(float(row["open"]), 4),
                "high": round(float(row["high"]), 4),
                "low": round(float(row["low"]), 4),
                "close": round(float(row["close"]), 4),
                "volume": int(row["volume"]),
            }
        )
    return records


# ---------------------------------------------------------------------------
# 启动入口
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)

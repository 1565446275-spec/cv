"""
backtest.py — 回测引擎

支持：
    - 全仓买入/卖出
    - 手续费（commission）
    - 滑点（slippage）
    - 每日净值序列（equity_curve）
    - 交易明细（trades）
    - 绩效指标（总收益率、年化收益、最大回撤、夏普比率、胜率等）
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict
from typing import Any

import numpy as np
import pandas as pd


@dataclass
class TradeRecord:
    """单笔交易的完整记录。"""

    buy_date: str
    sell_date: str
    buy_price: float
    sell_price: float
    return_pct: float  # 扣除手续费和滑点后的实际收益率


@dataclass
class BacktestResult:
    """回测输出结果。"""

    symbol: str
    strategy: str
    metrics: dict[str, Any]
    equity_curve: list[dict[str, Any]]
    price_series: list[dict[str, Any]]
    signals: list[dict[str, Any]]
    trades: list[dict[str, Any]]


def backtest(
    df: pd.DataFrame,
    signals: pd.Series,
    strategy_name: str,
    symbol: str,
    initial_cash: float = 100000.0,
    commission: float = 0.0003,
    slippage: float = 0.0001,
) -> BacktestResult:
    """
    执行回测。

    Parameters
    ----------
    df : pd.DataFrame
        OHLCV 数据，必须包含 'close' 列，index 为 DatetimeIndex。
    signals : pd.Series
        信号序列，+1=买入，0=持有，-1=卖出，index 与 df 一致。
    strategy_name : str
        策略名称（透传）。
    symbol : str
        股票代码（透传）。
    initial_cash : float
        初始资金。
    commission : float
        手续费率（双向收取）。
    slippage : float
        滑点比例（成交价偏移）。

    Returns
    -------
    BacktestResult
    """
    df = df.copy()
    # 对齐信号索引
    signals = signals.reindex(df.index, fill_value=0)

    close = df["close"].values
    dates = df.index
    n = len(df)

    cash = float(initial_cash)
    shares = 0.0
    in_position = False

    equity_curve = np.zeros(n)
    trade_list: list[TradeRecord] = []
    signal_log: list[dict[str, Any]] = []

    buy_entry_price = 0.0
    buy_entry_date = ""

    # 信号说明映射
    signal_reasons = {
        1: "",
        -1: "",
    }

    for i in range(n):
        date_str = str(dates[i].date())
        sig = signals.iloc[i]
        price = float(close[i])

        # 计算当前持仓市值
        position_value = shares * price
        total_equity = cash + position_value
        equity_curve[i] = total_equity

        if sig == 1 and not in_position:
            # 买入信号（不在持仓中）
            exec_price = price * (1 + slippage)  # 买入滑点向上
            fee = exec_price * initial_cash * commission
            available = cash - fee
            if available <= 0:
                continue

            shares = available / exec_price
            cash = 0.0  # 全仓
            in_position = True
            buy_entry_price = exec_price
            buy_entry_date = date_str

            signal_log.append({
                "date": date_str,
                "action": "buy",
                "price": round(exec_price, 4),
                "reason": "",
            })

        elif sig == -1 and in_position:
            # 卖出信号（持仓中）
            exec_price = price * (1 - slippage)  # 卖出滑点向下
            gross = shares * exec_price
            fee = gross * commission
            cash = gross - fee
            shares = 0.0
            in_position = False

            trade_return = (exec_price - buy_entry_price) / buy_entry_price

            trade_list.append(TradeRecord(
                buy_date=buy_entry_date,
                sell_date=date_str,
                buy_price=round(buy_entry_price, 4),
                sell_price=round(exec_price, 4),
                return_pct=round(trade_return, 6),
            ))

            signal_log.append({
                "date": date_str,
                "action": "sell",
                "price": round(exec_price, 4),
                "reason": "",
            })

    # 如果回测结束时仍持仓，强制平仓
    if in_position:
        last_price = float(close[-1])
        exec_price = last_price * (1 - slippage)
        gross = shares * exec_price
        fee = gross * commission
        cash = gross - fee
        shares = 0.0

        trade_return = (exec_price - buy_entry_price) / buy_entry_price
        trade_list.append(TradeRecord(
            buy_date=buy_entry_date,
            sell_date=str(dates[-1].date()),
            buy_price=round(buy_entry_price, 4),
            sell_price=round(exec_price, 4),
            return_pct=round(trade_return, 6),
        ))

    # 最终净值
    final_equity = cash + shares * float(close[-1])

    # ------------------------------------------------------------------
    # 绩效指标计算
    # ------------------------------------------------------------------
    total_return = (final_equity - initial_cash) / initial_cash

    # 年化收益率
    days = (dates[-1] - dates[0]).days
    if days > 0:
        annual_return = (1 + total_return) ** (365.0 / days) - 1
    else:
        annual_return = 0.0

    # 最大回撤
    peak = np.maximum.accumulate(equity_curve)
    drawdown = (equity_curve - peak) / peak
    max_drawdown = float(np.min(drawdown))

    # 夏普比率（使用日收益率，无风险利率=0）
    daily_returns = pd.Series(equity_curve).pct_change().dropna()
    if len(daily_returns) > 1 and daily_returns.std() > 0:
        sharpe = float(
            daily_returns.mean() / daily_returns.std() * math.sqrt(252)
        )
    else:
        sharpe = 0.0

    # 胜率
    total_trades = len(trade_list)
    if total_trades > 0:
        wins = sum(1 for t in trade_list if t.return_pct > 0)
        win_rate = wins / total_trades
        avg_return = sum(t.return_pct for t in trade_list) / total_trades
    else:
        win_rate = 0.0
        avg_return = 0.0

    metrics = {
        "total_return": round(total_return, 6),
        "annual_return": round(annual_return, 6),
        "max_drawdown": round(max_drawdown, 6),
        "sharpe": round(sharpe, 4),
        "win_rate": round(win_rate, 4),
        "total_trades": total_trades,
        "avg_return_per_trade": round(avg_return, 6),
    }

    # 给信号补充原因说明
    _enrich_signal_reasons(signal_log, strategy_name, df, signals)

    # 净值曲线序列
    equity_list = [
        {"date": str(dates[i].date()), "equity": round(float(equity_curve[i]), 4)}
        for i in range(n)
    ]

    # 收盘价序列
    price_list = [
        {"date": str(dates[i].date()), "close": round(float(close[i]), 4)}
        for i in range(n)
    ]

    # TradeRecord → dict
    trades_dict = [asdict(t) for t in trade_list]

    return BacktestResult(
        symbol=symbol,
        strategy=strategy_name,
        metrics=metrics,
        equity_curve=equity_list,
        price_series=price_list,
        signals=signal_log,
        trades=trades_dict,
    )


def _enrich_signal_reasons(
    signal_log: list[dict],
    strategy_name: str,
    df: pd.DataFrame,
    signals: pd.Series,
) -> None:
    """
    为信号补充可读的理由说明。
    此函数直接修改 signal_log 中的 reason 字段。
    """
    # 构建信号索引映射：date → signal value
    signal_map: dict[str, int] = {}
    for idx, val in signals.items():
        signal_map[str(idx.date())] = int(val)

    close = df["close"]

    for sig in signal_log:
        date_str = sig["date"]
        action = sig["action"]
        sig_val = signal_map.get(date_str, 0)
        sig["reason"] = _get_reason(
            strategy_name, action, date_str, df, sig_val
        )


def _get_reason(
    strategy_name: str,
    action: str,
    date_str: str,
    df: pd.DataFrame,
    sig_val: int,
) -> str:
    """生成单个信号的原因说明。"""
    try:
        idx = df.index.get_loc(pd.Timestamp(date_str))
    except (KeyError, LookupError):
        return ""

    reasons = {
        "ma_cross": {
            "buy": "短均线上穿长均线（金叉）",
            "sell": "短均线下穿长均线（死叉）",
        },
        "rsi_reversal": {
            "buy": "RSI 脱离超卖区回升",
            "sell": "RSI 脱离超买区回落",
        },
        "macd_cross": {
            "buy": "MACD 金叉（上穿信号线）",
            "sell": "MACD 死叉（下穿信号线）",
        },
        "bollinger_reversion": {
            "buy": "价格触及/跌破布林下轨",
            "sell": "价格触及/升穿布林上轨",
        },
    }

    strategy_reasons = reasons.get(strategy_name, {})
    return strategy_reasons.get(action, "")

# Quant Demo — 量化交易策略回测后端

> 本项目是简历展示站（cv-vercel）的量化交易演示后端。
> **仅供学习和展示，不构成任何投资建议。**

---

## 技术栈

| 组件 | 技术 |
|------|------|
| 框架 | FastAPI (Python 3.10+) |
| 数据源 | OpenBB SDK（优先）→ yfinance（fallback） |
| 策略引擎 | 自行实现（双均线、RSI、MACD、布林带） |
| 回测引擎 | 自行实现（全仓交易、手续费、滑点） |

### 许可证说明

本后端仅通过 pip 安装的 `openbb` 包调用其公开 API 获取行情数据，
**未复制、修改或衍生 OpenBB 内部源码**。

OpenBB 基于 **AGPLv3** 许可证发布，完整许可证文本见：
https://github.com/OpenBB-finance/OpenBB/blob/main/LICENSE

---

## 本地启动

### 1. 安装依赖

```bash
cd backend
pip install -r requirements.txt
```

> **注意**：`openbb` 包较大，首次安装可能需要 2-5 分钟。
> 如果 OpenBB 安装失败或不使用，系统会自动 fallback 到 `yfinance`。

### 2. 启动开发服务器

```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

### 3. 验证

```bash
curl http://127.0.0.1:8000/health
# 预期响应：{"status":"ok"}
```

Swagger 文档：http://127.0.0.1:8000/docs

---

## API 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/health` | 健康检查 |
| GET | `/api/quote?symbol=000001.SZ&start=2024-01-01&end=2025-01-01` | OHLCV 行情数据 |
| POST | `/api/backtest` | 执行策略回测（JSON 请求体） |
| GET | `/api/strategies` | 支持的策略列表 |

### 回测请求示例

```json
{
  "symbol": "000001.SZ",
  "start": "2024-01-01",
  "end": "2025-01-01",
  "strategy": "ma_cross",
  "initial_cash": 100000,
  "commission": 0.0003,
  "slippage": 0.0001,
  "params": {
    "short_window": 5,
    "long_window": 20
  }
}
```

---

## 前端集成

1. 本地开发时，打开 `../quant-demo.html`（前端已默认连接 `http://127.0.0.1:8000`）。

2. **部署到生产**后，修改 `quant-demo.html` 顶部的常量：

   ```javascript
   const API_BASE = "https://your-backend.onrender.com";
   ```

---

## 部署指南

由于 GitHub Pages **只能托管静态文件**，后端需要部署在支持 Python 的平台上：

### 方案 A：Render（推荐免费方案）

1. 在 [render.com](https://render.com) 注册
2. 创建 **Web Service** → 连接你的 GitHub 仓库
3. 设置：
   - **Root Directory**：`backend`
   - **Build Command**：`pip install -r requirements.txt`
   - **Start Command**：`uvicorn main:app --host 0.0.0.0 --port $PORT`
4. 部署后在 dashboard 获取公网 URL

### 方案 B：Railway

1. 在 [railway.com](https://railway.com) 注册
2. New Project → Deploy from GitHub
3. 设置 Start Command：`uvicorn main:app --host 0.0.0.0 --port $PORT`

### 方案 C：Fly.io

```bash
fly launch
fly deploy
```

### 方案 D：VPS（阿里云 / 腾讯云 / AWS）

```bash
# 在服务器上
git clone <你的仓库>
cd backend
pip install -r requirements.txt
nohup uvicorn main:app --host 0.0.0.0 --port 8000 &
```

### 部署后

将 `quant-demo.html` 中的 `API_BASE` 更新为后端公网地址：

```javascript
const API_BASE = "https://your-app.onrender.com";
```

---

## 项目结构

```
backend/
├── main.py            # FastAPI 应用入口、路由
├── data_provider.py   # 数据获取层（OpenBB → yfinance）
├── strategies.py      # 策略信号生成（4 种策略）
├── backtest.py        # 回测引擎
├── requirements.txt   # Python 依赖
└── README.md          # 本文件
```

## 支持的策略

| 策略 ID | 中文名 | 说明 |
|---------|--------|------|
| `ma_cross` | 双均线交叉 | 短均线上穿长均线买入，下穿卖出 |
| `rsi_reversal` | RSI 超卖超买反转 | RSI 脱离超卖区买入，脱离超买区卖出 |
| `macd_cross` | MACD 金叉死叉 | MACD 线上穿信号线买入，下穿卖出 |
| `bollinger_reversion` | 布林带均值回归 | 价格触及下轨买入，触及上轨卖出 |

---

## ⚠️ 风险提示

**量化交易策略存在重大亏损风险。**
- 历史回测结果不代表未来表现
- 本系统中的策略仅用于教学和面试展示
- 实盘交易前请充分测试并咨询专业人士
- 作者不对任何因使用本代码而产生的损失负责

---

## 许可

MIT License — 仅限学习和展示用途。

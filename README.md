# trademap

交易辅助看盘页，只在本机 `http://127.0.0.1:8765/` 上看，**不接任何交易**。定位：一张图上叠多重信号，
用来交叉验证；信号一律在 Python 里算好送给页面，页面只画不判。

2026-11-07 从 ballast 仓拆出（ballast 代号已退役归档；本仓保留了相关提交的历史）。

## 页面上有什么

- K 线由浏览器直连交易所公开接口：BTCUSDT 永续（Binance）、HYPE（Hyperliquid）。
- 周期一行按钮：15m · 30m · 1H · 2H · 4H · 1D · 3D · 1W · 1M，键盘 `1`–`9` 对应切换。
- 图表库原生指标：均线、布林带（20, 2）、SuperTrend、RSI、MACD、OBV；KDJ 是本仓写的。
- 🍅 9TD（操作者的 Pine 脚本移植，`trademap/td9.py`）：7 / 8 / 🍅 在 K 线上方，7 / 8 / 🤢 在下方，收在布林带外加 `*`。
- ◆ 扫底回收（探索版，`trademap/sweepmarks.py`，调 farseer 仓的检测器）：二档确认画菱形，空转多在 K 线下方、
  多转空在上方，颜色 = 临时信心分（高 亮绿 / 亮红，中 浅绿 / 橙，低 灰），旁边小字 `S3` 是分数；一档只画灰点。
  权重未冻结，只为显示。
- 手画的水平线会自动存到 `data/chart_drawings/<品种>.json`，`chartlevels.py` 读出来报价格相对位置。

## 怎么跑

```
(cd chart && npm ci --ignore-scripts && npm run build)      # 前端，改了 chart/src 后要重跑
FARSEER_SRC=/Users/tingluo/Documents/CC/farseer/src \
  /Users/tingluo/Documents/CC/farseer/.venv/bin/python chartserve.py --port 8765
```

扫底回收信号需要 farseer 的源码和它的解释器（polars、numpy）；换别的解释器启动，9TD 照常、
`/api/sweep` 返回 500。服务只绑 127.0.0.1，并校验 Host / Origin。

```
.venv/bin/python -m pytest -q        # 或用 farseer 的解释器，否则 test_sweepmarks 整模块跳过
```

## 接口

| 路径 | 作用 |
|---|---|
| `GET /api/marks?symbol=&tf=` | 9TD 标记，只算已收盘 K 线 |
| `GET /api/sweep?symbol=&tf=` | 扫底回收事件（拉 1000 根） |
| `GET/POST /api/drawings?symbol=` | 手画线的读写 |

`tf` 取值 `15 30 60 120 240 D 3D W M`。3 日线：Binance 自家 3d K 线比「从 1970 年起每 3 天一格」晚开一天，
页面对永续直接拉 Binance 原生 3d K 线，后端也用原生 3d，两边一致；Hyperliquid 的 3d 与格子重合。

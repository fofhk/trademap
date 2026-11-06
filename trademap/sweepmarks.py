"""「扫底回收」反转信号（探索版）在图上的标记。只用来看，不接任何交易。

检测不在这里：腿识别、两档确认、去重、手写大背景分段表都直接调用 farseer 仓库的
``farseer.research.sweep``（v0，预注册 sweep_20261106_reclaim_exploration），这里只做三件事：
把图表的 K 线转成它要的格式、按页面当前周期跑一遍、给每个事件打一个临时信心分。

临时信心分（v0，权重未冻结，只为显示）：
    顺势（手写分段表）+2；扫过 7 天前低（做空一侧：7 天前高）+1；极值根 RSI14 < 35（> 65）+1；
    腿内收在布林带（20, 2）外的根数 >= 2 +1；腿跌幅 >= 4 倍 ATR14 +1。
    高 >= 5，中 3-4，低 <= 2。
特征口径照 ``farseer.research.sweep.features``：做空一侧在取负后的价格上算（RSI 自动变成
100 - RSI、布林上下轨互换），所以两侧用同一组条件。

检测器按 1 小时设计，这里按页面当前周期跑：腿长、等待根数仍按根数计，「7 天前」按该周期的
根数折算。手写分段表是 BTC 的拐点，只用于 BTCUSDT；别的标的不判顺逆势（with_trend = None，
不加那 2 分）。

farseer 源码位置由环境变量 FARSEER_SRC 指定（默认操作者 Mac 上的主 checkout），它依赖
polars 与 numpy：chartserve 要用装了它们的解释器启动。导入在第一次调用时才做，导入失败
只影响 /api/sweep，不影响 9TD。纯计算，无 I/O（除读 farseer 包内的分段表）。
"""

from __future__ import annotations

import math
import os
import sys

from ballast.td9 import Bar

DEFAULT_FARSEER_SRC = "/Users/tingluo/Documents/CC/farseer/src"
FETCH_LIMIT = 1000          # 7 天前低在 1 小时线上要 168 根 + 腿长，500 根不够
MAX_WAIT = 72               # 二档最长等待根数（scan.py 的默认值）
REGIME_SYMBOLS = {"BTCUSDT"}
DAY_MS = 86_400_000

SWEEP_LOOKBACK_DAYS = 7
RSI_EXTREME = 35.0
OUTSIDE_BB_MIN = 2
DROP_ATR_MIN = 4.0
HIGH_MIN, MID_MIN = 5, 3


class SweepError(RuntimeError):
    """检测器不可用或输入无法计算。从不表示「没有事件」。"""


def _farseer():
    src = os.environ.get("FARSEER_SRC", DEFAULT_FARSEER_SRC)
    if src not in sys.path:
        sys.path.insert(0, src)
    try:
        import polars as pl
        import numpy as np
        from farseer.research.sweep import features as FT
        from farseer.research.sweep import legs as LG
        from farseer.research.sweep import scan as SC
        from farseer.signals import rsi as RSI
        from farseer.signals import td_sequential as TD
    except ImportError as e:
        raise SweepError(f"farseer sweep detector not importable from FARSEER_SRC={src!r} "
                         f"with {sys.executable}: {e}") from None
    return pl, np, FT, LG, SC, RSI, TD


def load_segments():
    """farseer 包内的手写大背景分段表。"""
    _, _, FT, *_ = _farseer()
    return FT.load_segments()


def score(with_trend: bool | None, swept_7d: bool, rsi_ext: float, bars_outside_bb: int,
          drop_atr: float) -> int:
    """临时信心分。所有输入都已按方向定向（做空一侧 = 取负后的价格）。"""
    s = 2 if with_trend else 0
    s += 1 if swept_7d else 0
    s += 1 if (not math.isnan(rsi_ext) and rsi_ext < RSI_EXTREME) else 0
    s += 1 if bars_outside_bb >= OUTSIDE_BB_MIN else 0
    s += 1 if drop_atr >= DROP_ATR_MIN else 0
    return s


def level(s: int) -> str:
    return "high" if s >= HIGH_MIN else "mid" if s >= MID_MIN else "low"


def events(bars: list[Bar], tf_ms: int, segments=None) -> list[dict]:
    """已收盘的 K 线（旧到新）-> 事件列表（按确认时刻排序）。

    ``segments``：``load_segments()`` 的返回；None = 不判顺逆势。
    每个事件：{time（确认 K 线开盘时刻）, side, tier, score, level, with_trend,
    leg_start_price, leg_ext_price, confirm_price, stop_pct}。tier 1 = 只到一档（收复 50%）。
    """
    pl, np, FT, LG, SC, RSI, TD = _farseer()
    if not bars:
        return []
    df = pl.DataFrame({"t": [b.time for b in bars], "o": [b.open for b in bars],
                       "h": [b.high for b in bars], "l": [b.low for b in bars],
                       "c": [b.close for b in bars]})
    params = LG.LegParams()
    lookback = max(1, round(SWEEP_LOOKBACK_DAYS * DAY_MS / tf_ms))
    t = df["t"].to_numpy()
    out: list[dict] = []
    for side in (1, -1):
        o, h, l, c = FT.orient(df, side)
        _, evs = SC.detect({"1h": df}, side, params, MAX_WAIT)
        if not evs:
            continue
        rsi = np.array([math.nan if x is None else x for x in RSI.from_closes(list(c))])
        bb_lo = np.array(TD.compute(pl.DataFrame({"t": t, "o": o, "h": h, "l": l, "c": c})).bb_lower)
        for ev in evs:
            g = ev.leg
            cbar = ev.t2 if ev.t2 is not None else ev.t1
            a0 = g.i - lookback
            swept = a0 >= 0 and g.ext_px < float(np.min(l[a0:g.i]))
            outside = int(np.sum(c[g.i:g.j + 1] < bb_lo[g.i:g.j + 1]))
            wt = None
            if segments is not None:
                wt = FT.with_trend(side, FT.regime_manual_at(segments, int(t[cbar]) + tf_ms))
            s = score(wt, swept, float(rsi[g.j]), outside, g.drop_atr)
            out.append({
                "time": int(t[cbar]),
                "side": "long" if side > 0 else "short",
                "tier": 2 if ev.t2 is not None else 1,
                "score": s,
                "level": level(s),
                "with_trend": wt,
                "leg_start_price": abs(g.start_px),
                "leg_ext_price": abs(g.ext_px),
                "confirm_price": abs(float(c[cbar])),
                "stop_pct": 100 * (float(c[cbar]) - g.ext_px) / abs(float(c[cbar])),
            })
    return sorted(out, key=lambda e: (e["time"], e["side"]))

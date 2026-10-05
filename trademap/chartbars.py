"""Closed OHLC bars from the SAME venue the chart page draws, for td9.py.

The chart (chart/src/main.js) shows Binance USD-M perp candles for BTCUSDT and
Hyperliquid candles for HYPE. Marks computed from any other venue's closes
would sit on candles they were not computed from, so the venues here follow
ballast.chartlevels.SYMBOLS' `provider`, not Bybit.

Public, unauthenticated endpoints, stdlib urllib only. Every failure -- network,
HTTP status, a body that does not parse, zero bars -- raises ChartBarsError:
the caller must never mistake "upstream broke" for "no marks".
"""

from __future__ import annotations

import json
import math
import urllib.error
import urllib.parse
import urllib.request

from ballast.chartlevels import SYMBOLS
from ballast.td9 import Bar

BINANCE_KLINES = "https://fapi.binance.com/fapi/v1/klines"
HYPERLIQUID_INFO = "https://api.hyperliquid.xyz/info"
DEFAULT_LIMIT = 500
DEFAULT_TIMEOUT = 10.0

# Chart timeframe (the page's <select> values, Vela's canonical keys) -> interval.
# Both venues happen to spell these the same; kept per venue so a divergence is
# one edit, not a silent mismatch.
INTERVALS = {
    "binance": {"15": "15m", "60": "1h", "240": "4h", "D": "1d", "W": "1w"},
    "hyperliquid": {"15": "15m", "60": "1h", "240": "4h", "D": "1d", "W": "1w"},
}
INTERVAL_MS = {"15": 15 * 60_000, "60": 3_600_000, "240": 4 * 3_600_000,
               "D": 86_400_000, "W": 7 * 86_400_000}
TIMEFRAMES = tuple(INTERVAL_MS)

# Chart symbol -> the venue's own name for it (Vela's `binance:BTCUSDT.P` is the
# USD-M perp, which fapi calls plain BTCUSDT).
VENUE_SYMBOL = {"BTCUSDT": "BTCUSDT", "HYPE": "HYPE"}
if set(VENUE_SYMBOL) != set(SYMBOLS):   # a symbol added there must get a venue name here
    raise ImportError(f"chartbars VENUE_SYMBOL {sorted(VENUE_SYMBOL)} != chartlevels SYMBOLS {sorted(SYMBOLS)}")


class ChartBarsError(RuntimeError):
    """Bars could not be fetched or read. Never means 'no bars'."""


def _price(v, where: str) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise ChartBarsError(f"{where}: not a number: {v!r}") from None
    if not math.isfinite(x) or x <= 0:
        raise ChartBarsError(f"{where}: bad price {v!r}")
    return x


def _int(v, where: str) -> int:
    if isinstance(v, bool) or not isinstance(v, int):
        raise ChartBarsError(f"{where}: expected integer ms, got {v!r}")
    return v


def _finish(bars: list[Bar], venue: str) -> list[Bar]:
    if not bars:
        raise ChartBarsError(f"{venue}: zero bars returned")
    for i in range(1, len(bars)):
        if bars[i].time <= bars[i - 1].time:
            raise ChartBarsError(f"{venue}: bar times not increasing at index {i}")
    return bars


def parse_binance(raw, now_ms: int) -> list[Bar]:
    """fapi /klines rows: [openTime, o, h, l, c, v, closeTime, ...], prices as strings.

    closeTime is the bar's last millisecond (open + interval - 1), so the bar is
    closed once the clock is strictly past it.
    """
    if not isinstance(raw, list):
        raise ChartBarsError(f"binance: expected a list of klines, got {type(raw).__name__}: {str(raw)[:200]}")
    bars = []
    for i, k in enumerate(raw):
        where = f"binance kline[{i}]"
        if not isinstance(k, list) or len(k) < 7:
            raise ChartBarsError(f"{where}: malformed row {str(k)[:100]}")
        bars.append(Bar(time=_int(k[0], where), open=_price(k[1], where), high=_price(k[2], where),
                        low=_price(k[3], where), close=_price(k[4], where),
                        closed=now_ms > _int(k[6], where)))
    return _finish(bars, "binance")


def parse_hyperliquid(raw, now_ms: int) -> list[Bar]:
    """candleSnapshot items: {t: open ms, T: close ms (open + interval - 1), o, h, l, c, ...}."""
    if not isinstance(raw, list):
        raise ChartBarsError(f"hyperliquid: expected a list of candles, got {type(raw).__name__}: {str(raw)[:200]}")
    bars = []
    for i, c in enumerate(raw):
        where = f"hyperliquid candle[{i}]"
        if not isinstance(c, dict):
            raise ChartBarsError(f"{where}: malformed item {str(c)[:100]}")
        bars.append(Bar(time=_int(c.get("t"), where), open=_price(c.get("o"), where),
                        high=_price(c.get("h"), where), low=_price(c.get("l"), where),
                        close=_price(c.get("c"), where), closed=now_ms > _int(c.get("T"), where)))
    return _finish(bars, "hyperliquid")


def _get_json(req, timeout: float, venue: str):
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        body = e.read()[:200].decode("utf-8", "replace")
        raise ChartBarsError(f"{venue}: HTTP {e.code}: {body}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise ChartBarsError(f"{venue}: {type(e).__name__}: {e}") from None
    except ValueError as e:
        raise ChartBarsError(f"{venue}: response is not JSON: {e}") from None


def fetch(symbol: str, tf: str, now_ms: int, limit: int = DEFAULT_LIMIT,
          timeout: float = DEFAULT_TIMEOUT) -> list[Bar]:
    """The most recent ~`limit` bars, oldest first; the last one may be forming
    (its `closed` says so -- td9 drops it)."""
    if symbol not in SYMBOLS:
        raise ChartBarsError(f"unknown symbol {symbol!r}")
    if tf not in INTERVAL_MS:
        raise ChartBarsError(f"unknown timeframe {tf!r}")
    venue = SYMBOLS[symbol]["provider"]
    interval = INTERVALS[venue][tf]
    if venue == "binance":
        url = BINANCE_KLINES + "?" + urllib.parse.urlencode(
            {"symbol": VENUE_SYMBOL[symbol], "interval": interval, "limit": limit})
        return parse_binance(_get_json(url, timeout, venue), now_ms)
    if venue == "hyperliquid":
        # candleSnapshot takes a time window, not a count: ask for `limit` bars'
        # worth plus slack (the forming bar, a boundary bar).
        body = {"type": "candleSnapshot",
                "req": {"coin": VENUE_SYMBOL[symbol], "interval": interval,
                        "startTime": now_ms - (limit + 2) * INTERVAL_MS[tf], "endTime": now_ms}}
        req = urllib.request.Request(HYPERLIQUID_INFO, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        return parse_hyperliquid(_get_json(req, timeout, venue), now_ms)[-limit:]
    raise ChartBarsError(f"no bar source for provider {venue!r}")

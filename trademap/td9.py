"""The operator's "🍅 9TD with Bollinger Bands" TradingView script, ported to Python.

This module is the source of truth for those marks: the chart page only draws
what it is given, so that if the marks later feed a Ballast signal, the signal
and the picture come from the same code. Pure -- no I/O, no clock.

Pine source being ported (semantics kept exactly, including its quirks):

    TD := (close > close[4]) ? nz(TD[1]) + 1 : 0
    TS := (close < close[4]) ? nz(TS[1]) + 1 : 0
    TDUp = TD - ta.valuewhen(TD < TD[1], TD, 1)
    TDDn = TS - ta.valuewhen(TS < TS[1], TS, 1)
    basis = ta.sma(close, 20); dev = 2.0 * ta.stdev(close, 20)
    upper = basis + dev; lower = basis - dev
    TDUp == 7/8/9 -> mark above the bar "7" / "8" / "🍅"
    TDDn == 7/8/9 -> mark below the bar "7" / "8" / "🤢"
    "*" when that mark's close is beyond the band on the same side

Pine details that decide real outputs:

  * `close[4]` is na on the first 4 bars; `na > x` is false, so they count 0.
  * `ta.valuewhen(cond, src, 1)` is `src` at the SECOND most recent bar
    (counting the current one) where `cond` held -- na until there have been
    two. While it is na, TDUp is na and nothing is marked. Because TD only ever
    falls by resetting to 0, the value it returns is 0 and TDUp == TD after
    warm-up; it is still implemented literally, so a future edit to the Pine
    that breaks that coincidence would be visible here, not papered over.
  * `ta.stdev` is the POPULATION standard deviation (divide by N, not N-1).
  * Counts past 9 keep climbing and are not marked.

Only CLOSED bars are marked. TradingView evaluates the forming bar too and
repaints its mark as price moves; a mark that can vanish is no use as a signal,
so the forming bar is dropped before anything is computed.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

LOOKBACK = 4          # close vs close[4]
BB_LENGTH = 20
BB_MULT = 2.0

TEXT = {"up": {7: "7", 8: "8", 9: "🍅"}, "down": {7: "7", 8: "8", 9: "🤢"}}


@dataclass(frozen=True)
class Bar:
    time: int            # bar OPEN time, epoch ms (what the chart keys candles by)
    open: float
    high: float
    low: float
    close: float
    closed: bool         # decided by the caller from the venue's close time and a clock


class Td9Error(ValueError):
    """Bars that cannot be evaluated. Never means 'no marks'."""


def _valuewhen_1(cond: list[bool], src: list[int]) -> list[int | None]:
    """ta.valuewhen(cond, src, 1): src at the 2nd most recent bar where cond held."""
    out: list[int | None] = []
    hits: list[int] = []
    for c, v in zip(cond, src):
        if c:
            hits.append(v)
        out.append(hits[-2] if len(hits) >= 2 else None)
    return out


def _setup_counts(closes: list[float], rising: bool) -> list[int]:
    counts: list[int] = []
    for i, c in enumerate(closes):
        if i < LOOKBACK:
            hit = False                       # close[4] is na -> comparison false
        else:
            prior = closes[i - LOOKBACK]
            hit = c > prior if rising else c < prior
        counts.append((counts[-1] if counts else 0) + 1 if hit else 0)   # nz(TD[1]) + 1
    return counts


def _relative(counts: list[int]) -> list[int | None]:
    # `TD < TD[1]`: TD[1] is na on the first bar, and na comparisons are false.
    cond = [i > 0 and counts[i] < counts[i - 1] for i in range(len(counts))]
    base = _valuewhen_1(cond, counts)
    return [None if b is None else c - b for c, b in zip(counts, base)]


def _bands(closes: list[float]) -> list[tuple[float, float] | None]:
    out: list[tuple[float, float] | None] = []
    for i in range(len(closes)):
        if i < BB_LENGTH - 1:
            out.append(None)                  # ta.sma / ta.stdev are na before 20 bars
            continue
        w = closes[i - BB_LENGTH + 1:i + 1]
        mean = sum(w) / BB_LENGTH
        sd = math.sqrt(sum((x - mean) ** 2 for x in w) / BB_LENGTH)   # population
        out.append((mean + BB_MULT * sd, mean - BB_MULT * sd))
    return out


def closed_bars(bars: list[Bar]) -> list[Bar]:
    """Drop the forming bar. Only the LAST bar may be unclosed; an open bar in the
    middle means the input is garbled, and computing over it would be guessing."""
    for i, b in enumerate(bars[:-1]):
        if not b.closed:
            raise Td9Error(f"bar {i} (time {b.time}) is not closed but is not the last bar")
    if bars and not bars[-1].closed:
        return bars[:-1]
    return list(bars)


def marks(bars: list[Bar]) -> list[dict]:
    """Closed bars, oldest first -> the script's marks, oldest first.

    Each mark: {time, side: "up"|"down", count: 7|8|9, text, star: bool}.
    """
    bars = closed_bars(bars)
    for i in range(1, len(bars)):
        if bars[i].time <= bars[i - 1].time:
            raise Td9Error(f"bar times not strictly increasing at index {i}")
    closes = [b.close for b in bars]
    up = _relative(_setup_counts(closes, rising=True))
    down = _relative(_setup_counts(closes, rising=False))
    bands = _bands(closes)

    out: list[dict] = []
    for i, b in enumerate(bars):
        band = bands[i]
        for side, rel in (("up", up[i]), ("down", down[i])):
            if rel not in (7, 8, 9):
                continue
            if band is None:
                star = False                  # na band -> comparison false
            elif side == "up":
                star = b.close > band[0]
            else:
                star = b.close < band[1]
            out.append({"time": b.time, "side": side, "count": rel,
                        "text": TEXT[side][rel], "star": star})
    return out

"""Hand-drawn chart levels -> where the Bybit price sits relative to them.

Read-only prototype. The operator draws horizontal levels on a Vela chart
(chart/, served by chartserve.py); this module reads what was saved and says
which side of each level the price is on and how far. It places nothing and
alerts nobody.

What counts as a level, and why the rest is reported rather than dropped:

  * `hline` -- one anchor, only its price matters; spans all time.
  * `hray`  -- one anchor, extends RIGHT from anchors[0].time. Before that
    time the line does not exist on the chart, so it is not a level then.
    Vela anchors carry `time` in epoch MILLISECONDS (core/drawings/geometry.ts).
  * `visible: false` -- the operator hid it on the chart; a hidden line is
    one they chose to take out of play, so it is not live.
  * every other drawing type (trend lines, boxes, fibs...) is counted by type
    in `ignored`. A level the operator thinks they drew that silently fails
    to appear is the failure this guards against.

A malformed file raises ChartLevelsError. Zero levels is a legitimate answer
only when the file genuinely contains none; it must never be what a parse
error looks like.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

# Chart symbol -> where it is drawn and what it is compared against on Bybit.
# chartserve.py accepts only these; chart/src/main.js carries the same list.
# The venues differ on purpose: the chart gets free public bars from
# Binance/Hyperliquid, the price that matters is Bybit linear. Basis between
# venues is real and is why the report says so.
SYMBOLS = {
    "BTCUSDT": {"provider": "binance", "bybit": "BTCUSDT"},
    "HYPE": {"provider": "hyperliquid", "bybit": "HYPEUSDT"},
}

LEVEL_TYPES = ("hline", "hray")
DEFAULT_NEAR_BPS = 50.0
DEFAULT_STALE_DAYS = 7.0


class ChartLevelsError(ValueError):
    """The saved drawings could not be read as levels. Never means 'no levels'."""


@dataclass(frozen=True)
class Level:
    id: str
    type: str
    label: str
    price: float
    start_ms: int | None     # hray only: inactive before this time


@dataclass(frozen=True)
class Saved:
    provider: str
    symbol: str
    saved_at: float
    levels: list[Level]
    ignored: dict[str, int]  # drawing type -> count not treated as a level
    hidden: int              # level-type drawings the operator hid


@dataclass(frozen=True)
class Assessed:
    level: Level
    side: str                # "above" = price is above the level
    distance_bps: float      # abs, relative to the level price
    near: bool


def _fail(where: str, why: str):
    raise ChartLevelsError(f"{where}: {why}")


def _num(v, where: str) -> float:
    # bool is an int in Python; a `true` price is malformed, not 1.0.
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        _fail(where, f"expected a finite number, got {v!r}")
    return float(v)


def _label(d: dict, where: str) -> str:
    text = d.get("text")
    if text is None:
        return d["id"]
    if not isinstance(text, dict):
        _fail(where, f"text must be an object, got {type(text).__name__}")
    value = text.get("value")
    if not isinstance(value, str):
        _fail(where, f"text.value must be a string, got {value!r}")
    return value.strip() or d["id"]


def parse(raw: dict) -> Saved:
    """A chartserve.py file (already JSON-decoded) -> Saved. Raises on anything malformed."""
    if not isinstance(raw, dict):
        _fail("file", "top level must be an object")
    for key in ("provider", "symbol"):
        if not isinstance(raw.get(key), str):
            _fail("file", f"missing or non-string {key!r}")
    saved_at = _num(raw.get("saved_at"), "file.saved_at")
    doc = raw.get("doc")
    if not isinstance(doc, dict):
        _fail("file", "missing 'doc' object")
    if doc.get("version") != 1:
        _fail("doc", f"unsupported version {doc.get('version')!r} (this reader knows 1)")
    drawings = doc.get("drawings")
    if not isinstance(drawings, list):
        _fail("doc", "'drawings' must be a list")

    levels: list[Level] = []
    ignored: Counter = Counter()
    hidden = 0
    for i, d in enumerate(drawings):
        where = f"drawings[{i}]"
        if not isinstance(d, dict):
            _fail(where, "not an object")
        if not isinstance(d.get("id"), str) or not isinstance(d.get("type"), str):
            _fail(where, "missing string 'id' or 'type'")
        where = f"{where} ({d['id']})"
        if d["type"] not in LEVEL_TYPES:
            ignored[d["type"]] += 1
            continue
        visible = d.get("visible", True)
        if not isinstance(visible, bool):
            _fail(where, f"visible must be a bool, got {visible!r}")
        if not visible:
            hidden += 1
            continue
        anchors = d.get("anchors")
        if not isinstance(anchors, list) or not anchors or not isinstance(anchors[0], dict):
            _fail(where, "needs at least one anchor")
        price = _num(anchors[0].get("price"), f"{where}.anchors[0].price")
        if price <= 0:
            _fail(where, f"non-positive price {price}")
        start_ms = None
        if d["type"] == "hray":
            start_ms = int(_num(anchors[0].get("time"), f"{where}.anchors[0].time"))
        levels.append(Level(d["id"], d["type"], _label(d, where), price, start_ms))

    return Saved(raw["provider"], raw["symbol"], saved_at, levels, dict(ignored), hidden)


def load(path: str | Path) -> Saved:
    p = Path(path)
    try:
        raw = json.loads(p.read_text())
    except ValueError as e:
        raise ChartLevelsError(f"{p}: not valid JSON ({e})") from e
    try:
        return parse(raw)
    except ChartLevelsError as e:
        raise ChartLevelsError(f"{p}: {e}") from e


def active(levels: list[Level], now_ms: int) -> tuple[list[Level], list[Level]]:
    """(active, not_yet_started). An hray starts at its anchor; hlines are always on."""
    on, later = [], []
    for lv in levels:
        (later if lv.start_ms is not None and now_ms < lv.start_ms else on).append(lv)
    return on, later


def assess(levels: list[Level], price: float, near_bps: float = DEFAULT_NEAR_BPS) -> list[Assessed]:
    """Side and distance of `price` from each level, nearest first.

    Distance is measured against the level, so the same line reads the same
    whichever side price approaches from. `near` is inclusive: a price exactly
    `near_bps` away is near.
    """
    if not math.isfinite(price) or price <= 0:
        raise ChartLevelsError(f"price must be a positive number, got {price!r}")
    out = []
    for lv in levels:
        bps = abs(price - lv.price) / lv.price * 1e4
        side = "above" if price > lv.price else "below" if price < lv.price else "at"
        out.append(Assessed(lv, side, bps, bps <= near_bps))
    return sorted(out, key=lambda a: a.distance_bps)


def is_stale(saved_at: float, now: float, days: float = DEFAULT_STALE_DAYS) -> bool:
    """Levels nobody has touched for `days` may describe a market that has moved on.

    Strictly older than the window is stale; exactly `days` old is not yet.
    """
    return now - saved_at > days * 86400

"""Sweep-reclaim marks (exploratory, display only): the score must mean what the
legend says, on both sides, and a broken upstream must never look like "no events".

The expensive errors here are the quiet ones: the short side scored on un-negated
prices (RSI > 65 read as < 35, the wrong band), the regime bonus applied to a
symbol the BTC regime table was not written for, a mark on a bar that has not
closed, or an outage drawn as an empty chart. Detection itself is farseer's
(farseer.research.sweep) and is not re-tested here.

Needs farseer's source (FARSEER_SRC) and polars/numpy; without them the whole
module skips -- run it with farseer's interpreter.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import re
import threading
import urllib.error
import urllib.request
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

import chartserve
from ballast import chartbars, sweepmarks as SM, td9

try:
    SM._farseer()
except SM.SweepError as e:
    pytest.skip(f"farseer sweep detector unavailable: {e}", allow_module_level=True)

H = 3_600_000
T0 = 1_790_000_000_000          # 2026-09-21 UTC
LEG_START, RECLAIM = 300, 314   # bar indices in _synth()


def _synth(wick=False, mirror=False, n_tail=30, last_closed=True):
    """300 calm bars (range ~1.6, ATR ~1), an 8-bar drop of 12 (~10 ATR, far below
    the 7-day low, closes under the lower band, RSI deep oversold), then a climb
    that closes back above the leg's start on bar 314 -- tier 2 there.
    ``wick``: one bar 50 bars before the leg wicks to 80, under the leg's low, so
    the leg no longer sweeps the 7-day low. ``mirror``: prices reflected about 100,
    the same picture upside down (a short)."""
    path = [100 + 0.8 * math.sin(k * 0.7) for k in range(LEG_START)]
    p = path[-1]
    for _ in range(8):
        p -= 1.5
        path.append(p)
    for _ in range(8):
        p += 2.0
        path.append(p)
    path += [p] * n_tail
    bars = []
    for k, c in enumerate(path):
        o = path[k - 1] if k else c
        h, l = max(o, c) + 0.3, min(o, c) - 0.3
        if wick and k == 250:
            l = 80.0
        if mirror:
            o, h, l, c = 200 - o, 200 - l, 200 - h, 200 - c
        bars.append(td9.Bar(T0 + k * H, o, h, l, c, True))
    if not last_closed:
        b = bars[-1]
        bars[-1] = td9.Bar(b.time, b.open, b.high, b.low, b.close, False)
    return bars


def _tier2(events):
    return [e for e in events if e["tier"] == 2]


def _segs(regime):
    return [(T0 - 400 * H, None, regime)]


# ── the score ───────────────────────────────────────────────────────────────
def test_each_criterion_is_worth_what_the_legend_says():
    base = dict(with_trend=False, swept_7d=False, rsi_ext=50.0, bars_outside_bb=0, drop_atr=3.0)
    assert SM.score(**base) == 0
    assert SM.score(**{**base, "with_trend": True}) == 2
    assert SM.score(**{**base, "swept_7d": True}) == 1
    assert SM.score(**{**base, "rsi_ext": 34.9}) == 1
    assert SM.score(**{**base, "bars_outside_bb": 2}) == 1
    assert SM.score(**{**base, "drop_atr": 4.0}) == 1
    assert SM.score(True, True, 10.0, 5, 9.0) == 6


def test_thresholds_are_strict_where_the_legend_says_strict():
    """RSI < 35 (35 itself earns nothing), >= 2 bars outside, >= 4 ATR."""
    assert SM.score(False, False, 35.0, 1, 3.999) == 0
    assert SM.score(False, False, math.nan, 0, 0.0) == 0      # RSI undefined in warm-up: no point
    assert SM.score(None, False, 50.0, 0, 0.0) == 0           # no regime label: no trend bonus


@pytest.mark.parametrize("s,lvl", [(0, "low"), (2, "low"), (3, "mid"), (4, "mid"), (5, "high"), (6, "high")])
def test_levels(s, lvl):
    assert SM.level(s) == lvl


# ── events over bars ────────────────────────────────────────────────────────
def test_long_reclaim_is_one_tier2_event_on_the_reclaim_bar():
    ev = _tier2(SM.events(_synth(), H, None))
    assert len(ev) == 1
    e = ev[0]
    assert (e["time"] - T0) // H == RECLAIM and e["side"] == "long"
    # swept 7d + RSI + 2 bars under the band + >= 4 ATR, no regime table -> 4
    assert (e["score"], e["level"], e["with_trend"]) == (4, "mid", None)
    assert e["leg_ext_price"] < e["leg_start_price"] <= e["confirm_price"]
    assert e["stop_pct"] == pytest.approx(100 * (e["confirm_price"] - e["leg_ext_price"]) / e["confirm_price"])


def test_short_side_is_scored_on_the_mirrored_picture():
    """The same move upside down must score the same: RSI > 65, closes ABOVE the
    upper band, a 7-day HIGH swept. Scoring the short on raw prices would lose
    those points (or award them to the wrong side)."""
    long_ev = _tier2(SM.events(_synth(), H, None))[0]
    short = _tier2(SM.events(_synth(mirror=True), H, None))
    assert len(short) == 1
    e = short[0]
    assert (e["side"], e["time"], e["score"]) == ("short", long_ev["time"], long_ev["score"])
    assert e["leg_ext_price"] > e["leg_start_price"] >= e["confirm_price"]
    assert e["stop_pct"] > 0
    assert e["leg_ext_price"] == pytest.approx(200 - long_ev["leg_ext_price"])


def test_regime_table_adds_two_only_with_the_trend():
    none = _tier2(SM.events(_synth(), H, None))[0]
    up = _tier2(SM.events(_synth(), H, _segs("up")))[0]
    down = _tier2(SM.events(_synth(), H, _segs("down")))[0]
    assert (up["with_trend"], up["score"], up["level"]) == (True, none["score"] + 2, "high")
    assert (down["with_trend"], down["score"]) == (False, none["score"])
    short_down = _tier2(SM.events(_synth(mirror=True), H, _segs("down")))[0]
    assert (short_down["with_trend"], short_down["score"]) == (True, none["score"] + 2)


def test_regime_is_read_at_the_confirming_close():
    """A segment that starts exactly at the reclaim bar's close covers it; one that
    starts a bar later does not (the label at a time is start <= t < end)."""
    close = T0 + (RECLAIM + 1) * H
    at = _tier2(SM.events(_synth(), H, [(close, None, "up")]))[0]
    later = _tier2(SM.events(_synth(), H, [(close + 1, None, "up")]))[0]
    assert at["with_trend"] is True and later["with_trend"] is None


def test_a_deeper_low_inside_seven_days_costs_the_sweep_point():
    clean = _tier2(SM.events(_synth(), H, None))
    wick = [e for e in _tier2(SM.events(_synth(wick=True), H, None)) if (e["time"] - T0) // H == RECLAIM]
    assert len(wick) == 1 and wick[0]["score"] == clean[0]["score"] - 1


def test_seven_days_scale_with_the_timeframe():
    """On 4H bars, 7 days is 42 bars: the same 250-bar-old wick is far outside it,
    so the sweep point comes back."""
    bars = [td9.Bar(T0 + (b.time - T0) * 4, b.open, b.high, b.low, b.close, True) for b in _synth(wick=True)]
    ev = [e for e in _tier2(SM.events(bars, 4 * H, None)) if (e["time"] - T0) // (4 * H) == RECLAIM]
    assert len(ev) == 1 and ev[0]["score"] == _tier2(SM.events(_synth(), H, None))[0]["score"]


def test_too_few_bars_is_empty_not_an_error():
    assert SM.events(_synth()[:40], 7 * 24 * H, None) == []
    assert SM.events([], H, None) == []


# ── chartserve /api/sweep ───────────────────────────────────────────────────
EVENT_KEYS = {"time", "side", "tier", "score", "level", "with_trend", "leg_start_price",
              "leg_ext_price", "confirm_price", "stop_pct"}


def test_payload_shape_and_fetch_limit():
    seen = {}

    def fetch(symbol, tf, now_ms, limit=chartbars.DEFAULT_LIMIT):
        seen["limit"] = limit
        return _synth(last_closed=False)
    p = chartserve.sweep_payload("BTCUSDT", "60", 7, fetch=fetch)
    assert seen["limit"] == 1000 and chartbars.DEFAULT_LIMIT == 500     # 9TD keeps 500
    assert p["venue"] == "binance" and p["regime_table"] is True
    assert p["closed_bars"] == len(_synth()) - 1                       # forming bar dropped
    json.dumps(p)                                                      # serializable as-is
    assert p["events"] and all(set(e) == EVENT_KEYS for e in p["events"])
    e = _tier2(p["events"])[0]
    assert e["side"] in ("long", "short") and e["tier"] in (1, 2) and e["level"] in ("high", "mid", "low")
    assert isinstance(e["time"], int) and isinstance(e["score"], int)


def test_regime_table_is_btc_only():
    """The table's pivots are BTC prices; HYPE gets no trend verdict, not BTC's."""
    p = chartserve.sweep_payload("HYPE", "60", 7, fetch=lambda s, t, n, limit: _synth())
    assert p["regime_table"] is False
    assert p["events"] and all(e["with_trend"] is None for e in p["events"])


def test_a_reclaim_on_the_forming_bar_is_not_marked():
    bars = _synth(n_tail=0)                                            # reclaim bar is the last bar
    assert (bars[-1].time - T0) // H == RECLAIM + 1
    bars = bars[:-1]
    closed = chartserve.sweep_payload("HYPE", "60", 7, fetch=lambda s, t, n, limit: bars)
    assert [(e["time"] - T0) // H for e in _tier2(closed["events"])] == [RECLAIM]
    b = bars[-1]
    forming = bars[:-1] + [td9.Bar(b.time, b.open, b.high, b.low, b.close, False)]
    p = chartserve.sweep_payload("HYPE", "60", 7, fetch=lambda s, t, n, limit: forming)
    assert _tier2(p["events"]) == []


def test_upstream_failure_is_502_not_empty():
    def broken(symbol, tf, now_ms, limit):
        raise chartbars.ChartBarsError("binance: HTTP 418: teapot")
    with pytest.raises(chartserve.Rejected) as e:
        chartserve.sweep_payload("BTCUSDT", "60", 1, fetch=broken)
    assert e.value.status == 502 and "teapot" in e.value.reason
    with pytest.raises(chartserve.Rejected) as e:
        chartserve.sweep_payload("BTCUSDT", "60", 1, fetch=lambda s, t, n, limit: [td9.Bar(T0, 1, 1, 1, 1, False)])
    assert e.value.status == 502                                       # only a forming bar


def test_detector_unavailable_is_500_with_reason(monkeypatch):
    def gone():
        raise SM.SweepError("No module named 'polars'")
    monkeypatch.setattr(SM, "_farseer", gone)
    with pytest.raises(chartserve.Rejected) as e:
        chartserve.sweep_payload("BTCUSDT", "60", 1, fetch=lambda s, t, n, limit: _synth())
    assert e.value.status == 500 and "polars" in e.value.reason


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setattr(chartbars, "fetch", lambda symbol, tf, now_ms, **kw: _synth())

    class Hd(chartserve.Handler):
        def log_message(self, *a):
            pass
    Hd.store = tmp_path / "store"
    srv = ThreadingHTTPServer(("127.0.0.1", 0), partial(Hd, directory=str(tmp_path)))
    Hd.port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()
    srv.server_close()


def _get(srv, path, host=None):
    port = srv.server_address[1]
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers={"Host": host or f"127.0.0.1:{port}"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)


def test_api_sweep_over_http(server, tmp_path):
    status, body = _get(server, "/api/sweep?symbol=BTCUSDT&tf=60")
    assert status == 200 and [(e["time"] - T0) // H for e in _tier2(body["events"])] == [RECLAIM]
    assert _get(server, "/api/sweep?symbol=ETHUSDT&tf=60")[0] == 400
    assert _get(server, "/api/sweep?symbol=BTCUSDT&tf=M")[0] == 400
    assert _get(server, "/api/sweep?symbol=BTCUSDT&tf=60", host="evil.example:80")[0] == 403
    assert not (tmp_path / "store").exists()                           # nothing written


# ── the page ────────────────────────────────────────────────────────────────
def test_page_keeps_sweep_marks_out_of_chart_drawings_and_off_9td_shapes():
    """Like the 9TD marks: labels only, no way to reach the autosaved drawings,
    and a shape the 9TD marks never use."""
    root = Path(__file__).resolve().parent.parent / "chart" / "src"

    def code(name):
        return "\n".join(ln.split("//")[0] for ln in (root / name).read_text().splitlines())

    js = code("sweepmarks.js")
    assert re.search(r"\bchart\b", js) is None and re.search(r"\bdrawings\b", js) is None
    assert "emit({ labels:" in js and "style: 'diamond'" in js
    assert "diamond" not in code("td9marks.js")
    main_js = code("main.js")
    assert "setSweep(" in main_js and "addNativeIndicator(SWEEP_TYPE)" in main_js
    for line in main_js.splitlines():
        if "drawings" in line:
            assert "sweep" not in line.lower(), line

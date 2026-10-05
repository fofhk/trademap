"""9TD marks: a port of the operator's TradingView script that must agree with it.

These marks may later feed Ballast signals, so the expensive errors are the
ones where the port quietly disagrees with the chart the operator trusts: a
mark one bar early (counting the first 4 bars), a mark during the script's
warm-up (ignoring valuewhen's na), a "*" decided by the wrong stdev, a 🍅 on a
bar that has not closed and may not exist in a minute, or an upstream outage
served as "no marks today".
"""

from __future__ import annotations

import json
import re
import statistics
import threading
import urllib.error
import urllib.request
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

import chartserve
from ballast import chartbars, chartlevels, td9

H = 3_600_000
T0 = 1_790_000_000_000

# Flat, then two up-resets and two down-resets: after this, ta.valuewhen(..., 1)
# has its two occurrences on both sides and the next rising bar counts 1.
WARM = [100.0] * 4 + [101, 99, 101, 99]


def _bars(closes, last_closed=True):
    out = [td9.Bar(T0 + i * H, c, c + 1, c - 1, c, True) for i, c in enumerate(closes)]
    if not last_closed:
        b = out[-1]
        out[-1] = td9.Bar(b.time, b.open, b.high, b.low, b.close, False)
    return out


def _rising(n, start=102.0, step=1.0):
    return [start + k * step for k in range(n)]


def _summary(marks):
    """(bar index, side, count) -- index relative to T0 so a shift by one bar shows."""
    return [((m["time"] - T0) // H, m["side"], m["count"]) for m in marks]


# ── counting ────────────────────────────────────────────────────────────────
def test_nine_rising_closes_mark_seven_eight_nine_only():
    """The script plots nothing for counts 1-6; only 7, 8 and 9 are marks."""
    closes = WARM + _rising(9)
    m = td9.marks(_bars(closes))
    n = len(WARM)
    assert _summary(m) == [(n + 6, "up", 7), (n + 7, "up", 8), (n + 8, "up", 9)]
    assert [x["text"] for x in m] == ["7", "8", "🍅"]


def test_falling_closes_mark_below_with_the_sick_face():
    closes = [100.0] * 4 + [99, 101, 99, 101] + _rising(9, start=98, step=-1)
    m = td9.marks(_bars(closes))
    assert [(x["side"], x["count"], x["text"]) for x in m] == [
        ("down", 7, "7"), ("down", 8, "8"), ("down", 9, "🤢")]


def test_a_single_non_rising_bar_resets_the_count():
    """A close EQUAL to close[4] is not 'greater': the run is broken, the 9th bar
    gets no tomato, and counting restarts from 1."""
    run1 = _rising(8)                         # counts 1..8 (marks 7, 8)
    reset = [run1[-4]]                        # == close[4] -> 0
    run2 = _rising(9, start=110)              # each > its close[4] -> counts 1..9
    closes = WARM + run1 + reset + run2
    n = len(WARM)
    assert _summary(td9.marks(_bars(closes))) == [
        (n + 6, "up", 7), (n + 7, "up", 8),
        (n + 15, "up", 7), (n + 16, "up", 8), (n + 17, "up", 9)]


def test_counts_past_nine_are_not_marked():
    closes = WARM + _rising(13)
    assert [x["count"] for x in td9.marks(_bars(closes))] == [7, 8, 9]


def test_first_four_bars_never_count():
    """close[4] is na on bars 0-3 and na > x is false in Pine. Counting them would
    put every mark of the first run one or more bars early."""
    assert td9._setup_counts(_rising(10), rising=True) == [0, 0, 0, 0, 1, 2, 3, 4, 5, 6]
    assert td9._setup_counts(_rising(10, step=-1), rising=False) == [0, 0, 0, 0, 1, 2, 3, 4, 5, 6]


def test_valuewhen_warmup_gives_no_mark_until_two_resets():
    """TradingView shows nothing until TD has reset twice (valuewhen(.., 1) is na
    before that), even when the raw count passes 7, 8, 9."""
    assert td9.marks(_bars(_rising(20))) == []                 # never reset
    one_reset = [100.0] * 4 + [101, 99] + _rising(12)            # one reset only
    assert td9._relative(td9._setup_counts(one_reset, True))[-1] is None
    assert td9.marks(_bars(one_reset)) == []
    assert td9.marks(_bars(WARM + _rising(9)))                  # two resets: marks


def test_after_warmup_tdup_equals_td():
    """TD only falls by resetting to 0, so valuewhen returns 0 and TDUp == TD --
    but only once warmed up; before that it is na, not TD."""
    counts = td9._setup_counts(WARM + _rising(12), rising=True)
    rel = td9._relative(counts)
    first = next(i for i, r in enumerate(rel) if r is not None)
    assert all(r is None for r in rel[:first])
    assert rel[first:] == counts[first:]
    assert any(c > 0 for c in counts[:first])                   # TD was nonzero while TDUp was na


# ── the "*" ─────────────────────────────────────────────────────────────────
def _star_case(x, side):
    if side == "up":
        prefix = [100.0] * 12 + WARM + _rising(8, step=0.1)
    else:
        prefix = [100.0] * 12 + [100.0] * 4 + [99, 101, 99, 101] + _rising(8, start=98, step=-0.1)
    closes = prefix + [x]
    w = closes[-20:]
    mean = sum(w) / 20
    return closes, (mean + 2 * statistics.pstdev(w), mean + 2 * statistics.stdev(w),
                    mean - 2 * statistics.pstdev(w), mean - 2 * statistics.stdev(w))


def test_star_uses_population_stdev_up():
    """ta.stdev divides by N. Here the 9 closes beyond the population band but
    INSIDE the (wider) sample band: Pine prints "*", a sample-stdev port would not."""
    closes, (pop_up, samp_up, _, _) = _star_case(103.9, "up")
    assert pop_up < closes[-1] < samp_up                       # the case is decisive
    nine = td9.marks(_bars(closes))[-1]
    assert (nine["count"], nine["star"]) == (9, True)


def test_star_uses_population_stdev_down():
    closes, (_, _, pop_lo, samp_lo) = _star_case(96.05, "down")
    assert samp_lo < closes[-1] < pop_lo
    nine = td9.marks(_bars(closes))[-1]
    assert (nine["side"], nine["count"], nine["star"]) == ("down", 9, True)


def test_no_star_inside_the_band():
    closes, (pop_up, _, _, _) = _star_case(103.0, "up")
    assert closes[-1] < pop_up
    assert td9.marks(_bars(closes))[-1]["star"] is False


def test_no_star_before_twenty_bars():
    """The band is na for the first 19 bars; na comparisons are false, so even a
    huge 9th close gets no star there."""
    closes = WARM + _rising(8) + [1000.0]
    assert len(closes) < 20
    nine = td9.marks(_bars(closes))[-1]
    assert (nine["count"], nine["star"]) == (9, False)


# ── closed bars only ────────────────────────────────────────────────────────
def test_forming_bar_is_not_marked():
    """TradingView repaints the forming bar; we must not emit a mark that can vanish."""
    closes = WARM + _rising(7)                 # the 7th rise is the last bar
    assert [x["count"] for x in td9.marks(_bars(closes, last_closed=True))] == [7]
    assert td9.marks(_bars(closes, last_closed=False)) == []


def test_an_unclosed_bar_in_the_middle_is_an_error():
    bars = _bars(WARM + _rising(9))
    b = bars[3]
    bars[3] = td9.Bar(b.time, b.open, b.high, b.low, b.close, False)
    with pytest.raises(td9.Td9Error):
        td9.marks(bars)


# ── venue bars ──────────────────────────────────────────────────────────────
# Shapes as returned by the live endpoints (2026-10-05), trimmed.
BINANCE_FIXTURE = [
    [1791144000000, "85387.50", "86770.00", "85354.50", "86482.80", "32084.825", 1791158399999,
     "2765281882.10840", 600919, "16620.330", "1432374179.73000", "0"],
    [1791158400000, "86482.80", "86659.00", "86164.40", "86616.00", "6241.879", 1791172799999,
     "539303205.89100", 134070, "3040.635", "262775289.43000", "0"],
]
HL_FIXTURE = [
    {"t": 1791151200000, "T": 1791154799999, "s": "HYPE", "i": "1h", "o": "90.576", "c": "90.408",
     "h": "91.242", "l": "90.241", "v": "216680.32", "n": 12122},
    {"t": 1791154800000, "T": 1791158399999, "s": "HYPE", "i": "1h", "o": "90.408", "c": "90.573",
     "h": "90.65", "l": "90.335", "v": "102647.38", "n": 6905},
]


def test_binance_kline_parsing_and_closed_flag():
    now = 1791161777000                        # inside the 2nd bar
    bars = chartbars.parse_binance(BINANCE_FIXTURE, now)
    assert bars[0] == td9.Bar(1791144000000, 85387.5, 86770.0, 85354.5, 86482.8, True)
    assert bars[1].close == 86616.0 and bars[1].closed is False
    # closeTime is the bar's LAST ms: at exactly closeTime the bar is still forming.
    assert chartbars.parse_binance(BINANCE_FIXTURE, 1791172799999)[1].closed is False
    assert chartbars.parse_binance(BINANCE_FIXTURE, 1791172800000)[1].closed is True


def test_hyperliquid_candle_parsing_and_closed_flag():
    bars = chartbars.parse_hyperliquid(HL_FIXTURE, 1791158399999)
    assert bars[0] == td9.Bar(1791151200000, 90.576, 91.242, 90.241, 90.408, True)
    assert bars[1].closed is False
    assert chartbars.parse_hyperliquid(HL_FIXTURE, 1791158400000)[1].closed is True


@pytest.mark.parametrize("parse,bad", [
    (chartbars.parse_binance, {"code": -1121, "msg": "Invalid symbol."}),    # error body
    (chartbars.parse_binance, []),                                            # nothing
    (chartbars.parse_binance, [[1, "1", "1", "1", "x", "1", 2]]),             # bad price
    (chartbars.parse_binance, list(reversed(BINANCE_FIXTURE))),               # out of order
    (chartbars.parse_hyperliquid, []),
    (chartbars.parse_hyperliquid, [{"t": 1, "o": "1", "h": "1", "l": "1", "c": "1"}]),  # no T
    (chartbars.parse_hyperliquid, "rate limited"),
])
def test_bad_venue_responses_raise_rather_than_return_nothing(parse, bad):
    with pytest.raises(chartbars.ChartBarsError):
        parse(bad, 1791161777000)


def test_venues_follow_the_chart_not_bybit():
    """Marks must sit on the candles they were computed from."""
    assert set(chartbars.VENUE_SYMBOL) == set(chartlevels.SYMBOLS)
    assert {s: v["provider"] for s, v in chartlevels.SYMBOLS.items()} == {
        "BTCUSDT": "binance", "HYPE": "hyperliquid"}
    for venue in ("binance", "hyperliquid"):
        assert set(chartbars.INTERVALS[venue]) == set(chartbars.TIMEFRAMES)


# ── chartserve /api/marks ───────────────────────────────────────────────────
def test_marks_payload_upstream_failure_is_502():
    def broken(symbol, tf, now_ms):
        raise chartbars.ChartBarsError("binance: HTTP 418: teapot")
    with pytest.raises(chartserve.Rejected) as e:
        chartserve.marks_payload("BTCUSDT", "60", 1, fetch=broken)
    assert e.value.status == 502 and "teapot" in e.value.reason


def test_marks_payload_with_only_a_forming_bar_is_502_not_empty():
    def forming(symbol, tf, now_ms):
        return _bars([100.0], last_closed=False)
    with pytest.raises(chartserve.Rejected) as e:
        chartserve.marks_payload("BTCUSDT", "60", 1, fetch=forming)
    assert e.value.status == 502


def test_marks_payload_ok():
    p = chartserve.marks_payload("HYPE", "240", 5, fetch=lambda s, t, n: _bars(WARM + _rising(9), last_closed=False))
    assert p["venue"] == "hyperliquid" and p["closed_bars"] == len(WARM) + 8
    assert [m["count"] for m in p["marks"]] == [7, 8]          # the forming 9th is dropped


@pytest.fixture
def server(tmp_path, monkeypatch):
    """A real chartserve on an ephemeral loopback port, with a drawings file in its store."""
    store = tmp_path / "store"
    store.mkdir()
    rec = {"provider": "binance", "symbol": "BTCUSDT", "saved_at": 1_790_000_000,
           "doc": {"version": 1, "drawings": [
               {"id": "dw-1", "type": "hline", "paneId": "price",
                "anchors": [{"time": T0, "price": 60000.0}], "style": {},
                "locked": False, "visible": True, "zIndex": 1, "createdAt": T0}]}}
    (store / "BTCUSDT.json").write_text(json.dumps(rec, indent=2))
    calls = {"fail": False}

    def fake_fetch(symbol, tf, now_ms, **kw):
        if calls["fail"]:
            raise chartbars.ChartBarsError("hyperliquid: URLError: down")
        return _bars(WARM + _rising(9))
    monkeypatch.setattr(chartbars, "fetch", fake_fetch)

    class H(chartserve.Handler):
        def log_message(self, *a):
            pass
    H.store = store
    srv = ThreadingHTTPServer(("127.0.0.1", 0), partial(H, directory=str(tmp_path)))
    H.port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv, store, calls
    srv.shutdown()
    srv.server_close()


def _get(srv, path, host=None):
    port = srv.server_address[1]
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}",
                                 headers={"Host": host or f"127.0.0.1:{port}"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)


def test_api_marks_over_http(server):
    srv, _, _ = server
    status, body = _get(srv, "/api/marks?symbol=BTCUSDT&tf=240")
    assert status == 200 and [m["count"] for m in body["marks"]] == [7, 8, 9]


@pytest.mark.parametrize("query", ["symbol=ETHUSDT&tf=60", "symbol=BTCUSDT&tf=1", "symbol=BTCUSDT",
                                   "tf=60", "symbol=btcusdt&tf=60", "symbol=BTCUSDT&tf=M"])
def test_api_marks_rejects_unknown_symbol_or_tf(server, query):
    srv, _, _ = server
    status, body = _get(srv, f"/api/marks?{query}")
    assert status == 400 and "error" in body


def test_api_marks_checks_host(server):
    srv, _, _ = server
    status, _ = _get(srv, "/api/marks?symbol=BTCUSDT&tf=60", host="evil.example:80")
    assert status == 403


def test_api_marks_upstream_failure_is_502_with_reason(server):
    srv, _, calls = server
    calls["fail"] = True
    status, body = _get(srv, "/api/marks?symbol=HYPE&tf=60")
    assert status == 502 and "down" in body["error"]


def test_marks_never_touch_the_levels_file(server):
    """Marks are computed per request and returned, never stored: the drawings
    file (the operator's levels, which chartlevels.py reads) must be byte-for-byte
    unchanged, and no other file may appear in the store."""
    srv, store, _ = server
    before = (store / "BTCUSDT.json").read_bytes()
    levels_before = chartlevels.load(store / "BTCUSDT.json")
    for tf in chartbars.TIMEFRAMES:
        assert _get(srv, f"/api/marks?symbol=BTCUSDT&tf={tf}")[0] == 200
        assert _get(srv, f"/api/marks?symbol=HYPE&tf={tf}")[0] == 200
    assert (store / "BTCUSDT.json").read_bytes() == before
    assert sorted(p.name for p in store.iterdir()) == ["BTCUSDT.json"]
    assert chartlevels.load(store / "BTCUSDT.json") == levels_before
    assert _get(srv, "/api/drawings?symbol=BTCUSDT")[1] == json.loads(before)["doc"]


def test_page_keeps_marks_out_of_chart_drawings():
    """chart.drawings is autosaved to the operator's levels file, which
    chartlevels.py reads. The marks module must have no way to reach it: it only
    emits indicator labels, and its code never names the chart or its drawings.
    main.js must hand marks to setMarks, and its drawings calls must not see them."""
    root = Path(__file__).resolve().parent.parent / "chart" / "src"

    def code(name):
        lines = (root / name).read_text().splitlines()
        return "\n".join(ln.split("//")[0] for ln in lines)

    marks_js = code("td9marks.js")
    assert re.search(r"\bchart\b", marks_js) is None
    assert re.search(r"\bdrawings\b", marks_js) is None
    assert "emit({ labels:" in marks_js
    main_js = code("main.js")
    assert "setMarks(" in main_js
    for line in main_js.splitlines():
        if "drawings" in line:
            assert "marks" not in line.lower(), line

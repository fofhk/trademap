"""Hand-drawn levels: what counts as a live level, and what the server will store.

The operator reads this report to decide whether price is near a line they
care about. The expensive errors are all silent ones: a hidden line reported
as live, a trend line that vanished from the count, a parse failure that looks
like "no levels", or a page on some other site writing into the drawings.
"""

from __future__ import annotations

import json

import pytest

import chartserve
from ballast import chartlevels as cl

T0 = 1790000000000        # an anchor time, epoch ms (Vela's unit)
DAY = 86400


def _d(id_, type_="hline", price=60000.0, time=T0, visible=True, text=None):
    d = {"id": id_, "type": type_, "paneId": "price",
         "anchors": [{"time": time, "price": price}],
         "style": {}, "locked": False, "visible": visible, "zIndex": 1, "createdAt": T0}
    if text is not None:
        d["text"] = {"value": text, "size": "normal", "hAlign": "left", "vAlign": "center"}
    return d


def _file(*drawings, saved_at=1_790_000_000):
    return {"provider": "binance", "symbol": "BTCUSDT", "saved_at": saved_at,
            "doc": {"version": 1, "drawings": list(drawings)}}


# ── what counts as a level ──────────────────────────────────────────────────
def test_a_hidden_line_is_not_a_live_level():
    """Hiding a line on the chart is how the operator takes it out of play.
    Reporting it as NEAR would be an alert about a level they retired."""
    s = cl.parse(_file(_d("dw-1", price=60000), _d("dw-2", price=60010, visible=False)))
    assert [lv.id for lv in s.levels] == ["dw-1"]
    assert s.hidden == 1


def test_non_horizontal_drawings_are_counted_as_ignored_not_dropped():
    """If the operator draws a trend line meaning it as a level, the report must
    show that something was skipped -- otherwise the missing level is invisible."""
    s = cl.parse(_file(_d("dw-1"), _d("dw-2", "trendline"), _d("dw-3", "trendline"),
                       _d("dw-4", "box")))
    assert len(s.levels) == 1
    assert s.ignored == {"trendline": 2, "box": 1}


def test_text_is_the_label_and_id_is_the_fallback():
    s = cl.parse(_file(_d("dw-1", text=" weekly high "), _d("dw-2"), _d("dw-3", text="  ")))
    assert [lv.label for lv in s.levels] == ["weekly high", "dw-2", "dw-3"]


def test_an_hray_does_not_exist_before_its_anchor_time():
    """A horizontal ray starts at its anchor and extends right; before that
    moment there is no line on the chart, so there is nothing to be near."""
    s = cl.parse(_file(_d("ray", "hray", time=T0), _d("line", "hline")))
    on, later = cl.active(s.levels, T0 - 1)
    assert [lv.id for lv in on] == ["line"] and [lv.id for lv in later] == ["ray"]
    on, later = cl.active(s.levels, T0)          # inclusive at the anchor
    assert {lv.id for lv in on} == {"ray", "line"} and later == []


def test_hray_time_is_milliseconds():
    """Vela anchors are epoch ms. Reading them as seconds would put every ray
    ~55,000 years in the future, i.e. silently never active."""
    s = cl.parse(_file(_d("ray", "hray", time=T0)))
    now_ms = (T0 // 1000 + DAY) * 1000           # one day after the anchor
    on, _ = cl.active(s.levels, now_ms)
    assert [lv.id for lv in on] == ["ray"]


# ── malformed input fails loud ──────────────────────────────────────────────
@pytest.mark.parametrize("bad", [
    {"provider": "binance", "symbol": "BTCUSDT", "saved_at": 1, "doc": {"version": 2, "drawings": []}},
    {"provider": "binance", "symbol": "BTCUSDT", "saved_at": 1, "doc": {"version": 1}},
    {"provider": "binance", "symbol": "BTCUSDT", "doc": {"version": 1, "drawings": []}},
    {"symbol": "BTCUSDT", "saved_at": 1, "doc": {"version": 1, "drawings": []}},
    _file({"type": "hline"}),                                    # no id
    _file({**_d("dw-1"), "anchors": []}),                        # no anchor
    _file({**_d("dw-1"), "anchors": [{"time": T0, "price": "60000"}]}),
    _file({**_d("dw-1"), "anchors": [{"time": T0, "price": True}]}),
    _file({**_d("dw-1"), "visible": "no"}),
    _file({**_d("dw-1", "hray"), "anchors": [{"price": 60000}]}),  # hray without time
    [],
])
def test_a_malformed_file_raises_instead_of_reading_as_no_levels(bad):
    """Zero levels is a valid answer; a parse error must never look like one."""
    with pytest.raises(cl.ChartLevelsError):
        cl.parse(bad)


def test_load_names_the_file_on_bad_json(tmp_path):
    p = tmp_path / "BTCUSDT.json"
    p.write_text("{not json")
    with pytest.raises(cl.ChartLevelsError, match="BTCUSDT.json"):
        cl.load(p)


# ── assess ──────────────────────────────────────────────────────────────────
def test_side_is_where_price_sits_and_rows_are_nearest_first():
    s = cl.parse(_file(_d("support", price=58000), _d("resist", price=61000),
                       _d("far", price=70000)))
    rows = cl.assess(s.levels, 60000, near_bps=50)
    assert [(a.level.id, a.side) for a in rows] == [
        ("resist", "below"), ("support", "above"), ("far", "below")]
    assert rows[0].distance_bps == pytest.approx(1000 / 61000 * 1e4)


def test_near_threshold_is_inclusive_at_the_boundary():
    """50 bps of 60000 is exactly 300. The line is drawn by hand; at the stated
    threshold the operator wants to hear about it, not miss it by a rounding."""
    lv = cl.parse(_file(_d("x", price=60000))).levels
    assert cl.assess(lv, 60300, near_bps=50)[0].near is True
    assert cl.assess(lv, 60300.01, near_bps=50)[0].near is False
    assert cl.assess(lv, 59700, near_bps=50)[0].near is True


def test_a_nonsense_price_is_refused():
    lv = cl.parse(_file(_d("x"))).levels
    for p in (0, -1, float("nan")):
        with pytest.raises(cl.ChartLevelsError):
            cl.assess(lv, p)


# ── staleness ───────────────────────────────────────────────────────────────
def test_staleness_flips_just_past_the_window():
    """Levels untouched for a week may describe a market that has moved on;
    the report says so rather than presenting them as current."""
    saved = 1_790_000_000
    assert cl.is_stale(saved, saved + 7 * DAY, days=7) is False
    assert cl.is_stale(saved, saved + 7 * DAY + 1, days=7) is True


# ── chartserve validation ───────────────────────────────────────────────────
def _post(symbol="BTCUSDT", provider="binance", doc=None):
    return json.dumps({"provider": provider, "symbol": symbol,
                       "doc": doc if doc is not None else {"version": 1, "drawings": []}}).encode()


def test_server_stores_only_configured_symbols():
    """The symbol becomes a filename; anything outside the list -- traversal,
    lowercase, a symbol chartlevels cannot map to Bybit -- is refused."""
    assert chartserve.validate_post(_post())["symbol"] == "BTCUSDT"
    for bad in ("../x", "btcusdt", "ETHUSDT", "A", "", None, 5):
        with pytest.raises(chartserve.Rejected) as e:
            chartserve.validate_post(_post(symbol=bad))
        assert e.value.status == 400


def test_server_refuses_a_symbol_filed_under_the_wrong_venue():
    with pytest.raises(chartserve.Rejected):
        chartserve.validate_post(_post(symbol="HYPE", provider="binance"))


@pytest.mark.parametrize("doc", [{"version": 2, "drawings": []}, {"version": 1},
                                 {"version": 1, "drawings": {}}, []])
def test_server_refuses_a_doc_chartlevels_could_not_read(doc):
    with pytest.raises(chartserve.Rejected):
        chartserve.validate_post(_post(doc=doc))


def test_server_refuses_oversized_bodies_before_reading_them():
    assert chartserve.check_length(str(chartserve.MAX_BODY)) == chartserve.MAX_BODY
    for bad in (str(chartserve.MAX_BODY + 1), None, "abc", "-1"):
        with pytest.raises(chartserve.Rejected):
            chartserve.check_length(bad)
    big = _post(doc={"version": 1, "drawings": [{"pad": "x" * chartserve.MAX_BODY}]})
    with pytest.raises(chartserve.Rejected) as e:
        chartserve.validate_post(big)
    assert e.value.status == 413


def test_server_refuses_foreign_origin_and_host():
    """Loopback binding does not stop a page in the same browser. A cross-site
    POST carries its own Origin; a DNS-rebinding page carries its own Host."""
    port = 8765
    for ok in ("http://127.0.0.1:8765", "http://localhost:8765"):
        chartserve.check_origin(ok, port)
    for bad in ("https://evil.example", "http://127.0.0.1:9999", "null", None,
                "http://127.0.0.1:8765.evil.example"):
        with pytest.raises(chartserve.Rejected):
            chartserve.check_origin(bad, port)
    chartserve.check_host("127.0.0.1:8765", port)
    for bad in ("evil.example:8765", "127.0.0.1", "127.0.0.1:9999", None):
        with pytest.raises(chartserve.Rejected):
            chartserve.check_host(bad, port)


def test_saved_file_round_trips_into_levels(tmp_path):
    """What the server writes is exactly what chartlevels reads."""
    rec = chartserve.validate_post(_post(doc={"version": 1, "drawings": [_d("dw-1", text="pivot")]}))
    path = chartserve.save(tmp_path, rec)
    assert not list(tmp_path.glob("*.tmp"))
    s = cl.load(path)
    assert (s.symbol, s.provider, [lv.label for lv in s.levels]) == ("BTCUSDT", "binance", ["pivot"])
    assert chartserve.load_doc(tmp_path, "BTCUSDT") == rec["doc"]
    assert chartserve.load_doc(tmp_path, "HYPE") == {"version": 1, "drawings": []}


def test_server_refuses_to_bind_off_loopback(monkeypatch):
    """The POST endpoint writes files; it must not be reachable from the LAN.
    Refused before any socket exists."""
    def no_socket(*a, **k):
        raise AssertionError("a server was constructed for a non-loopback bind")
    monkeypatch.setattr(chartserve, "ThreadingHTTPServer", no_socket)
    assert chartserve.main(["--bind", "0.0.0.0"]) == 2

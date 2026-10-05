#!/usr/bin/env python3
"""Serve the levels chart (chart/dist/) and store what is drawn on it. Local only.

Read-only prototype: this process holds no key and talks to no exchange. It
exists so a browser page can hand its drawings to chartlevels.py.

    (cd chart && npm ci --ignore-scripts && npm run build)
    python3 chartserve.py                 # http://127.0.0.1:8765/
    python3 chartlevels.py                # read what was drawn

Binds loopback only and refuses anything else: the POST endpoint writes files,
and nothing on the network should be able to do that. Loopback alone is not
enough against a page in the same browser, so every request must also carry a
Host of 127.0.0.1/localhost on this port (a DNS-rebinding page arrives with its
own hostname), and every POST an Origin of the same (a cross-site form or fetch
carries the attacker's origin).

GET /api/marks?symbol=&tf= serves the 9TD marks (ballast/td9.py) over public,
unauthenticated venue bars (ballast/chartbars.py). It is the only outbound call
this process makes, and it carries no credentials. Marks are computed fresh on
every request and never written anywhere -- in particular not into the
drawings store, which is the operator's levels and nothing else.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.parse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from ballast import chartbars, td9
from ballast.chartlevels import SYMBOLS

HERE = Path(__file__).resolve().parent
DIST = HERE / "chart" / "dist"
DEFAULT_STORE = Path("data") / "chart_drawings"
DEFAULT_PORT = 8765
LOOPBACK = ("127.0.0.1", "localhost")
MAX_BODY = 256 * 1024
SYMBOL_RE = re.compile(r"^[A-Z0-9]{2,20}$")
EMPTY_DOC = {"version": 1, "drawings": []}


class Rejected(Exception):
    def __init__(self, status: int, reason: str):
        super().__init__(reason)
        self.status = status
        self.reason = reason


# ── validation (pure; the handler only wires these to HTTP) ─────────────────
def check_host(host: str | None, port: int) -> None:
    if host not in {f"{h}:{port}" for h in LOOPBACK}:
        raise Rejected(403, f"bad Host {host!r}")


def check_origin(origin: str | None, port: int) -> None:
    # A browser always sends Origin on POST, so a missing one is not a browser
    # we served; fail closed rather than guess.
    if origin not in {f"http://{h}:{port}" for h in LOOPBACK}:
        raise Rejected(403, f"bad Origin {origin!r}")


def check_symbol(symbol) -> str:
    if not isinstance(symbol, str) or not SYMBOL_RE.match(symbol) or symbol not in SYMBOLS:
        raise Rejected(400, f"symbol {symbol!r} not in {sorted(SYMBOLS)}")
    return symbol


def check_tf(tf) -> str:
    if not isinstance(tf, str) or tf not in chartbars.TIMEFRAMES:
        raise Rejected(400, f"tf {tf!r} not in {list(chartbars.TIMEFRAMES)}")
    return tf


def marks_payload(symbol: str, tf: str, now_ms: int, fetch=None) -> dict:
    """Fetch bars and run td9. An upstream or data failure is a 502 with the
    reason: an empty `marks` list must only ever mean the script marked nothing."""
    try:
        bars = td9.closed_bars((fetch or chartbars.fetch)(symbol, tf, now_ms))
        marks = td9.marks(bars)
    except (chartbars.ChartBarsError, td9.Td9Error) as e:
        raise Rejected(502, f"marks unavailable for {symbol} {tf}: {e}") from None
    if not bars:
        raise Rejected(502, f"marks unavailable for {symbol} {tf}: no closed bars")
    return {"symbol": symbol, "tf": tf, "venue": SYMBOLS[symbol]["provider"],
            "computed_at": now_ms, "closed_bars": len(bars),
            "last_closed": bars[-1].time, "marks": marks}


def check_length(content_length: str | None) -> int:
    """Decided from the header, before a byte of the body is read."""
    try:
        n = int(content_length)
    except (TypeError, ValueError):
        raise Rejected(411, "Content-Length required") from None
    if n < 0 or n > MAX_BODY:
        raise Rejected(413, f"body {n} bytes, limit {MAX_BODY}")
    return n


def validate_post(body: bytes) -> dict:
    """POST body -> the record to store. Raises Rejected with the reason."""
    if len(body) > MAX_BODY:
        raise Rejected(413, f"body {len(body)} bytes, limit {MAX_BODY}")
    try:
        req = json.loads(body)
    except ValueError as e:
        raise Rejected(400, f"invalid JSON: {e}") from None
    if not isinstance(req, dict):
        raise Rejected(400, "body must be a JSON object")
    symbol = check_symbol(req.get("symbol"))
    provider = SYMBOLS[symbol]["provider"]
    if req.get("provider") != provider:
        raise Rejected(400, f"{symbol} is drawn on {provider!r}, got {req.get('provider')!r}")
    doc = req.get("doc")
    if not isinstance(doc, dict) or doc.get("version") != 1:
        raise Rejected(400, "doc.version must be 1")
    if not isinstance(doc.get("drawings"), list):
        raise Rejected(400, "doc.drawings must be a list")
    return {"provider": provider, "symbol": symbol, "saved_at": int(time.time()), "doc": doc}


def save(store: Path, record: dict) -> Path:
    store.mkdir(parents=True, exist_ok=True)
    path = store / f"{record['symbol']}.json"
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(record, indent=2, ensure_ascii=False))
    os.replace(tmp, path)  # chartlevels.py never sees half a file
    return path


def load_doc(store: Path, symbol: str) -> dict:
    path = store / f"{symbol}.json"
    if not path.exists():
        return EMPTY_DOC
    return json.loads(path.read_text())["doc"]


# ── HTTP ────────────────────────────────────────────────────────────────────
class Handler(SimpleHTTPRequestHandler):
    store: Path
    port: int

    def _json(self, status: int, obj) -> None:
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _reject(self, r: Rejected) -> None:
        self.log_message("rejected %s %s: %s", self.command, self.path, r.reason)
        self._json(r.status, {"error": r.reason})

    def do_GET(self):
        try:
            check_host(self.headers.get("Host"), self.port)
            url = urllib.parse.urlsplit(self.path)
            if url.path == "/api/marks":
                q = urllib.parse.parse_qs(url.query)
                symbol = check_symbol((q.get("symbol") or [None])[0])
                tf = check_tf((q.get("tf") or [None])[0])
                try:
                    payload = marks_payload(symbol, tf, int(time.time() * 1000))
                except Rejected:
                    raise
                except Exception as e:
                    # A bug here must not fall through to the drawings error below
                    # and be reported as a corrupt levels file.
                    raise Rejected(500, f"marks failed: {type(e).__name__}: {e}") from None
                return self._json(200, payload)
            if url.path == "/api/drawings":
                q = urllib.parse.parse_qs(url.query)
                symbol = check_symbol((q.get("symbol") or [None])[0])
                return self._json(200, load_doc(self.store, symbol))
        except Rejected as r:
            return self._reject(r)
        except (OSError, ValueError, KeyError, TypeError) as e:
            # A corrupt file must not load as an empty chart: the page would
            # then save that emptiness over it. The page disables saving on 5xx.
            return self._reject(Rejected(500, f"stored drawings unreadable: {type(e).__name__}: {e}"))
        return super().do_GET()

    def do_HEAD(self):
        try:
            check_host(self.headers.get("Host"), self.port)
        except Rejected as r:
            return self._reject(r)
        return super().do_HEAD()

    def do_POST(self):
        try:
            check_host(self.headers.get("Host"), self.port)
            check_origin(self.headers.get("Origin"), self.port)
            if urllib.parse.urlsplit(self.path).path != "/api/drawings":
                raise Rejected(404, "no such endpoint")
            n = check_length(self.headers.get("Content-Length"))
            record = validate_post(self.rfile.read(n))
        except Rejected as r:
            return self._reject(r)
        path = save(self.store, record)
        self.log_message("saved %s: %d drawings", path, len(record["doc"]["drawings"]))
        self._json(200, {"ok": True, "saved_at": record["saved_at"]})


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--bind", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--store", type=Path, default=DEFAULT_STORE,
                    help=f"where drawings are written (default {DEFAULT_STORE})")
    args = ap.parse_args(argv)

    if args.bind != "127.0.0.1":
        print(f"refusing to bind {args.bind!r}: loopback 127.0.0.1 only", file=sys.stderr)
        return 2
    if not (DIST / "index.html").exists():
        print(f"{DIST} not built: (cd chart && npm ci --ignore-scripts && npm run build)",
              file=sys.stderr)
        return 2

    Handler.store = args.store.resolve()
    Handler.port = args.port
    server = ThreadingHTTPServer((args.bind, args.port), partial(Handler, directory=str(DIST)))
    print(f"serving http://127.0.0.1:{args.port}/  drawings -> {Handler.store}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())

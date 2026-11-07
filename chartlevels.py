#!/usr/bin/env python3
"""Where is the Bybit price relative to the levels drawn by hand? Places nothing.

    python3 chartlevels.py                       # every saved symbol
    python3 chartlevels.py --symbol HYPE
    python3 chartlevels.py --price 60000 --symbol BTCUSDT   # offline

Levels come from chartserve.py (data/chart_drawings/<SYMBOL>.json); the price
is Bybit's public linear ticker, read without a key. The chart and Bybit are
different venues, so a level drawn a few bps from price may be on the other
side of it here -- the report says which venue each number came from.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from trademap import chartlevels as cl

DEFAULT_STORE = Path("data") / "chart_drawings"
TICKER = "https://api.bybit.com/v5/market/tickers"


def bybit_last(symbol: str, timeout: float = 10.0) -> float:
    """Public ticker, no credentials. BybitClient signs every request, and a
    level reader has no business holding a key that can trade."""
    url = f"{TICKER}?" + urllib.parse.urlencode({"category": "linear", "symbol": symbol})
    with urllib.request.urlopen(url, timeout=timeout) as r:
        body = json.load(r)
    if body.get("retCode") != 0:
        raise RuntimeError(f"Bybit {symbol}: {body.get('retCode')} {body.get('retMsg')}")
    return float(body["result"]["list"][0]["lastPrice"])


def report(saved: cl.Saved, price: float, price_src: str, near_bps: float,
           stale_days: float, now: float) -> str:
    on, later = cl.active(saved.levels, int(now * 1000))
    rows = cl.assess(on, price, near_bps)
    age_d = (now - saved.saved_at) / 86400
    saved_txt = datetime.fromtimestamp(saved.saved_at, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    out = [
        f"== {saved.symbol}  levels drawn on {saved.provider}, price from {price_src}: {price:,.6g}",
        f"   saved {saved_txt} ({age_d:.1f} d ago)",
    ]
    if cl.is_stale(saved.saved_at, now, stale_days):
        out.append(f"   STALE: levels not touched for more than {stale_days:g} days")
    if rows:
        w = max(len("level"), *(len(a.level.label) for a in rows))
        out.append(f"   {'level':<{w}}  {'price':>12}  {'side':<5}  {'bps':>8}")
        for a in rows:
            flag = "  NEAR" if a.near else ""
            out.append(f"   {a.level.label:<{w}}  {a.level.price:>12,.6g}  {a.side:<5}  "
                       f"{a.distance_bps:>8.1f}{flag}")
    else:
        out.append("   no active levels")
    for lv in later:
        start = datetime.fromtimestamp(lv.start_ms / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        out.append(f"   not yet active: {lv.label} @ {lv.price:,.6g} (hray starts {start})")
    if saved.hidden:
        out.append(f"   hidden on chart, not counted: {saved.hidden}")
    if saved.ignored:
        kinds = ", ".join(f"{t}×{n}" for t, n in sorted(saved.ignored.items()))
        out.append(f"   ignored (not hline/hray): {kinds}")
    out.append(f"   side = price relative to level; NEAR = within {near_bps:g} bps")
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--symbol", choices=sorted(cl.SYMBOLS))
    ap.add_argument("--store", type=Path, default=DEFAULT_STORE)
    ap.add_argument("--near-bps", type=float, default=cl.DEFAULT_NEAR_BPS)
    ap.add_argument("--stale-days", type=float, default=cl.DEFAULT_STALE_DAYS)
    ap.add_argument("--price", type=float, help="use this price instead of Bybit (offline)")
    args = ap.parse_args(argv)

    if args.symbol:
        paths = [args.store / f"{args.symbol}.json"]
    else:
        paths = sorted(args.store.glob("*.json"))
        if not paths:
            print(f"no saved drawings in {args.store} -- draw some via chartserve.py", file=sys.stderr)
            return 1
    if args.price is not None and len(paths) > 1:
        # One override price applied to BTC and HYPE alike would be nonsense.
        print("--price needs --symbol when more than one symbol is saved", file=sys.stderr)
        return 2

    now = time.time()
    failed = False
    for path in paths:
        try:
            saved = cl.load(path)
            if saved.symbol not in cl.SYMBOLS:
                raise cl.ChartLevelsError(f"{path}: unknown symbol {saved.symbol!r}")
            bybit = cl.SYMBOLS[saved.symbol]["bybit"]
            if args.price is not None:
                price, src = args.price, "--price"
            else:
                price, src = bybit_last(bybit), f"Bybit linear {bybit}"
            print(report(saved, price, src, args.near_bps, args.stale_days, now))
        except (OSError, cl.ChartLevelsError, RuntimeError, KeyError, ValueError) as e:
            # One bad file must not hide the others, nor pass for "no levels".
            print(f"== {path.name}: FAILED {type(e).__name__}: {e}", file=sys.stderr)
            failed = True
        print()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

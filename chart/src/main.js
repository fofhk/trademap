// Hand-drawn levels -> chartserve.py -> chartlevels.py. Read-only prototype:
// nothing here or downstream places an order.
import { Vela } from '@luxalgo/vela';
import { BinanceProvider } from '@luxalgo/vela/providers/binance';
import { HyperliquidProvider } from '@luxalgo/vela/providers/hyperliquid';

// Must match ballast/chartlevels.py SYMBOLS -- the server rejects anything else,
// so a drift here shows up as a failed save, not as levels filed under the
// wrong name.
const SYMBOLS = [
    { symbol: 'BTCUSDT', provider: 'binance', vela: 'binance:BTCUSDT.P' },
    { symbol: 'HYPE', provider: 'hyperliquid', vela: 'hyperliquid:HYPE' },
];
const SAVE_DEBOUNCE_MS = 1000;

const select = document.getElementById('symbol');
const statusEl = document.getElementById('status');
for (const s of SYMBOLS) select.add(new Option(`${s.symbol} (${s.provider})`, s.symbol));

function status(text, isError = false) {
    statusEl.textContent = text;
    statusEl.classList.toggle('err', isError);
}

let current = SYMBOLS[0];
// Saving stays off until this symbol's saved drawings are on the chart. Without
// it, the empty chart at startup (or a failed GET) would be POSTed and overwrite
// the operator's levels with nothing.
let loaded = false;
let lastSaved = null;
let timer = null;

const chart = new Vela('#chart', { symbol: current.vela, timeframe: '60', live: true, theme: 'dark' });
chart.data.registerProvider('binance', new BinanceProvider());
chart.data.registerProvider('hyperliquid', new HyperliquidProvider());

async function loadDrawings(sym) {
    const r = await fetch(`/api/drawings?symbol=${encodeURIComponent(sym.symbol)}`);
    if (!r.ok) throw new Error(`GET ${r.status}: ${await r.text()}`);
    chart.drawings.fromJSON(await r.json());
    lastSaved = JSON.stringify(chart.drawings.toJSON());
}

async function flush() {
    clearTimeout(timer);
    timer = null;
    if (!loaded) return;
    const sym = current;
    const doc = chart.drawings.toJSON();
    const text = JSON.stringify(doc);
    if (text === lastSaved) return;
    // The DrawingsDocument carries no symbol, so the envelope names it.
    const r = await fetch('/api/drawings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ provider: sym.provider, symbol: sym.symbol, doc }),
    });
    if (!r.ok) throw new Error(`POST ${r.status}: ${await r.text()}`);
    lastSaved = text;
    status(`${sym.symbol}: ${doc.drawings.length} drawings saved ${new Date().toLocaleTimeString()}`);
}

function scheduleSave() {
    if (!loaded) return;
    clearTimeout(timer);
    timer = setTimeout(() => flush().catch((e) => status(`save failed — ${e.message}`, true)), SAVE_DEBOUNCE_MS);
}

chart.on('drawing:created', scheduleSave);
chart.on('drawing:edited', scheduleSave);
chart.on('drawing:removed', scheduleSave);
// Undo/redo (and a few store-level replacements) change the drawings without
// emitting any chart event in Vela 0.8.1, and the store's own onChange is not
// public. A cheap comparison against the last saved text catches those.
setInterval(() => {
    if (loaded && timer === null && JSON.stringify(chart.drawings.toJSON()) !== lastSaved) scheduleSave();
}, 2000);
// Closing the tab inside the debounce window would otherwise lose the last edit.
window.addEventListener('pagehide', () => {
    if (!loaded) return;
    const doc = chart.drawings.toJSON();
    if (JSON.stringify(doc) === lastSaved) return;
    fetch('/api/drawings', {
        method: 'POST',
        keepalive: true,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ provider: current.provider, symbol: current.symbol, doc }),
    });
});

select.addEventListener('change', async () => {
    const next = SYMBOLS.find((s) => s.symbol === select.value);
    select.disabled = true;
    try {
        // Save the old symbol's drawings under the old name BEFORE swapping.
        await flush();
    } catch (e) {
        select.value = current.symbol;
        select.disabled = false;
        status(`not switching: save failed — ${e.message}`, true);
        return;
    }
    loaded = false;
    current = next;
    status(`loading ${next.symbol}…`);
    try {
        await loadDrawings(next);
        loaded = true;
        status(`${next.symbol}: ${chart.drawings.all().length} drawings loaded`);
    } catch (e) {
        chart.drawings.fromJSON({ version: 1, drawings: [] });
        status(`load failed, saving disabled — ${e.message}`, true);
    }
    select.disabled = false;
    await chart.setMarket({ symbol: next.vela });
});

loadDrawings(current)
    .then(() => {
        loaded = true;
        status(`${current.symbol}: ${chart.drawings.all().length} drawings loaded`);
    })
    .catch((e) => status(`load failed, saving disabled — ${e.message}`, true));

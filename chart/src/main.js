// Hand-drawn levels -> chartserve.py -> chartlevels.py. Read-only prototype:
// nothing here or downstream places an order.
import { Vela } from '@luxalgo/vela';
import { BinanceProvider } from '@luxalgo/vela/providers/binance';
import { HyperliquidProvider } from '@luxalgo/vela/providers/hyperliquid';
// Both register a native-indicator type on import; that must happen before the
// chart is constructed.
import { KDJ_TYPE } from './kdj.js';
import { TD9_TYPE, setMarks } from './td9marks.js';
import { SWEEP_TYPE, setSweep } from './sweepmarks.js';

// Must match ballast/chartlevels.py SYMBOLS -- the server rejects anything else,
// so a drift here shows up as a failed save, not as levels filed under the
// wrong name.
const SYMBOLS = [
    { symbol: 'BTCUSDT', provider: 'binance', vela: 'binance:BTCUSDT.P' },
    { symbol: 'HYPE', provider: 'hyperliquid', vela: 'hyperliquid:HYPE' },
];
// Drawings are anchored in time+price, so they carry across timeframes; only
// the bars change. Remembered per browser, purely a viewing convenience.
const TIMEFRAMES = [['15', '15m'], ['60', '1H'], ['240', '4H'], ['D', '1D'], ['W', '1W']];
const SAVE_DEBOUNCE_MS = 1000;
// The 9TD marks only change when a bar closes; a minute's lag behind the close
// is the price of not tracking each venue's bar boundaries here.
const MARKS_REFRESH_MS = 60_000;

const select = document.getElementById('symbol');
const statusEl = document.getElementById('status');
for (const s of SYMBOLS) select.add(new Option(`${s.symbol} (${s.provider})`, s.symbol));

const marksEl = document.getElementById('marks');
const tfSelect = document.getElementById('timeframe');
for (const [tf, label] of TIMEFRAMES) tfSelect.add(new Option(label, tf));
let timeframe = '60';
try {
    const saved = localStorage.getItem('ballast.timeframe');
    if (TIMEFRAMES.some(([tf]) => tf === saved)) timeframe = saved;
} catch { /* storage blocked: default 1H */ }
tfSelect.value = timeframe;

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

const chart = new Vela('#chart', { symbol: current.vela, timeframe, live: true, theme: 'dark' });
chart.data.registerProvider('binance', new BinanceProvider());
chart.data.registerProvider('hyperliquid', new HyperliquidProvider());

// Display-only studies, TradingView default parameters spelled out so a change
// of library defaults cannot silently change what the operator is looking at.
// Each gets Vela's legend row (eye to hide, ✕ to remove until reload).
chart.addNativeIndicator('moving-average', { inputs: { maType: 'SMA', length: 20 } });
chart.addNativeIndicator('bollinger-bands', { inputs: { length: 20, mult: 2 } });
chart.addNativeIndicator('supertrend', { inputs: { atrLength: 10, mult: 3 } });
chart.addNativeIndicator('rsi', { inputs: { length: 14 } });
chart.addNativeIndicator('macd', { inputs: { fastLength: 12, slowLength: 26, signalLength: 9 } });
chart.addNativeIndicator('on-balance-volume');
chart.addNativeIndicator(KDJ_TYPE, { inputs: { length: 9, m1: 3, m2: 3 } });
chart.addNativeIndicator(TD9_TYPE);
chart.addNativeIndicator(SWEEP_TYPE);

// #marks shows the 9TD part and the sweep part side by side; either one failing
// turns the line red.
const marksParts = { td9: ['', false], sweep: ['', false] };
function marksStatus(part, text, isError = false) {
    marksParts[part] = [text, isError];
    marksEl.textContent = [marksParts.td9[0], marksParts.sweep[0]].filter(Boolean).join(' · ');
    marksEl.classList.toggle('err', marksParts.td9[1] || marksParts.sweep[1]);
}

// Marks come from Python (chartserve /api/marks -> ballast/td9.py), never from
// this page. A failure is shown, not rendered as "no marks".
let marksSeq = 0;
async function loadMarks() {
    const seq = ++marksSeq;
    const sym = current;
    const tf = tfSelect.value;
    try {
        const r = await fetch(`/api/marks?symbol=${encodeURIComponent(sym.symbol)}&tf=${encodeURIComponent(tf)}`);
        const body = await r.json().catch(() => ({ error: `HTTP ${r.status}, body not JSON` }));
        if (!r.ok) throw new Error(body.error ?? `HTTP ${r.status}`);
        if (seq !== marksSeq) return; // a newer request (symbol/timeframe switch) owns the display
        setMarks(sym.vela, tf, body.marks);
        const last = new Date(body.last_closed).toLocaleString();
        marksStatus('td9', `9TD: ${body.marks.length} marks over ${body.closed_bars} closed bars (last ${last})`);
    } catch (e) {
        if (seq !== marksSeq) return;
        // Stale marks from the previous fetch would look current; clear them.
        setMarks(sym.vela, tf, []);
        marksStatus('td9', `9TD marks failed — ${e.message}`, true);
    }
}

// Sweep-reclaim events (chartserve /api/sweep -> ballast/sweepmarks.py), same
// rules as the 9TD marks: computed in Python, a failure shown, never "none".
let sweepSeq = 0;
async function loadSweep() {
    const seq = ++sweepSeq;
    const sym = current;
    const tf = tfSelect.value;
    try {
        const r = await fetch(`/api/sweep?symbol=${encodeURIComponent(sym.symbol)}&tf=${encodeURIComponent(tf)}`);
        const body = await r.json().catch(() => ({ error: `HTTP ${r.status}, body not JSON` }));
        if (!r.ok) throw new Error(body.error ?? `HTTP ${r.status}`);
        if (seq !== sweepSeq) return;
        setSweep(sym.vela, tf, body.events);
        const n2 = body.events.filter((e) => e.tier === 2).length;
        const n1 = body.events.length - n2;
        marksStatus('sweep', `扫底回收 ${n2} 个${n1 ? `（另一档 ${n1}）` : ''}`);
    } catch (e) {
        if (seq !== sweepSeq) return;
        setSweep(sym.vela, tf, []);
        marksStatus('sweep', `扫底回收失败 — ${e.message}`, true);
    }
}

function loadOverlays() {
    loadMarks();
    loadSweep();
}
setInterval(loadOverlays, MARKS_REFRESH_MS);

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

tfSelect.addEventListener('change', () => {
    try { localStorage.setItem('ballast.timeframe', tfSelect.value); } catch { /* ignore */ }
    chart.setMarket({ timeframe: tfSelect.value });
    loadOverlays();
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
    loadOverlays();
    await chart.setMarket({ symbol: next.vela });
});

loadOverlays();
loadDrawings(current)
    .then(() => {
        loaded = true;
        status(`${current.symbol}: ${chart.drawings.all().length} drawings loaded`);
    })
    .catch((e) => status(`load failed, saving disabled — ${e.message}`, true));

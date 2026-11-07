// ◆ 扫底回收（探索版）marks, DRAWN here but COMPUTED in Python (chartserve.py
// /api/sweep -> trademap/sweepmarks.py -> farseer's detector). Display only:
// nothing here or downstream trades on it, and the score is a provisional v0.
//
// Same rendering path and the same rule as td9marks.js: indicator labels, never
// the user-drawings store (that store is autosaved as the operator's levels).
// This module has no reference to the chart object at all.
//
// Shape: Vela 0.8.1 label styles are label_* bubbles, circle, square, diamond,
// flag, arrowup/down, triangleup/down, cross, xcross, text_outline, none. 9TD
// uses triangles and text, so these are diamonds: long below the confirming
// bar, short above. A tier-1 event (reclaimed 50%, not 100%) is only a small
// grey "•" text glyph -- the smallest point shape Vela draws is a 6px-radius
// disc, no smaller than the 9TD triangles.
import { registerNativeIndicator, stableSeriesId } from '@luxalgo/vela/plugin';
import { sameTf } from './timeframes.js';

export const SWEEP_TYPE = 'trademap-sweep';

const LEVEL_COLOR = {
    long: { high: '#00e676', mid: '#8bc34a', low: '#9e9e9e' },
    short: { high: '#ff1744', mid: '#ff7043', low: '#9e9e9e' },
};
const TIER1_COLOR = '#9e9e9e';
const SHAPE_SIZE = 'small'; // 12px font -> 7px diamond radius, a touch larger than 9TD's triangles
const TEXT_SIZE = 'small';  // 12px font -> 15px line height

// As in td9marks.js: the shape's own text would be drawn below it (onto the
// candle for an above-bar mark), so the score is a separate style:'none' label
// pushed one line-height outward with blank lines.
function stacked(text, rank, above) {
    const blanks = new Array(2 * rank).fill('');
    return (above ? [text, ...blanks] : [...blanks, text]).join('\n');
}

let current = { symbol: null, tf: null, events: [] };
const running = new Set();

/** The events for (Vela symbol, timeframe). Only an instance on that exact market shows them. */
export function setSweep(symbol, tf, events) {
    current = { symbol, tf, events };
    for (const inst of running) inst.emit();
}

function labelsFor(symbol, tf) {
    if (symbol !== current.symbol || !sameTf(tf, current.tf)) return [];
    const out = [];
    current.events.forEach((e, ordinal) => {
        const above = e.side === 'short';
        const base = { paneId: '', xloc: 'bar_time', x: e.time, y: 0, yloc: above ? 'abovebar' : 'belowbar',
            textAlign: 'center', fontFamily: 'default' };
        const id = (part) => stableSeriesId({ instanceId: SWEEP_TYPE, kind: 'label', title: `${e.side}-${e.time}-${e.tier}-${part}`, ordinal });
        if (e.tier !== 2) {
            out.push({ ...base, id: id('dot'), style: 'none', text: '•', textColor: TIER1_COLOR, size: 'tiny' });
            return;
        }
        const color = LEVEL_COLOR[e.side][e.level];
        out.push({ ...base, id: id('shape'), style: 'diamond', color, size: SHAPE_SIZE });
        out.push({ ...base, id: id('text'), style: 'none', text: stacked(`S${e.score}`, 1, above), textColor: color, size: TEXT_SIZE });
    });
    return out;
}

class SweepMarks {
    ctx = null;
    shown = false;

    start(ctx) { this.ctx = ctx; this.shown = true; running.add(this); this.emit(); }
    onBars() {}            // events change only when Python says so (setSweep)
    onViewport() {}
    setInputs() {}
    suspend() { this.shown = false; }
    resume() { this.shown = true; this.emit(); }
    stop() { running.delete(this); this.ctx = null; }

    emit() {
        if (!this.ctx || !this.shown) return;
        this.ctx.emit({ labels: labelsFor(this.ctx.symbol, this.ctx.timeframe) });
        this.ctx.setStatus('idle');
    }
}

registerNativeIndicator({
    type: SWEEP_TYPE,
    title: '扫底回收（探索版）',
    shortTitle: '扫底回收',
    paneHint: 'price',
    overlay: true,
    inputsSchema: () => [],
    defaultInputs: () => ({}),
    create: () => new SweepMarks(),
});

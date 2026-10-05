// 🍅 9TD marks, DRAWN here but COMPUTED in Python (ballast/td9.py via
// chartserve.py /api/marks), so a future Ballast signal and this picture can
// never disagree. Nothing in this file decides a count.
//
// Why a native indicator emitting `labels`: in Vela 0.8.1 that is the path
// that paints price-anchored point shapes (Pine's plotshape/label: yloc
// abovebar/belowbar, triangle styles, text), positioned from the bar's own
// high/low. The `markers` series kind exists in the model but the 0.8.1 native
// renderer does not paint it, and timeline marks sit on the time axis, not at
// price.
//
// Why NOT the user-drawings store: the chart's user drawings are the
// operator's hand-drawn levels. main.js autosaves that store to
// data/chart_drawings/<SYM>.json and chartlevels.py reads it, so anything put
// there would be saved and reported as a level. Indicator output lives in the
// indicator's model, which is never serialized by that save path; this module
// deliberately has no reference to the chart object at all.
import { registerNativeIndicator, stableSeriesId } from '@luxalgo/vela/plugin';

export const TD9_TYPE = 'ballast-td9';

// TradingView's color.white / color.gray / color.purple / color.yellow, as in the script.
const COUNT_COLOR = { 7: '#ffffff', 8: '#787b86', 9: '#9c27b0' };
const STAR_COLOR = '#ffeb3b';
const SHAPE_SIZE = 'tiny';  // 10px font -> 6px triangle radius
const TEXT_SIZE = 'small';  // 12px font -> 15px line height

// Labels have no pixel offset, and Vela draws a point shape's own text BELOW
// the shape whichever side it is on -- for an above-bar mark that lands the
// digit on the candle. So the text is a separate style:'none' label at the
// same anchor, pushed one (count) or two (star) line-heights outward with blank
// lines: the renderer centres a multi-line label on its anchor.
function stacked(text, rank, side) {
    const blanks = new Array(2 * rank).fill('');
    return (side === 'up' ? [text, ...blanks] : [...blanks, text]).join('\n');
}

let current = { symbol: null, tf: null, marks: [] };
const running = new Set();

/** The marks for (Vela symbol, timeframe). Only an instance on that exact market shows them. */
export function setMarks(symbol, tf, marks) {
    current = { symbol, tf, marks };
    for (const inst of running) inst.emit();
}

function labelsFor(symbol, tf) {
    // A response for another market (a slow fetch after a switch) must not be
    // painted onto this one's candles.
    if (symbol !== current.symbol || tf !== current.tf) return [];
    const out = [];
    current.marks.forEach((m, ordinal) => {
        const up = m.side === 'up';
        const yloc = up ? 'abovebar' : 'belowbar';
        const color = COUNT_COLOR[m.count];
        const base = { paneId: '', xloc: 'bar_time', x: m.time, y: 0, yloc, textAlign: 'center', fontFamily: 'default' };
        const id = (part) => stableSeriesId({ instanceId: TD9_TYPE, kind: 'label', title: `${m.side}-${m.time}-${part}`, ordinal });
        out.push({ ...base, id: id('shape'), style: up ? 'triangledown' : 'triangleup', color, size: SHAPE_SIZE });
        out.push({ ...base, id: id('text'), style: 'none', text: stacked(m.text, 1, m.side), textColor: color, size: TEXT_SIZE });
        if (m.star) out.push({ ...base, id: id('star'), style: 'none', text: stacked('*', 2, m.side), textColor: STAR_COLOR, size: TEXT_SIZE });
    });
    return out;
}

class Td9Marks {
    ctx = null;
    shown = false;

    start(ctx) { this.ctx = ctx; this.shown = true; running.add(this); this.emit(); }
    onBars() {}            // marks change only when Python says so (setMarks)
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
    type: TD9_TYPE,
    title: '🍅 9TD with Bollinger Bands',
    shortTitle: '9TD',
    paneHint: 'price',
    overlay: true,
    inputsSchema: () => [],
    defaultInputs: () => ({}),
    create: () => new Td9Marks(),
});

// KDJ (9, 3, 3), display only. Vela 0.8.1 ships Stochastic but no KDJ, so it is
// registered here as a native indicator through the plugin SDK; it then gets the
// same legend row (eye / gear / ✕), own pane and settings dialog as the built-ins.
//
// The common Chinese definition (通达信 / 同花顺), not TradingView's Stochastic:
//   RSV = (C − LLV(L,n)) / (HHV(H,n) − LLV(L,n)) × 100
//   K = (m1−1)/m1·K_prev + 1/m1·RSV      (2/3, 1/3 at m1 = 3)
//   D = (m2−1)/m2·D_prev + 1/m2·K
//   J = 3K − 2D,  K and D seeded at 50
import { registerNativeIndicator, stableSeriesId, INFO, WARNING, TRADE_EXIT } from '@luxalgo/vela/plugin';

export const KDJ_TYPE = 'trademap-kdj';

/** bars -> { k, d, j } arrays aligned to bars; NaN before the first full n-bar window. */
export function computeKdj(bars, n = 9, m1 = 3, m2 = 3) {
    const len = bars.length;
    const k = new Array(len).fill(NaN);
    const d = new Array(len).fill(NaN);
    const j = new Array(len).fill(NaN);
    let kPrev = 50;
    let dPrev = 50;
    let rsvPrev = null;
    for (let i = n - 1; i < len; i++) {
        let hh = -Infinity;
        let ll = Infinity;
        for (let w = i - n + 1; w <= i; w++) {
            if (bars[w].high > hh) hh = bars[w].high;
            if (bars[w].low < ll) ll = bars[w].low;
        }
        // A flat window has no range to place the close in. Dividing by zero would
        // put NaN into K and, through the recursion, into every bar after it.
        const rsv = hh === ll ? (rsvPrev ?? 50) : ((bars[i].close - ll) / (hh - ll)) * 100;
        rsvPrev = rsv;
        kPrev = ((m1 - 1) * kPrev + rsv) / m1;
        dPrev = ((m2 - 1) * dPrev + kPrev) / m2;
        k[i] = kPrev;
        d[i] = dPrev;
        j[i] = 3 * kPrev - 2 * dPrev;
    }
    return { k, d, j };
}

const INPUTS = [
    { key: 'length', title: 'Period', type: 'int', defval: 9, min: 1, max: 500, step: 1 },
    { key: 'm1', title: 'K smoothing', type: 'int', defval: 3, min: 1, max: 100, step: 1 },
    { key: 'm2', title: 'D smoothing', type: 'int', defval: 3, min: 1, max: 100, step: 1 },
];

const intOf = (inputs, key, fallback) => (Number.isInteger(inputs[key]) && inputs[key] > 0 ? inputs[key] : fallback);

class Kdj {
    ctx = null;
    inputs = {};

    start(ctx, inputs) { this.ctx = ctx; this.inputs = inputs; this.emit(); }
    onBars() { this.emit(); }
    onViewport() {}
    setInputs(inputs) { this.inputs = inputs; if (this.ctx) this.emit(); }
    suspend() {}
    resume() { this.emit(); }
    stop() { this.ctx = null; }

    emit() {
        if (!this.ctx) return;
        const bars = this.ctx.bars();
        const { k, d, j } = computeKdj(bars, intOf(this.inputs, 'length', 9), intOf(this.inputs, 'm1', 3), intOf(this.inputs, 'm2', 3));
        const line = (title, values, color, ordinal) => ({
            id: stableSeriesId({ instanceId: KDJ_TYPE, kind: 'line', title, ordinal }),
            title,
            paneId: '', // stamped by the orchestrator
            kind: 'line',
            points: bars.map((b, i) => ({ time: b.time, value: Number.isFinite(values[i]) ? values[i] : null })),
            style: { color, width: 1, lineStyle: 'solid' },
        });
        this.ctx.emit({ series: [line('K', k, WARNING, 0), line('D', d, INFO, 1), line('J', j, TRADE_EXIT, 2)] });
        this.ctx.setStatus('idle');
    }
}

registerNativeIndicator({
    type: KDJ_TYPE,
    title: 'KDJ',
    paneHint: 'new',
    overlay: false,
    inputsSchema: () => INPUTS,
    defaultInputs: () => Object.fromEntries(INPUTS.map((i) => [i.key, i.defval])),
    create: () => new Kdj(),
});

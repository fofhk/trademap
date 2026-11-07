// The nine chart periods, in the order of the header buttons; `key` is the
// keyboard shortcut (the digits 1..9 in that same order).
//
// Values are the strings chartserve.py accepts (trademap/chartbars.py
// TIMEFRAMES) and Vela parses: minutes as a number, D/W/M, "3D" = three days.
// Vela canonicalises "D" to "1440" and "3D" to "4320" internally, so what an
// indicator instance reports as its timeframe may not be spelled the way the
// page spelled it -- compare with sameTf, never with ===.
export const TIMEFRAMES = [
    { value: '15', label: '15m', key: '1' },
    { value: '30', label: '30m', key: '2' },
    { value: '60', label: '1H', key: '3' },
    { value: '120', label: '2H', key: '4' },
    { value: '240', label: '4H', key: '5' },
    { value: 'D', label: '1D', key: '6' },
    { value: '3D', label: '3D', key: '7' },
    { value: 'W', label: '1W', key: '8' },
    { value: 'M', label: '1M', key: '9' },
];
export const DEFAULT_TIMEFRAME = '60';

// Same unit table as Vela's parseTimeframe (core/timeframe): a bare number is
// minutes, D/W/M are 1440/10080/43200 minutes; an unparseable string stays as
// typed, so two garbage values compare equal only to themselves.
const UNIT_MINUTES = { D: 1440, W: 10080, M: 43200, H: 60 };
export function canonicalTf(tf) {
    const m = /^(\d*)\s*([a-zA-Z]?)$/.exec(String(tf ?? '').trim());
    if (!m) return String(tf);
    const unit = m[2].toUpperCase();
    const count = m[1] === '' ? 1 : parseInt(m[1], 10);
    if (unit === '') return m[1] === '' ? String(tf) : String(count);
    if (!(unit in UNIT_MINUTES)) return String(tf);
    return String(count * UNIT_MINUTES[unit]);
}
export function sameTf(a, b) {
    return canonicalTf(a) === canonicalTf(b);
}

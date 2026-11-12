import { COLORS, colorFor, finite, parseObject } from './liveSignals.js';

/**
 * What the Lightbarrier tab reads from the broker: lightbarrierd's records (tsOS-audio, pylightbarrier's
 * README) and envsensed's readings (tsOS-base, pyenvsense's README).
 */
export const LIGHTBARRIER_TOPICS = {
    lbEvents: '+/lightbarrier/event/json',
    lbState: '+/lightbarrier/state/json',
    envsense: '+/envsense/+/json',
};

// The barrier pairs, outer/inner and top/bottom, in the order of the legend
export const LB_PAIRS = ['ot', 'ob', 'it', 'ib'];
const PAIR_LABELS = { ot: 'Outer top', ob: 'Outer bottom', it: 'Inner top', ib: 'Inner bottom' };
// The outer pairs in blues, the inner in oranges: top dark, bottom light
const PAIR_COLORS = { ot: COLORS[0], ob: COLORS[5], it: COLORS[1], ib: COLORS[6] };

// The directions of a passage (pylightbarrier's `passage.py`), in the order of the legend
export const LB_DIRECTIONS = ['OUT_TO_IN', 'IN_TO_OUT', 'OUT_TO_OUT', 'IN_TO_IN', 'PARTIAL_OUT', 'PARTIAL_IN'];
const DIRECTION_LABELS = {
    OUT_TO_IN: 'Out → in',
    IN_TO_OUT: 'In → out',
    OUT_TO_OUT: 'Out → out (turned)',
    IN_TO_IN: 'In → in (turned)',
    PARTIAL_OUT: 'Outer only',
    PARTIAL_IN: 'Inner only',
};
const DIRECTION_COLORS = {
    OUT_TO_IN: COLORS[3],
    IN_TO_OUT: COLORS[2],
    OUT_TO_OUT: COLORS[4],
    IN_TO_IN: COLORS[1],
    PARTIAL_OUT: COLORS[5],
    PARTIAL_IN: COLORS[7],
};

// The values of a state record that are shown, with their unit
export const LB_STATE_VALUES = {
    diff: { label: 'Difference', unit: 'V' },
    active: { label: 'Active', unit: 'V' },
    ambient: { label: 'Ambient', unit: 'V' },
    duration_avg_s: { label: 'Measurement (mean)', unit: 's' },
    duration_max_s: { label: 'Measurement (max)', unit: 's' },
};

const text = value => (typeof value === 'string' && value !== '' ? value : null);

/**
 * The record's time: `time` (tsOS's MQTT conventions), or `timestamp` of lightbarrierd before them,
 * or envsense's `_time` ("2026-06-01 09:10:21.994007+02:00", a space instead of the T).
 */
function timeOf(message) {
    const at = text(message.time) ?? text(message.timestamp) ?? text(message._time);
    const epochMs = at === null ? NaN : Date.parse(at.replace(' ', 'T').replace(/(\.\d{3})\d+/, '$1'));
    return { at, epochMs: Number.isFinite(epochMs) ? epochMs : null };
}

/**
 * Read a passage (`.../lightbarrier/event/json`):
 * `{"time", "direction", "duration_s", "delta_t_s", "avg_block_time_s", "triggers"}`.
 * `values` is `{duration: s}`, the dots have one size.
 * @returns {{at: string|null, epochMs: number|null, duration: null, key: string, values: {duration?: number},
 *            deltaT: number|null, avgBlock: number|null, triggers: string|null}|null} null without a direction
 */
export function parseLbEvent(payload) {
    const message = parseObject(payload);
    if (!message || text(message.direction) === null) return null;
    const duration = finite(message.duration_s);
    return {
        ...timeOf(message),
        duration: null,
        key: message.direction,
        values: duration === null ? {} : { duration },
        deltaT: finite(message.delta_t_s),
        avgBlock: finite(message.avg_block_time_s),
        triggers: text(message.triggers),
    };
}

/**
 * Read a barrier pair's state (`.../lightbarrier/state/json`), one per pair every 60 s:
 * `{"time", "pair", "ambient", "active", "diff", "duration_avg_s", "duration_max_s"}` (V and s).
 * @returns {{at: string|null, epochMs: number|null, duration: null, key: string, values: Object<string, number>}|null}
 *          null without a pair
 */
export function parseLbState(payload) {
    const message = parseObject(payload);
    if (!message || text(message.pair) === null) return null;
    const values = {};
    for (const name of Object.keys(LB_STATE_VALUES)) {
        if (finite(message[name]) !== null) values[name] = message[name];
    }
    return { ...timeOf(message), duration: null, key: message.pair, values };
}

/**
 * Read an envsense reading (`<host>/envsense/<sensor_id>/json`): `{"_time", "Temperature (°C)", "Humidity (%)"}`
 * and, of other sensors, more values (`In1 (V)`, ...). The sensor is only in the topic.
 * @returns {{at: string|null, epochMs: number|null, duration: null, key: string, values: Object<string, number>}|null}
 *          null without a sensor in the topic
 */
export function parseEnvsense(payload, topic = '') {
    const sensor = /\/envsense\/([^/]+)\/json$/.exec(topic)?.[1];
    const message = parseObject(payload);
    if (!message || !sensor) return null;
    const values = {};
    for (const [name, value] of Object.entries(message)) {
        if (!name.startsWith('_') && finite(value) !== null) values[name] = value;
    }
    return { ...timeOf(message), duration: null, key: sensor, values };
}

export function pairLabel(pair) {
    return PAIR_LABELS[pair] ? `${PAIR_LABELS[pair]} (${pair})` : pair;
}

export function pairColor(pair) {
    return PAIR_COLORS[pair] ?? colorFor(pair);
}

export function directionLabel(direction) {
    return DIRECTION_LABELS[direction] ?? direction;
}

export function directionColor(direction) {
    return DIRECTION_COLORS[direction] ?? colorFor(direction);
}

/** Legend order: the known keys in their order, the others after them by name. */
export function orderBy(known) {
    return (a, b) => {
        const ia = known.indexOf(a);
        const ib = known.indexOf(b);
        return (ia < 0 ? known.length : ia) - (ib < 0 ? known.length : ib) || String(a).localeCompare(String(b));
    };
}

/** "Temperature" and "°C" of envsense's "Temperature (°C)". */
export function splitUnit(name) {
    const match = /^(.*?)\s*\(([^)]*)\)$/.exec(name);
    return match ? { label: match[1], unit: match[2] } : { label: name, unit: '' };
}

/** The envsense values to choose from, as they arrived: Temperature and Humidity first. */
export function envValueNames(events) {
    const names = new Set();
    for (const event of events) for (const name of Object.keys(event.values)) names.add(name);
    const first = ['Temperature (°C)', 'Humidity (%)'];
    return [...names].sort(orderBy(first));
}

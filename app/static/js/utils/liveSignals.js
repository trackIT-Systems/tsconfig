/**
 * What the live dashboard does with radiotracking's messages, apart from the
 * browser: reading them, keeping the last few minutes of them, and the numbers
 * the charts need (dot sizes, colours, axis ranges, clock formats).
 */

/** The topics the dashboard reads; the station prefix is a wildcard (host name or radiotracking's `station`). */
export const LIVE_TOPICS = {
    matched: '+/radiotracking/matched/json',
    calibrated: '+/radiotracking/calibrated/json',
    raw: '+/radiotracking/device/+/json',
};

/** The length of the rolling window. */
export const WINDOW_MS = 5 * 60 * 1000;

/**
 * The tabs of the Live page, each shown when its service is available. More follow (health, audio):
 * a tab is a template (`partials/live_<id>.html`) and an entry here.
 */
export const LIVE_TABS = [
    { id: 'radiotracking', label: 'Radiotracking', icon: 'fa-broadcast-tower', service: 'radiotracking' },
];

/** The time windows to choose from (ms): up to the one that is kept, `WINDOW_MS`. */
export const VIEW_OPTIONS = [30 * 1000, 60 * 1000, 2 * 60 * 1000, WINDOW_MS];

/** Memory bound if a station sees signals at a very high rate. */
export const MAX_POINTS = 20000;

/** How far ahead of the browser's clock a signal may be drawn; a station clock further off is shown as skipped. */
export const MAX_AHEAD_MS = 10 * 60 * 1000;

/** Offsets up to this are the network and the clocks' normal jitter; more is worth saying. */
export const CLOCK_TOLERANCE_MS = 5000;

// Keys of a raw message that are not values to plot (everything else numeric is)
const NOT_VALUES = new Set(['Time', 'Device']);

const finite = value => (typeof value === 'number' && Number.isFinite(value) ? value : null);

function parseObject(payload) {
    let message;
    try {
        message = JSON.parse(payload);
    } catch {
        return null;
    }
    return message !== null && typeof message === 'object' && !Array.isArray(message) ? message : null;
}

function timeOf(message) {
    const at = typeof message.Time === 'string' ? message.Time : null;
    const epochMs = at === null ? NaN : Date.parse(at);
    return { at, epochMs: Number.isFinite(epochMs) ? epochMs : null };
}

/**
 * Read a matched or calibrated message:
 * `{"Time": ISO-8601, "Frequency": Hz, "Duration": s, "<sdr>": power | null, ...}`.
 * Every key besides those three is an SDR; one without a power (null: not calibrated) is left out.
 * @returns {{at: string|null, epochMs: number|null, frequency: number|null, duration: number|null,
 *            key: null, values: Object<string, number>} | null} null if it is not such a message;
 *   `epochMs` is `Time` as milliseconds, null if it does not read as a time
 */
export function parseSignal(payload) {
    const message = parseObject(payload);
    if (!message) return null;
    const values = {};
    for (const [key, value] of Object.entries(message)) {
        if (!['Time', 'Frequency', 'Duration'].includes(key) && finite(value) !== null) values[key] = value;
    }
    return { ...timeOf(message), frequency: finite(message.Frequency), duration: finite(message.Duration), key: null, values };
}

/**
 * Read a raw signal of one SDR (`.../device/<dev>/json`):
 * `{"Device": "0", "Time": ..., "Frequency": Hz, "Duration": s, "max (dBW)": ..., ...}`.
 * `values` has every number in it (Frequency and Duration too), to choose from.
 * @returns {{at: string|null, epochMs: number|null, frequency: number|null, duration: number|null,
 *            key: string, values: Object<string, number>} | null} null without a `Device`
 */
export function parseRaw(payload) {
    const message = parseObject(payload);
    if (!message || message.Device === undefined || message.Device === null) return null;
    const values = {};
    for (const [key, value] of Object.entries(message)) {
        if (!NOT_VALUES.has(key) && finite(value) !== null) values[key] = value;
    }
    return {
        ...timeOf(message),
        frequency: finite(message.Frequency),
        duration: finite(message.Duration),
        key: String(message.Device),
        values,
    };
}

// The values of raw signals that are not in the unit the plot shows
const RAW_UNITS = {
    Frequency: { label: 'Frequency (MHz)', scale: 1e-6 },
    Duration: { label: 'Duration (ms)', scale: 1e3 },
};

/** What the dashboard calls a value of a raw signal, and the factor from the message's unit to the plot's. */
export function rawParam(name) {
    return RAW_UNITS[name] ?? { label: name, scale: 1 };
}

/** The values of a raw signal that can be plotted, before any signal has shown which there are. */
export const RAW_PARAMS = ['max (dBW)', 'avg (dBW)', 'snr (dB)', 'noise (dBW)', 'std (dB)', 'Frequency', 'Duration'];

/**
 * The signals of the last `windowMs`, newest last: `{time (s), at, frequency, duration, key, values}`.
 *
 * A signal is placed at its own `Time`, the station's clock, and the window
 * follows the clock of the browser. That is the point: a station whose clock
 * is off, or whose detections come in late, shows up as signals away from
 * "now" instead of being hidden. `offsetMs` (arrival minus `Time` of the last
 * signal) says by how much, and signals whose `Time` is too far from the
 * browser's clock to be drawn are counted in `skipped`.
 */
export class EventWindow {
    constructor(windowMs = WINDOW_MS, maxPoints = MAX_POINTS, maxAheadMs = MAX_AHEAD_MS) {
        this.windowMs = windowMs;
        this.maxPoints = maxPoints;
        this.maxAheadMs = maxAheadMs;
        this.clear();
    }

    clear() {
        this.events = []; // ascending by time
        this.offsetMs = null; // the last signal's arrival minus its Time
        this.skipped = 0; // signals not drawn: their Time is too far from the browser's clock
    }

    get length() {
        return this.events.length;
    }

    /**
     * The right edge of the window (ms): now, or the newest signal if the
     * station's clock is ahead (by at most `maxAheadMs`).
     */
    edge(now = Date.now()) {
        const newest = this.events.length ? this.events[this.events.length - 1].time * 1000 : -Infinity;
        return Math.max(now, newest);
    }

    /** Add a parsed signal that arrived at `now` (ms). Returns whether it is in the window now. */
    add(signal, now = Date.now()) {
        // Without a readable Time all there is, is the arrival
        const ms = signal.epochMs ?? now;
        this.offsetMs = now - ms;
        if (ms < this.edge(now) - this.windowMs || ms > now + this.maxAheadMs) {
            this.skipped++;
            return false;
        }

        // Mostly the newest signal, but the order of arrival is not the order of Time
        let index = this.events.length;
        while (index > 0 && this.events[index - 1].time > ms / 1000) index--;
        const { epochMs, ...rest } = signal;
        this.events.splice(index, 0, { ...rest, time: ms / 1000 });
        this.trim(now);
        return true;
    }

    /** Forget what is older than the window (and anything beyond the memory bound). */
    trim(now = Date.now()) {
        const cutoff = (this.edge(now) - this.windowMs) / 1000;
        let drop = 0;
        while (drop < this.events.length && this.events[drop].time < cutoff) drop++;
        drop = Math.max(drop, this.events.length - this.maxPoints);
        if (drop > 0) this.events.splice(0, drop);
    }
}

// A dot's diameter in px is proportional to the signal's duration: this wide at the reference duration,
// but never smaller or larger than the bounds, so a very short or very long signal stays visible.
const DOT_PX = 6;
const DOT_REFERENCE_S = 0.020;
const DOT_MIN_PX = 3;
const DOT_MAX_PX = 24;

/**
 * The diameter (px) of a signal's dot: 6 px for 20 ms, proportional to the duration
 * (40 ms is twice as wide), between 3 and 24 px. The default size if the duration is unknown.
 */
export function dotSize(durationSeconds) {
    if (typeof durationSeconds !== 'number' || !Number.isFinite(durationSeconds) || durationSeconds <= 0) {
        return DOT_PX;
    }
    return Math.min(DOT_MAX_PX, Math.max(DOT_MIN_PX, DOT_PX * durationSeconds / DOT_REFERENCE_S));
}

// The first four are the antennas' compass directions: SDR 0 blue (north), 1 yellow (east), 2 red (south),
// 3 green (west). The rest are other Okabe-Ito colours (distinguishable with the common kinds of colour blindness).
export const COLORS = ['#0072B2', '#E69F00', '#CC3311', '#009E73', '#CC79A7', '#56B4E9', '#C9B800', '#777777'];

/**
 * The colour of a series. The same key has the same colour on every chart: an SDR is
 * its number (device 0, 1, ...), anything else a fixed pick from its name.
 */
export function colorFor(key) {
    const text = String(key);
    const index = /^\d+$/.test(text)
        ? Number(text)
        : [...text].reduce((sum, char) => sum + char.charCodeAt(0), 0);
    return COLORS[index % COLORS.length];
}

/** "30 seconds", "1 minute", "5 minutes": a time window for the selector and the counts. */
export function formatWindow(ms) {
    const seconds = Math.round(ms / 1000);
    if (seconds < 60) return `${seconds} seconds`;
    const minutes = Math.round(seconds / 60);
    return `${minutes} minute${minutes === 1 ? '' : 's'}`;
}

/** The index of the first signal at or after `seconds` (events are in ascending order of time). */
export function firstAtOrAfter(events, seconds) {
    let low = 0;
    let high = events.length;
    while (low < high) {
        const middle = (low + high) >> 1;
        if (events[middle].time < seconds) low = middle + 1;
        else high = middle;
    }
    return low;
}

/** A key's sort order among series: numbers as numbers, so SDR 2 comes before SDR 10. */
export function compareKeys(a, b) {
    return String(a).localeCompare(String(b), undefined, { numeric: true });
}

// What the power axis shows before anything has arrived (a SDR's usual range), and the
// least it spans: one signal alone would otherwise give an axis of no height.
const EMPTY_POWER = { dBW: [-100, -20], dBm: [-120, -40] };
const MIN_SPAN_DB = 10;
const PADDING_DB = 5;

/**
 * The power axis for the data's extremes (null while there is no data), in whole dB.
 * @returns {[number, number]}
 */
export function powerRange(min, max, unit) {
    if (min === null || max === null || min === undefined || max === undefined) {
        return EMPTY_POWER[unit] ?? EMPTY_POWER.dBW;
    }
    const middle = (min + max) / 2;
    const half = Math.max((max - min) / 2 + PADDING_DB, MIN_SPAN_DB / 2);
    return [Math.floor(middle - half), Math.ceil(middle + half)];
}

const MIN_SPAN_MHZ = 0.05;

/** The frequency axis (MHz) for the data's extremes: padded, and at least 50 kHz high. */
export function frequencyRange(min, max) {
    if (min === null || max === null || min === undefined || max === undefined) return [150, 151];
    const middle = (min + max) / 2;
    const half = Math.max((max - min) / 2 * 1.2, MIN_SPAN_MHZ / 2);
    return [middle - half, middle + half];
}

/** An axis for any value: padded by 8% of what the data spans, or by a little if it is one value. */
export function valueRange(min, max) {
    if (min === null || max === null || min === undefined || max === undefined) return [0, 1];
    const span = max - min;
    const pad = span > 0 ? span * 0.08 : Math.max(Math.abs(max) * 0.05, 1);
    return [min - pad, max + pad];
}

/** The ceiling of the power axes (dBW): a static value, no signal is stronger. */
export const POWER_CEILING_DBW = -30;

/**
 * What the radiotracking configuration says about the signals, for the axes (the API's
 * `/api/radiotracking`, which tsconfig already parses). Each is null if the config
 * does not have a usable number.
 * @returns {{thresholdDbw: number|null, centerFreqHz: number|null, sampleRateHz: number|null,
 *            minDurationMs: number|null, maxDurationMs: number|null} | null} null if it is not a config
 */
export function radiotrackingLimits(config) {
    if (config === null || typeof config !== 'object') return null;
    const number = value => (typeof value === 'number' && Number.isFinite(value) ? value : null);
    return {
        thresholdDbw: number(config.analysis?.signal_threshold_dbw),
        centerFreqHz: number(config['rtl-sdr']?.center_freq),
        sampleRateHz: number(config['rtl-sdr']?.sample_rate),
        minDurationMs: number(config.analysis?.signal_min_duration_ms),
        maxDurationMs: number(config.analysis?.signal_max_duration_ms),
    };
}

/** The power axis the configuration gives (dBW): from the signal threshold to the static ceiling. Null if unknown. */
export function powerLimits(limits) {
    const low = limits?.thresholdDbw;
    return typeof low === 'number' && low < POWER_CEILING_DBW ? [low, POWER_CEILING_DBW] : null;
}

/** The frequency axis the configuration gives (MHz): what the SDR sees, the centre frequency plus and minus half the sample rate. */
export function frequencyLimits(limits) {
    const { centerFreqHz: center, sampleRateHz: rate } = limits ?? {};
    return typeof center === 'number' && typeof rate === 'number' && rate > 0
        ? [(center - rate / 2) / 1e6, (center + rate / 2) / 1e6]
        : null;
}

/** The duration axis the configuration gives (ms): the shortest to the longest signal that is detected. */
export function durationLimits(limits) {
    const { minDurationMs: min, maxDurationMs: max } = limits ?? {};
    return typeof min === 'number' && typeof max === 'number' && min < max ? [min, max] : null;
}

/**
 * An axis a little wider than its limits, so a dot that sits on a limit is not cut in half
 * (3% of the span on each side). Null stays null.
 */
export function padRange(range, fraction = 0.03) {
    if (!range) return null;
    const pad = (range[1] - range[0]) * fraction;
    return [range[0] - pad, range[1] + pad];
}

/**
 * An axis for values that are fitted to the data, but never spans less than `minSpan`
 * (dB values: 10 dB), padded by 8% of what the data spans. `empty` before there is data.
 */
export function spreadRange(min, max, minSpan, empty = [0, minSpan]) {
    if (min === null || max === null || min === undefined || max === undefined) return empty;
    const pad = (max - min) * 0.08;
    let low = min - pad;
    let high = max + pad;
    if (high - low < minSpan) {
        const middle = (low + high) / 2;
        low = middle - minSpan / 2;
        high = middle + minSpan / 2;
    }
    return [low, high];
}

/** An axis tick without float noise: -60, 0.25, 150.155. */
export function formatTick(value, decimals = 3) {
    return String(Number(value.toFixed(decimals)));
}

const two = n => String(n).padStart(2, '0');

/** A time of day in the browser's time zone, 24 hours: "14:05" or "14:05:09". For the time axis. */
export function formatClock(seconds, withSeconds = false) {
    const date = new Date(seconds * 1000);
    const base = `${two(date.getHours())}:${two(date.getMinutes())}`;
    return withSeconds ? `${base}:${two(date.getSeconds())}` : base;
}

/** "14:05:09.123", for the tooltip. */
export function formatClockMs(ms) {
    const date = new Date(ms);
    return `${formatClock(ms / 1000, true)}.${String(date.getMilliseconds()).padStart(3, '0')}`;
}

/** MHz for a Hz value, as the tooltips show it. */
export function formatFrequency(hz) {
    return hz === null || hz === undefined ? '–' : `${(hz / 1e6).toFixed(3)} MHz`;
}

/** Milliseconds for a seconds value. */
export function formatDuration(seconds) {
    return seconds === null || seconds === undefined ? '–' : `${(seconds * 1000).toFixed(1)} ms`;
}

/** A clock difference for people: "0.4 s", "2 min 5 s", "1 h 2 min". */
export function formatOffset(ms) {
    const seconds = Math.abs(ms) / 1000;
    if (seconds < 10) return `${seconds.toFixed(1)} s`;
    if (seconds < 60) return `${Math.round(seconds)} s`;
    const minutes = Math.floor(seconds / 60);
    if (minutes < 60) return `${minutes} min ${Math.round(seconds - minutes * 60)} s`;
    const hours = Math.floor(minutes / 60);
    if (hours < 48) return `${hours} h ${minutes - hours * 60} min`;
    return `${Math.round(hours / 24)} days`;
}

/** The URL of the broker's WebSocket: same host and scheme as the page, behind Caddy at /mqtt. */
export function brokerUrl(location, baseUrl = '') {
    const scheme = location.protocol === 'https:' ? 'wss:' : 'ws:';
    return `${scheme}//${location.host}${baseUrl}/mqtt`;
}

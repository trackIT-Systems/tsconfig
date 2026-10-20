import { COLORS, colorFor, finite, parseObject } from './liveSignals.js';

/** What the Soundscapepipe tab reads from the broker. */
export const SOUNDSCAPE_TOPICS = {
    detections: '+/soundscapepipe/detection/json',
    levels: '+/soundscapepipe/state/json',
};

/**
 * birdedge stamps a detection with the start of its 5-second window and publishes it once the
 * window is analysed, about 8 s later: the clock warning only shows beyond this.
 */
export const DETECTION_DELAY_MS = 30 * 1000;

// The level metrics drawn (the SNR is in the tooltip), in the order of the legend
export const LEVEL_METRICS = ['peak', 'rms'];
const METRIC_LABELS = { peak: 'Peak', rms: 'RMS', snr: 'SNR' };

// Channel 1 in blues, channel 2 in oranges: the peak dark, the RMS light
const LEVEL_COLORS = {
    'peak-0': COLORS[0],
    'rms-0': COLORS[5],
    'peak-1': COLORS[1],
    'rms-1': COLORS[6],
};

// ALSA's PCM states (snd_pcm_state_t), as soundscapepipe reports its capture device's
const PCM_STATES = ['open', 'setup', 'prepared', 'running', 'xrun', 'draining', 'paused', 'suspended', 'disconnected'];

function timeOf(message) {
    const at = typeof message.time === 'string' ? message.time : null;
    const epochMs = at === null ? NaN : Date.parse(at);
    return { at, epochMs: Number.isFinite(epochMs) ? epochMs : null };
}

const text = value => (typeof value === 'string' && value !== '' ? value : null);

/**
 * Read a detection (`.../soundscapepipe/detection/json`):
 * `{"time", "duration", "species", "confidence", "detector", "channel", ...}` and the detector's own
 * values (yolobat: `low`, `high` in Hz, `call_type`, `ct_conf`). The dots have one size, so
 * `duration` is null and the detection's length (20 ms to 5 s) is `seconds`.
 * @returns {{at: string|null, epochMs: number|null, duration: null, key: string,
 *            values: {confidence?: number}, species: string, seconds: number|null, channel: *,
 *            low: number|null, high: number|null, callType: string|null}|null} null if it is not a detection
 */
export function parseDetection(payload) {
    const message = parseObject(payload);
    if (!message || text(message.species) === null || text(message.detector) === null) return null;
    const confidence = finite(message.confidence);
    return {
        ...timeOf(message),
        duration: null,
        key: message.detector,
        values: confidence === null ? {} : { confidence },
        species: message.species,
        seconds: finite(message.duration),
        channel: message.channel ?? null,
        low: finite(message.low),
        high: finite(message.high),
        callType: text(message.call_type),
    };
}

/**
 * Read a level report (`.../soundscapepipe/state/json`):
 * `{"time", "state", "channels", "peak": [dBFS per channel], "rms": [...], "snr": [...]}`.
 * `values` is keyed `<metric>-<channel>` (`peak-0`, `rms-1`, `snr-0`).
 * @returns {{at: string|null, epochMs: number|null, duration: null, key: null,
 *            values: Object<string, number>, state: number|null, channels: number|null}|null}
 */
export function parseLevels(payload) {
    const message = parseObject(payload);
    if (!message) return null;
    const values = {};
    for (const metric of Object.keys(METRIC_LABELS)) {
        const levels = Array.isArray(message[metric]) ? message[metric] : [];
        levels.forEach((value, channel) => {
            if (finite(value) !== null) values[`${metric}-${channel}`] = value;
        });
    }
    return {
        ...timeOf(message),
        duration: null,
        key: null,
        values,
        state: finite(message.state),
        channels: finite(message.channels),
    };
}

/**
 * The detectors soundscapepipe.yml configures (`GET /api/soundscapepipe`): those under `detectors`,
 * and `lure` if it has tasks. In the order of the configuration.
 */
export function configuredDetectors(config) {
    const detectors = config?.detectors && typeof config.detectors === 'object' ? Object.keys(config.detectors) : [];
    if (Array.isArray(config?.lure?.tasks) && config.lure.tasks.length > 0) detectors.push('lure');
    return detectors;
}

/**
 * The names of the species the models know (`GET /api/soundscapepipe/species`), by what a detection
 * calls them: birdedge and AudioProtoPNet by the scientific name, yolobat by its code (`Nnoc`).
 * @returns {Object<string, {scientific: string, english: string, german: string}>}
 */
export function speciesNames(species) {
    const names = {};
    for (const list of Object.values(species ?? {})) {
        if (!Array.isArray(list)) continue;
        for (const entry of list) {
            const label = entry?.modelLabel || entry?.scientific;
            if (label) {
                names[label] = { scientific: entry.scientific || label, english: entry.english || '', german: entry.german || '' };
            }
        }
    }
    return names;
}

export function levelColor(key) {
    return LEVEL_COLORS[key] ?? colorFor(key);
}

/** "Ch 1 Peak" for `peak-0`. */
export function levelLabel(key) {
    const [metric, channel] = key.split('-');
    return `Ch ${Number(channel) + 1} ${METRIC_LABELS[metric] ?? metric}`;
}

/** "running (3)" for an ALSA PCM state. */
export function pcmStateText(state) {
    if (state === null || state === undefined) return '–';
    return PCM_STATES[state] ? `${PCM_STATES[state]} (${state})` : String(state);
}

/**
 * The dBFS axis: up to just above full scale (0 dBFS, where the input clips), down to
 * -60 dBFS or below the quietest level shown.
 */
export function levelRange(min) {
    return [Math.min(-60, (min ?? -60) - 5), 3];
}

import { finite, parseObject } from './liveSignals.js';

/** What the Lotek FSK tab reads from the broker (lotekfskd of tsOS-fsk, pylotekfsk's `docs/mqtt.md`). */
export const LOTEKFSK_TOPICS = {
    fskMatched: '+/lotekfsk/matched/json',
    fskDetections: '+/lotekfsk/receiver/+/json',
    fskState: '+/lotekfsk/state/json',
    fskDaemon: '+/lotekfsk/daemon/json',
};

// A receiver state is healthy, broken, or the daemon closed it
const HEALTHY_STATES = new Set(['connected', 'running']);
const FAILED_STATES = new Set(['unresponsive', 'disconnected']);

// The numbers of a receiver state and of the daemon status that are shown
const STATE_VALUES = ['slot', 'receiver_serial', 'uptime_s', 'detections', 'reconnects', 'frame_errors'];
const DAEMON_VALUES = ['uptime_s', 'receivers_connected', 'receivers_total', 'detections', 'matched', 'open_groups', 'dropped_invalid_crc', 'dropped_events'];

function timeOf(message) {
    const at = typeof message.time === 'string' ? message.time : null;
    const epochMs = at === null ? NaN : Date.parse(at);
    return { at, epochMs: Number.isFinite(epochMs) ? epochMs : null };
}

const text = value => (typeof value === 'string' && value !== '' ? value : null);

function numbers(message, names) {
    const values = {};
    for (const name of names) {
        if (finite(message[name]) !== null) values[name] = message[name];
    }
    return values;
}

/**
 * Read a matched record (`.../lotekfsk/matched/json`): one transmission of a tag as all receivers heard it,
 * `{"time", "tag_id_hex", "receiver_count", "duplicates", "rssi_0": dB|null, "rssi_1": ...}`.
 * `values` has the RSSI of each slot that heard it, keyed by the slot ("0", "1", ...). The dots have one
 * size, so `duration` is null.
 * @returns {{at: string|null, epochMs: number|null, duration: null, key: string, values: Object<string, number>,
 *            tag: string, receivers: number|null, duplicates: number|null}|null} null without a tag
 */
export function parseFskMatched(payload) {
    const message = parseObject(payload);
    if (!message || text(message.tag_id_hex) === null) return null;
    const values = {};
    for (const [name, value] of Object.entries(message)) {
        const slot = /^rssi_(\d+)$/.exec(name)?.[1];
        if (slot !== undefined && finite(value) !== null) values[slot] = value;
    }
    return {
        ...timeOf(message),
        duration: null,
        key: message.tag_id_hex,
        values,
        tag: message.tag_id_hex,
        receivers: finite(message.receiver_count),
        duplicates: finite(message.duplicates),
    };
}

/**
 * Read a detection (`.../lotekfsk/receiver/<serial>/json`): one report of one receiver,
 * `{"time", "slot", "receiver_serial", "tag_id_hex", "rssi_dbm", "crc8_valid", ...}`.
 * `values` is `{<slot>: rssi}`, as a matched record's of one slot.
 * @returns {{at: string|null, epochMs: number|null, duration: null, key: string, values: Object<string, number>,
 *            tag: string, serial: number|null, crcValid: boolean|null}|null} null without a slot or a tag
 */
export function parseFskDetection(payload) {
    const message = parseObject(payload);
    const slot = message ? finite(message.slot) : null;
    if (slot === null || text(message.tag_id_hex) === null) return null;
    const rssi = finite(message.rssi_dbm);
    return {
        ...timeOf(message),
        duration: null,
        key: String(slot),
        values: rssi === null ? {} : { [slot]: rssi },
        tag: message.tag_id_hex,
        serial: finite(message.receiver_serial),
        crcValid: typeof message.crc8_valid === 'boolean' ? message.crc8_valid : null,
    };
}

/**
 * Read a receiver state (`.../lotekfsk/state/json`), one per slot on every change and every
 * `status.interval_s` (60 s): `{"time", "slot", "receiver_serial", "usb_serial", "port", "state",
 * "uptime_s", "detections", "last_detection", "reconnects", "frame_errors", "error"}`.
 * @returns {{at: string|null, epochMs: number|null, duration: null, key: string, values: Object<string, number>,
 *            state: string|null, usbSerial: string|null, port: string|null, lastDetection: string|null,
 *            error: string|null}|null} null without a slot
 */
export function parseFskState(payload) {
    const message = parseObject(payload);
    const slot = message ? finite(message.slot) : null;
    if (slot === null) return null;
    return {
        ...timeOf(message),
        duration: null,
        key: String(slot),
        values: numbers(message, STATE_VALUES),
        state: text(message.state),
        usbSerial: text(message.usb_serial),
        port: text(message.port),
        lastDetection: text(message.last_detection),
        error: text(message.error),
    };
}

/**
 * Read the daemon status (`.../lotekfsk/daemon/json`), every `status.interval_s`:
 * `{"time", "version", "uptime_s", "receivers_connected", "receivers_total", "detections", "matched", ...}`.
 * @returns {{at: string|null, epochMs: number|null, duration: null, key: null, values: Object<string, number>,
 *            version: string|null}|null} null without an uptime (not a daemon status)
 */
export function parseFskDaemon(payload) {
    const message = parseObject(payload);
    if (!message || finite(message.uptime_s) === null) return null;
    return { ...timeOf(message), duration: null, key: null, values: numbers(message, DAEMON_VALUES), version: text(message.version) };
}

/** The latest event of each key in these events (ascending in time), by key. */
export function latestByKey(events) {
    const latest = {};
    for (const event of events) latest[event.key] = event;
    return latest;
}

/** `success` for a healthy receiver state, `danger` for a broken one, `secondary` otherwise (stopped, unknown). */
export function stateClass(state) {
    if (HEALTHY_STATES.has(state)) return 'success';
    if (FAILED_STATES.has(state)) return 'danger';
    return 'secondary';
}

/**
 * The tags in these matched records, the one heard last first: how often, when last (ms), the
 * strongest RSSI of each slot and of all.
 * @returns {{tag: string, count: number, lastMs: number, best: number|null, slots: Object<string, number>}[]}
 */
export function tagSummary(events) {
    const tags = new Map();
    for (const event of events) {
        let row = tags.get(event.tag);
        if (!row) tags.set(event.tag, row = { tag: event.tag, count: 0, lastMs: 0, best: null, slots: {} });
        row.count++;
        row.lastMs = Math.max(row.lastMs, event.time * 1000);
        for (const [slot, rssi] of Object.entries(event.values)) {
            if (row.slots[slot] === undefined || rssi > row.slots[slot]) row.slots[slot] = rssi;
            if (row.best === null || rssi > row.best) row.best = rssi;
        }
    }
    return [...tags.values()].sort((a, b) => b.lastMs - a.lastMs || a.tag.localeCompare(b.tag));
}

/** The detections of each tag in these detections whose CRC8 did not match, by tag: `{bad, checked}`. */
export function crcSummary(events) {
    const tags = {};
    for (const event of events) {
        if (event.crcValid === null) continue;
        const row = tags[event.tag] ??= { bad: 0, checked: 0 };
        row.checked++;
        if (!event.crcValid) row.bad++;
    }
    return tags;
}

/** "1 h 2 min" for seconds of uptime. */
export function formatUptime(seconds) {
    if (!Number.isFinite(seconds)) return '–';
    if (seconds < 60) return `${Math.round(seconds)} s`;
    const minutes = Math.floor(seconds / 60);
    if (minutes < 60) return `${minutes} min`;
    const hours = Math.floor(minutes / 60);
    if (hours < 48) return `${hours} h ${minutes - hours * 60} min`;
    return `${Math.floor(hours / 24)} d ${hours % 24} h`;
}

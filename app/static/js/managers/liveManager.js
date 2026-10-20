import { serverModeManager } from './serverModeManager.js';
import {
    EventWindow,
    LIVE_TOPICS,
    WINDOW_MS,
    brokerUrl,
    parseRaw,
    parseSignal,
    radiotrackingLimits,
} from '../utils/liveSignals.js';
import { SOUNDSCAPE_TOPICS, parseDetection, parseLevels, parsePerformance } from '../utils/liveSoundscape.js';

const baseUrl = () => window.BASE_URL || '';

/** Load a classic script once; resolves when it has run. */
function loadScript(src) {
    return new Promise((resolve, reject) => {
        const script = document.createElement('script');
        script.src = src;
        script.onload = resolve;
        script.onerror = () => reject(new Error(`Failed to load ${src}`));
        document.head.appendChild(script);
    });
}

function loadStyle(href) {
    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = href;
    document.head.appendChild(link);
}

/** What is read from the broker: the topic to subscribe to, which messages are meant, how to read them. */
const SOURCES = {
    matched: { topic: LIVE_TOPICS.matched, owns: /\/radiotracking\/matched\/json$/, parse: parseSignal },
    calibrated: { topic: LIVE_TOPICS.calibrated, owns: /\/radiotracking\/calibrated\/json$/, parse: parseSignal },
    raw: { topic: LIVE_TOPICS.raw, owns: /\/radiotracking\/device\/[^/]+\/json$/, parse: parseRaw },
    detections: { topic: SOUNDSCAPE_TOPICS.detections, owns: /\/soundscapepipe\/detection\/json$/, parse: parseDetection },
    levels: { topic: SOUNDSCAPE_TOPICS.levels, owns: /\/soundscapepipe\/state\/json$/, parse: parseLevels },
    performance: { topic: SOUNDSCAPE_TOPICS.performance, owns: /\/soundscapepipe\/performance\/json$/, parse: parsePerformance },
};

/**
 * What the live dashboard reads from the station's own broker, for all its
 * panels: one connection (MQTT over WebSockets at /mqtt, behind the login) and,
 * for each kind of message, the signals of the last five minutes.
 *
 * Nothing is stored or read on the server: the signals are held here, in the
 * browser's memory. They survive a switch between matched and calibrated (both
 * are read while the panel is shown) and Stop (what was read stays on the
 * chart), but not a panel going away: the time axis keeps moving, so signals
 * held across a visit would come back with a hole in them. A panel that goes
 * away `discard`s what nobody else reads. The connection is only open while a
 * panel wants something (`acquire`), so the station's data is not fetched in
 * the background.
 */
export class LiveManager {
    constructor() {
        this.windows = Object.fromEntries(Object.keys(SOURCES).map(name => [name, new EventWindow()]));
        this.received = Object.fromEntries(Object.keys(SOURCES).map(name => [name, 0])); // since it was wanted
        this.subscribedAt = Object.fromEntries(Object.keys(SOURCES).map(name => [name, Date.now()]));
        this.wanted = Object.fromEntries(Object.keys(SOURCES).map(name => [name, 0]));
        // chosen on the page, kept while it is open: whether the raw signals are read, how much of what is held is shown,
        // which detectors' detections are not shown
        this.prefs = { collectRaw: false, viewMs: WINDOW_MS, hiddenDetectors: [] };
        this.status = 'offline'; // connecting | connected | offline | failed
        this.error = '';
        this.limits = null; // what radiotracking.ini says about the signals, for the axes
        this.limitsRequest = null;
        this.client = null;
        this.listeners = new Set();
        this.librariesLoaded = null;
    }

    /** The libraries are only needed on the Live tab, so they are loaded when it is first opened. */
    loadLibraries() {
        this.librariesLoaded ??= (async () => {
            const vendor = `${baseUrl()}/static/vendor`;
            if (!window.uPlot) {
                loadStyle(`${vendor}/uplot/css/uPlot.min.css`);
                await loadScript(`${vendor}/uplot/js/uPlot.iife.min.js`);
            }
            if (!window.mqtt) await loadScript(`${vendor}/mqtt/js/mqtt.min.js`);
        })().catch(error => {
            this.librariesLoaded = null; // try again next time
            throw error;
        });
        return this.librariesLoaded;
    }

    /**
     * Read what radiotracking's configuration says about the signals (threshold, centre frequency,
     * sample rate, duration limits) from tsconfig's API, for the axes. Without it (no
     * configuration, or the request fails) the axes fit the data instead.
     */
    loadLimits() {
        // Panels that open together (a tab's) share one request
        if (this.limitsRequest && Date.now() - this.limitsRequest.time < 3000) return this.limitsRequest.promise;
        const promise = (async () => {
            try {
                const response = await fetch(serverModeManager.buildApiUrl('/api/radiotracking'));
                this.limits = response.ok ? radiotrackingLimits(await response.json()) : null;
            } catch (error) {
                console.error('Failed to load the radiotracking configuration for the axes:', error);
                this.limits = null;
            }
            return this.limits;
        })();
        this.limitsRequest = { time: Date.now(), promise };
        return promise;
    }

    /** Call `listener` when the connection's status changes; returns the function that stops it. */
    onChange(listener) {
        this.listeners.add(listener);
        return () => this.listeners.delete(listener);
    }

    /**
     * Forget the signals of these sources, unless a panel still reads them (it is drawing
     * them). For a panel that goes away: a later visit starts with an empty chart, not with a gap.
     */
    discard(sources) {
        for (const name of sources) {
            if (this.wanted[name] === 0) {
                this.windows[name].clear();
                this.received[name] = 0;
            }
        }
    }

    /** Show only the last `ms` of what is held (at most all of it). */
    setView(ms) {
        this.prefs.viewMs = Math.min(WINDOW_MS, Math.max(1000, ms));
        this.#changed();
    }

    #changed() {
        for (const listener of this.listeners) listener();
    }

    /**
     * Subscribe to these sources (`matched`, `calibrated`, `raw`, `detections`, `levels`, `performance`) until the returned
     * function is called. Needs the libraries (`loadLibraries()`). The connection
     * opens with the first panel that wants something and closes with the last.
     */
    acquire(sources) {
        for (const name of sources) {
            if (this.wanted[name]++ === 0) {
                this.received[name] = 0;
                this.subscribedAt[name] = Date.now();
                if (this.client?.connected) this.client.subscribe(SOURCES[name].topic, { qos: 0 });
            }
        }
        if (!this.client) this.#connect();

        let released = false;
        return () => {
            if (released) return;
            released = true;
            for (const name of sources) {
                if (--this.wanted[name] === 0 && this.client?.connected) {
                    this.client.unsubscribe(SOURCES[name].topic);
                }
            }
            if (Object.values(this.wanted).every(count => count === 0)) this.#disconnect();
        };
    }

    #connect() {
        this.status = 'connecting';
        this.error = '';
        const client = window.mqtt.connect(brokerUrl(window.location, baseUrl()), {
            clientId: `tsconfig-live-${Math.random().toString(16).slice(2, 10)}`,
            reconnectPeriod: 3000,
            connectTimeout: 10000,
            // Subscribed to what is wanted on every connect() below, not by mqtt.js
            resubscribe: false,
        });
        this.client = client;
        client.on('connect', () => {
            this.status = 'connected';
            this.error = '';
            for (const [name, source] of Object.entries(SOURCES)) {
                if (this.wanted[name] > 0) client.subscribe(source.topic, { qos: 0 });
            }
            this.#changed();
        });
        client.on('reconnect', () => { this.status = 'connecting'; this.#changed(); });
        client.on('offline', () => { this.status = 'offline'; this.#changed(); });
        client.on('close', () => {
            if (this.status === 'connected') {
                this.status = 'offline';
                this.#changed();
            }
        });
        client.on('error', error => { this.error = error?.message || String(error); this.#changed(); });
        client.on('message', (topic, payload) => this.ingest(topic, payload.toString()));
        this.#changed();
    }

    #disconnect() {
        this.client?.end(true);
        this.client = null;
        this.status = 'offline';
        this.#changed();
    }

    /** Take a message of the broker into the window of its kind. Returns whether it is in a window now. */
    ingest(topic, payload, now = Date.now()) {
        const name = Object.keys(SOURCES).find(candidate => SOURCES[candidate].owns.test(topic));
        if (!name) return false;
        this.received[name]++;
        const signal = SOURCES[name].parse(payload);
        // A calibrated message from a station without calibration has no powers to show
        if (!signal || Object.keys(signal.values).length === 0) return false;
        return this.windows[name].add(signal, now);
    }

    /** Forget everything and close the connection (for tests). */
    reset() {
        this.#disconnect();
        for (const name of Object.keys(SOURCES)) {
            this.windows[name].clear();
            this.received[name] = 0;
            this.wanted[name] = 0;
        }
        this.prefs.collectRaw = false;
        this.prefs.viewMs = WINDOW_MS;
        this.prefs.hiddenDetectors = [];
        this.error = '';
        this.listeners.clear();
        this.librariesLoaded = null;
        this.limits = null;
        this.limitsRequest = null;
    }
}

export const liveManager = new LiveManager();

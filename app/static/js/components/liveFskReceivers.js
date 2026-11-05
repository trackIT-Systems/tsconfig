import { liveManager } from '../managers/liveManager.js';
import { compose } from '../mixins/compose.js';
import { lifecycleMixin } from '../mixins/lifecycleMixin.js';
import { serverModeMixin } from '../mixins/serverModeMixin.js';
import { colorFor, compareKeys, formatClock } from '../utils/liveSignals.js';
import { formatUptime, latestByKey, stateClass } from '../utils/liveLotekfsk.js';

const SOURCES = ['fskState', 'fskDaemon', 'fskDetections'];

/** "14:05:09" for an ISO time, or "–". */
function clockOf(at) {
    const ms = at ? Date.parse(at) : NaN;
    return Number.isFinite(ms) ? formatClock(ms / 1000, true) : '–';
}

/**
 * "Lotek FSK: Receivers": a card per receiver (slot) with its latest state, and a line for the
 * daemon. lotekfskd sends them on every change and every `status.interval_s` (60 s), so after
 * the tab is opened it can take that long; meanwhile the receivers that sent detections are
 * shown from those (slot, serial, last detection).
 */
export function liveFskReceivers() {
    let release = null;

    return compose(lifecycleMixin(), serverModeMixin(), {
        status: 'idle', // connecting | connected | offline | failed, or idle: reading nothing
        error: '',
        receivers: [], // a card per slot
        daemon: null, // the latest daemon status
        waiting: true, // no state has arrived yet

        async init() {
            try {
                await liveManager.loadLibraries();
            } catch (error) {
                this.status = 'failed';
                this.error = 'The MQTT library could not be loaded.';
                console.error(error);
                return;
            }
            if (this.destroyed) return;

            release = liveManager.acquire(SOURCES);
            this.onDestroy(liveManager.onChange(() => this.refreshStatus()));
            this.onDestroy(() => {
                release?.();
                release = null;
                liveManager.discard(SOURCES);
            });
            this.refreshStatus();
            this.tick();
            this.every(1000, () => this.tick());
        },

        refreshStatus() {
            this.status = release ? liveManager.status : 'idle';
            this.error = release ? liveManager.error : '';
        },

        /** Once a second: the latest state of each receiver, or what its detections say. */
        tick() {
            // As the charts: Alpine does not always destroy a panel in a nested x-if that is left
            if (this.$el && !this.$el.isConnected) {
                this.destroy();
                return;
            }
            const now = Date.now();
            for (const name of SOURCES) liveManager.windows[name].trim(now);

            const states = latestByKey(liveManager.windows.fskState.events);
            const heard = latestByKey(liveManager.windows.fskDetections.events);
            const slots = [...new Set([...Object.keys(states), ...Object.keys(heard)])].sort(compareKeys);
            const receivers = slots.map(slot => this.card(slot, states[slot], heard[slot]));
            const daemon = liveManager.windows.fskDaemon.events.at(-1) ?? null;

            if (JSON.stringify(receivers) !== JSON.stringify(this.receivers)) this.receivers = receivers;
            const summary = daemon && { ...daemon.values, version: daemon.version, at: clockOf(daemon.at) };
            if (JSON.stringify(summary) !== JSON.stringify(this.daemon)) this.daemon = summary;
            this.waiting = Object.keys(states).length === 0;
        },

        /** What the card of a slot shows: from its latest state, else from its latest detection. */
        card(slot, state, detection) {
            const values = state?.values ?? {};
            const lastHeard = detection?.at ?? state?.lastDetection ?? null;
            return {
                slot,
                color: colorFor(slot),
                state: state?.state ?? 'heard',
                badge: state ? stateClass(state.state) : 'info',
                serial: values.receiver_serial ?? detection?.serial ?? null,
                usbSerial: state?.usbSerial ?? null,
                port: state?.port ?? null,
                uptime: state ? formatUptime(values.uptime_s) : '–',
                detections: values.detections ?? null,
                reconnects: values.reconnects ?? null,
                frameErrors: values.frame_errors ?? null,
                lastDetection: clockOf(lastHeard),
                updated: clockOf(state?.at),
                error: state?.error ?? null,
            };
        },

        formatUptime,
    });
}

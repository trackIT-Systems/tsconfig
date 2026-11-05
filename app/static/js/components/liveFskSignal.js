import { liveManager } from '../managers/liveManager.js';
import { compose } from '../mixins/compose.js';
import { lifecycleMixin } from '../mixins/lifecycleMixin.js';
import { liveChartMixin } from '../mixins/liveChartMixin.js';
import { serverModeMixin } from '../mixins/serverModeMixin.js';
import { colorFor, compareKeys, formatTick, powerRange } from '../utils/liveSignals.js';

/** The two sources of the RSSI: a dot per receiver of each transmission, or each receiver's own reports. */
export const FSK_SIGNAL_SOURCES = {
    fskMatched: { label: 'Matched', noun: 'transmission' },
    fskDetections: { label: 'Detections', noun: 'detection' },
};

/**
 * "Lotek FSK: Signal Strength": the RSSI of the tags' transmissions over time, a dot per
 * receiver (slot), in the slot's colour (as the SDRs': 0 blue, 1 yellow, 2 red, 3 green). Both
 * sources are read while the panel is open, so switching between them loses nothing.
 *
 * The RSSI is in dB on the receiver's own scale: the receiver sends a byte, -byte / 2 in 0.5 dB
 * steps (pylotekfsk's `rssi_dbm`), and Lotek's protocol document gives it no unit or calibration.
 * It compares receivers and transmissions, not powers at the antenna.
 */
export function liveFskSignal() {
    const serials = {}; // receiver serial by slot, from the detections

    return compose(lifecycleMixin(), serverModeMixin(), liveChartMixin(), {
        sources: FSK_SIGNAL_SOURCES,
        source: 'fskMatched',
        yLabel: 'RSSI (dB, receiver scale)',

        get noun() {
            return FSK_SIGNAL_SOURCES[this.source].noun;
        },

        liveSources() {
            return ['fskMatched', 'fskDetections'];
        },

        eventWindow() {
            return liveManager.windows[this.source];
        },

        // The axes need nothing from lotekfsk.yml
        loadLimits() {
            return null;
        },

        forEachDot(event, add) {
            for (const [slot, rssi] of Object.entries(event.values)) add(slot, rssi);
        },

        /** Which receiver each slot is: only detections say. */
        onTick() {
            const detections = liveManager.windows.fskDetections;
            detections.trim(this.now);
            for (const event of detections.events) {
                if (event.serial !== null) serials[event.key] = event.serial;
            }
        },

        yRange(min, max) {
            // The receivers report about what an SDR does in dBm: that range before there is data
            return powerRange(min, max, 'dBm');
        },

        formatY(value) {
            return formatTick(value, 1);
        },

        legendLabel(slot) {
            return serials[slot] ? `Receiver ${slot} (${serials[slot]})` : `Receiver ${slot}`;
        },

        tipRows(event) {
            const rows = [{ text: `Tag: ${event.tag}` }];
            if (this.source === 'fskMatched') {
                const duplicates = event.duplicates ? `, ${event.duplicates} duplicate${event.duplicates === 1 ? '' : 's'}` : '';
                rows.push({ text: `Heard by ${event.receivers ?? '–'} receiver${event.receivers === 1 ? '' : 's'}${duplicates}` });
            } else {
                rows.push({ text: `CRC: ${event.crcValid === null ? '–' : event.crcValid ? 'valid' : 'invalid'}` });
            }
            return [
                ...rows,
                ...Object.entries(event.values)
                    .sort(([a], [b]) => compareKeys(a, b))
                    .map(([slot, rssi]) => ({ text: `${this.legendLabel(slot)}: ${rssi.toFixed(1)} dB`, color: colorFor(slot) })),
            ];
        },

        /** Switch between the matched transmissions and the receivers' own detections. */
        setSource(source) {
            if (!(source in FSK_SIGNAL_SOURCES) || source === this.source) return;
            this.source = source;
            this.rebuildChart();
        },
    });
}

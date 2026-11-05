import { liveManager } from '../managers/liveManager.js';
import { compose } from '../mixins/compose.js';
import { lifecycleMixin } from '../mixins/lifecycleMixin.js';
import { liveChartMixin } from '../mixins/liveChartMixin.js';
import { serverModeMixin } from '../mixins/serverModeMixin.js';
import { colorFor, compareKeys, formatClock } from '../utils/liveSignals.js';
import { crcSummary, tagSummary } from '../utils/liveLotekfsk.js';

/**
 * "Lotek FSK: Tags": which tags were heard when, a row per tag and a dot per transmission (matched
 * record), in the tag's colour; and under the chart, a table of the tags in the time window: how
 * often, when last, the strongest RSSI per receiver, and how many of their detections had a bad CRC8.
 *
 * The rows are the tags in the window in the order of their IDs, so a tag keeps its row while
 * it is heard; a tag that arrives between two ticks goes below them until the next.
 */
export function liveFskTags() {
    let rows = new Map(); // tag -> row on the vertical axis

    const rowOf = tag => {
        if (!rows.has(tag)) rows.set(tag, rows.size);
        return rows.get(tag);
    };

    return compose(lifecycleMixin(), serverModeMixin(), liveChartMixin(), {
        noun: 'transmission', // what the count counts
        yLabel: 'Tag',
        tags: [], // the table: tagSummary() and the CRC of each
        slots: [], // the receivers in the table, its columns

        liveSources() {
            return ['fskMatched', 'fskDetections'];
        },

        eventWindow() {
            return liveManager.windows.fskMatched;
        },

        // The axes need nothing from lotekfsk.yml
        loadLimits() {
            return null;
        },

        forEachDot(event, add) {
            add(event.tag, rowOf(event.tag));
        },

        /** The rows for the tags of the window, and the table. */
        onTick(signals) {
            const shown = signals.events.slice(this.firstShown(signals, this.now));
            const summary = tagSummary(shown);
            rows = new Map([...summary.map(row => row.tag)].sort().map((tag, index) => [tag, index]));

            const detections = liveManager.windows.fskDetections;
            detections.trim(this.now);
            const crc = crcSummary(detections.events.slice(this.firstShown(detections, this.now)));
            const tags = summary.map(row => ({
                ...row,
                last: formatClock(row.lastMs / 1000, true),
                crc: crc[row.tag] ?? null,
            }));
            const slots = [...new Set(summary.flatMap(row => Object.keys(row.slots)))].sort(compareKeys);
            if (JSON.stringify(tags) !== JSON.stringify(this.tags)) this.tags = tags;
            if (JSON.stringify(slots) !== JSON.stringify(this.slots)) this.slots = slots;
        },

        /** A row per tag, half a row of room above and below. */
        yRange(min, max) {
            return [-0.5, Math.max(rows.size - 1, max ?? 0) + 0.5];
        },

        /** The tag of a row; nothing between rows. */
        formatY(value) {
            if (!Number.isInteger(value)) return '';
            for (const [tag, row] of rows) {
                if (row === value) return tag;
            }
            return '';
        },

        colorOf(tag) {
            return colorFor(tag);
        },

        // The axis names the tags
        legendLabel() {
            return null;
        },

        tipRows(event) {
            const duplicates = event.duplicates ? `, ${event.duplicates} duplicate${event.duplicates === 1 ? '' : 's'}` : '';
            return [
                { text: `Tag: ${event.tag}`, color: colorFor(event.tag) },
                { text: `Heard by ${event.receivers ?? '–'} receiver${event.receivers === 1 ? '' : 's'}${duplicates}` },
                ...Object.entries(event.values)
                    .sort(([a], [b]) => compareKeys(a, b))
                    .map(([slot, rssi]) => ({ text: `Receiver ${slot}: ${rssi.toFixed(1)} dB`, color: colorFor(slot) })),
            ];
        },

        /** The strongest RSSI of a tag at a receiver, for the table. */
        rssiText(row, slot) {
            const rssi = row.slots[slot];
            return rssi === undefined ? '–' : rssi.toFixed(1);
        },

        /** "3 of 12" detections with a bad CRC8, or "–" if the receivers do not report it. */
        crcText(row) {
            return row.crc ? `${row.crc.bad} of ${row.crc.checked}` : '–';
        },

        colorFor,
    });
}

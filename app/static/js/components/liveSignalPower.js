import { liveManager } from '../managers/liveManager.js';
import { compose } from '../mixins/compose.js';
import { lifecycleMixin } from '../mixins/lifecycleMixin.js';
import { liveChartMixin } from '../mixins/liveChartMixin.js';
import { serverModeMixin } from '../mixins/serverModeMixin.js';
import {
    colorFor,
    compareKeys,
    formatDuration,
    formatFrequency,
    formatTick,
    padRange,
    powerLimits,
    powerRange,
} from '../utils/liveSignals.js';

/** The two sources of the power: matched (dBW, uncalibrated) and calibrated (dBm at the antenna port). */
export const POWER_SOURCES = {
    matched: { label: 'Matched', unit: 'dBW' },
    calibrated: { label: 'Calibrated', unit: 'dBm' },
};

// How long a calibrated station may be silent before we say why that could be
const CALIBRATED_HINT_MS = 30 * 1000;

/**
 * "Radiotracking: Signal Power": the power of the matched signals over time, a dot per
 * SDR and signal, the diameter showing the duration. Both sources are read while the panel
 * is open, so switching between them loses nothing.
 */
export function liveSignalPower() {
    return compose(lifecycleMixin(), serverModeMixin(), liveChartMixin(), {
        noun: 'signal', // what the count counts
        sources: POWER_SOURCES,
        source: 'matched',

        get unit() {
            return POWER_SOURCES[this.source].unit;
        },

        get yLabel() {
            return `Power (${this.unit})`;
        },

        /** Nothing has arrived for the calibrated source: say why that may be. */
        get calibratedHint() {
            return this.source === 'calibrated'
                && this.status === 'connected'
                && this.count === 0
                && liveManager.received.calibrated === 0
                && this.now - liveManager.subscribedAt.calibrated >= CALIBRATED_HINT_MS;
        },

        liveSources() {
            return ['matched', 'calibrated'];
        },

        eventWindow() {
            return liveManager.windows[this.source];
        },

        forEachDot(event, add) {
            for (const [sdr, power] of Object.entries(event.values)) add(sdr, power);
        },

        /**
         * Matched power (dBW) has fixed limits: from radiotracking's signal threshold, below which
         * nothing is detected, to a static -30 dBW. Calibrated power (dBm at the antenna port) is
         * fitted to the data: the configuration does not bound it.
         */
        yRange(min, max) {
            const limits = this.source === 'matched' ? padRange(powerLimits(liveManager.limits)) : null;
            return limits ?? powerRange(min, max, this.unit);
        },

        formatY(value) {
            return formatTick(value, 1);
        },

        legendLabel(key) {
            return `SDR ${key}`;
        },

        tipRows(event) {
            return [
                { text: `Frequency: ${formatFrequency(event.frequency)}` },
                { text: `Duration: ${formatDuration(event.duration)}` },
                ...Object.entries(event.values)
                    .sort(([a], [b]) => compareKeys(a, b))
                    .map(([sdr, power]) => ({ text: `SDR ${sdr}: ${power.toFixed(1)} ${this.unit}`, color: colorFor(sdr) })),
            ];
        },

        /** Switch between matched (dBW) and calibrated (dBm) power. */
        setSource(source) {
            if (!(source in POWER_SOURCES) || source === this.source) return;
            this.source = source;
            this.rebuildChart();
        },
    });
}

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
    frequencyLimits,
    frequencyRange,
    padRange,
} from '../utils/liveSignals.js';

/**
 * "Radiotracking: Frequency Usage": the frequency of the matched signals over time, a dot
 * per signal, the diameter showing the duration.
 */
export function liveFrequencyUsage() {
    return compose(lifecycleMixin(), serverModeMixin(), liveChartMixin(), {
        noun: 'signal', // what the count counts
        yLabel: 'Frequency (MHz)',

        liveSources() {
            return ['matched'];
        },

        eventWindow() {
            return liveManager.windows.matched;
        },

        forEachDot(event, add) {
            if (event.frequency !== null) add('frequency', event.frequency / 1e6);
        },

        /** What the SDR sees: the centre frequency plus and minus half the sample rate, from the configuration. */
        yRange(min, max) {
            return padRange(frequencyLimits(liveManager.limits)) ?? frequencyRange(min, max);
        },

        formatY(value) {
            return formatTick(value, 3);
        },

        // One series: no legend
        legendLabel() {
            return null;
        },

        tipRows(event) {
            return [
                { text: `Frequency: ${formatFrequency(event.frequency)}` },
                { text: `Duration: ${formatDuration(event.duration)}` },
                ...Object.entries(event.values)
                    .sort(([a], [b]) => compareKeys(a, b))
                    .map(([sdr, power]) => ({ text: `SDR ${sdr}: ${power.toFixed(1)} dBW`, color: colorFor(sdr) })),
            ];
        },
    });
}

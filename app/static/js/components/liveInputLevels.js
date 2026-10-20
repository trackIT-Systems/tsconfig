import { liveManager } from '../managers/liveManager.js';
import { compose } from '../mixins/compose.js';
import { lifecycleMixin } from '../mixins/lifecycleMixin.js';
import { liveChartMixin } from '../mixins/liveChartMixin.js';
import { serverModeMixin } from '../mixins/serverModeMixin.js';
import { formatTick } from '../utils/liveSignals.js';
import { LEVEL_METRICS, levelColor, levelLabel, levelRange, pcmStateText } from '../utils/liveSoundscape.js';

/**
 * "Soundscapepipe: Input Levels": the peak and RMS level of every input channel (dBFS), a
 * dot per report. soundscapepipe reports every `status_interval` (60 s by default).
 */
export function liveInputLevels() {
    return compose(lifecycleMixin(), serverModeMixin(), liveChartMixin(), {
        noun: 'report', // what the count counts
        yLabel: 'Level (dBFS)',

        liveSources() {
            return ['levels'];
        },

        eventWindow() {
            return liveManager.windows.levels;
        },

        // The axes need nothing from soundscapepipe.yml
        loadLimits() {
            return null;
        },

        forEachDot(event, add) {
            for (const [key, value] of Object.entries(event.values)) {
                if (LEVEL_METRICS.includes(key.split('-')[0])) add(key, value);
            }
        },

        yRange(min) {
            return levelRange(min);
        },

        formatY(value) {
            return formatTick(value, 1);
        },

        colorOf(key) {
            return levelColor(key);
        },

        legendLabel(key) {
            return levelLabel(key);
        },

        tipRows(event) {
            const rows = Object.entries(event.values)
                .sort(([a], [b]) => a.split('-')[1] - b.split('-')[1] || a.localeCompare(b))
                .map(([key, value]) => ({
                    text: `${levelLabel(key)}: ${value.toFixed(1)} ${key.startsWith('snr') ? 'dB' : 'dBFS'}`,
                    color: key.startsWith('snr') ? undefined : levelColor(key),
                }));
            return [...rows, { text: `Capture: ${pcmStateText(event.state)}` }];
        },
    });
}

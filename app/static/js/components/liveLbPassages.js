import { liveManager } from '../managers/liveManager.js';
import { compose } from '../mixins/compose.js';
import { lifecycleMixin } from '../mixins/lifecycleMixin.js';
import { liveChartMixin } from '../mixins/liveChartMixin.js';
import { serverModeMixin } from '../mixins/serverModeMixin.js';
import { formatTick, spreadRange } from '../utils/liveSignals.js';
import { LB_DIRECTIONS, directionColor, directionLabel, orderBy } from '../utils/liveLightbarrier.js';

const byDirection = orderBy(LB_DIRECTIONS);

const seconds = value => (value === null ? '–' : `${value.toFixed(3)} s`);

/**
 * "Lightbarrier: Passages": a dot per passage that lightbarrierd detected, at its start, as high as
 * it took (first block to last unblock), in the colour of its direction.
 */
export function liveLbPassages() {
    return compose(lifecycleMixin(), serverModeMixin(), liveChartMixin(), {
        noun: 'passage', // what the count counts
        yLabel: 'Duration (s)',

        liveSources() {
            return ['lbEvents'];
        },

        eventWindow() {
            return liveManager.windows.lbEvents;
        },

        // The axes need nothing from lightbarrier.yml
        loadLimits() {
            return null;
        },

        forEachDot(event, add) {
            if (event.values.duration !== undefined) add(event.key, event.values.duration);
        },

        yRange(min, max) {
            const [, high] = spreadRange(0, max, 0.5, [0, 1]);
            return [0, high];
        },

        formatY(value) {
            return formatTick(value, 2);
        },

        colorOf(direction) {
            return directionColor(direction);
        },

        compareLegend(a, b) {
            return byDirection(a, b);
        },

        legendLabel(direction) {
            return directionLabel(direction);
        },

        tipRows(event) {
            return [
                { text: directionLabel(event.key), color: directionColor(event.key) },
                { text: `Duration: ${seconds(event.values.duration ?? null)}` },
                { text: `Outer to inner: ${seconds(event.deltaT)}` },
                { text: `Blocked (mean): ${seconds(event.avgBlock)}` },
                { text: `Triggers: ${event.triggers ?? '–'}` },
            ];
        },
    });
}

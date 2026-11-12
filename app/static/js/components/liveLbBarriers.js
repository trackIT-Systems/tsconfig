import { liveManager } from '../managers/liveManager.js';
import { compose } from '../mixins/compose.js';
import { lifecycleMixin } from '../mixins/lifecycleMixin.js';
import { liveChartMixin } from '../mixins/liveChartMixin.js';
import { serverModeMixin } from '../mixins/serverModeMixin.js';
import { formatTick, spreadRange } from '../utils/liveSignals.js';
import { LB_PAIRS, LB_STATE_VALUES, orderBy, pairColor, pairLabel } from '../utils/liveLightbarrier.js';

const byPair = orderBy(LB_PAIRS);

/**
 * "Lightbarrier: Barriers": a value of each barrier pair's state, a dot per pair and state.
 * lightbarrierd sends them every 60 s: the mean ambient voltage, the maximum with the LED on, the
 * mean difference (what is compared with `threshold`: below it the pair is blocked), and how long a
 * measurement took.
 */
export function liveLbBarriers() {
    return compose(lifecycleMixin(), serverModeMixin(), liveChartMixin(), {
        noun: 'state',
        params: LB_STATE_VALUES,
        param: 'diff',

        get yLabel() {
            const { label, unit } = LB_STATE_VALUES[this.param];
            return `${label} (${unit})`;
        },

        liveSources() {
            return ['lbState'];
        },

        eventWindow() {
            return liveManager.windows.lbState;
        },

        loadLimits() {
            return null;
        },

        // Not the whole state: only the value chosen, so the axis fits it
        forEachDot(event, add) {
            const value = event.values[this.param];
            if (value !== undefined) add(event.key, value);
        },

        yRange(min, max) {
            return LB_STATE_VALUES[this.param].unit === 'V'
                ? spreadRange(min, max, 0.2, [0, 1])
                : spreadRange(min, max, 0.002, [0, 0.01]);
        },

        formatY(value) {
            return formatTick(value, LB_STATE_VALUES[this.param].unit === 'V' ? 2 : 4);
        },

        colorOf(pair) {
            return pairColor(pair);
        },

        legendLabel(pair) {
            return pairLabel(pair);
        },

        tipRows(event) {
            return [
                { text: pairLabel(event.key), color: pairColor(event.key) },
                ...Object.entries(LB_STATE_VALUES)
                    .filter(([name]) => event.values[name] !== undefined)
                    .map(([name, { label, unit }]) => ({ text: `${label}: ${event.values[name].toFixed(3)} ${unit}` })),
            ];
        },

        /** Show another value of the states. */
        setParam(param) {
            if (!(param in LB_STATE_VALUES) || param === this.param) return;
            this.param = param;
            this.rebuildChart();
        },

        compareLegend(a, b) {
            return byPair(a, b);
        },
    });
}

import { liveManager } from '../managers/liveManager.js';
import { compose } from '../mixins/compose.js';
import { lifecycleMixin } from '../mixins/lifecycleMixin.js';
import { liveChartMixin } from '../mixins/liveChartMixin.js';
import { serverModeMixin } from '../mixins/serverModeMixin.js';
import { formatTick, spreadRange } from '../utils/liveSignals.js';
import { envValueNames, splitUnit } from '../utils/liveLightbarrier.js';

const DEFAULT_PARAM = 'Temperature (°C)';

/**
 * "Environment": a value of envsensed's readings (temperature, humidity, ...), a dot per sensor
 * and reading, the sensor's `id` in envsense.yml naming the series. envsensed reads every
 * `hardware.interval_s` (60 s by default). The values to choose from are those that arrived.
 */
export function liveEnvironment() {
    return compose(lifecycleMixin(), serverModeMixin(), liveChartMixin(), {
        noun: 'reading',
        param: DEFAULT_PARAM,
        paramOptions: [DEFAULT_PARAM],

        get yLabel() {
            return this.param;
        },

        liveSources() {
            return ['envsense'];
        },

        eventWindow() {
            return liveManager.windows.envsense;
        },

        loadLimits() {
            return null;
        },

        forEachDot(event, add) {
            const value = event.values[this.param];
            if (value !== undefined) add(event.key, value);
        },

        onTick(signals) {
            const names = envValueNames(signals.events);
            if (!names.includes(DEFAULT_PARAM)) names.unshift(DEFAULT_PARAM);
            if (names.join('\n') !== this.paramOptions.join('\n')) this.paramOptions = names;
        },

        yRange(min, max) {
            const { unit } = splitUnit(this.param);
            if (unit === '%') return spreadRange(min, max, 10, [0, 100]);
            if (unit === '°C') return spreadRange(min, max, 4, [0, 30]);
            return spreadRange(min, max, 0.1, [0, 1]);
        },

        formatY(value) {
            return formatTick(value, 2);
        },

        legendLabel(sensor) {
            return sensor;
        },

        tipRows(event) {
            return [
                { text: `Sensor: ${event.key}`, color: this.colorOf(event.key) },
                ...Object.entries(event.values).map(([name, value]) => {
                    const { label, unit } = splitUnit(name);
                    return { text: `${label}: ${value.toFixed(2)}${unit ? ` ${unit}` : ''}` };
                }),
            ];
        },

        /** Show another value of the readings. */
        setParam(param) {
            if (param === this.param || !this.paramOptions.includes(param)) return;
            this.param = param;
            this.rebuildChart();
        },
    });
}

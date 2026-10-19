import { liveManager } from '../managers/liveManager.js';
import { compose } from '../mixins/compose.js';
import { lifecycleMixin } from '../mixins/lifecycleMixin.js';
import { liveChartMixin } from '../mixins/liveChartMixin.js';
import { serverModeMixin } from '../mixins/serverModeMixin.js';
import {
    RAW_PARAMS,
    colorFor,
    durationLimits,
    formatDuration,
    formatFrequency,
    formatTick,
    frequencyLimits,
    padRange,
    powerLimits,
    rawParam,
    spreadRange,
    valueRange,
} from '../utils/liveSignals.js';

/**
 * "Radiotracking: Signal Detail": the raw signals of every SDR over time, a dot per
 * signal, the diameter showing the duration, and the vertical axis showing a value of the
 * signal to choose (max, avg, noise, snr, ...).
 *
 * The raw signals are many more than the matched ones, so they are only read after the
 * panel was told to (`collecting`), to spare the connection, over LTE for instance.
 */
export function liveSignalDetail() {
    return compose(lifecycleMixin(), serverModeMixin(), liveChartMixin(), {
        noun: 'raw signal', // what the count counts
        param: RAW_PARAMS[0],
        paramOptions: RAW_PARAMS.map(name => ({ name, label: rawParam(name).label })),
        collecting: liveManager.prefs.collectRaw, // kept while the page is open

        get yLabel() {
            return rawParam(this.param).label;
        },

        liveSources() {
            return this.collecting ? ['raw'] : [];
        },

        liveWindows() {
            return ['raw'];
        },

        eventWindow() {
            return liveManager.windows.raw;
        },

        forEachDot(event, add) {
            const value = event.values[this.param];
            if (value !== undefined) add(event.key, value * rawParam(this.param).scale);
        },

        /**
         * The axis of the chosen value. Max and avg power have the limits of Signal Power, frequency
         * those of Frequency Usage and duration the configured shortest and longest signal. The
         * other dB values (std, noise, snr) are fitted to the data, but never span less than 10 dB.
         */
        yRange(min, max) {
            const limits = liveManager.limits;
            const configured = {
                'max (dBW)': () => powerLimits(limits),
                'avg (dBW)': () => powerLimits(limits),
                Frequency: () => frequencyLimits(limits),
                Duration: () => durationLimits(limits),
            }[this.param]?.();
            if (configured) return padRange(configured);
            return /\(dB[A-Za-z]*\)$/.test(this.param) ? spreadRange(min, max, 10) : valueRange(min, max);
        },

        formatY(value) {
            return formatTick(value, 2);
        },

        legendLabel(key) {
            return `SDR ${key}`;
        },

        /** The values to choose from: the usual ones, and any other the signals have. */
        onTick(signals) {
            const names = new Set(RAW_PARAMS);
            for (const event of signals.events) {
                for (const name of Object.keys(event.values)) names.add(name);
            }
            const options = [...names].map(name => ({ name, label: rawParam(name).label }));
            if (JSON.stringify(options) !== JSON.stringify(this.paramOptions)) this.paramOptions = options;
        },

        tipRows(event) {
            const rows = [
                { text: `SDR ${event.key}`, color: colorFor(event.key) },
                { text: `Frequency: ${formatFrequency(event.frequency)}` },
                { text: `Duration: ${formatDuration(event.duration)}` },
            ];
            for (const [name, value] of Object.entries(event.values)) {
                if (name === 'Frequency' || name === 'Duration') continue;
                rows.push({ text: `${name}: ${value.toFixed(2)}` });
            }
            return rows;
        },

        setParam(param) {
            this.param = param;
            this.rebuildChart();
        },

        /** Start or stop reading the raw signals. What was read stays until it is five minutes old. */
        toggleCollect() {
            if (!this.ready) return;
            this.collecting = !this.collecting;
            liveManager.prefs.collectRaw = this.collecting;
            this.useSources(this.liveSources());
        },
    });
}

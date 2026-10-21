import { liveManager } from '../managers/liveManager.js';
import { serverModeManager } from '../managers/serverModeManager.js';
import { compose } from '../mixins/compose.js';
import { lifecycleMixin } from '../mixins/lifecycleMixin.js';
import { liveChartMixin } from '../mixins/liveChartMixin.js';
import { serverModeMixin } from '../mixins/serverModeMixin.js';
import { colorFor, formatTick } from '../utils/liveSignals.js';
import {
    DETECTION_DELAY_MS,
    analysisBlock,
    configuredDetectors,
    detectorColor,
    fallsBehind,
    formatDelay,
    latestReports,
    speciesNames,
} from '../utils/liveSoundscape.js';

/** Seconds for a detection's length: "5.0 s", "19.8 ms". */
function formatSeconds(seconds) {
    if (seconds === null) return '–';
    return seconds < 1 ? `${(seconds * 1000).toFixed(1)} ms` : `${seconds.toFixed(1)} s`;
}

/** What `GET <path>` returns, or null if it fails: the panel works without it. */
async function getJson(path) {
    try {
        const response = await fetch(serverModeManager.buildApiUrl(path));
        return response.ok ? await response.json() : null;
    } catch (error) {
        console.error(`Failed to load ${path}:`, error);
        return null;
    }
}

/**
 * "Soundscapepipe: Detections": the confidence of each detection over time, a dot per
 * detection in the colour of its species. A button per detector shows or hides its
 * detections: the detectors of soundscapepipe.yml, and any other that sends some.
 *
 * The detectors' inference reports (every `status_interval`) give how long a window takes from
 * its first sample to its detections (`delay_max`, the slowest window of the report) and how long a
 * window is: a box per detector, in its colour, shades the block of audio it is analysing now
 * (`analysisBlock()`). Its button has the same colour and shows the delay.
 */
export function liveDetections() {
    let names = {}; // species names by what detections call them (yolobat: `Nnoc`)

    return compose(lifecycleMixin(), serverModeMixin(), liveChartMixin(), {
        noun: 'detection', // what the count counts
        yLabel: 'Confidence',
        clockToleranceMs: DETECTION_DELAY_MS,
        detectors: [], // the buttons
        hiddenDetectors: [...liveManager.prefs.hiddenDetectors], // the manager's, copied for Alpine to see
        reports: {}, // the latest inference report of each detector

        liveSources() {
            return ['detections', 'performance'];
        },

        eventWindow() {
            return liveManager.windows.detections;
        },

        /** The configured detectors and the species names, instead of radiotracking's limits (the axes are fixed). */
        async loadLimits() {
            const [config, species] = await Promise.all([
                getJson('/api/soundscapepipe'),
                getJson('/api/soundscapepipe/species'),
            ]);
            this.detectors = [...new Set([...configuredDetectors(config), ...this.detectors])];
            names = speciesNames(species);
        },

        isShown(detector) {
            return !this.hiddenDetectors.includes(detector);
        },

        /** Show or hide a detector's detections. What is held is kept, so they come back at once. */
        toggleDetector(detector) {
            const hidden = this.isShown(detector)
                ? [...this.hiddenDetectors, detector]
                : this.hiddenDetectors.filter(name => name !== detector);
            liveManager.prefs.hiddenDetectors = hidden;
            this.hiddenDetectors = hidden;
            this.tick();
        },

        forEachDot(event, add) {
            if (event.values.confidence !== undefined && this.isShown(event.key)) {
                add(event.species, event.values.confidence);
            }
        },

        /**
         * A button for a detector that is not configured, as soon as it sends; the count of what
         * is shown; the latest inference reports.
         */
        onTick(signals) {
            const performance = liveManager.windows.performance;
            performance.trim(this.now);
            const reports = latestReports(performance.events);
            if (JSON.stringify(reports) !== JSON.stringify(this.reports)) this.reports = reports;

            const events = signals.events;
            const first = this.firstShown(signals, this.now);
            const seen = new Set([...this.detectors, ...Object.keys(reports)]);
            let shown = 0;
            for (let i = first; i < events.length; i++) {
                seen.add(events[i].key);
                if (this.isShown(events[i].key)) shown++;
            }
            if (seen.size !== this.detectors.length) this.detectors = [...seen];
            this.count = shown;
        },

        /** A box per detector shown, in its colour, over the block of audio it is analysing now. */
        spans(edge) {
            return this.detectors
                .filter(detector => this.isShown(detector))
                .map(detector => ({ ...analysisBlock(this.reports[detector], edge), color: detectorColor(detector) }))
                .filter(span => span.from !== undefined);
        },

        /** "8.1 s": how far a detector is behind real time, from its latest report. */
        delayOf(detector) {
            const delay = this.reports[detector]?.delay_max;
            return Number.isFinite(delay) ? formatDelay(delay) : '';
        },

        behind(detector) {
            return fallsBehind(this.reports[detector]);
        },

        /** The button in the detector's colour: filled while its detections are shown, outlined while not. */
        buttonStyle(detector) {
            const color = detectorColor(detector);
            return this.isShown(detector)
                ? `background-color: ${color}; border-color: ${color}; color: #fff`
                : `background-color: transparent; border-color: ${color}; color: ${color}`;
        },

        detectorTitle(detector) {
            const report = this.reports[detector];
            const action = this.isShown(detector) ? 'Hide' : 'Show';
            if (!report) return `${action} the detections of ${detector}`;
            const parts = [
                Number.isFinite(report.delay_max) ? `detections up to ${formatDelay(report.delay_max)} after the audio` : null,
                Number.isFinite(report.load) ? `load ${Math.round(report.load * 100)} %` : null,
                Number.isFinite(report.lag) ? `lag ${formatDelay(report.lag)}` : null,
                report.lost > 0 ? `${formatDelay(report.lost)} of audio not analysed` : null,
            ].filter(Boolean);
            return `${action} the detections of ${detector}. ${parts.join(', ')}`;
        },

        /** Under the chart: what the boxes are, with each block's length and delay. */
        get pendingText() {
            const blocks = this.detectors
                .filter(detector => this.isShown(detector) && analysisBlock(this.reports[detector], 0))
                .map(detector => {
                    const { window, delay_max: delay } = this.reports[detector];
                    return `${detector} ${formatDelay(window)} blocks, ${formatDelay(delay)} behind`;
                });
            return blocks.length > 0
                ? `Shaded: the block of audio each detector is analysing now (${blocks.join(', ')})`
                : '';
        },

        yRange() {
            return [-0.03, 1.03];
        },

        formatY(value) {
            return formatTick(value, 2);
        },

        speciesName(species) {
            return names[species]?.scientific ?? species;
        },

        legendLabel(species) {
            return this.speciesName(species);
        },

        tipRows(event) {
            const { english, german } = names[event.species] ?? {};
            const rows = [
                { text: this.speciesName(event.species), color: colorFor(event.species) },
                ...[english, german].filter(Boolean).map(text => ({ text })),
                { text: `Confidence: ${event.values.confidence.toFixed(2)}` },
                { text: `Detector: ${event.key}, channel ${event.channel ?? '–'}` },
                { text: `Duration: ${formatSeconds(event.seconds)}` },
            ];
            if (event.low !== null && event.high !== null) {
                rows.push({ text: `Frequency: ${(event.low / 1000).toFixed(1)}–${(event.high / 1000).toFixed(1)} kHz` });
            }
            if (event.callType !== null) rows.push({ text: `Call type: ${event.callType}` });
            return rows;
        },
    });
}

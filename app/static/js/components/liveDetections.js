import { liveManager } from '../managers/liveManager.js';
import { serverModeManager } from '../managers/serverModeManager.js';
import { compose } from '../mixins/compose.js';
import { lifecycleMixin } from '../mixins/lifecycleMixin.js';
import { liveChartMixin } from '../mixins/liveChartMixin.js';
import { serverModeMixin } from '../mixins/serverModeMixin.js';
import { colorFor, formatTick } from '../utils/liveSignals.js';
import {
    DETECTION_DELAY_MS,
    configuredDetectors,
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
 * its first sample to its detections (`delay_max`, the slowest window of the report). A dashed line
 * per detector marks how far it has analysed all of the audio: left of its line its detections are
 * complete, right of it more may come. The buttons show the delay too.
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

        /**
         * A line per detector shown, at its latest delay before the end of the axis: how far it has
         * analysed the audio. The slowest first, so its label is the lowest.
         */
        markers(edge) {
            return this.detectors
                .filter(detector => this.isShown(detector) && Number.isFinite(this.reports[detector]?.delay_max))
                .map(detector => ({ detector, delay: this.reports[detector].delay_max }))
                .sort((a, b) => b.delay - a.delay)
                .map(({ detector, delay }) => ({ at: edge - delay, label: `${detector} ${formatDelay(delay)}` }));
        },

        /** "8.1 s": how far a detector is behind real time, from its latest report. */
        delayOf(detector) {
            const delay = this.reports[detector]?.delay_max;
            return Number.isFinite(delay) ? formatDelay(delay) : '';
        },

        behind(detector) {
            return fallsBehind(this.reports[detector]);
        },

        buttonClass(detector) {
            const kind = this.behind(detector) ? 'warning' : 'primary';
            return this.isShown(detector) ? `btn-${kind}` : `btn-outline-${kind}`;
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

        /** Under the chart: what the dashed lines are. */
        get pendingText() {
            const shown = this.detectors.filter(detector => this.isShown(detector) && this.delayOf(detector));
            return shown.length > 0
                ? 'Dashed lines: left of its line a detector has analysed all of the audio; right of it, more detections may come'
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

import { liveManager } from '../managers/liveManager.js';
import { serverModeManager } from '../managers/serverModeManager.js';
import { compose } from '../mixins/compose.js';
import { lifecycleMixin } from '../mixins/lifecycleMixin.js';
import { liveChartMixin } from '../mixins/liveChartMixin.js';
import { serverModeMixin } from '../mixins/serverModeMixin.js';
import { colorFor, formatTick } from '../utils/liveSignals.js';
import { DETECTION_DELAY_MS, configuredDetectors, speciesNames } from '../utils/liveSoundscape.js';

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
 */
export function liveDetections() {
    let names = {}; // species names by what detections call them (yolobat: `Nnoc`)

    return compose(lifecycleMixin(), serverModeMixin(), liveChartMixin(), {
        noun: 'detection', // what the count counts
        yLabel: 'Confidence',
        clockToleranceMs: DETECTION_DELAY_MS,
        detectors: [], // the buttons
        hiddenDetectors: [...liveManager.prefs.hiddenDetectors], // the manager's, copied for Alpine to see

        liveSources() {
            return ['detections'];
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

        /** A button for a detector that is not configured, as soon as it sends; the count of what is shown. */
        onTick(signals) {
            const events = signals.events;
            const first = this.firstShown(signals, this.now);
            const seen = new Set(this.detectors);
            let shown = 0;
            for (let i = first; i < events.length; i++) {
                seen.add(events[i].key);
                if (this.isShown(events[i].key)) shown++;
            }
            if (seen.size !== this.detectors.length) this.detectors = [...seen];
            this.count = shown;
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

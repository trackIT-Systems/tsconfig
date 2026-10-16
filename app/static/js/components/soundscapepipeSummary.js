import { serverModeManager } from '../managers/serverModeManager.js';
import { serviceManager } from '../managers/serviceManager.js';
import { compose } from '../mixins/compose.js';
import { lifecycleMixin } from '../mixins/lifecycleMixin.js';

/**
 * The Soundscapepipe card of the status page: a summary of soundscapepipe.yml
 * and the state of the service. Reloads whenever the status page has refreshed
 * ('status-refreshed').
 */
export function soundscapepipeSummary() {
    return compose(lifecycleMixin(), {
        soundscapepipeConfig: null,
        soundscapepipeLoading: false,
        soundscapepipeService: null,

        init() {
            const onServices = () => {
                this.soundscapepipeService = serviceManager.findService('soundscapepipe');
            };
            serviceManager.subscribe(onServices);
            this.onDestroy(() => serviceManager.unsubscribe(onServices));
            this.listen(window, 'status-refreshed', event => this.onStatusRefreshed(event.detail));
        },

        async onStatusRefreshed({ availableServices }) {
            if (availableServices.includes('soundscapepipe')) {
                await this.loadSoundscapepipeConfig();
            } else {
                // The service is no longer available
                this.soundscapepipeConfig = null;
            }
        },

        async loadSoundscapepipeConfig() {
            this.soundscapepipeLoading = true;
            try {
                const response = await fetch(serverModeManager.buildApiUrl('/api/soundscapepipe'));
                if (response.ok) {
                    this.soundscapepipeConfig = await response.json();
                } else {
                    // If config doesn't exist or service isn't configured, clear it
                    this.soundscapepipeConfig = null;
                }
            } catch (err) {
                console.error('Failed to load soundscapepipe config:', err);
                this.soundscapepipeConfig = null;
            } finally {
                this.soundscapepipeLoading = false;
            }
        },

        // Names of the tasks of a detector, for the badges
        getDetectorTasks(detectorName) {
            const detector = this.soundscapepipeConfig?.detectors?.[detectorName];
            if (!detector || !detector.tasks || !Array.isArray(detector.tasks)) {
                return [];
            }
            return detector.tasks.map(task => task.name || 'Unnamed').filter(Boolean);
        },

        getRecordingGroups() {
            if (!this.soundscapepipeConfig || !this.soundscapepipeConfig.groups) {
                return [];
            }
            return Object.keys(this.soundscapepipeConfig.groups);
        },
    });
}

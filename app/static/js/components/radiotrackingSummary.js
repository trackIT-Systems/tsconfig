import { serverModeManager } from '../managers/serverModeManager.js';
import { compose } from '../mixins/compose.js';
import { lifecycleMixin } from '../mixins/lifecycleMixin.js';

/**
 * The Radiotracking card of the status page: a summary of radiotracking.ini.
 * Reloads whenever the status page has refreshed ('status-refreshed').
 */
export function radiotrackingSummary() {
    return compose(lifecycleMixin(), {
        radiotrackingConfig: null,
        radiotrackingLoading: false,

        init() {
            this.listen(window, 'status-refreshed', event => this.onStatusRefreshed(event.detail));
        },

        async onStatusRefreshed({ availableServices }) {
            if (availableServices.includes('radiotracking')) {
                await this.loadRadiotrackingConfig();
            } else {
                // The service is no longer available
                this.radiotrackingConfig = null;
            }
        },

        async loadRadiotrackingConfig() {
            this.radiotrackingLoading = true;
            try {
                const response = await fetch(serverModeManager.buildApiUrl('/api/radiotracking'));
                if (response.ok) {
                    this.radiotrackingConfig = await response.json();
                } else {
                    // If config doesn't exist or service isn't configured, clear it
                    this.radiotrackingConfig = null;
                }
            } catch (err) {
                console.error('Failed to load radiotracking config:', err);
                this.radiotrackingConfig = null;
            } finally {
                this.radiotrackingLoading = false;
            }
        },
    });
}

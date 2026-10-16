import { serverModeManager } from '../managers/serverModeManager.js';
import { serviceManager } from '../managers/serviceManager.js';
import { compose } from '../mixins/compose.js';
import { lifecycleMixin } from '../mixins/lifecycleMixin.js';
import { serviceActionMixin } from '../mixins/serviceActionMixin.js';
import { serviceLogsMixin } from '../mixins/serviceLogsMixin.js';
import { showToastMixin } from '../mixins/showToastMixin.js';

/**
 * The System Services card of the status page: the systemd units with their
 * state, and Start / Stop / Restart / Logs for each.
 *
 * It shows what serviceManager holds and follows it: the status page's
 * refresh fetches the units, every subscriber is updated.
 */
export function servicesList() {
    return compose(lifecycleMixin(), serviceActionMixin(), serviceLogsMixin(), showToastMixin(), {
        services: [],
        servicesLoading: true,
        actionLoading: false,

        init() {
            // A server has no systemd units of its own to show
            if (serverModeManager.isEnabled()) {
                this.servicesLoading = false;
                return;
            }
            const onServices = services => {
                this.services = services;
                this.servicesLoading = false;
            };
            serviceManager.subscribe(onServices);
            this.onDestroy(() => serviceManager.unsubscribe(onServices));
            // The refresh ended without units (it failed): do not show the spinner for ever
            this.listen(window, 'status-refreshed', () => {
                this.servicesLoading = false;
            });
        },

        /** Units to show: those not marked expert-only, unless expert mode is on. */
        getFilteredServices(expertMode) {
            if (expertMode) {
                return this.services;
            }
            return this.services.filter(service => !service.expert);
        },

        // serviceActionMixin refreshes the status after an action through this
        async loadServices() {
            this.servicesLoading = true;
            try {
                await serviceManager.getServices(true);
            } catch (err) {
                this.showToast(`Failed to load services: ${err.message}`, 'error');
                console.error('Services load error:', err);
                // Keep showing the previous data
            } finally {
                this.servicesLoading = false;
            }
        },

        streamAllLogs() {
            this.streamLogs('all');
        },
    });
}

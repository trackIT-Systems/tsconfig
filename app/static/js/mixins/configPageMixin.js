import { serverModeManager } from '../managers/serverModeManager.js';
import { serviceManager } from '../managers/serviceManager.js';
import { sendJson, serviceAction } from '../utils/apiUtils.js';
import { getSystemRefreshInterval } from '../utils/systemUtils.js';
import { compose } from './compose.js';
import { lifecycleMixin } from './lifecycleMixin.js';
import { saveStateMixin } from './saveStateMixin.js';
import { serverModeMixin } from './serverModeMixin.js';
import { serviceLogsMixin } from './serviceLogsMixin.js';
import { serviceActionMixin } from './serviceActionMixin.js';
import { toastMixin } from './toastMixin.js';

/**
 * What the settings pages of a service share: a status box for its systemd
 * unit that refreshes while the tab is shown, Save, Save & Restart and Deploy
 * (server mode), toasts, and the log viewer.
 *
 * The component provides `config` and `loadConfig()`, and overrides where it
 * differs: `buildPayload()` (what is sent, default `this.config`),
 * `loadData()` (what init() loads, default `loadConfig()`), `onSaved()`,
 * `validate()` and `init()` itself if its start-up is special.
 *
 * @param {object} options
 * @param {string} options.service - systemd unit shown in the status box and restarted by Save & Restart
 * @param {string} options.apiPath - config API, e.g. '/api/schedule'
 * @param {string} options.title - toast title; failures use `errorTitle`
 * @param {string} [options.errorTitle] - default `${title} Error`
 * @param {string} options.failureText - error text if the API's answer has none
 * @param {string[]} options.refreshOn - hash path (e.g. ['settings']) on which the status is refreshed
 * @param {number} [options.refreshSeconds] - status refresh interval, default the system's status_refresh_interval
 * @param {number} [options.initDelayMs=50] - pause before init() loads, so tabs do not all request at once
 * @param {string} [options.savedMessage] - toast after Save, default the API's message
 */
export function configPageMixin(options) {
    const {
        service,
        apiPath,
        title,
        errorTitle = `${title} Error`,
        failureText,
        refreshOn,
        refreshSeconds,
        initDelayMs = 50,
        savedMessage,
    } = options;

    return compose(lifecycleMixin(), serverModeMixin(), toastMixin(title, errorTitle), serviceLogsMixin(), saveStateMixin(), serviceActionMixin(), {
        serviceStatus: {
            active: false,
            enabled: false,
            status: 'unknown',
            uptime: 'N/A',
        },
        serviceStatusLoading: false,
        actionLoading: false,

        // --- start-up

        async init() {
            // Small delay to prevent simultaneous API calls during page load
            await new Promise(resolve => setTimeout(resolve, initDelayMs));
            await this.loadData();
            await this.startServiceStatusRefresh();
            // Server mode: another config group was chosen
            this.listen(window, 'config-group-changed', () => this.loadData());
            this.subscribe();
        },

        /** Override to listen for events of other components (use this.listen). */
        subscribe() {},

        /** What init() and a change of config group load. */
        async loadData() {
            await this.loadConfig();
        },

        // --- service status

        async loadServiceStatus() {
            this.serviceStatusLoading = true;
            try {
                const services = await serviceManager.getServices();
                const unit = services.find(candidate => candidate.name === service);
                if (unit) {
                    this.serviceStatus = {
                        active: unit.active,
                        enabled: unit.enabled,
                        status: unit.status,
                        uptime: unit.uptime || 'N/A',
                    };
                }
            } catch (error) {
                console.error(`Failed to load ${service} service status:`, error);
            } finally {
                this.serviceStatusLoading = false;
            }
        },

        /** Refresh the status box periodically, while the tab is shown (not in server mode, where there is no service). */
        async startServiceStatusRefresh() {
            const seconds = refreshSeconds ?? (await getSystemRefreshInterval());
            // destroy() may have run while this was still loading (tab left)
            this.every(seconds * 1000, () => {
                const hash = window.location.hash.slice(1).split('/');
                if (refreshOn.every((segment, i) => hash[i] === segment) && !this.serverMode) {
                    this.loadServiceStatus();
                }
            });
        },

        // --- saving

        /** The body of the PUT; override where the page's data is not the config as it is. */
        buildPayload() {
            return this.config;
        },

        /** Throw to refuse saving (after telling the user why). */
        validate() {},

        /** Called after a successful save, and after the restart that follows Save & Restart. */
        onSaved() {},

        async putConfig() {
            this.validate();
            return sendJson(serverModeManager.buildApiUrl(apiPath), this.buildPayload(), failureText);
        },

        // Save only; deployment is triggered separately via deployConfig()
        async saveConfig() {
            await this.handleSaveConfig(async () => {
                const data = await this.putConfig();
                // Server mode shows its message in handleSaveConfig
                if (!this.serverMode) {
                    this.showMessage(savedMessage ?? data.message, false);
                }
                this.onSaved();
            });
        },

        async saveAndRestartService() {
            await this.handleSaveAndRestartConfig(
                async () => {
                    const data = await this.putConfig();
                    this.showMessage(data.message, false);
                },
                async () => {
                    const data = await serviceAction(service, 'restart');
                    this.showMessage(`Configuration saved and ${data.message}`, false);
                    this.onSaved();
                    // Refresh the status after the restart
                    if (!this.serverMode) {
                        setTimeout(() => this.loadServiceStatus(), 2000);
                    }
                }
            );
        },

        async deployConfig() {
            await this.handleDeployConfig(serverModeManager.getCurrentConfigGroup());
        },
    });
}

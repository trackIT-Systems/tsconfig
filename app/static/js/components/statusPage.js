import { serverModeManager } from '../managers/serverModeManager.js';
import { serviceManager } from '../managers/serviceManager.js';
import { systemConfigManager } from '../managers/systemConfigManager.js';
import { compose } from '../mixins/compose.js';
import { lifecycleMixin } from '../mixins/lifecycleMixin.js';
import { showToastMixin } from '../mixins/showToastMixin.js';
import { apiUrl, getJsonShared } from '../utils/apiUtils.js';
import { formatBytes, formatDateTime, formatUptime } from '../utils/format.js';

/**
 * The status page: the system's state (uptime, load, memory, disks, time,
 * network, modem) and the refresh that keeps it current.
 *
 * The cards that load and show their own data are components of their own
 * inside this one's template: servicesList, radiotrackingSummary,
 * soundscapepipeSummary, powerSummary and geolocationMap. They follow the refresh: the
 * systemd units through serviceManager, the configs through the
 * 'status-refreshed' event, which carries the available services.
 */
export function statusPage() {
    return compose(lifecycleMixin(), showToastMixin(), {
        systemInfo: null,
        loading: true,
        refreshing: false,
        refreshIntervalSeconds: 30, // Default value, will be loaded from config
        // Reboot functionality
        rebootLoading: false,
        // Timedatectl status properties
        timedatectlStatus: null,
        timedatectlLoading: false,
        timedatectlError: null,
        // Network connectivity status properties
        networkConnectivity: null,
        networkConnectivityError: null,
        // Modem information properties
        modemInfo: null,
        modemError: null,
        // Services that have a config on this device (which cards to fill)
        availableServices: [],

        async initStatus() {
            // Don't initialize in server mode
            if (serverModeManager.isEnabled()) {
                return;
            }

            // Load system configuration first to get refresh interval
            await this.loadSystemConfig();
            // Load available services to check which services are configured
            await this.loadAvailableServices();
            // Also loads the configs of the cards (through 'status-refreshed')
            await this.refreshStatus();
            // Auto-refresh using configured interval when status tab is active
            // (not started if the tab was left while the data above was loading)
            this.every(this.refreshIntervalSeconds * 1000, () => {
                // Only refresh if status tab is active
                const currentHash = window.location.hash.slice(1);
                if (currentHash === 'status' || (currentHash === '' && this.activeConfig === 'status')) {
                    this.refreshStatus();
                }
            });
        },

        async refreshStatus() {
            // Use different loading states for initial load vs refresh
            if (this.systemInfo) {
                this.refreshing = true;
            } else {
                this.loading = true;
            }

            try {
                // Refresh system status, services, timedatectl, network connectivity, and modem info in parallel
                // Force refresh services (the services card follows serviceManager)
                const [statusResponse, timedatectlResponse, networkConnectivityResponse, modemResponse] = await Promise.all([
                    getJsonShared(apiUrl('/api/system-status')),
                    fetch(apiUrl('/api/timedatectl-status')),
                    fetch(apiUrl('/api/network-connectivity')),
                    fetch(apiUrl('/api/network/modem')),
                    serviceManager.getServices(true) // Force refresh
                ]);

                if (!statusResponse.ok) {
                    throw new Error(`HTTP ${statusResponse.status}: ${statusResponse.statusText}`);
                }

                const data = statusResponse.data;
                this.systemInfo = data;

                // Reset reboot loading state when uptime is refreshed
                // This handles the case where the system was rebooted and status is refreshed
                if (data.uptime !== undefined) {
                    this.rebootLoading = false;
                }

                // Handle timedatectl status
                if (timedatectlResponse.ok) {
                    this.timedatectlStatus = await timedatectlResponse.json();
                    this.timedatectlError = null;
                } else {
                    this.timedatectlError = `Failed to load timedatectl status: HTTP ${timedatectlResponse.status}`;
                    console.error('Timedatectl status error:', this.timedatectlError);
                }

                // Handle network connectivity status
                if (networkConnectivityResponse.ok) {
                    this.networkConnectivity = await networkConnectivityResponse.json();
                    this.networkConnectivityError = null;
                } else {
                    this.networkConnectivityError = `Failed to load network connectivity status: HTTP ${networkConnectivityResponse.status}`;
                    console.error('Network connectivity status error:', this.networkConnectivityError);
                }

                // Handle modem information
                if (modemResponse.ok) {
                    // Modem endpoint returns null if no modem found, which is valid
                    this.modemInfo = await modemResponse.json();
                    this.modemError = null;
                } else {
                    this.modemError = `Failed to load modem information: HTTP ${modemResponse.status}`;
                    console.error('Modem information error:', this.modemError);
                }
            } catch (err) {
                this.showToast(`Failed to load system status: ${err.message}`, 'error', { title: 'System Status Error' });
                console.error('Status refresh error:', err);
            } finally {
                this.loading = false;
                this.refreshing = false;
                // The cards that show their own data reload now
                window.dispatchEvent(new CustomEvent('status-refreshed', {
                    detail: { availableServices: this.availableServices },
                }));
            }
        },

        async loadSystemConfig() {
            try {
                const data = await systemConfigManager.getSystemConfig();
                this.refreshIntervalSeconds = data.status_refresh_interval || 30;
            } catch (err) {
                console.warn('Failed to load system config, using default refresh interval:', err);
                // Keep default value of 30 seconds
            }
        },

        // For the templates
        formatUptime,
        formatDateTime,
        formatBytes,

        async rebootSystem() {
            // Show confirmation dialog
            if (!confirm('Are you sure you want to reboot the system? This will restart the device and temporarily interrupt all services.')) {
                return;
            }

            this.rebootLoading = true;

            try {
                const response = await fetch(apiUrl('/api/systemd/reboot'), {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    }
                });

                if (!response.ok) {
                    const error = await response.json();
                    throw new Error(error.detail || 'Failed to reboot system');
                }

                const data = await response.json();

                // Show success message
                this.showToast(data.message || 'System reboot initiated. The system will restart shortly.', 'success', { title: 'System Reboot' });

                // Keep the loading state since the system will reboot
                // The page will become inaccessible, so no need to reset loading state

            } catch (err) {
                this.rebootLoading = false;

                // Show error message
                this.showToast(`Failed to reboot system: ${err.message}`, 'error', { title: 'System Reboot Failed' });

                console.error('Reboot error:', err);
            }
        },

        // Function to get filtered disks based on expert mode
        getFilteredDisks(expertMode) {
            if (expertMode) {
                // Expert mode: show all disks
                return this.systemInfo?.disk || [];
            } else {
                // Non-expert mode: show only devices with /data mountpoint
                return (this.systemInfo?.disk || []).filter(disk => {
                    const mountpoints = disk.mountpoints || [disk.mountpoint];
                    return mountpoints.includes('/data');
                });
            }
        },

        // Mountpoints with a Filebrowser link where it can show them: /data and
        // its subdirectories, and /mnt/<x> (served as /data/files/<x>). The
        // template renders paths with x-text, never as HTML.
        mountpointLinks(mountpoints) {
            const browsable = (mp) => {
                if (mp === '/data') return '';
                for (const prefix of ['/data/', '/mnt/']) {
                    if (mp.startsWith(prefix) && mp.length > prefix.length) {
                        return mp.substring(prefix.length);
                    }
                }
                return null;
            };
            return (mountpoints || []).map(mp => {
                const path = browsable(mp);
                if (path === null) return { path: mp, href: null };
                const encoded = path.split('/').map(encodeURIComponent).join('/');
                return { path: mp, href: path ? `/data/files/${encoded}/` : '/data/files/' };
            });
        },

        // Available services methods
        async loadAvailableServices() {
            try {
                // Build URL with config_group parameter if in server mode
                let url = '/api/available-services';
                if (serverModeManager.isEnabled() && serverModeManager.getCurrentConfigGroup()) {
                    url += `?config_group=${encodeURIComponent(serverModeManager.getCurrentConfigGroup())}`;
                }

                const response = await getJsonShared(apiUrl(url));
                if (response.ok) {
                    this.availableServices = response.data.available_services || [];
                } else {
                    console.error('Failed to load available services');
                    this.availableServices = [];
                }
            } catch (error) {
                console.error('Error loading available services:', error);
                this.availableServices = [];
            }
        },

        isServiceAvailable(serviceName) {
            return this.availableServices.includes(serviceName);
        },
    });
}

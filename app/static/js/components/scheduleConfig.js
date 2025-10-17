import { saveStateMixin } from '../mixins/saveStateMixin.js';
import { serviceActionMixin } from '../mixins/serviceActionMixin.js';
import { serviceManager } from '../managers/serviceManager.js';
import { getSystemRefreshInterval } from '../utils/systemUtils.js';
import { parseTimeString, updateTimeString } from '../utils/timeUtils.js';

import { apiUrl } from '../utils/apiUtils.js';

export function scheduleConfig() {
    return {
        ...saveStateMixin(),
        ...serviceActionMixin(),
        config: {
            force_on: false,
            button_delay: "00:00",
            schedule: []
        },
        // Service status tracking
        serviceStatus: {
            active: false,
            enabled: false,
            status: 'unknown',
            uptime: 'N/A'
        },
        serviceStatusLoading: false,
        refreshInterval: null, // For periodic service status refresh
        actionLoading: false,

        // Server mode helper
        get serverMode() {
            return window.serverModeManager?.isEnabled() || false;
        },

        async init() {
            // Small delay to prevent simultaneous API calls during page load
            await new Promise(resolve => setTimeout(resolve, 50));
            
            // Load configuration and set up periodic refresh
            await this.loadConfig();
            await this.setupPeriodicRefresh();
            
            // Listen for config group changes in server mode
            window.addEventListener('config-group-changed', async () => {
                await this.loadConfig();
            });
        },

        async setupPeriodicRefresh() {
            // Get the refresh interval from system config
            const refreshIntervalSeconds = await getSystemRefreshInterval();
            
            // Set up periodic refresh for service status
            if (this.refreshInterval) {
                clearInterval(this.refreshInterval);
            }
            
            this.refreshInterval = setInterval(() => {
                // Only refresh if settings tab is active
                const currentHash = window.location.hash.slice(1);
                const mainTab = currentHash.split('/')[0];
                if (mainTab === 'settings') {
                    this.loadServiceStatus();
                }
            }, refreshIntervalSeconds * 1000);
        },

        cleanup() {
            // Clean up interval when component is destroyed
            if (this.refreshInterval) {
                clearInterval(this.refreshInterval);
                this.refreshInterval = null;
            }
        },

        async refreshConfig() {
            try {
                // Reset to initial state
                this.config = {
                    force_on: false,
                    button_delay: "00:00",
                    schedule: []
                };
                
                // Clear any existing messages (now handled by toasts)
                
                // Reload the configuration
                await this.loadConfig();
            } catch (error) {
                this.showMessage(error.message, true);
            }
        },

        async loadConfig() {
            try {
                // Build API URL with config_group parameter if in server mode
                const url = window.serverModeManager?.buildApiUrl('/api/schedule') || '/api/schedule';
                const response = await fetch(url);
                if (response.status === 404) {
                    // Set default configuration for schedule
                    this.config = {
                        force_on: false,
                        button_delay: "01:00",
                        schedule: []
                    };
                    this.showMessage("No schedule configuration found. Using default values.", false);
                    return;
                }
                if (!response.ok) {
                    throw new Error('Failed to load schedule configuration');
                }
                const data = await response.json();
                this.config = data;
                
                // Process schedule entries to add UI helper properties
                this.config.schedule.forEach(entry => {
                    const startParts = parseTimeString(entry.start);
                    entry.startReference = startParts.reference;
                    entry.startSign = startParts.sign;
                    entry.startOffset = startParts.offset;
                    
                    const stopParts = parseTimeString(entry.stop);
                    entry.stopReference = stopParts.reference;
                    entry.stopSign = stopParts.sign;
                    entry.stopOffset = stopParts.offset;
                });
                
                // Load service status only in tracker mode (default mode)
                if (!this.serverMode) {
                    await this.loadServiceStatus();
                }
            } catch (error) {
                this.showMessage(error.message, true);
            }
        },

        async loadServiceStatus() {
            this.serviceStatusLoading = true;
            try {
                const services = await serviceManager.getServices();
                const wittypidService = services.find(service => service.name === 'wittypid');
                if (wittypidService) {
                    this.serviceStatus = {
                        active: wittypidService.active,
                        enabled: wittypidService.enabled,
                        status: wittypidService.status,
                        uptime: wittypidService.uptime || 'N/A'
                    };
                }
            } catch (error) {
                console.error('Failed to load service status:', error);
            } finally {
                this.serviceStatusLoading = false;
            }
        },

        parseTimeString,
        updateTimeString,

        addSchedule() {
            this.config.schedule.push({
                name: '',
                start: '00:00',
                stop: '00:00',
                startReference: 'time',
                startOffset: '00:00',
                startSign: '+',
                stopReference: 'time',
                stopOffset: '00:00',
                stopSign: '+'
            });
        },

        removeSchedule(index) {
            this.config.schedule.splice(index, 1);
        },

        async saveConfig() {
            const configSaveFunction = async () => {
                // Build API URL with config_group parameter if in server mode
                const url = window.serverModeManager?.buildApiUrl('/api/schedule') || '/api/schedule';
                const response = await fetch(url, {
                    method: 'PUT',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify(this.config)
                });
                if (!response.ok) {
                    const error = await response.json();
                    let errorMessage = error.detail?.message || 'Failed to save schedule configuration';
                    if (error.detail?.errors) {
                        errorMessage += '\nValidation errors: ' + error.detail.errors.join(', ');
                    }
                    if (error.detail?.validation_errors) {
                        const validationErrors = error.detail.validation_errors.map(err => 
                            `${err.loc.join('.')}: ${err.msg}`
                        ).join(', ');
                        errorMessage += '\nValidation errors: ' + validationErrors;
                    }
                    throw new Error(errorMessage);
                }
                const data = await response.json();
                this.showMessage(data.message, false);  // false means not an error, so it will be success
            };
            
            await this.handleSaveConfig(configSaveFunction);
        },

        async saveAndRestartService() {
            const configSaveFunction = async () => {
                // Build API URL with config_group parameter if in server mode
                const url = window.serverModeManager?.buildApiUrl('/api/schedule') || '/api/schedule';
                const response = await fetch(url, {
                    method: 'PUT',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify(this.config)
                });
                if (!response.ok) {
                    const error = await response.json();
                    let errorMessage = error.detail?.message || 'Failed to save schedule configuration';
                    if (error.detail?.errors) {
                        errorMessage += '\nValidation errors: ' + error.detail.errors.join(', ');
                    }
                    if (error.detail?.validation_errors) {
                        const validationErrors = error.detail.validation_errors.map(err => 
                            `${err.loc.join('.')}: ${err.msg}`
                        ).join(', ');
                        errorMessage += '\nValidation errors: ' + validationErrors;
                    }
                    throw new Error(errorMessage);
                }
                const data = await response.json();
                this.showMessage(data.message, false);  // false means not an error, so it will be success
            };
            
            const restartFunction = async () => {
                // Then restart the wittypid service
                const response = await fetch(apiUrl('/api/systemd/action'), {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({
                        service: 'wittypid',
                        action: 'restart'
                    })
                });
                
                const data = await response.json();
                
                if (!response.ok) {
                    throw new Error(data.detail || 'Failed to restart wittypid service');
                }
                
                this.showMessage(`Configuration saved and ${data.message}`, false);
                
                // Refresh service status after restart
                setTimeout(async () => {
                    await this.loadServiceStatus();
                }, 2000);
            };
            
            await this.handleSaveAndRestartConfig(configSaveFunction, restartFunction);
        },

        showMessage(message, isError) {
            // Use toast for immediate feedback
            if (window.toastManager) {
                const type = isError ? 'error' : 'success';
                const title = isError ? 'Schedule Configuration Error' : 'Schedule Configuration';
                window.toastManager.show(message, type, { title });
            }
            
            // Also dispatch a custom event that the parent can listen for (backward compatibility)
            window.dispatchEvent(new CustomEvent('show-message', {
                detail: { message, isError }
            }));
        }
    }
}


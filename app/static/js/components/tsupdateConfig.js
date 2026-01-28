import { saveStateMixin } from '../mixins/saveStateMixin.js';
import { serviceActionMixin } from '../mixins/serviceActionMixin.js';
import { serviceManager } from '../managers/serviceManager.js';
import { getSystemRefreshInterval } from '../utils/systemUtils.js';
import { apiUrl } from '../utils/apiUtils.js';
import { parseTimeString, updateTimeString } from '../utils/timeUtils.js';

export function tsupdateConfig() {
    return {
        ...saveStateMixin(),
        ...serviceActionMixin(),
        config: {
            check_interval: 3600,
            check_interval_hhmm: '01:00', // UI representation in HH:MM format
            include_prereleases: false,
            github_url: null,
            max_releases: 5,
            persist_timeout: 600,
            persist_timeout_hhmm: '00:10', // UI representation in HH:MM format
            update_countdown: 60,
            update_countdown_hhmm: '00:01', // UI representation in HH:MM format
            do: 'nothing',
            maintenance_check_interval: 3600,
            maintenance_check_interval_hhmm: '01:00', // UI representation in HH:MM format
            maintenance_do: 'check'
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
        
        // Maintenance schedule entry from schedule.yml (read by tsupdate)
        maintenanceSchedule: null,
        maintenanceScheduleLoading: false,

        // Server mode helper
        get serverMode() {
            return window.serverModeManager?.isEnabled() || false;
        },

        async init() {
            // Small delay to prevent simultaneous API calls during page load
            await new Promise(resolve => setTimeout(resolve, 50));
            
            // Load configuration and set up periodic refresh
            await this.loadConfig();
            await this.loadMaintenanceSchedule();
            await this.setupPeriodicRefresh();
            
            // Listen for config group changes in server mode
            window.addEventListener('config-group-changed', async () => {
                await this.loadConfig();
                await this.loadMaintenanceSchedule();
            });
            
            // Listen for schedule changes to refresh maintenance schedule display
            window.addEventListener('schedule-config-saved', async () => {
                await this.loadMaintenanceSchedule();
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
                // Only refresh if settings tab is active and not in server mode
                const currentHash = window.location.hash.slice(1);
                const mainTab = currentHash.split('/')[0];
                if (mainTab === 'settings' && !this.serverMode) {
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

        async loadServiceStatus() {
            this.serviceStatusLoading = true;
            try {
                const services = await serviceManager.getServices();
                const tsupdateService = services.find(service => service.name === 'tsupdate');
                if (tsupdateService) {
                    this.serviceStatus = {
                        active: tsupdateService.active,
                        enabled: tsupdateService.enabled,
                        status: tsupdateService.status,
                        uptime: tsupdateService.uptime || 'N/A'
                    };
                }
            } catch (error) {
                console.error('Failed to load service status:', error);
            } finally {
                this.serviceStatusLoading = false;
            }
        },

        // Helper function to convert seconds to HH:MM format
        secondsToHHMM(seconds) {
            const hours = Math.floor(seconds / 3600);
            const minutes = Math.floor((seconds % 3600) / 60);
            return `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}`;
        },

        // Helper function to convert HH:MM format to seconds
        hhmmToSeconds(hhmm) {
            if (!hhmm || typeof hhmm !== 'string') {
                return 3600; // Default 1 hour
            }
            const parts = hhmm.split(':');
            if (parts.length !== 2) {
                return 3600; // Default 1 hour
            }
            const hours = parseInt(parts[0], 10) || 0;
            const minutes = parseInt(parts[1], 10) || 0;
            return (hours * 3600) + (minutes * 60);
        },
        
        // Load maintenance schedule entry from schedule.yml
        async loadMaintenanceSchedule() {
            this.maintenanceScheduleLoading = true;
            try {
                // Build API URL with config_group parameter if in server mode
                const url = window.serverModeManager?.buildApiUrl('/api/schedule') || '/api/schedule';
                const response = await fetch(url);
                if (response.status === 404) {
                    this.maintenanceSchedule = null;
                    return;
                }
                if (!response.ok) {
                    console.warn('Failed to load schedule configuration for maintenance interval');
                    this.maintenanceSchedule = null;
                    return;
                }
                const scheduleData = await response.json();
                
                // Find the maintenance entry
                const maintenanceEntry = scheduleData.schedule?.find(entry => entry.name === 'maintenance');
                
                if (maintenanceEntry) {
                    // Process the entry to add UI helper properties (same as scheduleConfig does)
                    const startParts = parseTimeString(maintenanceEntry.start);
                    const stopParts = parseTimeString(maintenanceEntry.stop);
                    
                    this.maintenanceSchedule = {
                        name: maintenanceEntry.name,
                        start: maintenanceEntry.start,
                        stop: maintenanceEntry.stop,
                        startReference: startParts.reference,
                        startSign: startParts.sign,
                        startOffset: startParts.offset,
                        stopReference: stopParts.reference,
                        stopSign: stopParts.sign,
                        stopOffset: stopParts.offset
                    };
                } else {
                    this.maintenanceSchedule = null;
                }
            } catch (error) {
                console.error('Failed to load maintenance schedule:', error);
                this.maintenanceSchedule = null;
            } finally {
                this.maintenanceScheduleLoading = false;
            }
        },
        
        // Expose parseTimeString and updateTimeString for use in template
        parseTimeString,
        updateTimeString,

        async refreshConfig() {
            try {
                // Reset to initial state
                this.config = {
                    check_interval: 3600,
                    check_interval_hhmm: '01:00',
                    include_prereleases: false,
                    github_url: null,
                    max_releases: 5,
                    persist_timeout: 600,
                    persist_timeout_hhmm: '00:10',
                    update_countdown: 60,
                    update_countdown_hhmm: '00:01',
                    do: 'nothing',
                    maintenance_check_interval: 3600,
                    maintenance_check_interval_hhmm: '01:00',
                    maintenance_do: 'check'
                };
                
                // Reload the configuration
                await this.loadConfig();
            } catch (error) {
                this.showMessage(error.message, true);
            }
        },

        async loadConfig() {
            try {
                // Build API URL with config_group parameter if in server mode
                const url = window.serverModeManager?.buildApiUrl('/api/tsupdate') || '/api/tsupdate';
                const response = await fetch(url);
                if (response.status === 404) {
                    // Set default configuration
                    this.config = {
                        check_interval: 3600,
                        check_interval_hhmm: '01:00',
                        include_prereleases: false,
                        github_url: null,
                        max_releases: 5,
                        persist_timeout: 600,
                        persist_timeout_hhmm: '00:10',
                        update_countdown: 60,
                        update_countdown_hhmm: '00:01',
                        do: 'nothing',
                        maintenance_check_interval: 3600,
                        maintenance_check_interval_hhmm: '01:00',
                        maintenance_do: 'check'
                    };
                    this.showMessage("No tsupdate configuration found. Using default values.", false);
                    return;
                }
                if (!response.ok) {
                    throw new Error('Failed to load tsupdate configuration');
                }
                const data = await response.json();
                // Ensure github_url is null if empty string
                if (data.github_url === '') {
                    data.github_url = null;
                }
                // Convert time fields from seconds to HH:MM format for UI
                data.check_interval_hhmm = this.secondsToHHMM(data.check_interval || 3600);
                data.persist_timeout_hhmm = this.secondsToHHMM(data.persist_timeout || 600);
                data.update_countdown_hhmm = this.secondsToHHMM(data.update_countdown || 60);
                // Convert maintenance_check_interval if present, otherwise use default
                if (data.maintenance_check_interval !== null && data.maintenance_check_interval !== undefined) {
                    data.maintenance_check_interval_hhmm = this.secondsToHHMM(data.maintenance_check_interval);
                } else {
                    data.maintenance_check_interval = 3600;
                    data.maintenance_check_interval_hhmm = '01:00';
                }
                // Ensure do has a default value
                if (!data.do) {
                    data.do = 'nothing';
                }
                // Ensure maintenance_do has a default value
                if (!data.maintenance_do) {
                    data.maintenance_do = 'check';
                }
                this.config = data;
                
                // Load service status only in tracker mode (default mode)
                if (!this.serverMode) {
                    await this.loadServiceStatus();
                }
            } catch (error) {
                this.showMessage(error.message, true);
            }
        },

        async saveConfig() {
            const configSaveFunction = async () => {
                // Build API URL with config_group parameter if in server mode
                const url = window.serverModeManager?.buildApiUrl('/api/tsupdate') || '/api/tsupdate';
                
                // Prepare config for sending - convert HH:MM to seconds and remove UI-only fields
                const configToSend = { ...this.config };
                // Convert check_interval_hhmm to check_interval (seconds)
                configToSend.check_interval = this.hhmmToSeconds(configToSend.check_interval_hhmm);
                delete configToSend.check_interval_hhmm; // Remove UI-only field
                
                // Convert maintenance_check_interval_hhmm if present and not empty
                if (configToSend.maintenance_check_interval_hhmm && configToSend.maintenance_check_interval_hhmm.trim() !== '') {
                    configToSend.maintenance_check_interval = this.hhmmToSeconds(configToSend.maintenance_check_interval_hhmm);
                }
                delete configToSend.maintenance_check_interval_hhmm; // Remove UI-only field
                
                // Remove null/empty optional maintenance fields
                if (configToSend.maintenance_check_interval === null || configToSend.maintenance_check_interval === undefined) {
                    delete configToSend.maintenance_check_interval;
                }
                if (configToSend.maintenance_do === null || configToSend.maintenance_do === undefined || configToSend.maintenance_do === '') {
                    delete configToSend.maintenance_do;
                }
                
                if (configToSend.github_url === '') {
                    configToSend.github_url = null;
                }
                
                const response = await fetch(url, {
                    method: 'PUT',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify(configToSend)
                });
                if (!response.ok) {
                    const error = await response.json();
                    let errorMessage = error.detail?.message || 'Failed to save tsupdate configuration';
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
                
                // Only show message in tracker mode (server mode will show messages in the mixin)
                if (!this.serverMode) {
                    this.showMessage(data.message, false);  // false means not an error, so it will be success
                }
            };
            
            // In server mode, save and deploy; in tracker mode, just save
            if (this.serverMode) {
                const configGroup = window.serverModeManager?.getCurrentConfigGroup();
                await this.handleSaveAndDeployConfig(configSaveFunction, configGroup);
            } else {
                await this.handleSaveConfig(configSaveFunction);
            }
        },

        async saveAndRestartService() {
            const configSaveFunction = async () => {
                // Build API URL with config_group parameter if in server mode
                const url = window.serverModeManager?.buildApiUrl('/api/tsupdate') || '/api/tsupdate';
                
                // Prepare config for sending - convert HH:MM to seconds and remove UI-only fields
                const configToSend = { ...this.config };
                // Convert time fields from HH:MM to seconds
                configToSend.check_interval = this.hhmmToSeconds(configToSend.check_interval_hhmm);
                configToSend.persist_timeout = this.hhmmToSeconds(configToSend.persist_timeout_hhmm);
                configToSend.update_countdown = this.hhmmToSeconds(configToSend.update_countdown_hhmm);
                // Convert maintenance_check_interval_hhmm if present and not empty
                if (configToSend.maintenance_check_interval_hhmm && configToSend.maintenance_check_interval_hhmm.trim() !== '') {
                    configToSend.maintenance_check_interval = this.hhmmToSeconds(configToSend.maintenance_check_interval_hhmm);
                }
                // Remove UI-only fields
                delete configToSend.check_interval_hhmm;
                delete configToSend.persist_timeout_hhmm;
                delete configToSend.update_countdown_hhmm;
                delete configToSend.maintenance_check_interval_hhmm;
                
                // Remove null/empty optional maintenance fields
                if (configToSend.maintenance_check_interval === null || configToSend.maintenance_check_interval === undefined) {
                    delete configToSend.maintenance_check_interval;
                }
                if (configToSend.maintenance_do === null || configToSend.maintenance_do === undefined || configToSend.maintenance_do === '') {
                    delete configToSend.maintenance_do;
                }
                
                if (configToSend.github_url === '') {
                    configToSend.github_url = null;
                }
                
                const response = await fetch(url, {
                    method: 'PUT',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify(configToSend)
                });
                if (!response.ok) {
                    const error = await response.json();
                    let errorMessage = error.detail?.message || 'Failed to save tsupdate configuration';
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
                // Then restart the tsupdate service
                const response = await fetch(apiUrl('/api/systemd/action'), {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({
                        service: 'tsupdate',
                        action: 'restart'
                    })
                });
                
                const data = await response.json();
                
                if (!response.ok) {
                    throw new Error(data.detail || 'Failed to restart tsupdate service');
                }
                
                this.showMessage(`Configuration saved and ${data.message}`, false);
                
                // Refresh service status after restart (only in tracker mode)
                if (!this.serverMode) {
                    setTimeout(async () => {
                        await this.loadServiceStatus();
                    }, 2000);
                }
            };
            
            await this.handleSaveAndRestartConfig(configSaveFunction, restartFunction);
        },

        showMessage(message, isError) {
            // Use toast for immediate feedback
            if (window.toastManager) {
                const type = isError ? 'error' : 'success';
                const title = isError ? 'Updates Configuration Error' : 'Updates Configuration';
                window.toastManager.show(message, type, { title });
            }
            
            // Also dispatch a custom event that the parent can listen for (backward compatibility)
            window.dispatchEvent(new CustomEvent('show-message', {
                detail: { message, isError }
            }));
        }
    }
}


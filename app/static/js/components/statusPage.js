import { saveStateMixin } from '../mixins/saveStateMixin.js';
import { serviceManager } from '../managers/serviceManager.js';
import { systemConfigManager } from '../managers/systemConfigManager.js';
import { getSystemRefreshInterval } from '../utils/systemUtils.js';

export function statusPage() {
    return {
        ...saveStateMixin(),
        systemInfo: null,
        loading: true,
        refreshing: false,
        statusError: null,
        lastUpdated: null,
        refreshInterval: null,
        refreshIntervalSeconds: 30, // Default value, will be loaded from config
        // Systemd services properties
        services: [],
        servicesLoading: false,
        servicesError: null,
        actionLoading: false,
        actionMessage: '',
        actionError: false,
        // Service restart states (track individual service restart states)
        serviceRestartStates: {}, // serviceName -> 'idle' | 'restarting' | 'restarted'
        // Service start/stop states (track individual service start/stop states)
        serviceStartStopStates: {}, // serviceName -> 'idle' | 'starting' | 'stopping' | 'started' | 'stopped'
        // Reboot functionality
        rebootLoading: false,
        // Reboot protection functionality
        rebootProtectionEnabled: false,
        rebootProtectionLoading: false,
        // Timedatectl status properties
        timedatectlStatus: null,
        timedatectlLoading: false,
        timedatectlError: null,

        async initStatus() {
            // Load reboot protection status
            await this.loadRebootProtectionStatus();
            // Load system configuration first to get refresh interval
            await this.loadSystemConfig();
            await this.refreshStatus();
            await this.loadServices();
            // Auto-refresh using configured interval when status tab is active
            this.refreshInterval = setInterval(() => {
                // Only refresh if status tab is active
                const currentHash = window.location.hash.slice(1);
                if (currentHash === 'status' || (currentHash === '' && this.activeConfig === 'status')) {
                    this.refreshStatus();
                    this.loadServices();
                }
            }, this.refreshIntervalSeconds * 1000);
        },

        async refreshStatus() {
            // Use different loading states for initial load vs refresh
            if (this.systemInfo) {
                this.refreshing = true;
            } else {
                this.loading = true;
            }
            this.statusError = null;
            
            try {
                // Refresh system status, services, and timedatectl in parallel
                // Force refresh services since this is a manual refresh
                const [statusResponse, timedatectlResponse] = await Promise.all([
                    fetch('/api/system-status'),
                    fetch('/api/timedatectl-status'),
                    serviceManager.getServices(true) // Force refresh
                ]);
                
                if (!statusResponse.ok) {
                    throw new Error(`HTTP ${statusResponse.status}: ${statusResponse.statusText}`);
                }
                
                const data = await statusResponse.json();
                this.systemInfo = data;
                this.lastUpdated = new Date().toLocaleTimeString();
                
                // Handle timedatectl status
                if (timedatectlResponse.ok) {
                    const timedatectlData = await timedatectlResponse.json();
                    this.timedatectlStatus = timedatectlData;
                    this.timedatectlError = null;
                } else {
                    this.timedatectlError = `Failed to load timedatectl status: HTTP ${timedatectlResponse.status}`;
                    console.error('Timedatectl status error:', this.timedatectlError);
                }
                
                // Update local services data from the forced refresh
                this.services = serviceManager.services;
            } catch (err) {
                this.statusError = `Failed to load system status: ${err.message}`;
                console.error('Status refresh error:', err);
            } finally {
                this.loading = false;
                this.refreshing = false;
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

        formatUptime(seconds) {
            if (!seconds) return 'N/A';
            
            const days = Math.floor(seconds / (24 * 3600));
            const hours = Math.floor((seconds % (24 * 3600)) / 3600);
            const minutes = Math.floor((seconds % 3600) / 60);
            
            if (days > 0) {
                return `${days}d ${hours}h ${minutes}m`;
            } else if (hours > 0) {
                return `${hours}h ${minutes}m`;
            } else {
                return `${minutes}m`;
            }
        },

        formatDateTime(isoString) {
            if (!isoString) return 'N/A';
            
            try {
                const date = new Date(isoString);
                return date.toLocaleString();
            } catch (err) {
                return isoString;
            }
        },

        formatBytes(bytes) {
            if (!bytes || bytes === 0 || isNaN(bytes) || !isFinite(bytes)) return '0 B';
            
            const k = 1024;
            const sizes = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'];
            const i = Math.floor(Math.log(bytes) / Math.log(k));
            
            const formattedValue = parseFloat((bytes / Math.pow(k, i)).toFixed(2));
            
            // Additional safety check for the final result
            if (isNaN(formattedValue) || !isFinite(formattedValue)) return '0 B';
            
            return formattedValue + ' ' + sizes[i];
        },

        // Systemd services methods
        async loadServices() {
            this.servicesLoading = true;
            this.servicesError = null;
            
            try {
                const data = await serviceManager.getServices();
                // Only update services if request was successful
                this.services = data;
                // Clear any previous errors on successful load
                this.servicesError = null;
            } catch (err) {
                this.servicesError = `Failed to load services: ${err.message}`;
                console.error('Services load error:', err);
                // Don't clear services on error, keep showing previous data
            } finally {
                this.servicesLoading = false;
            }
        },

        // Function to get filtered services based on expert mode
        getFilteredServices(expertMode) {
            if (expertMode) {
                return this.services;
            } else {
                return this.services.filter(service => !service.expert);
            }
        },

        setServiceRestartState(serviceName, state) {
            this.serviceRestartStates[serviceName] = state;
            if (state === 'restarted') {
                setTimeout(() => {
                    this.serviceRestartStates[serviceName] = 'idle';
                }, 5000);
            }
        },

        getServiceRestartState(serviceName) {
            return this.serviceRestartStates[serviceName] || 'idle';
        },

        setServiceStartStopState(serviceName, state) {
            this.serviceStartStopStates[serviceName] = state;
            
            // Auto-reset after 5 seconds for completed states
            if (state === 'started' || state === 'stopped') {
                setTimeout(() => {
                    this.serviceStartStopStates[serviceName] = 'idle';
                }, 5000);
            }
        },

        getServiceStartStopState(serviceName) {
            return this.serviceStartStopStates[serviceName] || 'idle';
        },

        async performAction(serviceName, action) {
            this.actionLoading = true;
            this.actionMessage = '';
            this.actionError = false;
            
            // For restart actions, set the service-specific state
            if (action === 'restart') {
                this.setServiceRestartState(serviceName, 'restarting');
            }
            
            try {
                const response = await fetch('/api/systemd/action', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({
                        service: serviceName,
                        action: action
                    })
                });
                
                const data = await response.json();
                
                if (!response.ok) {
                    throw new Error(data.detail || `Failed to ${action} service`);
                }
                
                this.actionMessage = data.message;
                this.actionError = false;
                
                // For restart actions, set the service-specific state
                if (action === 'restart') {
                    this.setServiceRestartState(serviceName, 'restarted');
                }
                
                // Refresh services list after action
                setTimeout(async () => {
                    const services = await serviceManager.getServices(true); // Force refresh
                    this.services = services;
                }, 1000);
                
                // Clear message after 5 seconds
                setTimeout(() => {
                    this.actionMessage = '';
                }, 5000);
                
            } catch (err) {
                this.actionMessage = err.message;
                this.actionError = true;
                console.error(`Service ${action} error:`, err);
                
                // For restart actions, reset the service-specific state
                if (action === 'restart') {
                    this.setServiceRestartState(serviceName, 'idle');
                }
                
                // Clear error message after 10 seconds
                setTimeout(() => {
                    this.actionMessage = '';
                    this.actionError = false;
                }, 10000);
            } finally {
                this.actionLoading = false;
            }
        },

        async performStartStopAction(serviceName, action) {
            this.actionLoading = true;
            this.actionMessage = '';
            this.actionError = false;
            
            // Set the service-specific state
            if (action === 'start') {
                this.setServiceStartStopState(serviceName, 'starting');
            } else if (action === 'stop') {
                this.setServiceStartStopState(serviceName, 'stopping');
            }
            
            try {
                const response = await fetch('/api/systemd/action', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({
                        service: serviceName,
                        action: action
                    })
                });
                
                const data = await response.json();
                
                if (!response.ok) {
                    throw new Error(data.detail || `Failed to ${action} service`);
                }
                
                this.actionMessage = data.message;
                this.actionError = false;
                
                // Set the service-specific state to completed
                if (action === 'start') {
                    this.setServiceStartStopState(serviceName, 'started');
                } else if (action === 'stop') {
                    this.setServiceStartStopState(serviceName, 'stopped');
                }
                
                // Refresh services list after action
                setTimeout(async () => {
                    const services = await serviceManager.getServices(true); // Force refresh
                    this.services = services;
                }, 1000);
                
                // Clear message after 5 seconds
                setTimeout(() => {
                    this.actionMessage = '';
                }, 5000);
                
            } catch (err) {
                this.actionMessage = err.message;
                this.actionError = true;
                console.error(`Service ${action} error:`, err);
                
                // Reset the service-specific state on error
                this.setServiceStartStopState(serviceName, 'idle');
                
                // Clear error message after 10 seconds
                setTimeout(() => {
                    this.actionMessage = '';
                    this.actionError = false;
                }, 10000);
            } finally {
                this.actionLoading = false;
            }
        },

        async toggleEnable(serviceName, currentlyEnabled) {
            this.actionLoading = true;
            this.actionMessage = '';
            this.actionError = false;
            
            try {
                // Determine the action based on current state
                const action = currentlyEnabled ? 'disable' : 'enable';
                
                const response = await fetch('/api/systemd/action', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({
                        service: serviceName,
                        action: action
                    })
                });
                
                const data = await response.json();
                
                if (!response.ok) {
                    throw new Error(data.detail || `Failed to ${action} service`);
                }
                
                this.actionMessage = data.message;
                this.actionError = false;
                
                // Refresh services list after action
                setTimeout(async () => {
                    const services = await serviceManager.getServices(true); // Force refresh
                    this.services = services;
                }, 1000);
                
                // Clear message after 5 seconds
                setTimeout(() => {
                    this.actionMessage = '';
                }, 5000);
                
            } catch (err) {
                this.actionMessage = err.message;
                this.actionError = true;
                console.error(`Service toggle error:`, err);
                
                // Clear error message after 10 seconds
                setTimeout(() => {
                    this.actionMessage = '';
                    this.actionError = false;
                }, 10000);
            } finally {
                this.actionLoading = false;
            }
        },

        streamLogs(serviceName) {
            // Show the log modal
            const modal = new bootstrap.Modal(document.getElementById('logModal'));
            // Get the log viewer instance and start streaming
            const logViewerEl = document.getElementById('logModal');
            if (logViewerEl && logViewerEl._x_dataStack) {
                const logViewerInstance = logViewerEl._x_dataStack[0];
                logViewerInstance.startStreaming(serviceName);
            }
            modal.show();
            
            // Scroll to bottom after modal is shown and content is rendered
            setTimeout(() => {
                const container = document.getElementById('logContainer');
                if (container) {
                    container.scrollTop = container.scrollHeight;
                }
            }, 250);
        },

        async rebootSystem() {
            // Show confirmation dialog
            if (!confirm('Are you sure you want to reboot the system? This will restart the device and temporarily interrupt all services.')) {
                return;
            }

            this.rebootLoading = true;
            
            try {
                const response = await fetch('/api/systemd/reboot', {
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
                window.dispatchEvent(new CustomEvent('show-message', {
                    detail: { 
                        message: data.message || 'System reboot initiated. The system will restart shortly.', 
                        isError: false 
                    }
                }));
                
                // Keep the loading state since the system will reboot
                // The page will become inaccessible, so no need to reset loading state
                
            } catch (err) {
                this.rebootLoading = false;
                
                // Show error message
                window.dispatchEvent(new CustomEvent('show-message', {
                    detail: { 
                        message: `Failed to reboot system: ${err.message}`, 
                        isError: true 
                    }
                }));
                
                console.error('Reboot error:', err);
            }
        },

        // Reboot protection methods
        async loadRebootProtectionStatus() {
            try {
                const response = await fetch('/api/systemd/reboot-protection');
                if (!response.ok) {
                    throw new Error(`HTTP ${response.status}: ${response.statusText}`);
                }
                
                const data = await response.json();
                this.rebootProtectionEnabled = data.enabled;
            } catch (err) {
                console.error('Failed to load reboot protection status:', err);
                // Don't show error to user, just log it
            }
        },

        async toggleRebootProtection() {
            this.rebootProtectionLoading = true;
            
            try {
                const response = await fetch('/api/systemd/reboot-protection', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({
                        enabled: this.rebootProtectionEnabled
                    })
                });
                
                if (!response.ok) {
                    const error = await response.json();
                    throw new Error(error.detail || 'Failed to toggle reboot protection');
                }
                
                const data = await response.json();
                
                // Show success message
                this.actionMessage = data.message || `Reboot protection ${this.rebootProtectionEnabled ? 'enabled' : 'disabled'}`;
                this.actionError = false;
                
                // Show warnings if any
                if (data.warnings && data.warnings.length > 0) {
                    this.actionMessage += ` (Warnings: ${data.warnings.join(', ')})`;
                }
                
                // Clear message after 5 seconds
                setTimeout(() => {
                    this.actionMessage = '';
                }, 5000);
                
            } catch (err) {
                // Revert the toggle state on error
                this.rebootProtectionEnabled = !this.rebootProtectionEnabled;
                
                this.actionMessage = `Failed to toggle reboot protection: ${err.message}`;
                this.actionError = true;
                console.error('Reboot protection toggle error:', err);
                
                // Clear error message after 10 seconds
                setTimeout(() => {
                    this.actionMessage = '';
                    this.actionError = false;
                }, 10000);
            } finally {
                this.rebootProtectionLoading = false;
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

        // Function to format mountpoints with links for /mnt paths
        formatMountpointsWithLinks(mountpoints) {
            if (!mountpoints || mountpoints.length === 0) return '';
            
            const formattedMountpoints = mountpoints.map(mp => {
                if (mp.startsWith('/mnt/')) {
                    // Extract the path after /mnt/ and create a link that opens in new tab
                    const pathAfterMnt = mp.substring(5); // Remove '/mnt/' prefix
                    if (pathAfterMnt) {
                        return `<a href="/data/files/${pathAfterMnt}/" target="_blank" rel="noopener noreferrer" class="text-decoration-none">${mp} <i class="fas fa-external-link-alt fa-xs" title="Opens in new tab"></i></a>`;
                    }
                }
                return mp;
            });
            
            return formattedMountpoints.join(', ');
        }
    }
}


import { saveStateMixin } from '../mixins/saveStateMixin.js';
import { serviceActionMixin } from '../mixins/serviceActionMixin.js';
import { getSystemRefreshInterval } from '../utils/systemUtils.js';
import { apiUrl } from '../utils/apiUtils.js';

export function networkConfig() {
    return {
        ...saveStateMixin(),
        ...serviceActionMixin(),
        
        _initialized: false,
        _loadingServiceStatus: false,
        connections: [],
        serviceStatus: {
            active: false,
            status: 'unknown',
            uptime: '-'
        },
        actionLoading: false,
        hotspot: {
            ssid: '',
            password: ''
        },
        originalHotspot: {
            ssid: '',
            password: ''
        },
        cellular: {
            apn: '',
            username: '',
            password: '',
            pin: ''
        },
        originalCellular: {
            apn: '',
            username: '',
            password: '',
            pin: ''
        },
        modem: null,
        modemLoading: false,
        modemError: null,
        loading: false,
        refreshing: false,
        statusInterval: null,
        showPassword: false,
        showCellularPassword: false,
        showCellularPin: false,
        cellularSaveState: 'idle',
        
        // Server mode helper
        get serverMode() {
            return window.serverModeManager?.isEnabled() || false;
        },
        
        // Check if hotspot config has been modified
        get isModified() {
            return this.hotspot.password !== this.originalHotspot.password;
        },
        
        // Check if cellular config has been modified
        get isCellularModified() {
            return this.cellular.apn !== this.originalCellular.apn ||
                   this.cellular.username !== this.originalCellular.username ||
                   this.cellular.password !== this.originalCellular.password ||
                   this.cellular.pin !== this.originalCellular.pin;
        },
        
        async init() {
            // Prevent multiple initializations
            if (this._initialized) {
                return;
            }
            
            if (this.statusInterval) {
                this.stopAutoRefresh();
            }
            
            this._initialized = true;
            
            await this.loadServiceStatus();
            await this.loadConnections();
            await this.loadHotspotConfig();
            await this.loadCellularConfig();
            await this.loadModemDetails();
            await this.startAutoRefresh();
        },
        
        cleanup() {
            this.stopAutoRefresh();
            this._initialized = false;
        },
        
        async loadServiceStatus() {
            // Prevent multiple simultaneous requests
            if (this._loadingServiceStatus) {
                return;
            }
            
            this._loadingServiceStatus = true;
            
            try {
                const response = await fetch(apiUrl('/api/systemd/services'));
                
                if (!response.ok) {
                    throw new Error(`Failed to load service status: ${response.status}`);
                }
                
                const data = await response.json();
                
                // Find NetworkManager service
                const nmService = data.find(s => s.name === 'NetworkManager.service');
                
                if (nmService) {
                    this.serviceStatus = {
                        active: nmService.active,
                        status: nmService.status || 'unknown',
                        uptime: nmService.uptime || '-'
                    };
                } else {
                    this.serviceStatus = {
                        active: false,
                        status: 'not-found',
                        uptime: '-'
                    };
                }
            } catch (error) {
                console.error('Error loading NetworkManager service status:', error);
                this.serviceStatus = {
                    active: false,
                    status: 'error',
                    uptime: '-'
                };
            } finally {
                this._loadingServiceStatus = false;
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
        
        async loadConnections(showRefreshing = false) {
            // Prevent multiple simultaneous requests
            if (this.refreshing) {
                return;
            }
            
            if (showRefreshing) {
                this.refreshing = true;
            }
            
            try {
                const response = await fetch(apiUrl('/api/network/connections'));
                
                if (!response.ok) {
                    throw new Error(`Failed to load network connections: ${response.status}`);
                }
                
                const data = await response.json();
                this.connections = data || [];
            } catch (error) {
                console.error('Error loading network connections:', error);
                if (showRefreshing) {
                    this.dispatchMessage(error.message || 'Failed to load network connections', true);
                }
            } finally {
                if (showRefreshing) {
                    this.refreshing = false;
                }
            }
        },
        
        async refreshConnections() {
            await this.loadConnections(true);
        },
        
        async loadHotspotConfig() {
            try {
                const response = await fetch(apiUrl('/api/network/connections/hotspot'));
                
                if (!response.ok) {
                    if (response.status === 404) {
                        this.dispatchMessage('Hotspot configuration not found', true);
                        return;
                    }
                    throw new Error(`Failed to load hotspot configuration: ${response.status}`);
                }
                
                const data = await response.json();
                this.hotspot = {
                    ssid: data.ssid || '',
                    password: data.password || ''
                };
                this.originalHotspot = {
                    ssid: data.ssid || '',
                    password: data.password || ''
                };
            } catch (error) {
                console.error('Error loading hotspot configuration:', error);
                this.dispatchMessage(error.message || 'Failed to load hotspot configuration', true);
            }
        },
        
        async saveHotspotConfig() {
            if (!this.isModified) {
                this.dispatchMessage('No changes to save', false);
                return;
            }
            
            // Validate password
            if (this.hotspot.password.length < 8 || this.hotspot.password.length > 63) {
                this.dispatchMessage('Password must be between 8 and 63 characters', true);
                return;
            }
            
            this.saveState = 'saving';
            
            try {
                const response = await fetch(apiUrl('/api/network/connections/hotspot'), {
                    method: 'PATCH',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({
                        password: this.hotspot.password
                    })
                });
                
                if (!response.ok) {
                    const errorData = await response.json();
                    throw new Error(errorData.detail || 'Failed to update hotspot password');
                }
                
                const data = await response.json();
                
                // Update local state with confirmed values
                this.hotspot = {
                    ssid: data.config.ssid || '',
                    password: data.config.password || ''
                };
                this.originalHotspot = {
                    ssid: data.config.ssid || '',
                    password: data.config.password || ''
                };
                
                this.saveState = 'saved';
                this.dispatchMessage('Hotspot password updated successfully', false);
                
                // Reset save state after delay
                setTimeout(() => {
                    if (this.saveState === 'saved') {
                        this.saveState = 'idle';
                    }
                }, 2000);
            } catch (error) {
                console.error('Error saving hotspot password:', error);
                this.saveState = 'idle';
                this.dispatchMessage(error.message || 'Failed to save hotspot password', true);
            }
        },
        
        resetHotspotConfig() {
            this.hotspot.password = this.originalHotspot.password;
        },
        
        togglePasswordVisibility() {
            this.showPassword = !this.showPassword;
        },
        
        async loadCellularConfig() {
            try {
                const response = await fetch(apiUrl('/api/network/connections/cellular'));
                
                if (!response.ok) {
                    if (response.status === 404) {
                        this.dispatchMessage('Cellular configuration not found', true);
                        return;
                    }
                    throw new Error(`Failed to load cellular configuration: ${response.status}`);
                }
                
                const data = await response.json();
                this.cellular = {
                    apn: data.apn || '',
                    username: data.username || '',
                    password: data.password || '',
                    pin: data.pin || ''
                };
                this.originalCellular = {
                    apn: data.apn || '',
                    username: data.username || '',
                    password: data.password || '',
                    pin: data.pin || ''
                };
            } catch (error) {
                console.error('Error loading cellular configuration:', error);
                this.dispatchMessage(error.message || 'Failed to load cellular configuration', true);
            }
        },
        
        async loadModemDetails(showLoading = false) {
            if (showLoading) {
                this.modemLoading = true;
            }
            
            try {
                const response = await fetch(apiUrl('/api/network/modem'));
                
                if (!response.ok) {
                    throw new Error(`Failed to load modem details: ${response.status}`);
                }
                
                const data = await response.json();
                
                // Only update modem state with the new data
                this.modem = data;
                
                // Clear any previous errors on successful fetch
                this.modemError = null;
            } catch (error) {
                console.error('Error loading modem details:', error);
                this.modemError = error.message || 'Failed to load modem details';
                // Only clear modem data on actual error
                this.modem = null;
            } finally {
                if (showLoading) {
                    this.modemLoading = false;
                }
            }
        },
        
        async refreshModemDetails() {
            await this.loadModemDetails(true);
        },
        
        async saveCellularConfig() {
            if (!this.isCellularModified) {
                this.dispatchMessage('No changes to save', false);
                return;
            }
            
            // Validate PIN if provided
            if (this.cellular.pin && (this.cellular.pin.length < 4 || this.cellular.pin.length > 8 || !/^\d+$/.test(this.cellular.pin))) {
                this.dispatchMessage('PIN must be 4-8 digits', true);
                return;
            }
            
            this.cellularSaveState = 'saving';
            
            try {
                // Build update payload with all fields (including empty strings for clearing)
                const updates = {
                    apn: this.cellular.apn || null,
                    username: this.cellular.username || null,
                    password: this.cellular.password || null,
                    pin: this.cellular.pin || null
                };
                
                const response = await fetch(apiUrl('/api/network/connections/cellular'), {
                    method: 'PATCH',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify(updates)
                });
                
                if (!response.ok) {
                    const errorData = await response.json();
                    throw new Error(errorData.detail || 'Failed to update cellular configuration');
                }
                
                const data = await response.json();
                
                // Update local state with confirmed values
                this.cellular = {
                    apn: data.config.apn || '',
                    username: data.config.username || '',
                    password: data.config.password || '',
                    pin: data.config.pin || ''
                };
                this.originalCellular = {
                    apn: data.config.apn || '',
                    username: data.config.username || '',
                    password: data.config.password || '',
                    pin: data.config.pin || ''
                };
                
                this.cellularSaveState = 'saved';
                this.dispatchMessage('Cellular configuration updated successfully', false);
                
                // Reset save state after delay
                setTimeout(() => {
                    if (this.cellularSaveState === 'saved') {
                        this.cellularSaveState = 'idle';
                    }
                }, 2000);
            } catch (error) {
                console.error('Error saving cellular configuration:', error);
                this.cellularSaveState = 'idle';
                this.dispatchMessage(error.message || 'Failed to save cellular configuration', true);
            }
        },
        
        resetCellularConfig() {
            this.cellular = {
                apn: this.originalCellular.apn,
                username: this.originalCellular.username,
                password: this.originalCellular.password,
                pin: this.originalCellular.pin
            };
        },
        
        toggleCellularPasswordVisibility() {
            this.showCellularPassword = !this.showCellularPassword;
        },
        
        toggleCellularPinVisibility() {
            this.showCellularPin = !this.showCellularPin;
        },
        
        async startAutoRefresh() {
            // Clear any existing interval first
            this.stopAutoRefresh();
            
            // Refresh connection and service status periodically (without showing refreshing state)
            const interval = await getSystemRefreshInterval();
            
            this.statusInterval = setInterval(() => {
                // Only refresh if network tab is active and not already refreshing
                const currentHash = window.location.hash.slice(1);
                const isNetworkTabActive = currentHash === 'settings/network';
                
                if (isNetworkTabActive && !this.refreshing) {
                    this.loadServiceStatus();
                    this.loadConnections(false);
                    this.loadModemDetails(false);
                }
            }, interval * 1000);
        },
        
        stopAutoRefresh() {
            if (this.statusInterval) {
                clearInterval(this.statusInterval);
                this.statusInterval = null;
            }
        },
        
        getConnectionStatusBadgeClass(state) {
            switch (state) {
                case 'activated':
                    return 'bg-success';
                case 'activating':
                    return 'bg-warning';
                case 'deactivating':
                    return 'bg-warning';
                case 'deactivated':
                    return 'bg-secondary';
                default:
                    return 'bg-secondary';
            }
        },
        
        getConnectionStatusText(state) {
            switch (state) {
                case 'activated':
                    return 'Active';
                case 'activating':
                    return 'Activating...';
                case 'deactivating':
                    return 'Deactivating...';
                case 'deactivated':
                    return 'Inactive';
                default:
                    return state || 'Unknown';
            }
        },
        
        getConnectionTypeIcon(type) {
            switch (type) {
                case '802-11-wireless':
                case 'wifi':
                    return 'fas fa-wifi';
                case '802-3-ethernet':
                case 'ethernet':
                    return 'fas fa-ethernet';
                case 'gsm':
                case 'cellular':
                    return 'fas fa-signal';
                case 'wireguard':
                    return 'fas fa-shield-alt';
                case 'loopback':
                    return 'fas fa-circle';
                default:
                    return 'fas fa-network-wired';
            }
        },
        
        getModemStateBadgeClass(state) {
            if (!state) return 'bg-secondary';
            
            const stateLower = state.toLowerCase();
            if (stateLower.includes('connected') || stateLower.includes('registered')) {
                return 'bg-success';
            } else if (stateLower.includes('connecting') || stateLower.includes('searching')) {
                return 'bg-warning';
            } else if (stateLower.includes('disabled') || stateLower.includes('failed')) {
                return 'bg-danger';
            }
            return 'bg-secondary';
        },
        
        formatSignalStrength(dbm, percent) {
            if (dbm !== null && dbm !== undefined) {
                return `${dbm} dBm`;
            } else if (percent !== null && percent !== undefined) {
                return `${percent}%`;
            }
            return '-';
        },
        
        getSignalBars(percent) {
            if (percent === null || percent === undefined) return 0;
            if (percent >= 80) return 5;
            if (percent >= 60) return 4;
            if (percent >= 40) return 3;
            if (percent >= 20) return 2;
            if (percent > 0) return 1;
            return 0;
        },
        
        dispatchMessage(message, isError) {
            // Use global toast manager
            if (window.toastManager) {
                const title = isError ? 'Network - Error' : 'Network - Success';
                const type = isError ? 'error' : 'success';
                window.toastManager.show(message, type, { title });
            } else {
                // Fallback to console if toast manager not available
                console.log(`[Network ${isError ? 'ERROR' : 'SUCCESS'}] ${message}`);
            }
        }
    };
}


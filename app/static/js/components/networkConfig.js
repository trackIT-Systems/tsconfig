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
            password: '',
            band: '',
            channel: '',
            channel_width: '',
            hidden: false
        },
        originalHotspot: {
            ssid: '',
            password: '',
            band: '',
            channel: '',
            channel_width: '',
            hidden: false
        },
        wifiStation: {
            ssid: '',
            password: '',
            autoconnect: true,
        },
        originalWifiStation: {
            ssid: '',
            password: '',
            autoconnect: true,
        },
        wifiCapabilities: {
            bands: [],
            channel_widths: []
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
        showStationPassword: false,
        showCellularPassword: false,
        showCellularPin: false,
        cellularSaveState: 'idle',
        stationSaveState: 'idle',
        wifiScanResults: [],
        wifiScanLoading: false,
        wifiSsidShowDropdown: false,
        wifiSsidSelectedIndex: -1,
        wifiSsidFiltered: [],
        connectionUpStates: {},
        
        // Server mode helper
        get serverMode() {
            return window.serverModeManager?.isEnabled() || false;
        },
        
        // Check if hotspot config has been modified
        get isModified() {
            // Helper to normalize null and empty string for comparison
            const normalize = (val) => val || null;
            
            return this.hotspot.password !== this.originalHotspot.password ||
                   normalize(this.hotspot.band) !== normalize(this.originalHotspot.band) ||
                   normalize(this.hotspot.channel) !== normalize(this.originalHotspot.channel) ||
                   normalize(this.hotspot.channel_width) !== normalize(this.originalHotspot.channel_width) ||
                   this.hotspot.hidden !== this.originalHotspot.hidden;
        },
        
        // Get available channels for the selected band
        get availableChannels() {
            if (!this.hotspot.band || !this.wifiCapabilities.bands || this.hotspot.band === '') {
                // If no band selected or Auto, show all channels from all bands
                if (!this.wifiCapabilities.bands || this.wifiCapabilities.bands.length === 0) {
                    return [];
                }
                // Get default band (2.4GHz) channels
                const defaultBand = this.wifiCapabilities.bands.find(b => b.band === '2.4GHz');
                return defaultBand ? defaultBand.channels : [];
            }
            const band = this.wifiCapabilities.bands.find(b => b.band === this.hotspot.band);
            return band ? band.channels : [];
        },
        
        // Check if cellular config has been modified
        get isCellularModified() {
            return this.cellular.apn !== this.originalCellular.apn ||
                   this.cellular.username !== this.originalCellular.username ||
                   this.cellular.password !== this.originalCellular.password ||
                   this.cellular.pin !== this.originalCellular.pin;
        },

        get isStationModified() {
            return this.wifiStation.ssid !== this.originalWifiStation.ssid ||
                   this.wifiStation.password !== this.originalWifiStation.password ||
                   this.wifiStation.autoconnect !== this.originalWifiStation.autoconnect;
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
            await this.loadWifiCapabilities();
            await this.loadWifiStationConfig();
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
                const nmService = data.find(s => s.name === 'NetworkManager');
                
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
        
        async loadWifiCapabilities() {
            try {
                const response = await fetch(apiUrl('/api/network/wifi/capabilities'));
                
                if (!response.ok) {
                    if (response.status === 503) {
                        console.warn('WiFi device not available');
                        return;
                    }
                    throw new Error(`Failed to load WiFi capabilities: ${response.status}`);
                }
                
                const data = await response.json();
                this.wifiCapabilities = {
                    bands: data.bands || [],
                    channel_widths: data.channel_widths || []
                };
            } catch (error) {
                console.error('Error loading WiFi capabilities:', error);
                // Don't show error message to user - WiFi capabilities are optional
            }
        },
        
        async loadWifiStationConfig() {
            try {
                const response = await fetch(apiUrl('/api/network/connections/station'));

                if (!response.ok) {
                    if (response.status === 404) {
                        this.dispatchMessage('WiFi station configuration not found', true);
                        return;
                    }
                    throw new Error(`Failed to load WiFi station configuration: ${response.status}`);
                }

                const data = await response.json();
                this.wifiStation = {
                    ssid: data.ssid || '',
                    password: data.password || '',
                    autoconnect: data.autoconnect !== false,
                };
                this.originalWifiStation = {
                    ssid: data.ssid || '',
                    password: data.password || '',
                    autoconnect: data.autoconnect !== false,
                };
            } catch (error) {
                console.error('Error loading WiFi station configuration:', error);
                this.dispatchMessage(error.message || 'Failed to load WiFi station configuration', true);
            }
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
                // Convert null to empty string for select dropdowns
                this.hotspot = {
                    ssid: data.ssid || '',
                    password: data.password || '',
                    band: data.band || '',
                    channel: data.channel || '',
                    channel_width: data.channel_width || '',
                    hidden: data.hidden || false
                };
                this.originalHotspot = {
                    ssid: data.ssid || '',
                    password: data.password || '',
                    band: data.band || '',
                    channel: data.channel || '',
                    channel_width: data.channel_width || '',
                    hidden: data.hidden || false
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
                // Convert empty strings to null for proper API handling
                const payload = {
                    password: this.hotspot.password,
                    band: this.hotspot.band || null,
                    channel: this.hotspot.channel || null,
                    channel_width: this.hotspot.channel_width || null,
                    hidden: this.hotspot.hidden
                };
                
                const response = await fetch(apiUrl('/api/network/connections/hotspot'), {
                    method: 'PATCH',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify(payload)
                });
                
                if (!response.ok) {
                    const errorData = await response.json();
                    throw new Error(errorData.detail || 'Failed to update hotspot password');
                }
                
                const data = await response.json();
                
                // Update local state with confirmed values (convert null to empty string)
                this.hotspot = {
                    ssid: data.config.ssid || '',
                    password: data.config.password || '',
                    band: data.config.band || '',
                    channel: data.config.channel || '',
                    channel_width: data.config.channel_width || '',
                    hidden: data.config.hidden || false
                };
                this.originalHotspot = {
                    ssid: data.config.ssid || '',
                    password: data.config.password || '',
                    band: data.config.band || '',
                    channel: data.config.channel || '',
                    channel_width: data.config.channel_width || '',
                    hidden: data.config.hidden || false
                };
                
                this.saveState = 'saved';
                this.dispatchMessage('Hotspot configuration updated successfully', false);
                
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
            this.hotspot.band = this.originalHotspot.band || '';
            this.hotspot.channel = this.originalHotspot.channel || '';
            this.hotspot.channel_width = this.originalHotspot.channel_width || '';
            this.hotspot.hidden = this.originalHotspot.hidden;
        },

        async saveWifiStationConfig() {
            if (!this.isStationModified) {
                this.dispatchMessage('No changes to save', false);
                return;
            }

            const updates = {};
            if (this.wifiStation.ssid !== this.originalWifiStation.ssid) {
                updates.ssid = this.wifiStation.ssid;
            }
            if (this.wifiStation.password !== this.originalWifiStation.password) {
                updates.password = this.wifiStation.password;
            }
            if (this.wifiStation.autoconnect !== this.originalWifiStation.autoconnect) {
                updates.autoconnect = this.wifiStation.autoconnect;
            }

            if (Object.keys(updates).length === 0) {
                this.dispatchMessage('No changes to save', false);
                return;
            }

            if (updates.password !== undefined) {
                const p = updates.password;
                if (p.length < 8 || p.length > 63) {
                    this.dispatchMessage('Password must be 8-63 characters', true);
                    return;
                }
            }

            this.stationSaveState = 'saving';

            try {
                const response = await fetch(apiUrl('/api/network/connections/station'), {
                    method: 'PATCH',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify(updates),
                });

                if (!response.ok) {
                    const errorData = await response.json();
                    throw new Error(errorData.detail || 'Failed to update WiFi station configuration');
                }

                const data = await response.json();
                const cfg = data.config;

                this.wifiStation = {
                    ssid: cfg.ssid || '',
                    password: cfg.password || '',
                    autoconnect: cfg.autoconnect !== false,
                };
                this.originalWifiStation = {
                    ssid: cfg.ssid || '',
                    password: cfg.password || '',
                    autoconnect: cfg.autoconnect !== false,
                };

                this.stationSaveState = 'saved';
                this.dispatchMessage('WiFi station configuration updated successfully', false);

                setTimeout(() => {
                    if (this.stationSaveState === 'saved') {
                        this.stationSaveState = 'idle';
                    }
                }, 2000);
            } catch (error) {
                console.error('Error saving WiFi station configuration:', error);
                this.stationSaveState = 'idle';
                this.dispatchMessage(error.message || 'Failed to save WiFi station configuration', true);
            }
        },

        resetWifiStationConfig() {
            this.wifiStation = {
                ssid: this.originalWifiStation.ssid,
                password: this.originalWifiStation.password,
                autoconnect: this.originalWifiStation.autoconnect,
            };
        },

        toggleStationPasswordVisibility() {
            this.showStationPassword = !this.showStationPassword;
        },

        wifiApDropdownLabel(ap) {
            if (!ap) return '';
            const bits = [ap.ssid, `${ap.signal}%`, ap.security === '--' ? 'open' : ap.security];
            if (ap.active) {
                bits.push('connected');
            }
            return bits.join(' · ');
        },

        filterWifiSsidSuggestions(query) {
            const q = (query || '').trim().toLowerCase();
            const list = this.wifiScanResults || [];
            let rows = list;
            if (q) {
                rows = list.filter(
                    (ap) =>
                        (ap.ssid && ap.ssid.toLowerCase().includes(q)) ||
                        (ap.security && ap.security.toLowerCase().includes(q))
                );
            }
            this.wifiSsidFiltered = rows;
            this.wifiSsidSelectedIndex = rows.length > 0 ? 0 : -1;
        },

        async onWifiSsidFieldFocus(event) {
            this.filterWifiSsidSuggestions(event.target.value);
            this.wifiSsidShowDropdown = true;
            if (
                this.wifiScanResults.length === 0 &&
                this.serviceStatus.active &&
                !this.wifiScanLoading
            ) {
                await this.scanWifiNetworks(false);
                this.filterWifiSsidSuggestions(event.target.value);
            }
        },

        onWifiSsidFieldBlur() {
            setTimeout(() => {
                this.wifiSsidShowDropdown = false;
                this.wifiSsidSelectedIndex = -1;
            }, 150);
        },

        onWifiSsidKeydown(event) {
            if (!this.wifiSsidShowDropdown || this.wifiSsidFiltered.length === 0) {
                return;
            }
            switch (event.key) {
                case 'ArrowDown':
                    event.preventDefault();
                    this.wifiSsidSelectedIndex = Math.min(
                        this.wifiSsidSelectedIndex + 1,
                        this.wifiSsidFiltered.length - 1
                    );
                    break;
                case 'ArrowUp':
                    event.preventDefault();
                    this.wifiSsidSelectedIndex = Math.max(this.wifiSsidSelectedIndex - 1, 0);
                    break;
                case 'Enter':
                    event.preventDefault();
                    if (
                        this.wifiSsidSelectedIndex >= 0 &&
                        this.wifiSsidSelectedIndex < this.wifiSsidFiltered.length
                    ) {
                        this.selectWifiSsidAp(this.wifiSsidFiltered[this.wifiSsidSelectedIndex]);
                    }
                    break;
                case 'Escape':
                    this.wifiSsidShowDropdown = false;
                    this.wifiSsidSelectedIndex = -1;
                    break;
                default:
                    break;
            }
        },

        selectWifiSsidAp(ap) {
            if (!ap || ap.ssid === 'Hidden network') {
                this.dispatchMessage(
                    'Hidden networks need the exact SSID typed manually.',
                    true
                );
                return;
            }
            this.wifiStation.ssid = ap.ssid;
            this.wifiSsidShowDropdown = false;
            this.wifiSsidSelectedIndex = -1;
        },

        async scanWifiNetworks(rescan = true) {
            this.wifiScanLoading = true;
            if (rescan) {
                this.wifiScanResults = [];
            }
            try {
                const q = rescan ? 'true' : 'false';
                const response = await fetch(apiUrl(`/api/network/wifi/scan?rescan=${q}`));
                if (!response.ok) {
                    let detail = `Scan failed (${response.status})`;
                    try {
                        const err = await response.json();
                        if (err.detail) {
                            detail = typeof err.detail === 'string' ? err.detail : JSON.stringify(err.detail);
                        }
                    } catch {
                        /* ignore */
                    }
                    throw new Error(detail);
                }
                this.wifiScanResults = await response.json();
                this.filterWifiSsidSuggestions(this.wifiStation.ssid);
                if (rescan && this.wifiScanResults.length === 0) {
                    this.dispatchMessage('No networks found. Try again or move closer to an access point.', false);
                }
            } catch (error) {
                console.error('WiFi scan error:', error);
                this.dispatchMessage(error.message || 'WiFi scan failed', true);
            } finally {
                this.wifiScanLoading = false;
            }
        },
        
        onBandChange() {
            // When band changes, reset channel if it's not valid for the new band
            if (this.hotspot.channel && this.hotspot.channel !== '' && this.availableChannels.length > 0) {
                if (!this.availableChannels.includes(parseInt(this.hotspot.channel))) {
                    this.hotspot.channel = '';
                }
            }
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
        
        getConnectionUpState(connectionName) {
            return this.connectionUpStates[connectionName] || 'idle';
        },
        
        async bringConnectionUp(connectionName) {
            // Prevent multiple simultaneous requests for the same connection
            if (this.connectionUpStates[connectionName] === 'activating') {
                return;
            }
            
            // Set state to activating
            this.connectionUpStates[connectionName] = 'activating';
            
            try {
                const response = await fetch(apiUrl(`/api/network/connections/${encodeURIComponent(connectionName)}/up`), {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    }
                });
                
                if (!response.ok) {
                    const errorData = await response.json();
                    throw new Error(errorData.detail || 'Failed to bring connection up');
                }
                
                const data = await response.json();
                
                // Set state to activated briefly
                this.connectionUpStates[connectionName] = 'activated';
                
                // Show success message
                this.dispatchMessage(data.message || `Connection '${connectionName}' activated successfully`, false);
                
                // Refresh connections list to show updated status
                await this.loadConnections(false);
                
                // Reset state after delay
                setTimeout(() => {
                    if (this.connectionUpStates[connectionName] === 'activated') {
                        this.connectionUpStates[connectionName] = 'idle';
                    }
                }, 2000);
            } catch (error) {
                console.error('Error bringing connection up:', error);
                this.connectionUpStates[connectionName] = 'idle';
                this.dispatchMessage(error.message || `Failed to bring connection '${connectionName}' up`, true);
            }
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


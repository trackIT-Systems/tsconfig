// Shared System Config Manager - prevents duplicate API calls
const systemConfigManager = {
    config: null,
    fetchPromise: null,
    
    async getSystemConfig() {
        // If there's already a fetch in progress, return that promise
        if (this.fetchPromise) {
            return this.fetchPromise;
        }
        
        // Return cached config if available
        if (this.config) {
            return this.config;
        }
        
        // Create and store the fetch promise
        this.fetchPromise = this.fetchSystemConfig();
        
        try {
            const config = await this.fetchPromise;
            this.config = config;
            return config;
        } finally {
            this.fetchPromise = null;
        }
    },
    
    async fetchSystemConfig() {
        const response = await fetch('/api/systemd/config/system');
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }
        return await response.json();
    }
};

// Shared Service Manager - prevents duplicate API calls
const serviceManager = {
    services: [],
    lastFetch: null,
    fetchPromise: null,
    subscribers: new Set(),
    
    // Subscribe to service updates
    subscribe(callback) {
        this.subscribers.add(callback);
        // Immediately call with current data if available
        if (this.services.length > 0) {
            callback(this.services);
        }
    },
    
    // Unsubscribe from service updates
    unsubscribe(callback) {
        this.subscribers.delete(callback);
    },
    
    // Notify all subscribers
    notify() {
        this.subscribers.forEach(callback => callback(this.services));
    },
    
    // Get services with caching and deduplication
    async getServices(forceRefresh = false) {
        const now = Date.now();
        const cacheValid = this.lastFetch && (now - this.lastFetch) < 5000; // 5 second cache
        
        // Return cached data if valid and not forcing refresh
        if (!forceRefresh && cacheValid && this.services.length > 0) {
            return this.services;
        }
        
        // If there's already a fetch in progress, return that promise
        if (this.fetchPromise) {
            return this.fetchPromise;
        }
        
        // Create and store the fetch promise
        this.fetchPromise = this.fetchServices();
        
        try {
            const services = await this.fetchPromise;
            this.services = services;
            this.lastFetch = now;
            this.notify(); // Notify all subscribers
            return services;
        } finally {
            this.fetchPromise = null;
        }
    },
    
    // Actual API fetch
    async fetchServices() {
        const response = await fetch('/api/systemd/services');
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }
        return await response.json();
    },
    
    // Find a specific service by name
    findService(serviceName) {
        return this.services.find(service => service.name === serviceName);
    }
};

function configManager() {
    return {
        activeConfig: 'status',  // Default to status page
        message: '',
        error: false,
        warning: false,
        expertMode: false,  // Add expert mode state
        availableServices: [],  // List of services with config files available
        servicesLoaded: false,  // Track if services have been loaded
        hostname: 'Loading...',  // Dynamic hostname loaded via AJAX

        // URL parameter utilities
        getUrlParams() {
            return new URLSearchParams(window.location.search);
        },

        updateUrlParams(params) {
            const currentUrl = new URL(window.location);
            Object.entries(params).forEach(([key, value]) => {
                if (value === null || value === undefined || value === false) {
                    currentUrl.searchParams.delete(key);
                } else {
                    currentUrl.searchParams.set(key, value);
                }
            });
            
            // Update URL without reloading the page
            window.history.replaceState({}, '', currentUrl);
        },

        async init() {
            // Load available services and hostname first
            await Promise.all([
                this.loadAvailableServices(),
                this.loadHostname()
            ]);
            
            // Read expert mode from URL parameter
            const urlParams = this.getUrlParams();
            this.expertMode = urlParams.get('expert') === 'true';

            // Set initial active config based on URL hash
            const hash = window.location.hash.slice(1);
            if (hash === 'radiotracking' && this.isServiceAvailable('radiotracking')) {
                this.activeConfig = 'radiotracking';
            } else if (hash === 'schedule' && this.isServiceAvailable('schedule')) {
                this.activeConfig = 'schedule';
            } else if (hash === 'soundscapepipe' && this.isServiceAvailable('soundscapepipe')) {
                this.activeConfig = 'soundscapepipe';
            } else if (hash === 'status') {
                this.activeConfig = 'status';
            } else {
                this.activeConfig = 'status';  // Default to status
            }

            // Watch for expert mode changes and update URL
            this.$watch('expertMode', (value, oldValue) => {
                // Only update URL if this isn't the initial load
                if (oldValue !== undefined) {
                    this.updateUrlParams({ expert: value || null });
                }
            });

            // Update URL hash when active config changes
            this.$watch('activeConfig', (value) => {
                window.location.hash = value;
                
                // Ensure map is properly initialized when switching to schedule tab
                if (value === 'schedule') {
                    setTimeout(() => {
                        const scheduleComponent = this.$el.querySelector('[x-data*="scheduleConfig"]');
                        if (scheduleComponent && scheduleComponent._x_dataStack) {
                            const scheduleData = scheduleComponent._x_dataStack[0];
                            if (scheduleData && scheduleData.ensureMapVisible) {
                                scheduleData.ensureMapVisible();
                            }
                        }
                    }, 150);
                }
                
                // Ensure map is properly initialized when switching to soundscapepipe tab
                if (value === 'soundscapepipe') {
                    setTimeout(() => {
                        const soundscapeComponent = this.$el.querySelector('[x-data*="soundscapepipeConfig"]');
                        if (soundscapeComponent && soundscapeComponent._x_dataStack) {
                            const soundscapeData = soundscapeComponent._x_dataStack[0];
                            if (soundscapeData && soundscapeData.ensureMapVisible) {
                                soundscapeData.ensureMapVisible();
                            }
                        }
                    }, 150);
                }
            });

            // Listen for hash changes
            window.addEventListener('hashchange', () => {
                const hash = window.location.hash.slice(1);
                if (hash === 'status' || 
                    (hash === 'radiotracking' && this.isServiceAvailable('radiotracking')) ||
                    (hash === 'schedule' && this.isServiceAvailable('schedule')) ||
                    (hash === 'soundscapepipe' && this.isServiceAvailable('soundscapepipe'))) {
                    this.activeConfig = hash;
                }
            });

            // Listen for browser navigation changes (back/forward buttons)
            window.addEventListener('popstate', () => {
                const urlParams = this.getUrlParams();
                const newExpertMode = urlParams.get('expert') === 'true';
                
                // Update expert mode if it changed via browser navigation
                if (this.expertMode !== newExpertMode) {
                    this.expertMode = newExpertMode;
                }
                
                // Update active config based on hash
                const hash = window.location.hash.slice(1);
                if (hash === 'status' || 
                    (hash === 'radiotracking' && this.isServiceAvailable('radiotracking')) ||
                    (hash === 'schedule' && this.isServiceAvailable('schedule')) ||
                    (hash === 'soundscapepipe' && this.isServiceAvailable('soundscapepipe'))) {
                    this.activeConfig = hash;
                }
            });

            // Listen for message events from child components
            window.addEventListener('show-message', (event) => {
                this.showMessage(event.detail.message, event.detail.isError);
            });
            
            // Listen for Alpine.js custom events from child components (like soundscapepipe)
            this.$el.addEventListener('message', (event) => {
                this.message = event.detail.message;
                this.error = event.detail.error;
                this.warning = false;
            });
            
            // Set up periodic hostname refresh (every 30 seconds)
            setInterval(() => {
                this.loadHostname();
            }, 30000);
        },

        showMessage(message, isError) {
            this.message = message;
            this.error = isError;
            this.warning = !isError && message.includes("No configuration found");
        },

        async loadAvailableServices() {
            try {
                const response = await fetch('/api/available-services');
                if (response.ok) {
                    const data = await response.json();
                    this.availableServices = data.available_services || [];
                    this.servicesLoaded = true;
                } else {
                    console.error('Failed to load available services');
                    this.availableServices = [];
                    this.servicesLoaded = true;
                }
            } catch (error) {
                console.error('Error loading available services:', error);
                this.availableServices = [];
                this.servicesLoaded = true;
            }
        },

        async loadHostname() {
            try {
                const response = await fetch('/api/system-status');
                if (response.ok) {
                    const data = await response.json();
                    this.hostname = data.hostname || 'Unknown';
                } else {
                    console.error('Failed to load hostname from system status');
                    this.hostname = 'Unknown';
                }
            } catch (error) {
                console.error('Error loading hostname:', error);
                this.hostname = 'Unknown';
            }
        },

        isServiceAvailable(serviceName) {
            return this.availableServices.includes(serviceName);
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
        }
    }
}

function scheduleConfig() {
    return {
        ...saveStateMixin(),
        config: {
            lat: 0,
            lon: 0,
            force_on: false,
            button_delay: "00:00",
            schedule: []
        },
        map: null,
        marker: null,
        mapInitialized: false,
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

        async init() {
            // Small delay to prevent simultaneous API calls during page load
            await new Promise(resolve => setTimeout(resolve, 50));
            
            // Load configuration and set up periodic refresh
            await this.loadConfig();
            await this.setupPeriodicRefresh();
        },

        async setupPeriodicRefresh() {
            // Get the refresh interval from system config
            const refreshIntervalSeconds = await getSystemRefreshInterval();
            
            // Set up periodic refresh for service status
            if (this.refreshInterval) {
                clearInterval(this.refreshInterval);
            }
            
            this.refreshInterval = setInterval(() => {
                // Only refresh if schedule tab is active
                const currentHash = window.location.hash.slice(1);
                if (currentHash === 'schedule') {
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

        initMap() {
            // Only initialize if not already done and container is visible
            if (this.mapInitialized || !document.getElementById('map')) {
                return;
            }

            // Wait a bit to ensure the container is properly rendered
            setTimeout(() => {
                if (!document.getElementById('map') || this.mapInitialized) {
                    return;
                }

                // Initialize map with loaded coordinates
                this.map = L.map('map', {
                    center: [this.config.lat, this.config.lon],
                    zoom: 13,
                    zoomControl: true
                });
                
                // Add Mapbox satellite streets layer
                L.tileLayer('https://api.mapbox.com/styles/v1/mapbox/satellite-streets-v12/tiles/{z}/{x}/{y}?access_token=pk.eyJ1IjoidHJhY2tpdHN5c3RlbXMiLCJhIjoiY21iaHEwbXcwMDEzcTJqc2JhNzdobDluaSJ9.NLRmiJEDHQgPJEyceCA57g', {
                    attribution: '© Mapbox © OpenStreetMap',
                    maxZoom: 19
                }).addTo(this.map);

                // Add locate control
                L.control.locate({
                    position: 'topleft',
                    strings: {
                        title: "Show my location"
                    },
                    flyTo: true,
                    keepCurrentZoomLevel: true,
                    locateOptions: {
                        enableHighAccuracy: true
                    }
                }).addTo(this.map);

                // Add marker
                this.marker = L.marker([this.config.lat, this.config.lon], {
                    draggable: true
                }).addTo(this.map);

                // Update coordinates when marker is dragged
                this.marker.on('dragend', (e) => {
                    const position = e.target.getLatLng();
                    this.config.lat = position.lat.toFixed(8);
                    this.config.lon = position.lng.toFixed(8);
                });

                // Update marker when map is clicked
                this.map.on('click', (e) => {
                    const position = e.latlng;
                    this.marker.setLatLng(position);
                    this.config.lat = position.lat.toFixed(8);
                    this.config.lon = position.lng.toFixed(8);
                });

                // Handle location found event
                this.map.on('locationfound', (e) => {
                    this.config.lat = e.latlng.lat.toFixed(8);
                    this.config.lon = e.latlng.lng.toFixed(8);
                    this.updateMarkerFromInputs();
                });

                this.mapInitialized = true;
                
                // Force a resize to ensure tiles load properly
                setTimeout(() => {
                    if (this.map) {
                        this.map.invalidateSize();
                    }
                }, 100);
            }, 100);
        },

        ensureMapVisible() {
            // Call this when the schedule tab becomes active
            if (this.map && this.mapInitialized) {
                setTimeout(() => {
                    this.map.invalidateSize();
                    this.map.setView([this.config.lat, this.config.lon], 13);
                }, 50);
            } else if (!this.mapInitialized) {
                this.initMap();
            }
        },

        updateMarkerFromInputs() {
            if (this.marker && this.mapInitialized) {
                // Ensure coordinates are within bounds and have proper precision
                const lat = Math.min(Math.max(parseFloat(this.config.lat), -90), 90);
                const lon = Math.min(Math.max(parseFloat(this.config.lon), -180), 180);
                
                // Update the marker and map view
                this.marker.setLatLng([lat, lon]);
                this.map.setView([lat, lon]);
                
                // Update the input values with properly formatted numbers
                this.config.lat = lat.toFixed(8);
                this.config.lon = lon.toFixed(8);
            }
        },

        async refreshConfig() {
            try {
                // Reset to initial state
                this.config = {
                    lat: 0,
                    lon: 0,
                    force_on: false,
                    button_delay: "00:00",
                    schedule: []
                };
                
                // Clear any existing messages
                this.message = '';
                this.error = false;
                this.warning = false;
                
                // Reload the configuration
                await this.loadConfig();
            } catch (error) {
                this.showMessage(error.message, true);
            }
        },

        async loadConfig() {
            try {
                const response = await fetch('/api/schedule');
                if (response.status === 404) {
                    // Set default configuration for schedule
                    this.config = {
                        lat: 40.7128,
                        lon: -74.0060,
                        force_on: false,
                        button_delay: "01:00",
                        schedule: []
                    };
                    // Initialize map after config is loaded
                    if (!this.mapInitialized) {
                        this.initMap();
                    } else {
                        this.updateMarkerFromInputs();
                    }
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
                    const startParts = this.parseTimeString(entry.start);
                    entry.startReference = startParts.reference;
                    entry.startSign = startParts.sign;
                    entry.startOffset = startParts.offset;
                    
                    const stopParts = this.parseTimeString(entry.stop);
                    entry.stopReference = stopParts.reference;
                    entry.stopSign = stopParts.sign;
                    entry.stopOffset = stopParts.offset;
                });
                
                // Initialize map after config is loaded, or update if already initialized
                if (!this.mapInitialized) {
                    this.initMap();
                } else {
                    this.updateMarkerFromInputs();
                }
                
                // Load service status
                await this.loadServiceStatus();
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

        async toggleEnable(serviceName, currentlyEnabled) {
            this.actionLoading = true;
            
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
                
                // Use the same message system as the schedule component
                window.dispatchEvent(new CustomEvent('show-message', {
                    detail: { message: data.message, isError: false }
                }));
                
                // Refresh service status after action
                setTimeout(async () => {
                    await this.loadServiceStatus();
                }, 1000);
                
            } catch (err) {
                window.dispatchEvent(new CustomEvent('show-message', {
                    detail: { message: err.message, isError: true }
                }));
                console.error(`Service toggle error:`, err);
            } finally {
                this.actionLoading = false;
            }
        },

        parseTimeString(timeStr) {
            if (timeStr.includes('sunrise')) {
                return {
                    reference: 'sunrise',
                    sign: timeStr.includes('-') ? '-' : '+',
                    offset: timeStr.replace('sunrise', '').replace('+', '').replace('-', '').trim()
                };
            } else if (timeStr.includes('sunset')) {
                return {
                    reference: 'sunset',
                    sign: timeStr.includes('-') ? '-' : '+',
                    offset: timeStr.replace('sunset', '').replace('+', '').replace('-', '').trim()
                };
            } else if (timeStr.includes('dawn')) {
                return {
                    reference: 'dawn',
                    sign: timeStr.includes('-') ? '-' : '+',
                    offset: timeStr.replace('dawn', '').replace('+', '').replace('-', '').trim()
                };
            } else if (timeStr.includes('dusk')) {
                return {
                    reference: 'dusk',
                    sign: timeStr.includes('-') ? '-' : '+',
                    offset: timeStr.replace('dusk', '').replace('+', '').replace('-', '').trim()
                };
            } else if (timeStr.includes('noon')) {
                return {
                    reference: 'noon',
                    sign: timeStr.includes('-') ? '-' : '+',
                    offset: timeStr.replace('noon', '').replace('+', '').replace('-', '').trim()
                };


            } else {
                return {
                    reference: 'time',
                    sign: '+',
                    offset: timeStr
                };
            }
        },

        updateTimeString(entry, type) {
            const reference = entry[`${type}Reference`];
            const sign = entry[`${type}Sign`];
            const offset = entry[`${type}Offset`];
            
            if (reference === 'time') {
                entry[type] = offset;
            } else {
                entry[type] = `${reference}${sign}${offset}`;
            }
        },

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
                const response = await fetch('/api/schedule', {
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

        async downloadConfig() {
            try {
                const response = await fetch('/api/schedule/download', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify(this.config)
                });
                if (!response.ok) {
                    const error = await response.json();
                    let errorMessage = error.detail?.message || 'Failed to download schedule configuration';
                    if (error.detail?.errors) {
                        errorMessage += '\nValidation errors: ' + error.detail.errors.join(', ');
                    }
                    throw new Error(errorMessage);
                }

                // Create a blob from the response and trigger download
                const blob = await response.blob();
                const url = window.URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.style.display = 'none';
                a.href = url;
                a.download = 'schedule.yml';
                document.body.appendChild(a);
                a.click();
                window.URL.revokeObjectURL(url);
                document.body.removeChild(a);

                this.showMessage('Schedule configuration downloaded successfully!', false);
            } catch (error) {
                this.showMessage(error.message, true);
            }
        },

        async saveAndRestartService() {
            const configSaveFunction = async () => {
                const response = await fetch('/api/schedule', {
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
                const response = await fetch('/api/systemd/action', {
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
            // Dispatch a custom event that the parent can listen for
            window.dispatchEvent(new CustomEvent('show-message', {
                detail: { message, isError }
            }));
        }
    }
}

function radiotrackingConfig() {
    return {
        ...saveStateMixin(),
        config: {
            "rtl-sdr": {
                device: [],
                calibration: [],
                center_freq: 0,
                sample_rate: 0,
                sdr_callback_length: null,
                lna_gain: 0,
                mixer_gain: 0,
                vga_gain: 0,
                gain: 0,
                sdr_max_restart: 0,
                sdr_timeout_s: 0
            },
            "analysis": {
                signal_threshold_dbw: 0,
                snr_threshold_db: 0,
                signal_min_duration_ms: 0,
                signal_max_duration_ms: 0,
                fft_nperseg: 0,
                fft_window: ""
            },
            "matching": {
                matching_timeout_s: 0,
                matching_time_diff_s: 0,
                matching_bandwidth_hz: 0,
                matching_duration_diff_ms: 0
            },
            "publish": {
                path: "",
                mqtt_host: "",
                mqtt_port: 0,
                sig_stdout: false,
                match_stdout: false,
                csv: false,
                export_config: true,
                mqtt: false
            },
            "dashboard": {
                dashboard: false,
                dashboard_host: "",
                dashboard_port: 0,
                dashboard_signals: 0
            },
            "optional arguments": {
                verbose: 0,
                calibrate: false,
                config: "/boot/firmware/radiotracking.ini",
                station: null,
                schedule: []
            }
        },
        configLoaded: false,
        deviceCount: 1,
        isLoading: true,
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

        dispatchMessage(message, isError) {
            // Dispatch a custom event that the parent can listen for
            window.dispatchEvent(new CustomEvent('show-message', {
                detail: { message, isError }
            }));
        },

        addDevice() {
            // Add a new device with default values
            if (!this.configLoaded) return;
            this.config["rtl-sdr"].device.push('0');
            this.config["rtl-sdr"].calibration.push(0.0);
        },

        removeDevice(index) {
            // Only remove if there's more than one device
            if (!this.configLoaded) return;
            if (this.config["rtl-sdr"].device.length > 1) {
                this.config["rtl-sdr"].device.splice(index, 1);
                this.config["rtl-sdr"].calibration.splice(index, 1);
            }
        },

        updateDeviceList() {
            // Update device list based on deviceCount
            if (!this.configLoaded) return;
            
            const currentLength = this.config["rtl-sdr"].device.length;
            const targetLength = parseInt(this.deviceCount);
            
            if (targetLength > currentLength) {
                // Add new devices
                for (let i = currentLength; i < targetLength; i++) {
                    this.config["rtl-sdr"].device.push(i.toString());
                    this.config["rtl-sdr"].calibration.push(0.0);
                }
            } else if (targetLength < currentLength) {
                // Remove devices
                this.config["rtl-sdr"].device.splice(targetLength);
                this.config["rtl-sdr"].calibration.splice(targetLength);
            }
        },

        async init() {
            // Small delay to prevent simultaneous API calls during page load
            await new Promise(resolve => setTimeout(resolve, 100));
            
            // Load configuration and set up periodic refresh
            await this.loadConfig();
            await this.setupPeriodicRefresh();
        },

        async setupPeriodicRefresh() {
            // Get the refresh interval from system config
            const refreshIntervalSeconds = await getSystemRefreshInterval();
            
            // Set up periodic refresh for service status
            if (this.refreshInterval) {
                clearInterval(this.refreshInterval);
            }
            
            this.refreshInterval = setInterval(() => {
                // Only refresh if radiotracking tab is active
                const currentHash = window.location.hash.slice(1);
                if (currentHash === 'radiotracking') {
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
                const radiotrackingService = services.find(service => service.name === 'radiotracking');
                if (radiotrackingService) {
                    this.serviceStatus = {
                        active: radiotrackingService.active,
                        enabled: radiotrackingService.enabled,
                        status: radiotrackingService.status,
                        uptime: radiotrackingService.uptime || 'N/A'
                    };
                }
            } catch (error) {
                console.error('Failed to load service status:', error);
            } finally {
                this.serviceStatusLoading = false;
            }
        },

        async toggleEnable(serviceName, currentlyEnabled) {
            this.actionLoading = true;
            
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
                
                this.dispatchMessage(data.message, false);
                
                // Refresh service status after action
                setTimeout(async () => {
                    await this.loadServiceStatus();
                }, 1000);
                
            } catch (err) {
                this.dispatchMessage(err.message, true);
                console.error(`Service toggle error:`, err);
            } finally {
                this.actionLoading = false;
            }
        },

        async loadConfig() {
            try {
                this.isLoading = true;
                this.configLoaded = false;
                const response = await fetch('/api/radiotracking');
                if (response.status === 404) {
                    this.dispatchMessage("No radio tracking configuration found. Please create a configuration file first.", true);
                    this.isLoading = false;
                    return;
                }
                if (!response.ok) {
                    throw new Error('Failed to load radio tracking configuration');
                }
                const data = await response.json();
                // Update the existing config object instead of replacing it
                Object.assign(this.config, data);
                this.configLoaded = true;
                this.deviceCount = this.config["rtl-sdr"].device.length;
                
                // Load service status
                await this.loadServiceStatus();
            } catch (error) {
                this.dispatchMessage(error.message, true);
            } finally {
                this.isLoading = false;
            }
        },

        // Add computed property for center frequency in MHz
        get centerFreqMHz() {
            if (!this.configLoaded) return 0;
            return this.config["rtl-sdr"].center_freq / 1000000;
        },
        set centerFreqMHz(value) {
            if (!this.configLoaded) return;
            this.config["rtl-sdr"].center_freq = Math.round(value * 1000000);
        },

        // Add method to update center frequency
        updateCenterFreq() {
            // Ensure the value is within valid ranges
            const value = this.centerFreqMHz;
            if (value < 24) {
                this.centerFreqMHz = 24;
            } else if (value > 1766) {
                this.centerFreqMHz = 1766;
            }
        },

        // Add computed property for sample rate in kHz
        get sampleRateKHz() {
            if (!this.configLoaded) return 0;
            return Math.round(this.config["rtl-sdr"].sample_rate / 1000);
        },
        set sampleRateKHz(value) {
            if (!this.configLoaded) return;
            this.config["rtl-sdr"].sample_rate = value * 1000;
        },

        // Add method to update center frequency
        updateCenterFreq() {
            // Ensure the value is within valid ranges
            const value = this.centerFreqMHz;
            if (value < 24) {
                this.centerFreqMHz = 24;
            } else if (value > 1766) {
                this.centerFreqMHz = 1766;
            }
        },

        // Add computed property for bandwidth in kHz
        get bandwidthKHz() {
            if (!this.configLoaded) return 0;
            return this.config["matching"].matching_bandwidth_hz / 1000;
        },
        set bandwidthKHz(value) {
            if (!this.configLoaded) return;
            this.config["matching"].matching_bandwidth_hz = Math.round(value * 1000);
        },

        updateSampleRate() {
            // Ensure the value is within valid ranges
            const value = this.sampleRateKHz;
            if (value < 230 || (value > 300 && value < 900) || value > 3200) {
                // If outside valid ranges, set to nearest valid value
                if (value < 230) {
                    this.sampleRateKHz = 230;
                } else if (value > 300 && value < 900) {
                    this.sampleRateKHz = 300;
                } else if (value > 3200) {
                    this.sampleRateKHz = 3200;
                }
            }
        },

        updateBandwidth() {
            // Ensure the value is valid (minimum 0.1 kHz)
            const value = this.bandwidthKHz;
            if (value < 0.1) {
                this.bandwidthKHz = 0.1;
            }
        },

        async resetConfig() {
            // Reset by reloading from file
            await this.loadConfig();
        },

        async saveConfig() {
            const configSaveFunction = async () => {
                // Convert the frontend config format to the API format
                const apiConfig = {
                    optional_arguments: {
                        verbose: this.config["optional arguments"].verbose,
                        calibrate: this.config["optional arguments"].calibrate,
                        config: "/boot/firmware/radiotracking.ini", // Required field
                        station: this.config["optional arguments"].station || null,
                        schedule: this.config["optional arguments"].schedule || []
                    },
                    rtl_sdr: {
                        device: this.config["rtl-sdr"].device,
                        calibration: this.config["rtl-sdr"].calibration,
                        center_freq: this.config["rtl-sdr"].center_freq,
                        sample_rate: this.config["rtl-sdr"].sample_rate,
                        sdr_callback_length: this.config["rtl-sdr"].sdr_callback_length,
                        gain: this.config["rtl-sdr"].gain,
                        lna_gain: this.config["rtl-sdr"].lna_gain,
                        mixer_gain: this.config["rtl-sdr"].mixer_gain,
                        vga_gain: this.config["rtl-sdr"].vga_gain,
                        sdr_max_restart: this.config["rtl-sdr"].sdr_max_restart,
                        sdr_timeout_s: this.config["rtl-sdr"].sdr_timeout_s
                    },
                    analysis: {
                        fft_nperseg: this.config["analysis"].fft_nperseg,
                        fft_window: this.config["analysis"].fft_window,
                        signal_threshold_dbw: this.config["analysis"].signal_threshold_dbw,
                        snr_threshold_db: this.config["analysis"].snr_threshold_db,
                        signal_min_duration_ms: this.config["analysis"].signal_min_duration_ms,
                        signal_max_duration_ms: this.config["analysis"].signal_max_duration_ms
                    },
                    matching: {
                        matching_timeout_s: this.config["matching"].matching_timeout_s,
                        matching_time_diff_s: this.config["matching"].matching_time_diff_s,
                        matching_bandwidth_hz: this.config["matching"].matching_bandwidth_hz,
                        matching_duration_diff_ms: this.config["matching"].matching_duration_diff_ms
                    },
                    publish: {
                        sig_stdout: this.config["publish"].sig_stdout,
                        match_stdout: this.config["publish"].match_stdout,
                        path: this.config["publish"].path,
                        csv: this.config["publish"].csv,
                        export_config: this.config["publish"].export_config || true,
                        mqtt: this.config["publish"].mqtt,
                        mqtt_host: this.config["publish"].mqtt_host,
                        mqtt_port: this.config["publish"].mqtt_port
                    },
                    dashboard: {
                        dashboard: this.config["dashboard"].dashboard,
                        dashboard_host: this.config["dashboard"].dashboard_host,
                        dashboard_port: this.config["dashboard"].dashboard_port,
                        dashboard_signals: this.config["dashboard"].dashboard_signals
                    }
                };

                const response = await fetch('/api/radiotracking', {
                    method: 'PUT',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify(apiConfig)
                });
                if (!response.ok) {
                    const error = await response.json();
                    let errorMessage = error.detail?.message || 'Failed to save radio tracking configuration';
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
                this.dispatchMessage(data.message, false);  // false means not an error, so it will be success
            };
            
            await this.handleSaveConfig(configSaveFunction);
        },

        async downloadConfig() {
            try {
                // Convert the frontend config format to the API format (same as saveConfig)
                const apiConfig = {
                    optional_arguments: {
                        verbose: this.config["optional arguments"].verbose,
                        calibrate: this.config["optional arguments"].calibrate,
                        config: "/boot/firmware/radiotracking.ini", // Required field
                        station: this.config["optional arguments"].station || null,
                        schedule: this.config["optional arguments"].schedule || []
                    },
                    rtl_sdr: {
                        device: this.config["rtl-sdr"].device,
                        calibration: this.config["rtl-sdr"].calibration,
                        center_freq: this.config["rtl-sdr"].center_freq,
                        sample_rate: this.config["rtl-sdr"].sample_rate,
                        sdr_callback_length: this.config["rtl-sdr"].sdr_callback_length,
                        gain: this.config["rtl-sdr"].gain,
                        lna_gain: this.config["rtl-sdr"].lna_gain,
                        mixer_gain: this.config["rtl-sdr"].mixer_gain,
                        vga_gain: this.config["rtl-sdr"].vga_gain,
                        sdr_max_restart: this.config["rtl-sdr"].sdr_max_restart,
                        sdr_timeout_s: this.config["rtl-sdr"].sdr_timeout_s
                    },
                    analysis: {
                        fft_nperseg: this.config["analysis"].fft_nperseg,
                        fft_window: this.config["analysis"].fft_window,
                        signal_threshold_dbw: this.config["analysis"].signal_threshold_dbw,
                        snr_threshold_db: this.config["analysis"].snr_threshold_db,
                        signal_min_duration_ms: this.config["analysis"].signal_min_duration_ms,
                        signal_max_duration_ms: this.config["analysis"].signal_max_duration_ms
                    },
                    matching: {
                        matching_timeout_s: this.config["matching"].matching_timeout_s,
                        matching_time_diff_s: this.config["matching"].matching_time_diff_s,
                        matching_bandwidth_hz: this.config["matching"].matching_bandwidth_hz,
                        matching_duration_diff_ms: this.config["matching"].matching_duration_diff_ms
                    },
                    publish: {
                        sig_stdout: this.config["publish"].sig_stdout,
                        match_stdout: this.config["publish"].match_stdout,
                        path: this.config["publish"].path,
                        csv: this.config["publish"].csv,
                        export_config: this.config["publish"].export_config || true,
                        mqtt: this.config["publish"].mqtt,
                        mqtt_host: this.config["publish"].mqtt_host,
                        mqtt_port: this.config["publish"].mqtt_port
                    },
                    dashboard: {
                        dashboard: this.config["dashboard"].dashboard,
                        dashboard_host: this.config["dashboard"].dashboard_host,
                        dashboard_port: this.config["dashboard"].dashboard_port,
                        dashboard_signals: this.config["dashboard"].dashboard_signals
                    }
                };

                const response = await fetch('/api/radiotracking/download', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify(apiConfig)
                });
                
                if (!response.ok) {
                    const error = await response.json();
                    let errorMessage = error.detail?.message || 'Failed to download radio tracking configuration';
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

                // Create a blob from the response and trigger download
                const blob = await response.blob();
                const url = window.URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.style.display = 'none';
                a.href = url;
                a.download = 'radiotracking.ini';
                document.body.appendChild(a);
                a.click();
                window.URL.revokeObjectURL(url);
                document.body.removeChild(a);

                this.dispatchMessage('Radio tracking configuration downloaded successfully!', false);
            } catch (error) {
                this.dispatchMessage(error.message, true);
            }
        },

        async saveAndRestartService() {
            const configSaveFunction = async () => {
                // Convert the frontend config format to the API format
                const apiConfig = {
                    optional_arguments: {
                        verbose: this.config["optional arguments"].verbose,
                        calibrate: this.config["optional arguments"].calibrate,
                        config: "/boot/firmware/radiotracking.ini", // Required field
                        station: this.config["optional arguments"].station || null,
                        schedule: this.config["optional arguments"].schedule || []
                    },
                    rtl_sdr: {
                        device: this.config["rtl-sdr"].device,
                        calibration: this.config["rtl-sdr"].calibration,
                        center_freq: this.config["rtl-sdr"].center_freq,
                        sample_rate: this.config["rtl-sdr"].sample_rate,
                        sdr_callback_length: this.config["rtl-sdr"].sdr_callback_length,
                        gain: this.config["rtl-sdr"].gain,
                        lna_gain: this.config["rtl-sdr"].lna_gain,
                        mixer_gain: this.config["rtl-sdr"].mixer_gain,
                        vga_gain: this.config["rtl-sdr"].vga_gain,
                        sdr_max_restart: this.config["rtl-sdr"].sdr_max_restart,
                        sdr_timeout_s: this.config["rtl-sdr"].sdr_timeout_s
                    },
                    analysis: {
                        fft_nperseg: this.config["analysis"].fft_nperseg,
                        fft_window: this.config["analysis"].fft_window,
                        signal_threshold_dbw: this.config["analysis"].signal_threshold_dbw,
                        snr_threshold_db: this.config["analysis"].snr_threshold_db,
                        signal_min_duration_ms: this.config["analysis"].signal_min_duration_ms,
                        signal_max_duration_ms: this.config["analysis"].signal_max_duration_ms
                    },
                    matching: {
                        matching_timeout_s: this.config["matching"].matching_timeout_s,
                        matching_time_diff_s: this.config["matching"].matching_time_diff_s,
                        matching_bandwidth_hz: this.config["matching"].matching_bandwidth_hz,
                        matching_duration_diff_ms: this.config["matching"].matching_duration_diff_ms
                    },
                    publish: {
                        sig_stdout: this.config["publish"].sig_stdout,
                        match_stdout: this.config["publish"].match_stdout,
                        path: this.config["publish"].path,
                        csv: this.config["publish"].csv,
                        export_config: this.config["publish"].export_config || true,
                        mqtt: this.config["publish"].mqtt,
                        mqtt_host: this.config["publish"].mqtt_host,
                        mqtt_port: this.config["publish"].mqtt_port
                    },
                    dashboard: {
                        dashboard: this.config["dashboard"].dashboard,
                        dashboard_host: this.config["dashboard"].dashboard_host,
                        dashboard_port: this.config["dashboard"].dashboard_port,
                        dashboard_signals: this.config["dashboard"].dashboard_signals
                    }
                };

                const response = await fetch('/api/radiotracking', {
                    method: 'PUT',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify(apiConfig)
                });
                if (!response.ok) {
                    const error = await response.json();
                    let errorMessage = error.detail?.message || 'Failed to save radio tracking configuration';
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
                this.dispatchMessage(data.message, false);  // false means not an error, so it will be success
            };
            
            const restartFunction = async () => {
                // Then restart the radiotracking service
                const response = await fetch('/api/systemd/action', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({
                        service: 'radiotracking',
                        action: 'restart'
                    })
                });
                
                const data = await response.json();
                
                if (!response.ok) {
                    throw new Error(data.detail || 'Failed to restart radiotracking service');
                }
                
                this.dispatchMessage(`Configuration saved and ${data.message}`, false);
                
                // Refresh service status after restart
                setTimeout(async () => {
                    await this.loadServiceStatus();
                }, 2000);
            };
            
            await this.handleSaveAndRestartConfig(configSaveFunction, restartFunction);
        }
    }
}

function soundscapepipeConfig() {
    return {
        ...saveStateMixin(),
        config: {
            stream_port: 5001,
            lat: 50.85318,
            lon: 8.78735,
            input_device_match: "trackIT Analog Frontend",
            sample_rate: 384000,
            input_length_s: 0.1,
            channels: 2,
            detectors: {
                birdedge: {
                    detection_threshold: 0.0,
                    class_threshold: 0.3,
                    model_path: "",
                    channel_strategy: "mix",
                    tasks: []
                },
                yolobat: {
                    class_threshold: 0.3,
                    model_path: "",
                    channel_strategy: "mix",
                    tasks: []
                },
                schedule: {
                    enabled: false,
                    tasks: []
                }
            },
            output_device_match: "bcm2835 Headphones",
            speaker_enable_pin: 27,
            highpass_freq: 100,
            lure: {
                tasks: []
            },
            ratio: 0.0,
            length_s: 5,
            soundfile_limit: 5,
            soundfile_format: "flac",
            maximize_confidence: false,
            groups: {},
            disk_reserve_mb: 512
        },
        configLoaded: false,
        serviceStatus: {
            active: false,
            enabled: false,
            status: 'unknown',
            uptime: 'N/A'
        },
        refreshInterval: null,
        map: null,
        marker: null,
        mapInitialized: false,
        audioDevices: {},
        loadingDevices: false,
        modelFiles: {},
        loadingModels: false,
        lureFiles: {},
        loadingLureFiles: false,
        speciesData: {},
        loadingSpecies: false,
        actionLoading: false,
        diskInfo: [],

        async init() {
            // Small delay to prevent simultaneous API calls during page load
            await new Promise(resolve => setTimeout(resolve, 150));
            
            // Load model files first to ensure dropdown options are available
            await this.loadModelFiles();
            
            // Load disk info early for slider configuration
            await this.loadDiskInfo();
            
            // Then load config and other data
            await this.loadConfig();
            this.loadServiceStatus();
            this.loadAudioDevices();
            this.loadLureFiles();
            this.loadSpeciesData();
            
            // Set up watchers for detector enabled state changes
            this.setupDetectorWatchers();
            
            // Auto-refresh service status every 30 seconds when tab is active
            this.refreshInterval = setInterval(() => {
                // Only refresh if soundscapepipe tab is active
                const currentHash = window.location.hash.slice(1);
                if (currentHash === 'soundscapepipe') {
                    this.loadServiceStatus();
                }
            }, 30000);
        },

        cleanup() {
            if (this.refreshInterval) {
                clearInterval(this.refreshInterval);
                this.refreshInterval = null;
            }
        },

        async loadServiceStatus() {
            try {
                const services = await serviceManager.getServices();
                const soundscapepipeService = services.find(service => service.name === 'soundscapepipe');
                if (soundscapepipeService) {
                    this.serviceStatus = {
                        active: soundscapepipeService.active,
                        enabled: soundscapepipeService.enabled,
                        status: soundscapepipeService.status,
                        uptime: soundscapepipeService.uptime || 'N/A'
                    };
                }
            } catch (error) {
                console.error('Failed to load service status:', error);
            }
        },

        async toggleEnable(serviceName, currentlyEnabled) {
            this.actionLoading = true;
            
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
                
                this.showMessage(data.message, false);
                
                // Refresh service status after action
                setTimeout(async () => {
                    await this.loadServiceStatus();
                }, 1000);
                
            } catch (err) {
                this.showMessage(err.message, true);
                console.error(`Service toggle error:`, err);
            } finally {
                this.actionLoading = false;
            }
        },

        async loadConfig() {
            try {
                const response = await fetch('/api/soundscapepipe');
                if (response.ok) {
                    const data = await response.json();
                    // Handle backward compatibility for detectors - add enabled flags if missing
                    const detectors = data.detectors || {};
                    
                    // BirdEdge detector - enabled if present in config, disabled if not present
                    if (detectors.birdedge) {
                        detectors.birdedge.enabled = detectors.birdedge.enabled !== undefined ? detectors.birdedge.enabled : true;
                        detectors.birdedge.tasks = detectors.birdedge.tasks || [];
                        // Ensure tasks is always an array, not null
                        if (!Array.isArray(detectors.birdedge.tasks)) {
                            detectors.birdedge.tasks = [];
                        }
                        // Parse existing task time strings into UI components
                        detectors.birdedge.tasks.forEach(task => {
                            this.parseDetectorTaskTimeString(task, task.start, 'start');
                            this.parseDetectorTaskTimeString(task, task.stop, 'stop');
                        });
                        // Ensure channel_strategy exists with default value
                        if (detectors.birdedge.channel_strategy === undefined) {
                            detectors.birdedge.channel_strategy = "mix";
                        }
                    } else {
                        detectors.birdedge = { enabled: false, detection_threshold: 0.0, class_threshold: 0.3, model_path: "", channel_strategy: "mix", tasks: [] };
                    }
                    
                                        // YOLOBat detector - enabled if present in config, disabled if not present
                    if (detectors.yolobat) {
                        detectors.yolobat.enabled = detectors.yolobat.enabled !== undefined ? detectors.yolobat.enabled : true;
                        detectors.yolobat.tasks = detectors.yolobat.tasks || [];
                        // Ensure tasks is always an array, not null
                        if (!Array.isArray(detectors.yolobat.tasks)) {
                            detectors.yolobat.tasks = [];
                        }
                        // Parse existing task time strings into UI components
                        detectors.yolobat.tasks.forEach(task => {
                            this.parseDetectorTaskTimeString(task, task.start, 'start');
                            this.parseDetectorTaskTimeString(task, task.stop, 'stop');
                        });
                        // Use class_threshold if available, otherwise use detection_threshold for backwards compatibility
                        if (detectors.yolobat.class_threshold === undefined && detectors.yolobat.detection_threshold !== undefined) {
                            detectors.yolobat.class_threshold = detectors.yolobat.detection_threshold;
                        }
                        // Ensure class_threshold exists with default value
                        if (detectors.yolobat.class_threshold === undefined) {
                            detectors.yolobat.class_threshold = 0.3;
                        }
                        // Ensure channel_strategy exists with default value
                        if (detectors.yolobat.channel_strategy === undefined) {
                            detectors.yolobat.channel_strategy = "mix";
                        }
 
                    } else {
                        detectors.yolobat = { enabled: false, class_threshold: 0.3, model_path: "", channel_strategy: "mix", tasks: [] };
                    }
                    
                    // Static detector (schedule) - enabled if present in config, disabled if not present
                    if (detectors.schedule) {
                        detectors.schedule.enabled = detectors.schedule.enabled !== undefined ? detectors.schedule.enabled : true;
                        detectors.schedule.tasks = detectors.schedule.tasks || [];
                        // Ensure tasks is always an array, not null
                        if (!Array.isArray(detectors.schedule.tasks)) {
                            detectors.schedule.tasks = [];
                        }
                        // Parse existing task time strings into UI components
                        detectors.schedule.tasks.forEach(task => {
                            this.parseDetectorTaskTimeString(task, task.start, 'start');
                            this.parseDetectorTaskTimeString(task, task.stop, 'stop');
                        });
                    } else {
                        detectors.schedule = { enabled: false, tasks: [] };
                    }

                    // Handle lure tasks - parse existing time strings into UI components
                    const lure = data.lure || { tasks: [] };
                    // Ensure tasks is always an array, not null
                    if (!Array.isArray(lure.tasks)) {
                        lure.tasks = [];
                    }
                    if (lure.tasks && lure.tasks.length > 0) {
                        lure.tasks.forEach(task => {
                            this.parseLureTaskTimeString(task, task.start, 'start');
                            this.parseLureTaskTimeString(task, task.stop, 'stop');
                            // Ensure the time strings are properly synchronized after parsing
                            this.updateLureTaskTimeString(task, 'start');
                            this.updateLureTaskTimeString(task, 'stop');
                            // Ensure paths is always an array
                            if (!Array.isArray(task.paths)) {
                                task.paths = task.paths ? [task.paths] : [''];
                            }
                            // Ensure record is boolean
                            task.record = Boolean(task.record);
                        });
                    }

                    this.config = {
                        // Ensure all sections exist with defaults
                        stream_port: data.stream_port || 5001,
                        lat: data.lat || 50.85318,
                        lon: data.lon || 8.78735,
                        input_device_match: data.input_device_match || "trackIT Analog Frontend",
                        input_length_s: data.input_length_s || 0.1,
                        channels: data.channels || 2,
                        sample_rate: data.sample_rate || 384000,
                        detectors: detectors,
                        output_device_match: data.output_device_match || "bcm2835 Headphones",
                        speaker_enable_pin: data.speaker_enable_pin || 27,
                        highpass_freq: data.highpass_freq || 100,
                        lure: lure,
                        ratio: data.ratio || 0.0,
                        length_s: data.length_s || 5,
                        soundfile_limit: data.soundfile_limit || 5,
                        soundfile_format: data.soundfile_format || "flac",
                        maximize_confidence: data.maximize_confidence || false,
                        groups: data.groups || {},
                        disk_reserve_mb: Math.min(data.disk_reserve_mb || 512, this.getDataDiskSize())
                    };
                    
                    // Initialize group ratios and recording lengths with global values if not set
                    const globalRatio = this.config.ratio;
                    const globalLength = this.config.length_s;
                    Object.keys(this.config.groups).forEach(groupName => {
                        const group = this.config.groups[groupName];
                        if (group.ratio === undefined || group.ratio === null) {
                            group.ratio = globalRatio;
                        }
                        if (group.length_s === undefined || group.length_s === null) {
                            group.length_s = globalLength;
                        }
                        // Ensure maximize_confidence is always a boolean
                        if (group.maximize_confidence === undefined || group.maximize_confidence === null) {
                            group.maximize_confidence = false;
                        }
                    });
                    this.configLoaded = true;
                } else if (response.status === 404) {
                    // No configuration found, use defaults
                    this.configLoaded = true;
                    this.showMessage("No soundscapepipe configuration found. Using default values.", false);
                } else {
                    const error = await response.json();
                    throw new Error(error.detail || 'Failed to load soundscapepipe configuration');
                }
                
                // Auto-select default models for enabled detectors without a model
                this.autoSelectDefaultModels();
                
                // Initialize map after config is loaded, or update if already initialized
                if (!this.mapInitialized) {
                    setTimeout(() => this.initMap(), 200);
                } else {
                    this.updateMarkerFromInputs();
                }
            } catch (error) {
                this.showMessage(error.message, true);
                this.configLoaded = true; // Allow user to see form even if loading failed
                
                // Auto-select default models even if config loading failed
                this.autoSelectDefaultModels();
                
                // Initialize map with defaults even if config loading failed
                if (!this.mapInitialized) {
                    setTimeout(() => this.initMap(), 200);
                }
            }
        },

        async saveConfig() {
            const configSaveFunction = async () => {
                // Validate species groups first
                const speciesErrors = this.validateSpeciesGroups();
                if (speciesErrors.length > 0) {
                    this.showMessage('Species validation failed:\n• ' + speciesErrors.join('\n• '), true);
                    throw new Error('Species validation failed');
                }

                // Create a copy of the config and filter out disabled detectors
                const configToSave = { ...this.config };
                
                // Filter detectors to only include enabled ones
                configToSave.detectors = {};
                
                if (this.config.detectors.birdedge && this.config.detectors.birdedge.enabled) {
                    configToSave.detectors.birdedge = { ...this.config.detectors.birdedge };
                    delete configToSave.detectors.birdedge.enabled; // Remove the enabled flag from saved config
                    // Clean up tasks - convert UI components back to time strings
                    if (configToSave.detectors.birdedge.tasks) {
                        configToSave.detectors.birdedge.tasks = configToSave.detectors.birdedge.tasks.map(task => {
                            const cleanTask = { name: task.name, start: task.start, stop: task.stop };
                            return cleanTask;
                        });
                    }
                }
                
                if (this.config.detectors.yolobat && this.config.detectors.yolobat.enabled) {
                    configToSave.detectors.yolobat = { ...this.config.detectors.yolobat };
                    delete configToSave.detectors.yolobat.enabled; // Remove the enabled flag from saved config
                    // Set both thresholds to the same value from class_threshold
                    configToSave.detectors.yolobat.detection_threshold = this.config.detectors.yolobat.class_threshold;
                    // Remove schedule if it exists (YoloBat doesn't use schedule property)
                    delete configToSave.detectors.yolobat.schedule;
                    // Clean up tasks - convert UI components back to time strings
                    if (configToSave.detectors.yolobat.tasks) {
                        configToSave.detectors.yolobat.tasks = configToSave.detectors.yolobat.tasks.map(task => {
                            const cleanTask = { name: task.name, start: task.start, stop: task.stop };
                            return cleanTask;
                        });
                    }
                }
                
                if (this.config.detectors.schedule && this.config.detectors.schedule.enabled) {
                    configToSave.detectors.schedule = { ...this.config.detectors.schedule };
                    delete configToSave.detectors.schedule.enabled; // Remove the enabled flag from saved config
                    // Clean up tasks - convert UI components back to time strings
                    if (configToSave.detectors.schedule.tasks) {
                        configToSave.detectors.schedule.tasks = configToSave.detectors.schedule.tasks.map(task => {
                            const cleanTask = { name: task.name, start: task.start, stop: task.stop };
                            return cleanTask;
                        });
                    }
                }

                // Ensure all lure task time strings are synced with UI components before saving
                if (this.config.lure && this.config.lure.tasks) {
                    this.config.lure.tasks.forEach(task => {
                        this.updateLureTaskTimeString(task, 'start');
                        this.updateLureTaskTimeString(task, 'stop');
                    });
                }

                // Clean up lure tasks - convert UI components back to time strings and filter out empty tasks
                if (configToSave.lure && configToSave.lure.tasks) {
                    // Filter out tasks that don't have a species name (empty/incomplete tasks)
                    const validTasks = configToSave.lure.tasks.filter(task => 
                        task && task.species && task.species.trim() !== ''
                    );
                    
                    // Create a new array to avoid modifying the original that UI is bound to
                    configToSave.lure = {
                        ...configToSave.lure,
                        tasks: validTasks.map(task => {
                            const cleanTask = { 
                                species: task.species, 
                                paths: task.paths || [''],
                                start: task.start || '00:00', 
                                stop: task.stop || '00:00',
                                record: Boolean(task.record)
                            };
                            return cleanTask;
                        })
                    };
                } else if (configToSave.lure) {
                    // Ensure tasks is always an empty array, not null
                    configToSave.lure.tasks = [];
                }

                // Clean up groups - remove null/undefined values and filter empty species
                if (configToSave.groups) {
                    configToSave.groups = {};
                    Object.keys(this.config.groups).forEach(groupName => {
                        const group = this.config.groups[groupName];
                        const cleanGroup = {
                            species: (group.species || []).filter(species => species && species.trim() !== '')
                        };
                        
                        // Only include ratio if it's not null/undefined
                        if (group.ratio !== null && group.ratio !== undefined) {
                            cleanGroup.ratio = group.ratio;
                        }
                        
                        // Only include length_s if it's not null/undefined
                        if (group.length_s !== null && group.length_s !== undefined) {
                            cleanGroup.length_s = group.length_s;
                        }
                        
                        // Always include maximize_confidence as a boolean
                        cleanGroup.maximize_confidence = Boolean(group.maximize_confidence);
                        
                        configToSave.groups[groupName] = cleanGroup;
                    });
                }



                const response = await fetch('/api/soundscapepipe', {
                    method: 'PUT',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify(configToSave)
                });
                
                if (response.ok) {
                    this.showMessage('Soundscapepipe configuration saved successfully!', false);
                } else {
                    const error = await response.json();
                    let errorMessage = error.detail?.message || 'Failed to save soundscapepipe configuration';
                    if (error.detail?.errors) {
                        errorMessage += '\nErrors: ' + error.detail.errors.join(', ');
                    }
                    throw new Error(errorMessage);
                }
            };
            
            await this.handleSaveConfig(configSaveFunction);
        },

        async downloadConfig() {
            try {
                // Validate species groups first
                const speciesErrors = this.validateSpeciesGroups();
                if (speciesErrors.length > 0) {
                    this.showMessage('Species validation failed:\n• ' + speciesErrors.join('\n• '), true);
                    return;
                }

                // Create a copy of the config and filter out disabled detectors
                const configToDownload = { ...this.config };
                
                // Filter detectors to only include enabled ones
                configToDownload.detectors = {};
                
                if (this.config.detectors.birdedge && this.config.detectors.birdedge.enabled) {
                    configToDownload.detectors.birdedge = { ...this.config.detectors.birdedge };
                    delete configToDownload.detectors.birdedge.enabled; // Remove the enabled flag from downloaded config
                    // Clean up tasks - convert UI components back to time strings
                    if (configToDownload.detectors.birdedge.tasks) {
                        configToDownload.detectors.birdedge.tasks = configToDownload.detectors.birdedge.tasks.map(task => {
                            const cleanTask = { name: task.name, start: task.start, stop: task.stop };
                            return cleanTask;
                        });
                    }
                }
                
                if (this.config.detectors.yolobat && this.config.detectors.yolobat.enabled) {
                    configToDownload.detectors.yolobat = { ...this.config.detectors.yolobat };
                    delete configToDownload.detectors.yolobat.enabled; // Remove the enabled flag from downloaded config
                    // Set both thresholds to the same value from class_threshold
                    configToDownload.detectors.yolobat.detection_threshold = this.config.detectors.yolobat.class_threshold;
                    // Remove schedule if it exists (YoloBat doesn't use schedule property)
                    delete configToDownload.detectors.yolobat.schedule;
                    // Clean up tasks - convert UI components back to time strings
                    if (configToDownload.detectors.yolobat.tasks) {
                        configToDownload.detectors.yolobat.tasks = configToDownload.detectors.yolobat.tasks.map(task => {
                            const cleanTask = { name: task.name, start: task.start, stop: task.stop };
                            return cleanTask;
                        });
                    }
                }
                
                if (this.config.detectors.schedule && this.config.detectors.schedule.enabled) {
                    configToDownload.detectors.schedule = { ...this.config.detectors.schedule };
                    delete configToDownload.detectors.schedule.enabled; // Remove the enabled flag from downloaded config
                    // Clean up tasks - convert UI components back to time strings
                    if (configToDownload.detectors.schedule.tasks) {
                        configToDownload.detectors.schedule.tasks = configToDownload.detectors.schedule.tasks.map(task => {
                            const cleanTask = { name: task.name, start: task.start, stop: task.stop };
                            return cleanTask;
                        });
                    }
                }

                // Clean up lure tasks - convert UI components back to time strings and filter out empty tasks
                if (configToDownload.lure && configToDownload.lure.tasks) {
                    // Filter out tasks that don't have a species name (empty/incomplete tasks)
                    const validTasks = configToDownload.lure.tasks.filter(task => 
                        task && task.species && task.species.trim() !== ''
                    );
                    
                    configToDownload.lure.tasks = validTasks.map(task => {
                        const cleanTask = { 
                            species: task.species, 
                            paths: task.paths || [''],
                            start: task.start || '00:00', 
                            stop: task.stop || '00:00',
                            record: Boolean(task.record)
                        };
                        return cleanTask;
                    });
                } else if (configToDownload.lure) {
                    // Ensure tasks is always an empty array, not null
                    configToDownload.lure.tasks = [];
                }

                // Clean up groups - remove null/undefined values and filter empty species
                if (configToDownload.groups) {
                    configToDownload.groups = {};
                    Object.keys(this.config.groups).forEach(groupName => {
                        const group = this.config.groups[groupName];
                        const cleanGroup = {
                            species: (group.species || []).filter(species => species && species.trim() !== '')
                        };
                        
                        // Only include ratio if it's not null/undefined
                        if (group.ratio !== null && group.ratio !== undefined) {
                            cleanGroup.ratio = group.ratio;
                        }
                        
                        // Only include length_s if it's not null/undefined
                        if (group.length_s !== null && group.length_s !== undefined) {
                            cleanGroup.length_s = group.length_s;
                        }
                        
                        // Always include maximize_confidence as a boolean
                        cleanGroup.maximize_confidence = Boolean(group.maximize_confidence);
                        
                        configToDownload.groups[groupName] = cleanGroup;
                    });
                }

                const response = await fetch('/api/soundscapepipe/download', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify(configToDownload)
                });
                
                if (!response.ok) {
                    const error = await response.json();
                    let errorMessage = error.detail?.message || 'Failed to download soundscapepipe configuration';
                    if (error.detail?.errors) {
                        errorMessage += '\nErrors: ' + error.detail.errors.join(', ');
                    }
                    throw new Error(errorMessage);
                }

                // Create a blob from the response and trigger download
                const blob = await response.blob();
                const url = window.URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.style.display = 'none';
                a.href = url;
                a.download = 'soundscapepipe.yml';
                document.body.appendChild(a);
                a.click();
                window.URL.revokeObjectURL(url);
                document.body.removeChild(a);

                this.showMessage('Soundscapepipe configuration downloaded successfully!', false);
            } catch (error) {
                this.showMessage(error.message, true);
            }
        },

        async saveAndRestartService() {
            const configSaveFunction = async () => {
                // First save the configuration (custom logic to avoid interfering with regular save button)
                const speciesErrors = this.validateSpeciesGroups();
                if (speciesErrors.length > 0) {
                    this.showMessage('Species validation failed:\n• ' + speciesErrors.join('\n• '), true);
                    throw new Error('Species validation failed');
                }

                // Create a copy of the config and filter out disabled detectors (same logic as saveConfig)
                const configToSave = { ...this.config };
                
                // Filter detectors to only include enabled ones
                configToSave.detectors = {};
                
                if (this.config.detectors.birdedge && this.config.detectors.birdedge.enabled) {
                    configToSave.detectors.birdedge = { ...this.config.detectors.birdedge };
                    delete configToSave.detectors.birdedge.enabled;
                    if (configToSave.detectors.birdedge.tasks) {
                        configToSave.detectors.birdedge.tasks = configToSave.detectors.birdedge.tasks.map(task => {
                            const cleanTask = { name: task.name, start: task.start, stop: task.stop };
                            return cleanTask;
                        });
                    }
                }
                
                if (this.config.detectors.yolobat && this.config.detectors.yolobat.enabled) {
                    configToSave.detectors.yolobat = { ...this.config.detectors.yolobat };
                    delete configToSave.detectors.yolobat.enabled;
                    configToSave.detectors.yolobat.detection_threshold = this.config.detectors.yolobat.class_threshold;
                    delete configToSave.detectors.yolobat.schedule;
                    if (configToSave.detectors.yolobat.tasks) {
                        configToSave.detectors.yolobat.tasks = configToSave.detectors.yolobat.tasks.map(task => {
                            const cleanTask = { name: task.name, start: task.start, stop: task.stop };
                            return cleanTask;
                        });
                    }
                }
                
                if (this.config.detectors.schedule && this.config.detectors.schedule.enabled) {
                    configToSave.detectors.schedule = { ...this.config.detectors.schedule };
                    delete configToSave.detectors.schedule.enabled;
                    if (configToSave.detectors.schedule.tasks) {
                        configToSave.detectors.schedule.tasks = configToSave.detectors.schedule.tasks.map(task => {
                            const cleanTask = { name: task.name, start: task.start, stop: task.stop };
                            return cleanTask;
                        });
                    }
                }

                // Clean up lure and groups (same logic as saveConfig)
                if (this.config.lure && this.config.lure.tasks) {
                    this.config.lure.tasks.forEach(task => {
                        this.updateLureTaskTimeString(task, 'start');
                        this.updateLureTaskTimeString(task, 'stop');
                    });
                }

                if (configToSave.lure && configToSave.lure.tasks) {
                    const validTasks = configToSave.lure.tasks.filter(task => 
                        task && task.species && task.species.trim() !== ''
                    );
                    
                    configToSave.lure = {
                        ...configToSave.lure,
                        tasks: validTasks.map(task => {
                            const cleanTask = { 
                                species: task.species, 
                                paths: task.paths || [''],
                                start: task.start || '00:00', 
                                stop: task.stop || '00:00',
                                record: Boolean(task.record)
                            };
                            return cleanTask;
                        })
                    };
                } else if (configToSave.lure) {
                    configToSave.lure.tasks = [];
                }

                if (configToSave.groups) {
                    configToSave.groups = {};
                    Object.keys(this.config.groups).forEach(groupName => {
                        const group = this.config.groups[groupName];
                        const cleanGroup = {
                            species: (group.species || []).filter(species => species && species.trim() !== '')
                        };
                        
                        if (group.ratio !== null && group.ratio !== undefined) {
                            cleanGroup.ratio = group.ratio;
                        }
                        
                        if (group.length_s !== null && group.length_s !== undefined) {
                            cleanGroup.length_s = group.length_s;
                        }
                        
                        cleanGroup.maximize_confidence = Boolean(group.maximize_confidence);
                        
                        configToSave.groups[groupName] = cleanGroup;
                    });
                }

                // Save the configuration
                const saveResponse = await fetch('/api/soundscapepipe', {
                    method: 'PUT',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify(configToSave)
                });
                
                if (!saveResponse.ok) {
                    const error = await saveResponse.json();
                    let errorMessage = error.detail?.message || 'Failed to save soundscapepipe configuration';
                    if (error.detail?.errors) {
                        errorMessage += '\nErrors: ' + error.detail.errors.join(', ');
                    }
                    throw new Error(errorMessage);
                }
                
                if (!saveResponse.ok) {
                    const error = await saveResponse.json();
                    let errorMessage = error.detail?.message || 'Failed to save soundscapepipe configuration';
                    if (error.detail?.errors) {
                        errorMessage += '\nErrors: ' + error.detail.errors.join(', ');
                    }
                    throw new Error(errorMessage);
                }
            };
            
            const restartFunction = async () => {
                // Then restart the soundscapepipe service
                const response = await fetch('/api/systemd/action', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({
                        service: 'soundscapepipe',
                        action: 'restart'
                    })
                });
                
                const data = await response.json();
                
                if (!response.ok) {
                    throw new Error(data.detail || 'Failed to restart soundscapepipe service');
                }
                
                this.showMessage(`Configuration saved and ${data.message}`, false);
                
                // Refresh service status after restart
                setTimeout(async () => {
                    await this.loadServiceStatus();
                }, 2000);
            };
            
            await this.handleSaveAndRestartConfig(configSaveFunction, restartFunction);
        },

        resetConfig() {
            this.loadConfig();
        },

        showMessage(message, isError = false) {
            // Dispatch to parent component
            this.$dispatch('message', { message, error: isError });
        },

        streamLogs(serviceName) {
            // Show the log modal directly
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

        initMap() {
            // Only initialize if not already done and container is visible
            if (this.mapInitialized || !document.getElementById('soundscapeMap')) {
                return;
            }

            // Wait a bit to ensure the container is properly rendered
            setTimeout(() => {
                if (!document.getElementById('soundscapeMap') || this.mapInitialized) {
                    return;
                }

                // Initialize map with loaded coordinates
                this.map = L.map('soundscapeMap', {
                    center: [this.config.lat, this.config.lon],
                    zoom: 13,
                    zoomControl: true
                });
                
                // Add Mapbox satellite streets layer
                L.tileLayer('https://api.mapbox.com/styles/v1/mapbox/satellite-streets-v12/tiles/{z}/{x}/{y}?access_token=pk.eyJ1IjoidHJhY2tpdHN5c3RlbXMiLCJhIjoiY21iaHEwbXcwMDEzcTJqc2JhNzdobDluaSJ9.NLRmiJEDHQgPJEyceCA57g', {
                    attribution: '© Mapbox © OpenStreetMap',
                    maxZoom: 19
                }).addTo(this.map);

                // Add locate control
                L.control.locate({
                    position: 'topleft',
                    strings: {
                        title: "Show my location"
                    },
                    flyTo: true,
                    keepCurrentZoomLevel: true,
                    locateOptions: {
                        enableHighAccuracy: true
                    }
                }).addTo(this.map);

                // Add marker
                this.marker = L.marker([this.config.lat, this.config.lon], {
                    draggable: true
                }).addTo(this.map);

                // Update coordinates when marker is dragged
                this.marker.on('dragend', (e) => {
                    const position = e.target.getLatLng();
                    this.config.lat = parseFloat(position.lat.toFixed(8));
                    this.config.lon = parseFloat(position.lng.toFixed(8));
                });

                // Update marker when map is clicked
                this.map.on('click', (e) => {
                    const position = e.latlng;
                    this.marker.setLatLng(position);
                    this.config.lat = parseFloat(position.lat.toFixed(8));
                    this.config.lon = parseFloat(position.lng.toFixed(8));
                });

                // Handle location found event
                this.map.on('locationfound', (e) => {
                    this.config.lat = parseFloat(e.latlng.lat.toFixed(8));
                    this.config.lon = parseFloat(e.latlng.lng.toFixed(8));
                    this.updateMarkerFromInputs();
                });

                this.mapInitialized = true;
                
                // Force a resize to ensure tiles load properly
                setTimeout(() => {
                    if (this.map) {
                        this.map.invalidateSize();
                    }
                }, 100);
            }, 100);
        },

        ensureMapVisible() {
            // Call this when the soundscapepipe tab becomes active
            if (this.map && this.mapInitialized) {
                setTimeout(() => {
                    this.map.invalidateSize();
                    this.map.setView([this.config.lat, this.config.lon], 13);
                }, 50);
            } else if (!this.mapInitialized) {
                this.initMap();
            }
        },

        updateMarkerFromInputs() {
            if (this.marker && this.mapInitialized) {
                // Ensure coordinates are within bounds and have proper precision
                const lat = Math.min(Math.max(parseFloat(this.config.lat), -90), 90);
                const lon = Math.min(Math.max(parseFloat(this.config.lon), -180), 180);
                
                // Update the marker and map view
                this.marker.setLatLng([lat, lon]);
                this.map.setView([lat, lon]);
                
                // Update the input values with properly formatted numbers
                this.config.lat = parseFloat(lat.toFixed(8));
                this.config.lon = parseFloat(lon.toFixed(8));
            }
        },

        async loadAudioDevices() {
            this.loadingDevices = true;
            try {
                const response = await fetch('/api/soundscapepipe/audio-devices');
                if (response.ok) {
                    this.audioDevices = await response.json();
                } else {
                    const error = await response.json();
                    this.showMessage(`Failed to load audio devices: ${error.detail}`, true);
                }
            } catch (error) {
                this.showMessage(`Failed to load audio devices: ${error.message}`, true);
            } finally {
                this.loadingDevices = false;
            }
        },

        updateInputDeviceFromSelection(deviceName, selectedOption) {
            if (deviceName) {
                // Extract just the device name part (before the colon if it exists)
                // e.g. "trackIT Analog Frontend: Audio (hw:2,0)" -> "trackIT Analog Frontend"
                
                const cleanDeviceName = deviceName.split(':')[0].trim();
                this.config.input_device_match = cleanDeviceName;
                
                // Set sample rate from selected device
                if (selectedOption && selectedOption.dataset.sampleRate) {
                    this.config.sample_rate = Math.round(parseFloat(selectedOption.dataset.sampleRate));
                }

                // Set channels from selected device
                if (selectedOption && selectedOption.dataset.maxChannels) {
                    this.config.channels = Math.min(parseInt(selectedOption.dataset.maxChannels), 2);
                }
            }
        },

        updateOutputDeviceFromSelection(deviceName, selectedOption) {
            if (deviceName) {
                // Extract just the device name part (before the colon if it exists)
                // e.g. "USB AUDIO DEVICE: Audio (hw:3,0)" -> "USB AUDIO DEVICE"
                const cleanDeviceName = deviceName.split(':')[0].trim();
                this.config.output_device_match = cleanDeviceName;
            }
        },

        async loadModelFiles() {
            this.loadingModels = true;
            try {
                const response = await fetch('/api/soundscapepipe/model-files');
                if (response.ok) {
                    this.modelFiles = await response.json();
                    // Auto-select default models after loading model files
                    this.autoSelectDefaultModels();
                } else {
                    const error = await response.json();
                    this.showMessage(`Failed to load model files: ${error.detail}`, true);
                }
            } catch (error) {
                this.showMessage(`Failed to load model files: ${error.message}`, true);
            } finally {
                this.loadingModels = false;
            }
        },

        // Helper function to select default model when enabling a detector
        selectDefaultModel(detectorType) {
            if (!this.modelFiles || !this.modelFiles[detectorType]) {
                return;
            }

            const availableModels = this.modelFiles[detectorType];
            if (availableModels.length === 0) {
                return;
            }

            let selectedModel = null;

            // Define preferred model patterns
            const preferredPatterns = {
                'yolobat': 'yolobat11_2025.3.2',
                'birdedge': 'MarBird_EFL0_GER.onnx'
            };

            const preferredPattern = preferredPatterns[detectorType];
            if (preferredPattern) {
                // Look for preferred model first
                selectedModel = availableModels.find(model => 
                    model.toLowerCase().includes(preferredPattern.toLowerCase())
                );
            }

            // Fallback to first available model if no preferred model found
            if (!selectedModel) {
                selectedModel = availableModels[0];
            }

            // Set the selected model
            this.config.detectors[detectorType].model_path = selectedModel;
            
            // For yolobat, also load the labels
            if (detectorType === 'yolobat') {
                this.loadYoloBatLabels();
            }
        },



        // Auto-select default models for enabled detectors without a model
        autoSelectDefaultModels() {
            if (!this.configLoaded || !this.modelFiles) {
                return;
            }

            // Check each detector type
            ['birdedge', 'yolobat'].forEach(detectorType => {
                const detector = this.config.detectors[detectorType];
                if (detector && detector.enabled && !detector.model_path) {
                    this.selectDefaultModel(detectorType);
                }
            });
        },

        // Set up watchers for detector enabled state changes
        setupDetectorWatchers() {
            // Watch for birdedge detector enabled state changes
            this.$watch('config.detectors.birdedge.enabled', (enabled, oldEnabled) => {
                if (enabled) {
                    // If no model path or model path doesn't exist in available models, select default
                    const shouldAutoSelect = !this.config.detectors.birdedge.model_path || 
                                           (this.modelFiles.birdedge && 
                                            !this.modelFiles.birdedge.includes(this.config.detectors.birdedge.model_path));
                    
                    if (shouldAutoSelect) {
                        this.selectDefaultModel('birdedge');
                    }
                }
            });

            // Watch for yolobat detector enabled state changes
            this.$watch('config.detectors.yolobat.enabled', (enabled, oldEnabled) => {
                if (enabled) {
                    // If no model path or model path doesn't exist in available models, select default
                    const shouldAutoSelect = !this.config.detectors.yolobat.model_path || 
                                           (this.modelFiles.yolobat && 
                                            !this.modelFiles.yolobat.includes(this.config.detectors.yolobat.model_path));
                    
                    if (shouldAutoSelect) {
                        this.selectDefaultModel('yolobat');
                    }
                }
            });
        },

        async loadLureFiles() {
            this.loadingLureFiles = true;
            try {
                const response = await fetch('/api/soundscapepipe/lure-files');
                if (response.ok) {
                    this.lureFiles = await response.json();
                } else {
                    console.error('Failed to load lure files');
                    this.lureFiles = { directories: [], files: [] };
                }
            } catch (error) {
                console.error('Error loading lure files:', error);
                this.lureFiles = { directories: [], files: [] };
            } finally {
                this.loadingLureFiles = false;
            }
        },

        async loadSpeciesData() {
            this.loadingSpecies = true;
            try {
                const response = await fetch('/api/soundscapepipe/species');
                if (response.ok) {
                    this.speciesData = await response.json();
                } else {
                    console.error('Failed to load species data');
                    this.speciesData = { birdedge: [], yolobat: [] };
                }
                
                // Load YoloBat labels if a model is selected
                await this.loadYoloBatLabels();
            } catch (error) {
                console.error('Error loading species data:', error);
                this.speciesData = { birdedge: [], yolobat: [] };
            } finally {
                this.loadingSpecies = false;
            }
        },

        async loadDiskInfo() {
            try {
                const response = await fetch('/api/system-status');
                if (response.ok) {
                    const data = await response.json();
                    this.diskInfo = data.disk || [];
                } else {
                    console.error('Failed to load disk info');
                    this.diskInfo = [];
                }
            } catch (error) {
                console.error('Error loading disk info:', error);
                this.diskInfo = [];
            }
        },

        getDataDiskSize() {
            if (!this.diskInfo || !Array.isArray(this.diskInfo) || this.diskInfo.length === 0) {
                return 2048; // Default fallback of 2GB in MB
            }
            
            // Find the disk that contains /data, prioritizing exact matches
            let dataDisk = this.diskInfo.find(disk => disk.mountpoint === '/data');
            
            // Fallback to root filesystem if /data not found
            if (!dataDisk) {
                dataDisk = this.diskInfo.find(disk => disk.mountpoint === '/');
            }
            
            // Fallback to any disk that might contain /data
            if (!dataDisk) {
                dataDisk = this.diskInfo.find(disk => 
                    disk.mountpoint && disk.mountpoint.includes('/data')
                );
            }
            
            // Use the first available disk as last resort
            if (!dataDisk && this.diskInfo.length > 0) {
                dataDisk = this.diskInfo[0];
            }
            
            if (dataDisk && dataDisk.total && dataDisk.total > 0) {
                // Convert from bytes to megabytes and round down
                const sizeInMB = Math.floor(dataDisk.total / (1024 * 1024));
                // Ensure we have at least 1GB available for the slider
                return Math.max(sizeInMB, 1024);
            }
            
            return 2048; // Default fallback of 2GB in MB
        },

        async loadYoloBatLabels() {
            try {
                // Get the selected YoloBat model path
                const modelPath = this.config.detectors.yolobat?.model_path;
                if (!modelPath) {
                    // No model selected, keep yolobat labels empty
                    this.speciesData.yolobat = [];
                    return;
                }

                const response = await fetch(`/api/soundscapepipe/yolobat-labels?model_path=${encodeURIComponent(modelPath)}`);
                if (response.ok) {
                    const data = await response.json();
                    // Use enhanced labels if available, otherwise fallback to basic labels
                    if (data.enhanced_labels && data.enhanced_labels.length > 0) {
                        // Map enhanced labels to include the original model label for storage
                        this.speciesData.yolobat = data.enhanced_labels.map((enhanced, index) => ({
                            scientific: enhanced.scientific,
                            english: enhanced.english,
                            german: enhanced.german,
                            display: enhanced.display,
                            searchable: enhanced.searchable,
                            modelLabel: data.labels[index] // Store the original model label (e.g., "Ppip")
                        }));
                    } else {
                        // Fallback to basic labels if no enhanced data available
                        this.speciesData.yolobat = data.labels.map(label => ({
                            scientific: label,
                            english: "",
                            german: "",
                            display: label,
                            searchable: label.toLowerCase(),
                            modelLabel: label
                        }));
                    }
                } else {
                    console.error('Failed to load YoloBat labels');
                    this.speciesData.yolobat = [];
                }
            } catch (error) {
                console.error('Error loading YoloBat labels:', error);
                this.speciesData.yolobat = [];
            }
        },

        // Detector task management methods
        updateDetectorTaskTimeString(detectorName, task, type) {
            const reference = task[`${type}Reference`];
            const sign = task[`${type}Sign`];
            const offset = task[`${type}Offset`];
            
            if (reference === 'time') {
                task[type] = offset;
            } else {
                task[type] = `${reference}${sign}${offset}`;
            }
        },

        addDetectorTask(detectorName) {
            if (!this.config.detectors[detectorName].tasks) {
                this.config.detectors[detectorName].tasks = [];
            }
            
            this.config.detectors[detectorName].tasks.push({
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

        removeDetectorTask(detectorName, index) {
            this.config.detectors[detectorName].tasks.splice(index, 1);
        },

        parseDetectorTaskTimeString(task, timeStr, type) {
            if (timeStr.includes('sunrise')) {
                task[`${type}Reference`] = 'sunrise';
                task[`${type}Sign`] = timeStr.includes('-') ? '-' : '+';
                task[`${type}Offset`] = timeStr.replace('sunrise', '').replace('+', '').replace('-', '').trim();
            } else if (timeStr.includes('sunset')) {
                task[`${type}Reference`] = 'sunset';
                task[`${type}Sign`] = timeStr.includes('-') ? '-' : '+';
                task[`${type}Offset`] = timeStr.replace('sunset', '').replace('+', '').replace('-', '').trim();
            } else if (timeStr.includes('dawn')) {
                task[`${type}Reference`] = 'dawn';
                task[`${type}Sign`] = timeStr.includes('-') ? '-' : '+';
                task[`${type}Offset`] = timeStr.replace('dawn', '').replace('+', '').replace('-', '').trim();
            } else if (timeStr.includes('dusk')) {
                task[`${type}Reference`] = 'dusk';
                task[`${type}Sign`] = timeStr.includes('-') ? '-' : '+';
                task[`${type}Offset`] = timeStr.replace('dusk', '').replace('+', '').replace('-', '').trim();
            } else {
                // Assume it's a clock time
                task[`${type}Reference`] = 'time';
                task[`${type}Sign`] = '+';
                task[`${type}Offset`] = timeStr;
            }
        },

        // Lure task management methods
        updateLureTaskTimeString(task, type) {
            const reference = task[`${type}Reference`];
            const sign = task[`${type}Sign`];
            const offset = task[`${type}Offset`];
            
            if (reference === 'time') {
                task[type] = offset;
            } else {
                task[type] = `${reference}${sign}${offset}`;
            }
        },

        addLureTask() {
            if (!this.config.lure) {
                this.config.lure = { tasks: [] };
            }
            if (!this.config.lure.tasks) {
                this.config.lure.tasks = [];
            }
            
            this.config.lure.tasks.push({
                species: '',
                paths: [''],
                start: '00:00',
                stop: '00:00',
                record: false,
                startReference: 'time',
                startOffset: '00:00',
                startSign: '+',
                stopReference: 'time',
                stopOffset: '00:00',
                stopSign: '+'
            });
        },

        removeLureTask(index) {
            this.config.lure.tasks.splice(index, 1);
        },

        parseLureTaskTimeString(task, timeStr, type) {
            if (timeStr.includes('sunrise')) {
                task[`${type}Reference`] = 'sunrise';
                task[`${type}Sign`] = timeStr.includes('-') ? '-' : '+';
                task[`${type}Offset`] = timeStr.replace('sunrise', '').replace('+', '').replace('-', '').trim();
            } else if (timeStr.includes('sunset')) {
                task[`${type}Reference`] = 'sunset';
                task[`${type}Sign`] = timeStr.includes('-') ? '-' : '+';
                task[`${type}Offset`] = timeStr.replace('sunset', '').replace('+', '').replace('-', '').trim();
            } else if (timeStr.includes('dawn')) {
                task[`${type}Reference`] = 'dawn';
                task[`${type}Sign`] = timeStr.includes('-') ? '-' : '+';
                task[`${type}Offset`] = timeStr.replace('dawn', '').replace('+', '').replace('-', '').trim();
            } else if (timeStr.includes('dusk')) {
                task[`${type}Reference`] = 'dusk';
                task[`${type}Sign`] = timeStr.includes('-') ? '-' : '+';
                task[`${type}Offset`] = timeStr.replace('dusk', '').replace('+', '').replace('-', '').trim();
            } else {
                // Assume it's a clock time
                task[`${type}Reference`] = 'time';
                task[`${type}Sign`] = '+';
                task[`${type}Offset`] = timeStr;
            }
        },

        addLureTaskPath(taskIndex) {
            if (!this.config.lure.tasks[taskIndex].paths) {
                this.config.lure.tasks[taskIndex].paths = [];
            }
            this.config.lure.tasks[taskIndex].paths.push('');
        },

        removeLureTaskPath(taskIndex, pathIndex) {
            this.config.lure.tasks[taskIndex].paths.splice(pathIndex, 1);
        },

        // Species group management methods
        addGroup() {
            if (!this.config.groups) {
                this.config.groups = {};
            }
            
            // Create group with empty name - user will provide their own name
            const groupName = '';
            this.config.groups[groupName] = {
                ratio: 0.0,
                maximize_confidence: false,
                length_s: this.config.length_s || 5,
                species: ['']
            };
        },

        removeGroup(groupName) {
            if (this.config.groups && this.config.groups[groupName]) {
                delete this.config.groups[groupName];
            }
        },

        renameGroup(oldName, newName) {
            if (!newName || newName === oldName || !this.config.groups || !this.config.groups[oldName]) {
                return;
            }
            
            // Check if new name already exists
            if (this.config.groups[newName]) {
                this.showMessage(`Group name "${newName}" already exists!`, true);
                return;
            }
            
            // Create new group with new name
            this.config.groups[newName] = { ...this.config.groups[oldName] };
            
            // Delete old group
            delete this.config.groups[oldName];
        },

        addSpeciesToGroup(groupName) {
            if (!this.config.groups || !this.config.groups[groupName]) {
                return;
            }
            
            if (!this.config.groups[groupName].species) {
                this.config.groups[groupName].species = [];
            }
            
            this.config.groups[groupName].species.push('');
        },

        removeSpeciesFromGroup(groupName, speciesIndex) {
            if (!this.config.groups || !this.config.groups[groupName] || !this.config.groups[groupName].species) {
                return;
            }
            
            this.config.groups[groupName].species.splice(speciesIndex, 1);
        },

        updateSpeciesInput(event, groupName, speciesIndex) {
            // Simply store whatever the user typed - no automatic conversion
            // The datalist will provide the correct values for selection
            const inputValue = event.target.value;
            this.config.groups[groupName].species[speciesIndex] = inputValue;
        },

        findSpeciesByAnyName(searchTerm) {
            if (!this.speciesData.birdedge || !searchTerm) {
                return null;
            }
            
            const term = searchTerm.toLowerCase();
            return this.speciesData.birdedge.find(species => 
                species.scientific.toLowerCase() === term ||
                (species.english && species.english.toLowerCase() === term) ||
                (species.german && species.german.toLowerCase() === term)
            );
        },

        getSpeciesDisplayText(species) {
            if (!species) return '';
            
            let display = species.scientific;
            
            // Add common names in parentheses if available
            const commonNames = [];
            if (species.english) commonNames.push(species.english);
            if (species.german) commonNames.push(species.german);
            
            if (commonNames.length > 0) {
                display += ` (${commonNames.join(' / ')})`;
            }
            
            return display;
        },

        getConsistentSpeciesDisplayText(species, classifier) {
            if (!species) return '';
            
            // Format: "Scientific name, English name, German name (Classifier)"
            let parts = [species.scientific];
            
            if (species.english) {
                parts.push(species.english);
            }
            
            if (species.german) {
                parts.push(species.german);
            }
            
            let display = parts.join(', ');
            display += ` (${classifier})`;
            
            return display;
        },

        validateSpeciesGroups() {
            const errors = [];
            
            if (!this.config.groups) {
                return errors;
            }
            
            // Get valid species lists for enabled detectors
            const validBirdEdgeSpecies = new Set();
            const validYoloBatSpecies = new Set();
            
            if (this.config.detectors.birdedge?.enabled && this.speciesData.birdedge) {
                this.speciesData.birdedge.forEach(species => {
                    validBirdEdgeSpecies.add(species.scientific);
                });
            }
            
            if (this.config.detectors.yolobat?.enabled && this.speciesData.yolobat) {
                this.speciesData.yolobat.forEach(species => {
                    validYoloBatSpecies.add(species.modelLabel);
                });
            }
            
            // Check each group
            for (const [groupName, group] of Object.entries(this.config.groups)) {
                if (!group.species || !Array.isArray(group.species)) {
                    continue;
                }
                
                group.species.forEach((species, index) => {
                    if (!species || species.trim() === '') {
                        errors.push(`Group "${groupName}": Species at position ${index + 1} cannot be empty. Please enter a species label or remove the empty entry.`);
                        return;
                    }
                    
                    const isValidBirdEdge = validBirdEdgeSpecies.has(species);
                    const isValidYoloBat = validYoloBatSpecies.has(species);
                    
                    if (!isValidBirdEdge && !isValidYoloBat) {
                        // Check which detectors are enabled to provide helpful error message
                        const enabledDetectors = [];
                        if (this.config.detectors.birdedge?.enabled) enabledDetectors.push('BirdEdge');
                        if (this.config.detectors.yolobat?.enabled) enabledDetectors.push('YoloBat');
                        
                        if (enabledDetectors.length === 0) {
                            errors.push(`Group "${groupName}": Species "${species}" is invalid because no detectors are enabled.`);
                        } else {
                            errors.push(`Group "${groupName}": Species "${species}" is not valid for enabled detectors (${enabledDetectors.join(', ')}).`);
                        }
                    }
                });
            }
            
            return errors;
        }
    }
}

// Global function to get refresh interval for use across all configurations
async function getSystemRefreshInterval() {
    try {
        const data = await systemConfigManager.getSystemConfig();
        return data.status_refresh_interval || 30;
    } catch (err) {
        console.warn('Failed to load system config, using default refresh interval:', err);
        return 30; // Default fallback
    }
}

function statusPage() {
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
        }
    }
}

// Log Viewer Component for streaming journalctl logs
function logViewer() {
    return {
        ...saveStateMixin(),
        currentService: '',
        logs: [],
        isStreaming: false,
        streamError: null,
        autoScroll: true,
        eventSource: null,
        maxLogs: 1000, // Limit to prevent memory issues
        restartState: 'idle', // 'idle', 'restarting', 'restarted'

        init() {
            // Listen for modal close event to cleanup
            const modal = document.getElementById('logModal');
            modal.addEventListener('hidden.bs.modal', () => {
                this.stopStreaming();
            });
            
            // Listen for modal shown event to scroll to bottom
            modal.addEventListener('shown.bs.modal', () => {
                setTimeout(() => {
                    const container = document.getElementById('logContainer');
                    if (container) {
                        container.scrollTop = container.scrollHeight;
                    }
                }, 100);
            });
        },

        startStreaming(serviceName) {
            this.currentService = serviceName;
            this.logs = [];
            this.streamError = null;
            this.isStreaming = true;

            try {
                // Create event source for server-sent events
                this.eventSource = new EventSource(`/api/systemd/logs/${encodeURIComponent(serviceName)}`);
                
                this.eventSource.onmessage = (event) => {
                    const logLine = event.data;
                    // Only add non-empty lines to avoid empty lines at the start
                    if (logLine && logLine.trim()) {
                        this.logs.push(logLine);
                        
                        // Keep only the last maxLogs entries to prevent memory issues
                        if (this.logs.length > this.maxLogs) {
                            this.logs = this.logs.slice(-this.maxLogs);
                        }
                        
                        // Auto-scroll to bottom for new log entries
                        if (this.autoScroll) {
                            this.$nextTick(() => {
                                const container = document.getElementById('logContainer');
                                if (container) {
                                    container.scrollTop = container.scrollHeight;
                                }
                            });
                        }
                    }
                };

                this.eventSource.onerror = (error) => {
                    console.error('Log stream error:', error);
                    this.streamError = 'Connection to log stream failed';
                    this.isStreaming = false;
                    if (this.eventSource) {
                        this.eventSource.close();
                        this.eventSource = null;
                    }
                };
            } catch (error) {
                console.error('Error starting log stream:', error);
                this.streamError = 'Failed to start log streaming';
                this.isStreaming = false;
            }
        },

        stopStreaming() {
            if (this.eventSource) {
                this.eventSource.close();
                this.eventSource = null;
            }
            this.isStreaming = false;
        },

        clearLogs() {
            // Keep the last log line if there are any logs
            if (this.logs.length > 0) {
                this.logs = [this.logs[this.logs.length - 1]];
            }
        },

        async restartService() {
            if (!this.currentService) return;
            
            const restartFunction = async () => {
                // Call the systemd API directly
                const response = await fetch('/api/systemd/action', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({
                        service: this.currentService,
                        action: 'restart'
                    })
                });
                
                const data = await response.json();
                
                if (!response.ok) {
                    throw new Error(data.detail || `Failed to restart ${this.currentService} service`);
                }
                
                // Show success message if there's a way to display it
                // Try to find any component with showMessage method
                const configComponent = document.querySelector('[x-data*="Config"]');
                if (configComponent && configComponent._x_dataStack && configComponent._x_dataStack[0].showMessage) {
                    configComponent._x_dataStack[0].showMessage(`${data.message}`, false);
                }
            };
            
            try {
                this.restartState = 'restarting';
                await restartFunction();
                this.restartState = 'restarted';
                
                // Reset to idle state after 5 seconds
                setTimeout(() => {
                    this.restartState = 'idle';
                }, 5000);
                
            } catch (error) {
                this.restartState = 'idle';
                console.error('Failed to restart service:', error);
                
                // Show error message if there's a way to display it
                const configComponent = document.querySelector('[x-data*="Config"]');
                if (configComponent && configComponent._x_dataStack && configComponent._x_dataStack[0].showMessage) {
                    configComponent._x_dataStack[0].showMessage(error.message, true);
                } else {
                    // Fallback to alert
                    alert(`Failed to restart service: ${error.message}`);
                }
            }
        }
    };
}

// Shell Viewer Component for interactive shell with xterm.js and WebSocket
function shellViewer() {
    return {
        isConnected: false,
        isConnecting: false,
        connectionError: null,
        terminal: null,
        websocket: null,
        sessionId: null,
        fitAddon: null,
        resizeObserver: null,
        resizeHandler: null,

        init() {
            // Generate a unique session ID
            this.sessionId = 'shell_' + Math.random().toString(36).substr(2, 9);
            
            // Handle modal show event
            document.getElementById('shellModal').addEventListener('shown.bs.modal', () => {
                // Generate a new session ID each time the modal is opened
                this.sessionId = 'shell_' + Math.random().toString(36).substr(2, 9);
                
                // Reset connection state
                this.isConnected = false;
                this.isConnecting = false;
                this.connectionError = null;
                
                this.$nextTick(() => {
                    this.initTerminal();
                    setTimeout(() => {
                        this.connect();
                    }, 200); // Give terminal more time to initialize
                });
            });

            // Handle modal hide event
            document.getElementById('shellModal').addEventListener('hidden.bs.modal', () => {
                this.disconnect();
                this.destroyTerminal();
                
                // Reset all state
                this.isConnected = false;
                this.isConnecting = false;
                this.connectionError = null;
                this.websocket = null;
            });
        },

        initTerminal() {
            if (this.terminal) {
                this.destroyTerminal();
            }

            // Ensure terminal container is ready
            const terminalContainer = document.getElementById('terminal');
            if (!terminalContainer) {
                return;
            }

            // Create xterm.js terminal instance
            this.terminal = new Terminal({
                cursorBlink: true,
                fontSize: 14,
                fontFamily: 'Consolas, "Liberation Mono", Menlo, Courier, monospace',
                theme: {
                    background: '#000000',
                    foreground: '#ffffff',
                    cursor: '#ffffff',
                    selection: '#ffffff40'
                },
                convertEol: true,
                disableStdin: false,
                allowProposedApi: true
            });

            // Create and load the fit addon
            this.fitAddon = new FitAddon.FitAddon();
            this.terminal.loadAddon(this.fitAddon);

            // Open terminal in the container
            this.terminal.open(terminalContainer);

            // Show the terminal container
            terminalContainer.style.display = 'block';

            // Handle terminal input
            this.terminal.onData((data) => {
                if (this.websocket && this.websocket.readyState === WebSocket.OPEN) {
                    this.websocket.send(JSON.stringify({
                        type: 'input',
                        data: data
                    }));
                }
            });

            // Initial fit after a small delay to ensure container is properly sized
            this.$nextTick(() => {
                setTimeout(() => {
                    this.fitTerminal();
                }, 100);
            });

            // Handle window resize
            this.resizeHandler = () => {
                this.fitTerminal();
            };
            window.addEventListener('resize', this.resizeHandler);

            // Handle modal resize events
            const modal = document.getElementById('shellModal');
            this.resizeObserver = new ResizeObserver(() => {
                this.fitTerminal();
            });
            this.resizeObserver.observe(modal);
        },

        fitTerminal() {
            if (this.terminal && this.fitAddon) {
                try {
                    // Use the fit addon for proper terminal sizing
                    this.fitAddon.fit();
                    
                    // Get the new dimensions
                    const dimensions = this.fitAddon.proposeDimensions();
                    if (dimensions) {
                        // Send resize information to the backend PTY
                        if (this.websocket && this.websocket.readyState === WebSocket.OPEN) {
                            this.websocket.send(JSON.stringify({
                                type: 'resize',
                                cols: dimensions.cols,
                                rows: dimensions.rows
                            }));
                        }
                    }
                } catch (error) {
                    console.warn('Error fitting terminal:', error);
                    // Fallback to manual calculation
                    this.manualFitTerminal();
                }
            }
        },

        manualFitTerminal() {
            if (this.terminal) {
                const container = document.getElementById('terminal');
                if (container) {
                    const rect = container.getBoundingClientRect();
                    
                    // Create a temporary element to measure character dimensions
                    const measureElement = document.createElement('div');
                    measureElement.style.fontFamily = this.terminal.options.fontFamily;
                    measureElement.style.fontSize = this.terminal.options.fontSize + 'px';
                    measureElement.style.position = 'absolute';
                    measureElement.style.visibility = 'hidden';
                    measureElement.style.whiteSpace = 'pre';
                    measureElement.textContent = 'W'.repeat(10); // Use 'W' as it's typically the widest character
                    
                    document.body.appendChild(measureElement);
                    
                    const charWidth = measureElement.offsetWidth / 10;
                    const charHeight = measureElement.offsetHeight;
                    
                    document.body.removeChild(measureElement);
                    
                    // Calculate dimensions with some padding
                    const padding = 16; // Account for container padding
                    const cols = Math.floor((rect.width - padding) / charWidth);
                    const rows = Math.floor((rect.height - padding) / charHeight);
                    
                    if (cols > 0 && rows > 0) {
                        this.terminal.resize(cols, rows);
                        
                        // Send resize information to the backend
                        if (this.websocket && this.websocket.readyState === WebSocket.OPEN) {
                            this.websocket.send(JSON.stringify({
                                type: 'resize',
                                cols: cols,
                                rows: rows
                            }));
                        }
                    }
                }
            }
        },

        connect() {
            if (this.isConnecting || this.isConnected) {
                return;
            }

            this.isConnecting = true;
            this.connectionError = null;

            // Create WebSocket connection
            const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            const wsUrl = `${protocol}//${window.location.host}/api/shell/ws/${this.sessionId}`;
            
            this.websocket = new WebSocket(wsUrl);

            this.websocket.onopen = () => {
                this.isConnecting = false;
                this.isConnected = true;
                this.connectionError = null;
                
                if (this.terminal) {
                    this.terminal.clear();
                    this.terminal.write('\x1b[32mTerminal connected successfully!\x1b[0m\r\n');
                    this.terminal.write('\x1b[90mUsing your default shell...\x1b[0m\r\n');
                    
                    // Ensure terminal is properly sized after connection
                    setTimeout(() => {
                        this.fitTerminal();
                    }, 100);
                }
            };

            this.websocket.onmessage = (event) => {
                try {
                    const data = JSON.parse(event.data);
                    
                    if (data.type === 'output' && this.terminal) {
                        this.terminal.write(data.data);
                    } else if (data.type === 'error') {
                        if (this.terminal) {
                            this.terminal.write(`\x1b[31mError: ${data.data}\x1b[0m\r\n`);
                        }
                        this.connectionError = data.data;
                    } else if (data.type === 'exit') {
                        if (this.terminal) {
                            this.terminal.write(`\x1b[33m\r\n${data.data}\x1b[0m\r\n`);
                            this.terminal.write('\x1b[90mConnection will close automatically...\x1b[0m\r\n');
                        }
                        // Mark as disconnected
                        this.isConnected = false;
                        this.isConnecting = false;
                    }
                } catch (e) {
                    console.error('Error parsing WebSocket message:', e);
                }
            };

            this.websocket.onerror = (error) => {
                this.connectionError = 'WebSocket connection failed';
                this.isConnecting = false;
                this.isConnected = false;
                
                if (this.terminal) {
                    this.terminal.write('\x1b[31mConnection failed. Try closing and reopening the modal.\x1b[0m\r\n');
                }
            };

            this.websocket.onclose = (event) => {
                this.isConnecting = false;
                this.isConnected = false;
                
                if (this.terminal && !this.connectionError) {
                    this.terminal.write('\x1b[33m\r\nConnection closed\x1b[0m\r\n');
                }
            };
        },

        disconnect() {
            if (this.websocket) {
                this.websocket.close();
                this.websocket = null;
            }
            this.isConnected = false;
            this.isConnecting = false;
            this.connectionError = null;
        },

        reconnect() {
            this.disconnect();
            
            // Generate a new session ID for reconnection
            this.sessionId = 'shell_' + Math.random().toString(36).substr(2, 9);
            
            this.$nextTick(() => {
                // Ensure terminal is properly fitted before connecting
                if (this.terminal) {
                    setTimeout(() => {
                        this.fitTerminal();
                        // Wait a bit more to ensure terminal is fully ready
                        setTimeout(() => {
                            this.connect();
                        }, 100);
                    }, 100);
                } else {
                    this.connect();
                }
            });
        },

        clearTerminal() {
            if (this.terminal) {
                this.terminal.clear();
            }
        },

        destroyTerminal() {
            // Clean up resize observers and handlers
            if (this.resizeObserver) {
                this.resizeObserver.disconnect();
                this.resizeObserver = null;
            }
            
            if (this.resizeHandler) {
                window.removeEventListener('resize', this.resizeHandler);
                this.resizeHandler = null;
            }
            
            // Clean up terminal and addon carefully
            if (this.terminal) {
                try {
                    // Only dispose if the terminal is still valid and not already disposed
                    if (this.terminal.element && this.terminal.element.parentNode) {
                        this.terminal.dispose();
                    }
                } catch (error) {
                    // Silently handle disposal errors - they're usually harmless
                }
                this.terminal = null;
            }
            
            // Clear addon reference
            this.fitAddon = null;
            
            const terminalElement = document.getElementById('terminal');
            if (terminalElement) {
                terminalElement.style.display = 'none';
                terminalElement.innerHTML = '';
            }
        }
    };
}

// Save state management mixin
function saveStateMixin() {
    return {
        saveState: 'idle', // 'idle', 'saving', 'saved'
        saveRestartState: 'idle', // 'idle', 'saving', 'restarting', 'saved'
        
        setSaveState(state) {
            this.saveState = state;
            if (state === 'saved') {
                setTimeout(() => {
                    this.saveState = 'idle';
                }, 5000);
            }
        },
        
        setSaveRestartState(state) {
            this.saveRestartState = state;
            if (state === 'saved') {
                setTimeout(() => {
                    this.saveRestartState = 'idle';
                }, 5000);
            }
        },
        
        async handleSaveConfig(configSaveFunction) {
            try {
                this.setSaveState('saving');
                await configSaveFunction();
                this.setSaveState('saved');
            } catch (error) {
                this.setSaveState('idle');
                throw error;
            }
        },
        
        async handleSaveAndRestartConfig(configSaveFunction, restartFunction) {
            try {
                this.setSaveRestartState('saving');
                await configSaveFunction();
                this.setSaveRestartState('restarting');
                await restartFunction();
                this.setSaveRestartState('saved');
            } catch (error) {
                this.setSaveRestartState('idle');
                throw error;
            }
        }
    };
}

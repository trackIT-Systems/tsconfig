import { saveStateMixin } from '../mixins/saveStateMixin.js';
import { serviceManager } from '../managers/serviceManager.js';
import { getSystemRefreshInterval } from '../utils/systemUtils.js';
import { parseTimeString, updateTimeString } from '../utils/timeUtils.js';

export function scheduleConfig() {
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
                    const startParts = parseTimeString(entry.start);
                    entry.startReference = startParts.reference;
                    entry.startSign = startParts.sign;
                    entry.startOffset = startParts.offset;
                    
                    const stopParts = parseTimeString(entry.stop);
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


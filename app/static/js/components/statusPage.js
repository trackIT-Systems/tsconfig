import { saveStateMixin } from '../mixins/saveStateMixin.js';
import { serviceActionMixin } from '../mixins/serviceActionMixin.js';
import { serviceManager } from '../managers/serviceManager.js';
import { systemConfigManager } from '../managers/systemConfigManager.js';
import { getSystemRefreshInterval } from '../utils/systemUtils.js';
import { apiUrl } from '../utils/apiUtils.js';

export function statusPage() {
    return {
        ...saveStateMixin(),
        ...serviceActionMixin(),
        systemInfo: null,
        loading: true,
        refreshing: false,
        lastUpdated: null,
        refreshInterval: null,
        refreshIntervalSeconds: 30, // Default value, will be loaded from config
        // Systemd services properties
        services: [],
        servicesLoading: false,
        actionLoading: false,
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
        // Geolocation properties
        geolocation: null,
        geolocationMap: null,
        geolocationMarker: null,
        geolocationMapInitialized: false,
        // User location properties
        userLocationMarker: null,
        userLocationCircle: null,
        userLocationLine: null,
        showUserLocation: false,
        // Soundscapepipe properties
        soundscapepipeConfig: null,
        soundscapepipeLoading: false,
        soundscapepipeService: null,
        // Radiotracking properties
        radiotrackingConfig: null,
        radiotrackingLoading: false,
        radiotrackingService: null,
        // Available services (for checking if service configs exist)
        availableServices: [],

        async initStatus() {
            // Don't initialize in server mode
            if (window.serverModeManager?.isEnabled()) {
                return;
            }
            
            // Load system configuration first to get refresh interval
            await this.loadSystemConfig();
            // Load available services to check which services are configured
            await this.loadAvailableServices();
            await this.refreshStatus();
            await this.loadServices();
            // Load soundscapepipe config and service status only if service is available
            if (this.isServiceAvailable('soundscapepipe')) {
                await this.loadSoundscapepipeConfig();
            }
            // Load radiotracking config and service status only if service is available
            if (this.isServiceAvailable('radiotracking')) {
                await this.loadRadiotrackingConfig();
            }
            // Subscribe to service updates to refresh service status
            serviceManager.subscribe(() => {
                this.updateSoundscapepipeService();
                this.updateRadiotrackingService();
            });
            // Load geolocation
            await this.loadGeolocation();
            // Auto-refresh using configured interval when status tab is active
            this.refreshInterval = setInterval(() => {
                // Only refresh if status tab is active
                const currentHash = window.location.hash.slice(1);
                if (currentHash === 'status' || (currentHash === '' && this.activeConfig === 'status')) {
                    this.refreshStatus();
                    this.loadServices();
                    // Only load soundscapepipe config if service is available
                    if (this.isServiceAvailable('soundscapepipe')) {
                        this.loadSoundscapepipeConfig();
                    }
                    // Only load radiotracking config if service is available
                    if (this.isServiceAvailable('radiotracking')) {
                        this.loadRadiotrackingConfig();
                    }
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
            
            try {
                // Refresh system status, services, timedatectl, network connectivity, and modem info in parallel
                // Force refresh services since this is a manual refresh
                const [statusResponse, timedatectlResponse, networkConnectivityResponse, modemResponse] = await Promise.all([
                    fetch(apiUrl('/api/system-status')),
                    fetch(apiUrl('/api/timedatectl-status')),
                    fetch(apiUrl('/api/network-connectivity')),
                    fetch(apiUrl('/api/network/modem')),
                    serviceManager.getServices(true) // Force refresh
                ]);
                
                if (!statusResponse.ok) {
                    throw new Error(`HTTP ${statusResponse.status}: ${statusResponse.statusText}`);
                }
                
                const data = await statusResponse.json();
                this.systemInfo = data;
                this.lastUpdated = new Date().toLocaleTimeString();
                
                // Reset reboot loading state when uptime is refreshed
                // This handles the case where the system was rebooted and status is refreshed
                if (data.uptime !== undefined) {
                    this.rebootLoading = false;
                }
                
                // Handle timedatectl status
                if (timedatectlResponse.ok) {
                    const timedatectlData = await timedatectlResponse.json();
                    this.timedatectlStatus = timedatectlData;
                    this.timedatectlError = null;
                } else {
                    this.timedatectlError = `Failed to load timedatectl status: HTTP ${timedatectlResponse.status}`;
                    console.error('Timedatectl status error:', this.timedatectlError);
                }
                
                // Handle network connectivity status
                if (networkConnectivityResponse.ok) {
                    const networkConnectivityData = await networkConnectivityResponse.json();
                    this.networkConnectivity = networkConnectivityData;
                    this.networkConnectivityError = null;
                } else {
                    this.networkConnectivityError = `Failed to load network connectivity status: HTTP ${networkConnectivityResponse.status}`;
                    console.error('Network connectivity status error:', this.networkConnectivityError);
                }
                
                // Handle modem information
                if (modemResponse.ok) {
                    const modemData = await modemResponse.json();
                    // Modem endpoint returns null if no modem found, which is valid
                    this.modemInfo = modemData;
                    this.modemError = null;
                } else {
                    this.modemError = `Failed to load modem information: HTTP ${modemResponse.status}`;
                    console.error('Modem information error:', this.modemError);
                }
                
                // Update local services data from the forced refresh
                this.services = serviceManager.services;
                // Update soundscapepipe service status
                this.updateSoundscapepipeService();
                // Load soundscapepipe config only if service is available
                if (this.isServiceAvailable('soundscapepipe')) {
                    await this.loadSoundscapepipeConfig();
                } else {
                    // Clear config if service is no longer available
                    this.soundscapepipeConfig = null;
                }
                // Load radiotracking config only if service is available
                if (this.isServiceAvailable('radiotracking')) {
                    await this.loadRadiotrackingConfig();
                } else {
                    // Clear config if service is no longer available
                    this.radiotrackingConfig = null;
                }
            } catch (err) {
                this.showToast(`Failed to load system status: ${err.message}`, 'error', { title: 'System Status Error' });
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
            } catch (err) {
                this.showToast(`Failed to load services: ${err.message}`, 'error');
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


        showToast(message, type = 'info', options = {}) {
            // Use global toast manager
            if (window.toastManager) {
                const defaultTitle = type === 'success' ? 'Success' : 
                                   type === 'error' ? 'Error' : 
                                   type === 'warning' ? 'Warning' : 'System Status';
                window.toastManager.show(message, type, { title: defaultTitle, ...options });
            } else {
                // Fallback to console if toast manager not available
                console.log(`[STATUS ${type.toUpperCase()}] ${message}`);
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

        streamAllLogs() {
            // Stream all system logs
            this.streamLogs('all');
        },

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
                } else if (mp === '/data') {
                    // Link /data mountpoint directly to filebrowser root
                    return `<a href="/data/files/" target="_blank" rel="noopener noreferrer" class="text-decoration-none">${mp} <i class="fas fa-external-link-alt fa-xs" title="Opens in new tab"></i></a>`;
                } else if (mp.startsWith('/data/')) {
                    // Extract the path after /data/ and create a link
                    const pathAfterData = mp.substring(6); // Remove '/data/' prefix
                    if (pathAfterData) {
                        return `<a href="/data/files/${pathAfterData}/" target="_blank" rel="noopener noreferrer" class="text-decoration-none">${mp} <i class="fas fa-external-link-alt fa-xs" title="Opens in new tab"></i></a>`;
                    }
                }
                return mp;
            });
            
            return formattedMountpoints.join(', ');
        },

        async loadGeolocation() {
            try {
                const response = await fetch(apiUrl('/api/geolocation'));
                if (response.ok) {
                    this.geolocation = await response.json();
                    // Initialize map if geolocation data is available
                    if (this.geolocation) {
                        // Wait a bit to ensure DOM is ready
                        setTimeout(() => this.initGeolocationMap(), 200);
                    }
                }
            } catch (error) {
                console.error('Failed to load geolocation:', error);
                this.geolocation = null;
            }
        },

        getTrackerIcon() {
            // Determine icon based on OS name
            const osName = this.systemInfo?.os_release?.NAME || '';
            
            if (osName.includes('tsOS-audio')) {
                return 'microphone';
            } else if (osName.includes('tsOS-vhf')) {
                return 'tower-broadcast';
            } else {
                return 'microchip';  // default
            }
        },

        initGeolocationMap() {
            // Only initialize if we have geolocation data and map not already initialized
            if (!this.geolocation || this.geolocationMapInitialized || !document.getElementById('geolocationMap')) {
                return;
            }

            // Initialize map centered on the geolocation
            this.geolocationMap = L.map('geolocationMap', {
                center: [this.geolocation.lat, this.geolocation.lon],
                zoom: 19,
                zoomControl: true,
                dragging: true,
                touchZoom: true,
                scrollWheelZoom: true,
                doubleClickZoom: true,
                boxZoom: true
            });

            // Add Mapbox satellite streets layer with high-resolution tiles
            L.tileLayer('https://api.mapbox.com/styles/v1/mapbox/satellite-streets-v12/tiles/{z}/{x}/{y}@2x?access_token=pk.eyJ1IjoidHJhY2tpdHN5c3RlbXMiLCJhIjoiY21iaHEwbXcwMDEzcTJqc2JhNzdobDluaSJ9.NLRmiJEDHQgPJEyceCA57g', {
                attribution: '© Mapbox © OpenStreetMap',
                maxZoom: 19,
                tileSize: 512,
                zoomOffset: -1
            }).addTo(this.geolocationMap);

            // Add marker at the tracker position (non-draggable, read-only)
            const trackerIcon = L.VectorMarkers.icon({
                icon: this.getTrackerIcon(),
                prefix: 'fa',
                markerColor: 'var(--bs-purple)',  // Bootstrap purple CSS variable
                iconColor: 'white'
            });
            
            this.geolocationMarker = L.marker([this.geolocation.lat, this.geolocation.lon], {
                draggable: false,
                icon: trackerIcon
            }).addTo(this.geolocationMap);

            // Add locate control for user's current location
            const locateControl = L.control.locate({
                position: 'topright',
                strings: {
                    title: "Show my location"
                },
                locateOptions: {
                    maxZoom: 19,
                    enableHighAccuracy: true
                },
                flyTo: false,
                keepCurrentZoomLevel: true,
                onLocationError: function(err) {
                    // Suppress default alert, we handle it with custom toast
                },
                circleStyle: {
                    color: '#136aec',
                    fillColor: '#136aec',
                    fillOpacity: 0.15,
                    weight: 2
                },
                markerStyle: {
                    color: '#136aec',
                    fillColor: '#2A93EE',
                    fillOpacity: 0.7,
                    weight: 3,
                    opacity: 0.9,
                    radius: 9
                },
                icon: 'fas fa-crosshairs',
                iconLoading: 'fas fa-spinner fa-spin',
                showPopup: false,
                createButtonCallback: function (container, options) {
                    const link = L.DomUtil.create('a', 'leaflet-bar-part leaflet-bar-part-single', container);
                    link.title = options.strings.title;
                    const icon = L.DomUtil.create('i', 'fas fa-location-arrow', link);
                    return { link: link, icon: icon };
                }
            }).addTo(this.geolocationMap);

            // Listen for location events
            this.geolocationMap.on('locationfound', (e) => {
                this.showUserLocation = true;
                this.updateDistanceLine(e.latlng);
            });

            this.geolocationMap.on('locateactivate', () => {
                this.showUserLocation = true;
            });

            this.geolocationMap.on('locatedeactivate', () => {
                this.showUserLocation = false;
                if (this.userLocationLine) {
                    this.geolocationMap.removeLayer(this.userLocationLine);
                    this.userLocationLine = null;
                }
            });

            this.geolocationMap.on('locationerror', (e) => {
                // Suppress the default error alert from leaflet-locate
                e.preventDefault && e.preventDefault();
                
                let message = 'Unable to determine your location.';
                
                if (e.message.includes('permission') || e.message.includes('Permission')) {
                    message = 'Geolocation requires HTTPS. Please access the site via HTTPS or localhost to use this feature.';
                } else if (e.message.includes('timeout')) {
                    message = 'Location request timed out. Please try again.';
                } else if (e.message.includes('denied')) {
                    message = 'Location permission denied. Please allow location access in your browser settings.';
                }
                
                this.showToast(message, 'warning', { title: 'Geolocation Unavailable' });
            });

            this.geolocationMapInitialized = true;

            // Force a resize to ensure tiles load properly
            setTimeout(() => {
                if (this.geolocationMap) {
                    this.geolocationMap.invalidateSize();
                }
            }, 100);
        },

        updateDistanceLine(userLatLng) {
            // Remove existing line if present
            if (this.userLocationLine) {
                this.geolocationMap.removeLayer(this.userLocationLine);
            }

            // Create line between tracker and user location
            const trackerLatLng = [this.geolocation.lat, this.geolocation.lon];
            this.userLocationLine = L.polyline([trackerLatLng, userLatLng], {
                color: '#ffc107',
                weight: 3,
                opacity: 0.7,
                dashArray: '10, 10'
            }).addTo(this.geolocationMap);

            // Calculate distance
            const distance = this.geolocationMap.distance(trackerLatLng, userLatLng);
            
            // Add distance label in the middle of the line
            const midpoint = [(trackerLatLng[0] + userLatLng.lat) / 2, (trackerLatLng[1] + userLatLng.lng) / 2];
            
            const distanceText = distance > 1000 
                ? `${(distance / 1000).toFixed(2)} km` 
                : `${distance.toFixed(1)} m`;
            
            // Create a popup with distance information
            this.userLocationLine.bindPopup(`
                <div class="text-center">
                    <strong>Distance</strong><br>
                    ${distanceText}
                </div>
            `);
        },

        // Available services methods
        async loadAvailableServices() {
            try {
                // Build URL with config_group parameter if in server mode
                let url = '/api/available-services';
                if (window.serverModeManager?.isEnabled() && window.serverModeManager?.getCurrentConfigGroup()) {
                    url += `?config_group=${encodeURIComponent(window.serverModeManager.getCurrentConfigGroup())}`;
                }
                
                const response = await fetch(apiUrl(url));
                if (response.ok) {
                    const data = await response.json();
                    this.availableServices = data.available_services || [];
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

        // Soundscapepipe methods
        async loadSoundscapepipeConfig() {
            // Don't load if service is not available
            if (!this.isServiceAvailable('soundscapepipe')) {
                this.soundscapepipeConfig = null;
                return;
            }

            // Update service status
            this.updateSoundscapepipeService();

            this.soundscapepipeLoading = true;
            try {
                const url = window.serverModeManager?.buildApiUrl('/api/soundscapepipe') || apiUrl('/api/soundscapepipe');
                const response = await fetch(url);
                if (response.ok) {
                    this.soundscapepipeConfig = await response.json();
                } else {
                    // If config doesn't exist or service isn't configured, clear it
                    this.soundscapepipeConfig = null;
                }
            } catch (err) {
                console.error('Failed to load soundscapepipe config:', err);
                this.soundscapepipeConfig = null;
            } finally {
                this.soundscapepipeLoading = false;
            }
        },

        updateSoundscapepipeService() {
            this.soundscapepipeService = serviceManager.findService('soundscapepipe');
        },

        // Radiotracking methods
        async loadRadiotrackingConfig() {
            // Don't load if service is not available
            if (!this.isServiceAvailable('radiotracking')) {
                this.radiotrackingConfig = null;
                return;
            }

            // Update service status
            this.updateRadiotrackingService();

            this.radiotrackingLoading = true;
            try {
                const url = window.serverModeManager?.buildApiUrl('/api/radiotracking') || apiUrl('/api/radiotracking');
                const response = await fetch(url);
                if (response.ok) {
                    this.radiotrackingConfig = await response.json();
                } else {
                    // If config doesn't exist or service isn't configured, clear it
                    this.radiotrackingConfig = null;
                }
            } catch (err) {
                console.error('Failed to load radiotracking config:', err);
                this.radiotrackingConfig = null;
            } finally {
                this.radiotrackingLoading = false;
            }
        },

        updateRadiotrackingService() {
            this.radiotrackingService = serviceManager.findService('radiotracking');
        },

        getEnabledDetectors() {
            if (!this.soundscapepipeConfig || !this.soundscapepipeConfig.detectors) {
                return [];
            }
            // Extract detector names from the detectors object
            // Detectors are enabled if they exist in the config
            return Object.keys(this.soundscapepipeConfig.detectors).filter(detector => {
                const detectorConfig = this.soundscapepipeConfig.detectors[detector];
                // Include detector if it has configuration (not null/undefined)
                // Filter out if explicitly disabled
                return detectorConfig && detectorConfig.enabled !== false;
            });
        },

        getDetectorTasks(detectorName) {
            if (!this.soundscapepipeConfig || !this.soundscapepipeConfig.detectors) {
                return [];
            }
            const detector = this.soundscapepipeConfig.detectors[detectorName];
            if (!detector || !detector.tasks || !Array.isArray(detector.tasks)) {
                return [];
            }
            return detector.tasks.map(task => task.name || 'Unnamed').filter(Boolean);
        },

        getRecordingGroups() {
            if (!this.soundscapepipeConfig || !this.soundscapepipeConfig.groups) {
                return [];
            }
            return Object.keys(this.soundscapepipeConfig.groups);
        }
    }
}


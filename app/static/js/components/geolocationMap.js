import { serverModeManager } from '../managers/serverModeManager.js';
import { compose } from '../mixins/compose.js';
import { lifecycleMixin } from '../mixins/lifecycleMixin.js';
import { showToastMixin } from '../mixins/showToastMixin.js';
import { apiUrl } from '../utils/apiUtils.js';

/**
 * The Location card of the status page: the tracker's position on a map
 * (satellite imagery if tsconfig.yml has a Mapbox token, else OpenStreetMap),
 * with a button to show your own position and the distance to the tracker.
 *
 * @param {() => string|undefined} getOsName - Name of the OS, which decides the
 *     marker's icon. The template passes a closure over the status page's data.
 */
export function geolocationMap(getOsName = () => '') {
    return compose(lifecycleMixin(), showToastMixin(), {
        geolocation: null,
        geolocationMap: null,
        geolocationMarker: null,
        geolocationMapInitialized: false,
        // User location
        userLocationLine: null,

        async init() {
            // A server has no tracker position of its own
            if (serverModeManager.isEnabled()) {
                return;
            }
            await this.loadGeolocation();
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
            const osName = getOsName() || '';
            
            if (osName.includes('tsOS-audio')) {
                return 'microphone';
            } else if (osName.includes('tsOS-vhf')) {
                return 'tower-broadcast';
            } else {
                return 'microchip';  // default
            }
        },

        initGeolocationMap() {
            // Called with a delay: a status page that was removed meanwhile must not
            // take the map container of the one that replaced it
            if (this.destroyed) {
                return;
            }
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
            // Also stops leaflet-locate's position watch
            this.onDestroy(() => {
                if (this.geolocationMap) {
                    this.geolocationMap.remove();
                    this.geolocationMap = null;
                }
            });

            // Satellite imagery from Mapbox if tsconfig.yml has a token
            // (map.mapbox_token), otherwise the OpenStreetMap street map
            if (window.MAPBOX_TOKEN) {
                L.tileLayer('https://api.mapbox.com/styles/v1/mapbox/satellite-streets-v12/tiles/{z}/{x}/{y}@2x?access_token={accessToken}', {
                    accessToken: window.MAPBOX_TOKEN,
                    attribution: '© Mapbox © OpenStreetMap',
                    maxZoom: 19,
                    tileSize: 512,
                    zoomOffset: -1
                }).addTo(this.geolocationMap);
            } else {
                L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
                    attribution: '© OpenStreetMap contributors',
                    maxZoom: 19
                }).addTo(this.geolocationMap);
            }

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
                this.updateDistanceLine(e.latlng);
            });

            this.geolocationMap.on('locatedeactivate', () => {
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
    });
}

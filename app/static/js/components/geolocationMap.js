import { serverModeManager } from '../managers/serverModeManager.js';
import { compose } from '../mixins/compose.js';
import { lifecycleMixin } from '../mixins/lifecycleMixin.js';
import { showToastMixin } from '../mixins/showToastMixin.js';
import { apiUrl } from '../utils/apiUtils.js';

/**
 * The Location card of the status page: the tracker's position on a map
 * (satellite imagery if tsconfig.yml has a Mapbox token, else OpenStreetMap),
 * with a button to show your own position and the distance to the tracker.
 * Where the location daemon runs (/api/location), the latest GNSS fix is drawn
 * next to the configured position as a violet dot with its accuracy, in the
 * style of the browser's own position, refreshed every LOCATION_REFRESH_MS; the
 * card shows the mode, state and deviation. In stationary mode, "Measure with GPS"
 * asks the daemon for a fine fix (POST /api/location/fix). The map is zoomed to
 * show both positions when it opens; later fixes move the dot, not the view.
 *
 * @param {() => string|undefined} getOsName - Name of the OS, which decides the
 *     marker's icon. The template passes a closure over the status page's data.
 */
const LOCATION_REFRESH_MS = 10000;
// The GNSS fix; the user's own position (leaflet-locate) is blue in the same style
const FIX_COLOR = '#7c3aed';

export function geolocationMap(getOsName = () => '') {
    return compose(lifecycleMixin(), showToastMixin(), {
        // The configured position (/boot/firmware/geolocation)
        geolocation: null,
        // tslocationd's view (/run/tslocation/location.json), null without the daemon
        location: null,
        geolocationMap: null,
        geolocationMarker: null,
        // The latest GNSS fix: dot and accuracy circle
        fixMarker: null,
        fixCircle: null,
        // POST /api/location/fix sent, until the daemon reports `fixing`
        fixRequesting: false,
        geolocationMapInitialized: false,
        // User location
        userLocationLine: null,

        async init() {
            // A server has no tracker position of its own
            if (serverModeManager.isEnabled()) {
                return;
            }
            await this.loadGeolocation();
            // The fix moves in mobile mode, and a stationary station gets a new one now and then
            this.every(LOCATION_REFRESH_MS, () => this.refreshLocation());
        },

        fixRunning() {
            return this.fixRequesting || Boolean(this.location?.fixing);
        },

        async requestFix() {
            this.fixRequesting = true;
            try {
                const response = await fetch(apiUrl('/api/location/fix'), { method: 'POST' });
                if (!response.ok) {
                    const body = await response.json().catch(() => ({}));
                    this.showToast(body.detail || 'The location daemon did not answer', 'warning', { title: 'No GNSS fix' });
                    this.fixRequesting = false;
                    return;
                }
            } catch (error) {
                this.showToast('The station did not answer', 'warning', { title: 'No GNSS fix' });
                this.fixRequesting = false;
                return;
            }
            // The daemon sets `fixing` within a moment; from then on the refreshes follow it
            setTimeout(async () => {
                await this.refreshLocation();
                this.fixRequesting = false;
            }, 2000);
        },

        /** Zoom so the configured position and the fix are both on the map. */
        fitBoth() {
            const fix = this.location?.fix;
            if (!fix || !this.geolocation || !this.geolocationMap) {
                return;
            }
            this.geolocationMap.fitBounds(
                [[this.geolocation.lat, this.geolocation.lon], [fix.lat, fix.lon]],
                { maxZoom: 19, padding: [60, 60] }
            );
        },

        async refreshLocation() {
            try {
                const response = await fetch(apiUrl('/api/location'));
                if (!response.ok) {
                    return;
                }
                this.location = await response.json();
            } catch (error) {
                return;  // the next refresh tries again
            }
            if (this.geolocationMapInitialized) {
                this.updateFixMarker();
            this.fitBoth();
            } else if (this.trackerLatLng()) {
                // the first fix of a station without a configured position
                setTimeout(() => this.initGeolocationMap(), 200);
            }
        },

        async loadGeolocation() {
            const load = async (path) => {
                try {
                    const response = await fetch(apiUrl(path));
                    return response.ok ? await response.json() : null;
                } catch (error) {
                    console.error(`Failed to load ${path}:`, error);
                    return null;
                }
            };
            [this.geolocation, this.location] = await Promise.all([load('/api/geolocation'), load('/api/location')]);
            // Initialize the map if there is a position to show
            if (this.trackerLatLng()) {
                // Wait a bit to ensure DOM is ready
                setTimeout(() => this.initGeolocationMap(), 200);
            }
        },

        /** The tracker's position on the map: the configured one, else the latest fix. */
        trackerLatLng() {
            const position = this.geolocation || this.location?.fix;
            return position ? [position.lat, position.lon] : null;
        },

        formatDistance(metres) {
            return metres > 1000 ? `${(metres / 1000).toFixed(2)} km` : `${metres.toFixed(1)} m`;
        },

        /** The state in words for an operator: badge text, its colour and one sentence. */
        stateInfo() {
            const reference = Boolean(this.location?.reference);
            return {
                unknown: {
                    label: 'Not measured yet', cls: 'bg-secondary',
                    text: 'The position has not been measured with GPS yet.',
                },
                confirmed: {
                    label: 'At its configured position', cls: 'bg-success',
                    text: 'The last GPS measurement agrees with the configured position.',
                },
                deviating: {
                    label: reference ? 'Away from its configured position' : 'No configured position', cls: 'bg-warning text-dark',
                    text: reference
                        ? 'The last GPS measurement is too far from the configured position. GPS measures again in a few hours, to see whether the station was moved.'
                        : 'GPS measures again in a few hours; once the measurements agree, they are proposed as the position.',
                },
                proposed: {
                    label: 'Moved: new position proposed', cls: 'bg-warning text-dark',
                    text: 'Several GPS measurements agree on a new position. It can be adopted as the configured position on the server.',
                },
                tracking: {
                    label: 'Following the station', cls: 'bg-success',
                    text: 'GPS is on and records where the station goes.',
                },
                no_fix: {
                    label: 'No GPS reception', cls: 'bg-danger',
                    text: 'GPS was switched on but found no position. Check that the antenna is connected and sees the sky.',
                },
            }[this.location?.state] || { label: this.location?.state || '', cls: 'bg-secondary', text: '' };
        },

        modeText() {
            return {
                stationary: 'Stationary: GPS is switched on now and then to check the position, and off again to save power.',
                mobile: 'Mobile: GPS is always on and records the route.',
                off: 'Off: the position is not measured.',
            }[this.location?.mode] || this.location?.mode;
        },

        /** GPS is switched on now: a requested or due measurement, or always in mobile mode. */
        gpsOn() {
            return this.fixRunning() || this.location?.mode === 'mobile';
        },

        formatPosition(position) {
            return position ? `${position.lat.toFixed(6)}, ${position.lon.toFixed(6)}` : '';
        },

        /** "5 min ago" */
        formatAgo(time) {
            if (!time) {
                return 'unknown time';
            }
            const minutes = Math.max(0, Math.round((Date.now() - new Date(time).getTime()) / 60000));
            return minutes < 1 ? 'just now'
                : minutes < 120 ? `${minutes} min ago`
                : minutes < 2880 ? `${Math.round(minutes / 60)} h ago`
                : `${Math.round(minutes / 1440)} days ago`;
        },

        /** "3.11.2026, 16:57:22 (5 min ago)" */
        formatFixTime(time) {
            return time ? `${new Date(time).toLocaleString()} (${this.formatAgo(time)})` : 'unknown time';
        },

        modeLabel() {
            return { stationary: 'Stationary', mobile: 'Mobile', off: 'Off' }[this.location?.mode] || this.location?.mode;
        },

        /** The latest fix as a violet dot with its accuracy circle (like leaflet-locate's), details in a popup. */
        updateFixMarker() {
            const fix = this.location?.fix;
            if (!fix) {
                for (const layer of [this.fixMarker, this.fixCircle]) {
                    layer?.remove();
                }
                this.fixMarker = this.fixCircle = null;
                return;
            }
            const latLng = [fix.lat, fix.lon];
            const radius = fix.accuracy || 0;
            if (!this.fixCircle) {
                this.fixCircle = L.circle(latLng, {
                    radius, color: FIX_COLOR, fillColor: FIX_COLOR, fillOpacity: 0.15, weight: 2, interactive: false
                }).addTo(this.geolocationMap);
                this.fixMarker = L.circleMarker(latLng, {
                    radius: 9, color: FIX_COLOR, fillColor: FIX_COLOR, fillOpacity: 0.7, weight: 3, opacity: 0.9
                }).bindPopup('').addTo(this.geolocationMap);
            } else {
                this.fixCircle.setLatLng(latLng).setRadius(radius);
                this.fixMarker.setLatLng(latLng);
            }
            const lines = [
                ['Measured', this.formatFixTime(fix.time)],
                ['Position', this.formatPosition(fix)],
                ['Accuracy', fix.accuracy != null ? this.formatDistance(fix.accuracy) : 'unknown'],
                ['Altitude', fix.alt != null ? `${fix.alt} m` : 'unknown'],
                ['Satellites', fix.sats ?? 'unknown'],
            ];
            if (this.location.deviation_m != null) {
                lines.push(['From configured position', this.formatDistance(this.location.deviation_m)]);
            }
            this.fixMarker.setPopupContent(this.popupContent('GPS measurement', lines));
        },

        /** A marker popup: a title and label/value lines, built as DOM (no HTML from the data). */
        popupContent(title, lines) {
            const popup = document.createElement('div');
            const heading = document.createElement('div');
            heading.className = 'fw-bold mb-1';
            heading.textContent = title;
            popup.append(heading);
            for (const [label, value] of lines) {
                const line = document.createElement('div');
                const strong = document.createElement('strong');
                strong.textContent = `${label}: `;
                line.append(strong, String(value));
                popup.append(line);
            }
            return popup;
        },

        /** The configured position's popup, like the GPS measurement's. */
        referencePopup() {
            const reference = this.geolocation;
            const lines = [
                ['Position', this.formatPosition(reference)],
                ['Accuracy', reference.accuracy != null ? this.formatDistance(reference.accuracy) : 'unknown'],
                ['Altitude', reference.alt != null ? `${reference.alt} m` : 'unknown'],
            ];
            if (this.location?.deviation_m != null) {
                lines.push(['From GPS measurement', this.formatDistance(this.location.deviation_m)]);
            }
            return this.popupContent('Configured position', lines);
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
            // Only initialize if we have a position and map not already initialized
            const center = this.trackerLatLng();
            if (!center || this.geolocationMapInitialized || !document.getElementById('geolocationMap')) {
                return;
            }

            // Initialize map centered on the tracker
            this.geolocationMap = L.map('geolocationMap', {
                center,
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
            
            if (this.geolocation) {
                this.geolocationMarker = L.marker([this.geolocation.lat, this.geolocation.lon], {
                    draggable: false,
                    icon: trackerIcon
                }).bindPopup(() => this.referencePopup()).addTo(this.geolocationMap);
            }
            this.updateFixMarker();

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
            const trackerLatLng = this.trackerLatLng();
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
            
            const distanceText = this.formatDistance(distance);
            
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

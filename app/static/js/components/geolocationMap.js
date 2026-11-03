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
 * show every position when it opens and on "Show all positions"; refreshes move
 * the dot, not the view. Dashed lines connect the configured position, the fix
 * and, once shown, your own position; tapping a line gives the distance.
 *
 * @param {() => string|undefined} getOsName - Name of the OS, which decides the
 *     marker's icon. The template passes a closure over the status page's data.
 */
const LOCATION_REFRESH_MS = 10000;
// The GNSS fix; the user's own position (leaflet-locate) is blue in the same style
const FIX_COLOR = '#7c3aed';
// The dashed lines between the positions: yellow stands out on satellite imagery and street maps
const LINE_COLOR = '#ffc107';

export function geolocationMap(getOsName = () => '') {
    return compose(lifecycleMixin(), showToastMixin(), {
        // The configured position (/boot/firmware/geolocation)
        geolocation: null,
        // tslocationd's view (/run/tslocation/location.json), null without the daemon
        location: null,
        geolocationMap: null,
        // The latest GNSS fix: dot and accuracy circle
        fixMarker: null,
        fixCircle: null,
        // POST /api/location/fix sent, until the daemon reports `fixing`
        fixRequesting: false,
        geolocationMapInitialized: false,
        // The user's own location (leaflet-locate) and its accuracy in metres
        userLatLng: null,
        userAccuracy: null,
        // The dashed lines between the positions: configuredFix, userConfigured, userFix
        lines: {},

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

        /** The positions on the map: configured, GPS fix, and the user's own once located. */
        allLatLngs() {
            const fix = this.location?.fix;
            return [
                this.geolocation && [this.geolocation.lat, this.geolocation.lon],
                fix && [fix.lat, fix.lon],
                this.userLatLng && [this.userLatLng.lat, this.userLatLng.lng],
            ].filter(Boolean);
        },

        /** Zoom so every position is on the map. Only at start and on "Show all positions": refreshes keep the view. */
        fitAll() {
            const points = this.allLatLngs();
            if (!points.length || !this.geolocationMap) {
                return;
            }
            this.geolocationMap.fitBounds(points, { maxZoom: 19, padding: [60, 60] });
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
                this.updateLines();
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
            this.geolocationMap = L.map('geolocationMap', { center, zoom: 19 });
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

            // The configured position, read-only
            if (this.geolocation) {
                const icon = L.VectorMarkers.icon({
                    icon: this.getTrackerIcon(),
                    prefix: 'fa',
                    markerColor: 'var(--bs-purple)',  // Bootstrap purple CSS variable
                    iconColor: 'white'
                });
                L.marker([this.geolocation.lat, this.geolocation.lon], { icon })
                    .bindPopup(() => this.referencePopup()).addTo(this.geolocationMap);
            }
            this.updateFixMarker();
            this.updateLines();
            this.fitAll();

            const ShowAll = L.Control.extend({
                onAdd: () => {
                    const bar = L.DomUtil.create('div', 'leaflet-bar leaflet-control');
                    const link = L.DomUtil.create('a', 'leaflet-bar-part leaflet-bar-part-single', bar);
                    link.href = '#';
                    link.title = 'Show all positions';
                    link.setAttribute('role', 'button');
                    L.DomUtil.create('i', 'fas fa-expand', link);
                    L.DomEvent.disableClickPropagation(bar);
                    L.DomEvent.on(link, 'click', (e) => {
                        L.DomEvent.preventDefault(e);
                        this.fitAll();
                    });
                    return bar;
                }
            });
            new ShowAll({ position: 'topright' }).addTo(this.geolocationMap);

            // Add locate control for user's current location
            L.control.locate({
                position: 'topright',
                strings: {
                    title: "Show my location",
                    popup: () => this.userPopup()
                },
                locateOptions: {
                    maxZoom: 19,
                    enableHighAccuracy: true
                },
                keepCurrentZoomLevel: true,
                // Instead of leaflet-locate's alert: the toast of the `locationerror` handler below
                onLocationError: () => {},
                circleStyle: {
                    color: '#136aec',
                    fillColor: '#136aec',
                    fillOpacity: 0.15,
                    weight: 2,
                    // Taps reach the markers and lines below it
                    interactive: false
                },
                markerStyle: {
                    color: '#136aec',
                    fillColor: '#2A93EE',
                    fillOpacity: 0.7,
                    weight: 3,
                    opacity: 0.9,
                    radius: 9
                },
                icon: 'fas fa-location-arrow',
                iconLoading: 'fas fa-spinner fa-spin',
                showPopup: true
            }).addTo(this.geolocationMap);

            // Before leaflet-locate's own handler, which builds the popup with userPopup()
            this.geolocationMap.on('locationfound', (e) => {
                this.userLatLng = e.latlng;
                this.userAccuracy = e.accuracy;
                this.updateLines();
            });

            this.geolocationMap.on('locatedeactivate', () => {
                this.userLatLng = this.userAccuracy = null;
                this.updateLines();
            });

            this.geolocationMap.on('locationerror', (e) => {
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

        /** The user's own position's popup, like the GPS measurement's. */
        userPopup() {
            const user = this.userLatLng;
            const lines = [
                ['Position', this.formatPosition({ lat: user.lat, lon: user.lng })],
                ['Accuracy', this.userAccuracy != null ? this.formatDistance(this.userAccuracy) : 'unknown'],
            ];
            if (this.geolocation) {
                lines.push(['From configured position', this.formatDistance(this.geolocationMap.distance([this.geolocation.lat, this.geolocation.lon], user))]);
            }
            const fix = this.location?.fix;
            if (fix) {
                lines.push(['From GPS measurement', this.formatDistance(this.geolocationMap.distance([fix.lat, fix.lon], user))]);
            }
            return this.popupContent('Your position', lines);
        },

        /** The dashed lines between the configured position, the GPS fix and the user, with the distance on tap. */
        updateLines() {
            const fix = this.location?.fix;
            const configured = this.geolocation && [this.geolocation.lat, this.geolocation.lon];
            const measured = fix && [fix.lat, fix.lon];
            const user = this.userLatLng && [this.userLatLng.lat, this.userLatLng.lng];
            const lines = {
                configuredFix: configured && measured && [configured, measured, 'Configured position to GPS measurement'],
                userConfigured: user && configured && [user, configured, 'You to the configured position'],
                userFix: user && measured && [user, measured, 'You to the GPS measurement'],
            };
            for (const [key, line] of Object.entries(lines)) {
                if (!line) {
                    this.lines[key]?.remove();
                    delete this.lines[key];
                    continue;
                }
                const [from, to, title] = line;
                const layer = this.lines[key] ??= L.polyline([from, to], {
                    color: LINE_COLOR, weight: 3, opacity: 0.7, dashArray: '10, 10'
                }).bindPopup('').addTo(this.geolocationMap);
                layer.setLatLngs([from, to]);
                layer.setPopupContent(this.popupContent(title, [['Distance', this.formatDistance(this.geolocationMap.distance(from, to))]]));
            }
        },
    });
}

import { serverModeManager } from '../managers/serverModeManager.js';
import { systemConfigManager } from '../managers/systemConfigManager.js';
import { compose } from '../mixins/compose.js';
import { lifecycleMixin } from '../mixins/lifecycleMixin.js';

/**
 * Per power daemon: its title, the data blocks of its `cached` reply that
 * hold the controller's readings, best first, with how each block reaches the
 * controller, what the daemon's device id is, and the values shown, in
 * groups. Each value lists names to try in order, since the blocks of one
 * daemon do not all use the same name for a value.
 */
export const POWER_SOURCES = {
    solarlife: {
        title: 'SolarLife',
        blocks: ['status'],
        connections: { status: 'Bluetooth' },
        device: 'MAC address',
        groups: [
            { title: 'Battery', icon: 'fa-car-battery', values: [['battery_voltage'], ['battery_current'], ['battery_percentage']] },
            { title: 'Solar Panel', icon: 'fa-solar-panel', values: [['solar_panel_voltage'], ['solar_panel_power'], ['solar_panel_daily_energy']] },
            { title: 'Load', icon: 'fa-plug', values: [['load_voltage'], ['load_current']] },
            { title: 'Controller', icon: 'fa-microchip', values: [['solar_panel_charge_state'], ['device_built_in_temperature']] },
        ],
    },
    smartsolar: {
        // The cable has every value and a new reading each second, GATT has
        // every value once a minute, the advertisements only a few
        title: 'SmartSolar',
        blocks: ['vedirect', 'gatt', 'advert'],
        connections: { vedirect: 'VE.Direct cable', gatt: 'Bluetooth GATT', advert: 'Bluetooth advertisements' },
        device: 'Serial number',
        groups: [
            { title: 'Battery', icon: 'fa-car-battery', values: [['battery_voltage'], ['battery_current']] },
            { title: 'Solar Panel', icon: 'fa-solar-panel', values: [['panel_voltage'], ['pv_power', 'panel_power'], ['yield_today']] },
            { title: 'Load', icon: 'fa-plug', values: [['load_output_voltage'], ['load_current', 'load_output_current']] },
            { title: 'Controller', icon: 'fa-microchip', values: [['device_state'], ['charger_error']] },
        ],
    },
};

/**
 * The first block among `names` with values read within `recentSeconds`, or null.
 * @param {object} blocks - `blocks` of a daemon's `cached` reply
 * @param {string[]} names - block names, best first
 * @param {number} recentSeconds
 * @returns {{name: string, age_s: number, values: object[]} | null}
 */
export function recentBlock(blocks, names, recentSeconds) {
    for (const name of names) {
        const block = blocks?.[name];
        if (block?.values?.length && block.age_s != null && block.age_s <= recentSeconds) {
            return { name, age_s: block.age_s, values: block.values };
        }
    }
    return null;
}

/** A daemon value for display, with its unit; text for flags and missing values. */
export function formatPowerValue(item) {
    const { value, unit } = item;
    if (value === null || value === undefined) return '–';
    if (typeof value === 'boolean') return value ? 'yes' : 'no';
    return unit ? `${value} ${unit}` : String(value);
}

/**
 * A charge controller card of the status page, for one power daemon
 * ('solarlife' or 'smartsolar'). tsconfig only forwards the requests to the
 * daemon's control socket (/api/power/<source>); everything else is decided
 * here: the card shows only if the daemon has readings from the last
 * `power_recent_s` seconds (tsconfig.yml). Reloads the daemon's cached
 * readings whenever the status page has refreshed ('status-refreshed');
 * readNow() has the daemon read the controller first.
 */
export function powerSummary(source) {
    // An unknown source has no blocks, so its card never shows
    const definition = POWER_SOURCES[source] ?? { title: source, blocks: [], connections: {}, device: 'Device', groups: [] };
    return compose(lifecycleMixin(), {
        source,
        title: definition.title,
        // The controller as the daemon names it (serial number or MAC address)
        powerDevice: null,
        // The block shown: {name, age_s, values}; null hides the card
        powerBlock: null,
        powerLoading: false,
        // A live read (readNow) in progress, and why the last one failed
        powerReading: false,
        powerReadError: null,

        init() {
            this.listen(window, 'status-refreshed', () => this.loadPower());
        },

        /** One request to the daemon: the reply envelope, or null if it can't be reached. */
        async request(cmd, args) {
            const response = await fetch(serverModeManager.buildApiUrl(`/api/power/${source}`), {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ cmd, args }),
            });
            return response.ok ? await response.json() : null;
        },

        async loadPower() {
            this.powerLoading = true;
            try {
                const [reply, config] = await Promise.all([
                    this.request('cached', { block: 'all' }),
                    systemConfigManager.getSystemConfig(),
                ]);
                const cached = reply?.ok ? reply.result : null;
                this.powerBlock = recentBlock(cached?.blocks, definition.blocks, config?.power_recent_s ?? 300);
                this.powerDevice = this.powerBlock ? cached.device : null;
            } catch (err) {
                console.error(`Failed to load ${source} power data:`, err);
                this.powerBlock = null;
                this.powerDevice = null;
            } finally {
                this.powerLoading = false;
            }
        },

        /** Have the daemon read the block shown from the controller, then show the result. */
        async readNow() {
            const block = this.powerBlock?.name;
            if (!block || this.powerReading) return;
            this.powerReading = true;
            this.powerReadError = null;
            try {
                const reply = await this.request('read', { block });
                if (!reply) {
                    this.powerReadError = `${definition.title} daemon did not answer`;
                } else if (!reply.ok) {
                    this.powerReadError = reply.error?.message || 'Read failed';
                }
            } catch (err) {
                this.powerReadError = err.message;
            } finally {
                this.powerReading = false;
            }
            await this.loadPower();
        },

        /**
         * The groups of values, each with the values the shown block has;
         * empty groups left out. The controller group also names the device
         * and how its data arrived, as rows like the values.
         */
        get powerGroups() {
            const values = this.powerBlock?.values ?? [];
            const about = [
                { name: 'device', friendly: definition.device, unit: '', value: this.powerDevice },
                { name: 'connection', friendly: 'Connection', unit: '', value: definition.connections[this.powerBlock?.name] ?? this.powerBlock?.name },
            ].filter(item => item.value);
            return definition.groups
                .map(group => ({
                    title: group.title,
                    icon: group.icon,
                    values: [
                        ...group.values
                            .map(names => names.map(name => values.find(v => v.name === name)).find(Boolean))
                            .filter(Boolean),
                        ...(group.title === 'Controller' ? about : []),
                    ],
                }))
                .filter(group => group.values.length);
        },

        formatPowerValue,
    });
}

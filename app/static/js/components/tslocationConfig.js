import { compose } from '../mixins/compose.js';
import { configPageMixin } from '../mixins/configPageMixin.js';
import { serverModeManager } from '../managers/serverModeManager.js';

/**
 * System Settings > Location: tslocation.yml, the settings of the location
 * daemon tslocationd. The mode is a normal setting; the rest is shown in expert
 * mode. A field left empty is not written, so the daemon's default applies
 * (shown as placeholder, from /api/tslocation/defaults) and a later change of
 * that default still reaches the station.
 */

// Durations: seconds or a string such as 60s, 1h, 7d (the daemon parses them with pytimeparse)
export const TSLOCATION_FIELDS = [
    { key: 'fix_duration', label: 'Fix Duration', kind: 'duration', help: 'How long a fine fix averages positions.' },
    { key: 'fix_timeout', label: 'Fix Timeout', kind: 'duration', help: 'How long GNSS may run without a fix before giving up.' },
    { key: 'fix_max_age', label: 'Fix Max Age', kind: 'duration', help: 'A fine fix is made at boot when the last one is older.' },
    { key: 'check_interval', label: 'Check Interval', kind: 'duration', help: 'Coarse check (serving cell) while running.' },
    { key: 'threshold', label: 'Threshold (m)', kind: 'number', help: 'Distance from the configured position that counts as deviating.' },
    { key: 'recheck_interval', label: 'Recheck Interval', kind: 'duration', help: 'Fine fix interval while the station is deviating.' },
    { key: 'settle_count', label: 'Settle Count', kind: 'integer', help: 'Agreeing fixes needed for a proposed position.' },
    { key: 'track_interval', label: 'Track Interval', kind: 'duration', help: 'Record a fix at least this often, in GNSS seconds (the receiver reports once a second).' },
    { key: 'track_distance', label: 'Track Distance (m)', kind: 'number', help: 'Record a fix after moving this far.' },
    { key: 'time_max_age', label: 'Time Max Age', kind: 'duration', help: 'A fine fix for the clock when chrony has had no time source for longer.' },
];

// How the expert settings are grouped on the page; `modes` are those the group applies to
export const TSLOCATION_GROUPS = [
    {
        key: 'fix', title: 'Receiver and Fine Fix', icon: 'fa-satellite-dish', modes: ['stationary', 'mobile', 'off'],
        help: 'How a fine fix is made: GNSS is switched on, positions are averaged, and GNSS is switched off again.',
        fields: ['fix_duration', 'fix_timeout'],
    },
    {
        key: 'stationary', title: 'Stationary Checks', icon: 'fa-map-pin', modes: ['stationary'],
        help: 'When a fine fix is made, and when the station counts as moved.',
        fields: ['fix_max_age', 'check_interval', 'recheck_interval', 'threshold', 'settle_count'],
    },
    {
        key: 'mobile', title: 'Mobile Tracking', icon: 'fa-route', modes: ['mobile'],
        help: 'GNSS stays on; a track point goes to the track file and over MQTT at every interval or distance.',
        fields: ['track_interval', 'track_distance'],
    },
    {
        key: 'clock', title: 'Clock', icon: 'fa-clock', modes: ['stationary', 'mobile', 'off'],
        help: 'GNSS is also the time source of a station without network.',
        fields: ['time_max_age'],
    },
];

/** 3600 -> "1h", 90 -> "90s": the shortest exact form with one unit. */
export function formatDuration(seconds) {
    if (typeof seconds !== 'number' || !Number.isFinite(seconds)) {
        return '';
    }
    for (const [unit, size] of [['d', 86400], ['h', 3600], ['m', 60]]) {
        if (seconds >= size && seconds % size === 0) {
            return `${seconds / size}${unit}`;
        }
    }
    return `${seconds}s`;
}

function emptyConfig() {
    const config = { mode: 'stationary', receiver: '' };
    for (const field of TSLOCATION_FIELDS) {
        config[field.key] = '';
    }
    return config;
}

export function tslocationConfig() {
    return compose(configPageMixin({
        service: 'tslocation',
        apiPath: '/api/tslocation',
        title: 'Location Configuration',
        failureText: 'Failed to save the location configuration',
        refreshOn: ['settings'],
    }), {
        fields: TSLOCATION_FIELDS,
        groups: TSLOCATION_GROUPS.map(group => ({
            ...group,
            fields: group.fields.map(key => TSLOCATION_FIELDS.find(field => field.key === key)),
        })),

        /** Whether a group's settings matter in the selected mode (the others are shown dimmed). */
        groupApplies(group) {
            return group.modes.includes(this.config.mode);
        },
        config: emptyConfig(),
        defaults: {},

        async loadData() {
            await Promise.all([this.loadConfig(), this.loadDefaults()]);
        },

        async loadDefaults() {
            try {
                const response = await fetch(serverModeManager.buildApiUrl('/api/tslocation/defaults'));
                if (response.ok) {
                    this.defaults = await response.json();
                }
            } catch (error) {
                console.error('Failed to load the location defaults:', error);
            }
        },

        placeholder(field) {
            const value = this.defaults[field.key];
            if (value === undefined) {
                return 'default';
            }
            return `default: ${field.kind === 'duration' ? formatDuration(value) : value}`;
        },

        async refreshConfig() {
            this.config = emptyConfig();
            await this.loadConfig();
        },

        async loadConfig() {
            try {
                const response = await fetch(serverModeManager.buildApiUrl('/api/tslocation'));
                if (!response.ok && response.status !== 404) {
                    throw new Error('Failed to load the location configuration');
                }
                const data = response.ok ? (await response.json()) || {} : {};
                const config = emptyConfig();
                config.mode = data.mode || 'stationary';
                config.receiver = data.receiver || '';
                for (const field of TSLOCATION_FIELDS) {
                    config[field.key] = data[field.key] === undefined || data[field.key] === null ? '' : String(data[field.key]);
                }
                this.config = config;
                if (!this.serverMode) {
                    await this.loadServiceStatus();
                }
            } catch (error) {
                this.showMessage(error.message, true);
            }
        },

        /** Empty fields are left out (daemon default); numbers are sent as numbers, durations as written. */
        buildPayload() {
            const payload = { mode: this.config.mode };
            if (this.config.receiver) {
                payload.receiver = this.config.receiver;
            }
            for (const field of TSLOCATION_FIELDS) {
                const text = String(this.config[field.key] ?? '').trim();
                if (text === '') {
                    continue;
                }
                const number = Number(text);
                payload[field.key] = field.kind === 'duration' && Number.isNaN(number) ? text : number;
            }
            return payload;
        },

        validate() {
            for (const field of TSLOCATION_FIELDS) {
                const text = String(this.config[field.key] ?? '').trim();
                if (text === '') {
                    continue;
                }
                const number = Number(text);
                const bad = field.kind === 'integer' ? !Number.isInteger(number)
                    : field.kind === 'number' ? Number.isNaN(number)
                    : !(/^\d+(\.\d+)?$/.test(text) || /^[0-9.]+\s*[a-z]+(\s*[0-9.]+\s*[a-z]+)*$/i.test(text));
                if (bad) {
                    const expected = field.kind === 'duration' ? 'seconds or e.g. 60s, 1h, 7d' : field.kind === 'integer' ? 'a whole number' : 'a number';
                    this.showMessage(`${field.label}: "${text}" is not ${expected}`, true);
                    throw new Error(`Invalid ${field.key}`);
                }
            }
        },
    });
}

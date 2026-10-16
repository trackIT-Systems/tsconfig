import { compose } from '../mixins/compose.js';
import { configPageMixin } from '../mixins/configPageMixin.js';
import { serverModeManager } from '../managers/serverModeManager.js';

const DEFAULT_DEFAULTS = {
    scheduling_interval: '1m',
    scheduling_interval_hhmm: '00:01',
    topic_prefix: '',
    requires: [],
    qos: 1,
};

const INTERVAL_PATTERN = /^(\d+(?:\.\d+)?)\s*(s|sec|secs|second|seconds|m|min|mins|minute|minutes|h|hr|hrs|hour|hours|d|day|days)?$/i;

const UNIT_TO_SECONDS = {
    s: 1,
    sec: 1,
    secs: 1,
    second: 1,
    seconds: 1,
    m: 60,
    min: 60,
    mins: 60,
    minute: 60,
    minutes: 60,
    h: 3600,
    hr: 3600,
    hrs: 3600,
    hour: 3600,
    hours: 3600,
    d: 86400,
    day: 86400,
    days: 86400,
};

function requiresToString(requires) {
    if (!requires) return '';
    if (Array.isArray(requires)) return requires.join(', ');
    return String(requires);
}

function requiresFromString(value) {
    if (!value || !String(value).trim()) return [];
    return String(value)
        .split(',')
        .map((item) => item.trim())
        .filter(Boolean);
}

function intervalToSeconds(value) {
    if (!value || !String(value).trim()) return null;
    const match = String(value).trim().match(INTERVAL_PATTERN);
    if (!match) return null;
    const number = parseFloat(match[1]);
    const unit = (match[2] || 's').toLowerCase();
    const multiplier = UNIT_TO_SECONDS[unit];
    if (!multiplier) return null;
    const seconds = number * multiplier;
    return seconds > 0 ? seconds : null;
}

function secondsToHhmm(seconds) {
    if (seconds === null || seconds === undefined || Number.isNaN(seconds)) {
        return '';
    }
    const total = Math.max(0, Math.floor(seconds));
    let hours = Math.floor(total / 3600);
    let minutes = Math.floor((total % 3600) / 60);
    if (total > 0 && hours === 0 && minutes === 0) {
        minutes = 1;
    }
    return `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}`;
}

function hhmmToSeconds(hhmm) {
    if (!hhmm || typeof hhmm !== 'string') {
        return null;
    }
    const parts = hhmm.split(':');
    if (parts.length !== 2) {
        return null;
    }
    const hours = parseInt(parts[0], 10) || 0;
    const minutes = parseInt(parts[1], 10) || 0;
    const seconds = (hours * 3600) + (minutes * 60);
    return seconds > 0 ? seconds : null;
}

function secondsToIntervalString(seconds) {
    if (!seconds || seconds <= 0) return '';
    if (seconds % 3600 === 0) {
        return `${seconds / 3600}h`;
    }
    if (seconds % 60 === 0) {
        return `${seconds / 60}m`;
    }
    return `${seconds}s`;
}

function intervalToHhmm(value) {
    return secondsToHhmm(intervalToSeconds(value));
}

function hhmmToIntervalString(hhmm) {
    const seconds = hhmmToSeconds(hhmm);
    if (seconds === null) return '';
    return secondsToIntervalString(seconds);
}

function apiToUi(data) {
    const defaults = { ...DEFAULT_DEFAULTS, ...(data.DEFAULT || {}) };
    defaults.requiresText = requiresToString(defaults.requires);
    defaults.scheduling_interval_hhmm = intervalToHhmm(defaults.scheduling_interval) || DEFAULT_DEFAULTS.scheduling_interval_hhmm;

    const tasks = Object.entries(data)
        .filter(([section]) => section !== 'DEFAULT')
        .map(([name, task]) => ({
            name,
            func: task.func || '',
            scheduling_interval_hhmm: task.scheduling_interval ? intervalToHhmm(task.scheduling_interval) : '',
            requiresText: requiresToString(task.requires),
            qos: task.qos ?? '',
        }));

    return { defaults, tasks };
}

function uiToApi(defaults, tasks) {
    const payload = {};

    const defaultSection = {};
    const defaultInterval = hhmmToIntervalString(defaults.scheduling_interval_hhmm);
    if (defaultInterval) {
        defaultSection.scheduling_interval = defaultInterval;
    }
    if (defaults.topic_prefix !== undefined && defaults.topic_prefix !== '') {
        defaultSection.topic_prefix = defaults.topic_prefix;
    }
    const defaultRequires = requiresFromString(defaults.requiresText);
    if (defaultRequires.length > 0) {
        defaultSection.requires = defaultRequires;
    }
    if (defaults.qos !== '' && defaults.qos !== null && defaults.qos !== undefined) {
        defaultSection.qos = Number(defaults.qos);
    }
    if (Object.keys(defaultSection).length > 0) {
        payload.DEFAULT = defaultSection;
    }

    for (const task of tasks) {
        const sectionName = (task.name || '').trim();
        if (!sectionName) continue;

        const section = { func: task.func };
        const taskInterval = hhmmToIntervalString(task.scheduling_interval_hhmm);
        if (taskInterval) {
            section.scheduling_interval = taskInterval;
        }
        const taskRequires = requiresFromString(task.requiresText);
        if (taskRequires.length > 0) {
            section.requires = taskRequires;
        }
        if (task.qos !== '' && task.qos !== null && task.qos !== undefined) {
            section.qos = Number(task.qos);
        }
        payload[sectionName] = section;
    }

    return payload;
}

export function mqttutilConfig() {
    return compose(configPageMixin({
        service: 'mqttutil',
        apiPath: '/api/mqttutil',
        title: 'Reporting Configuration',
        failureText: 'Failed to save mqttutil configuration',
        refreshOn: ['settings', 'reporting'],
        refreshSeconds: 30,
        initDelayMs: 100,
    }), {
        defaults: { ...DEFAULT_DEFAULTS, requiresText: '' },
        tasks: [],
        configLoaded: false,
        async loadData() {
            await this.loadConfig();
            if (!this.serverMode) {
                this.loadServiceStatus();
            }
        },

        async refreshConfig() {
            await this.loadConfig();
        },

        async loadConfig() {
            try {
                const url = serverModeManager.buildApiUrl('/api/mqttutil');
                const response = await fetch(url);
                if (response.status === 404) {
                    this.defaults = {
                        ...DEFAULT_DEFAULTS,
                        requiresText: requiresToString(DEFAULT_DEFAULTS.requires),
                    };
                    this.tasks = [];
                    this.configLoaded = true;
                    this.showMessage('No mqttutil configuration found. Add tasks and save to create one.', false);
                    return;
                }
                if (!response.ok) {
                    throw new Error('Failed to load mqttutil configuration');
                }
                const data = await response.json();
                const ui = apiToUi(data);
                this.defaults = ui.defaults;
                this.tasks = ui.tasks;
                this.configLoaded = true;

                if (!this.serverMode) {
                    await this.loadServiceStatus();
                }
            } catch (error) {
                this.showMessage(error.message, true);
            }
        },

        addTask() {
            this.tasks.push({
                name: '',
                func: '',
                scheduling_interval_hhmm: '',
                requiresText: '',
                qos: '',
            });
        },

        removeTask(index) {
            this.tasks.splice(index, 1);
        },

        buildPayload() {
            return uiToApi(this.defaults, this.tasks);
        },

    });
}

// Fakes for the browser: the components only need a few globals.
import { serverModeManager } from '../../app/static/js/managers/serverModeManager.js';
import { serviceManager } from '../../app/static/js/managers/serviceManager.js';
import { systemConfigManager } from '../../app/static/js/managers/systemConfigManager.js';

/** Install window, fetch and toasts; returns what happened. Call restore() when done. */
export function installBrowser({ hash = '', services = [] } = {}) {
    const calls = []; // {url, method, body}
    const toasts = []; // {message, type, title}
    const events = []; // window events dispatched
    const listeners = new Map();

    const fakeWindow = {
        BASE_URL: '',
        location: { hash, search: '', origin: 'http://localhost' },
        addEventListener: (event, handler) => {
            listeners.set(event, [...(listeners.get(event) ?? []), handler]);
        },
        removeEventListener: (event, handler) => {
            listeners.set(event, (listeners.get(event) ?? []).filter(h => h !== handler));
        },
        dispatchEvent: event => {
            events.push(event.type);
            for (const handler of listeners.get(event.type) ?? []) handler(event);
        },
        toastManager: {
            show: (message, type, options) => toasts.push({ message, type, title: options?.title }),
            success: message => toasts.push({ message, type: 'success' }),
            error: message => toasts.push({ message, type: 'error' }),
        },
    };
    const saved = {
        window: globalThis.window,
        fetch: globalThis.fetch,
        CustomEvent: globalThis.CustomEvent,
        console: globalThis.console,
    };
    globalThis.window = fakeWindow;
    globalThis.CustomEvent = class CustomEvent {
        constructor(type, init) {
            this.type = type;
            this.detail = init?.detail;
        }
    };

    const responses = []; // [{match: RegExp, method, status, body}]
    globalThis.fetch = async (url, options = {}) => {
        const method = options.method ?? 'GET';
        calls.push({ url, method, body: options.body ? JSON.parse(options.body) : undefined });
        if (method === 'GET' && String(url).endsWith('/api/systemd/services')) {
            return { ok: true, status: 200, json: async () => services };
        }
        const rule = responses.find(r => r.match.test(url) && (r.method ?? method) === method);
        const status = rule?.status ?? 200;
        return { ok: status < 400, status, json: async () => rule?.body ?? { message: 'ok' } };
    };

    // The managers are module singletons: reset what the tests change
    serverModeManager.enabled = false;
    serverModeManager.currentConfigGroup = null;
    serviceManager.services = [];
    serviceManager.lastFetch = null;
    systemConfigManager.config = { status_refresh_interval: 5 };

    return {
        calls,
        toasts,
        events,
        window: fakeWindow,
        respond: rule => responses.unshift(rule),
        restore() {
            globalThis.window = saved.window;
            globalThis.fetch = saved.fetch;
            globalThis.CustomEvent = saved.CustomEvent;
            serverModeManager.enabled = false;
            serverModeManager.currentConfigGroup = null;
        },
        listenerCount: event => (listeners.get(event) ?? []).length,
    };
}

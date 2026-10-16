import assert from 'node:assert/strict';
import { test } from 'node:test';
import { serverModeManager } from '../../app/static/js/managers/serverModeManager.js';
import { compose } from '../../app/static/js/mixins/compose.js';
import { configPageMixin } from '../../app/static/js/mixins/configPageMixin.js';
import { installBrowser } from './helpers.mjs';

const OPTIONS = {
    service: 'tsschedule',
    apiPath: '/api/schedule',
    title: 'Schedule Configuration',
    failureText: 'Failed to save schedule configuration',
    refreshOn: ['settings'],
    initDelayMs: 0,
};

/** A page like scheduleConfig: a config, loadConfig() and a hook. */
function page(extra = {}, options = OPTIONS) {
    let loads = 0;
    const component = compose(configPageMixin(options), {
        config: { schedule: [1, 2] },
        async loadConfig() {
            loads++;
        },
        loads: () => loads,
        ...extra,
    });
    // Alpine's x-data proxy provides `this`; the methods only need the object itself
    return component;
}

test('Save PUTs the config, toasts the API message and calls onSaved', async t => {
    const browser = installBrowser();
    t.after(browser.restore);
    browser.respond({ match: /api\/schedule/, method: 'PUT', body: { message: 'Schedule updated' } });
    let saved = 0;
    const component = page({ onSaved: () => saved++ });

    await component.saveConfig();

    assert.deepEqual(browser.calls.map(c => `${c.method} ${c.url}`), ['PUT /api/schedule']);
    assert.deepEqual(browser.calls[0].body, { schedule: [1, 2] });
    assert.deepEqual(browser.toasts, [{ message: 'Schedule updated', type: 'success', title: 'Schedule Configuration' }]);
    assert.equal(saved, 1);
    assert.equal(component.saveState, 'saved');
});

test('Save sends buildPayload(), not the config as it is', async t => {
    const browser = installBrowser();
    t.after(browser.restore);
    const component = page({ buildPayload: () => ({ converted: true }) });
    await component.saveConfig();
    assert.deepEqual(browser.calls[0].body, { converted: true });
});

test('a failed Save throws the API error with its field errors, and the button recovers', async t => {
    const browser = installBrowser();
    t.after(browser.restore);
    browser.respond({
        match: /api\/schedule/,
        method: 'PUT',
        status: 400,
        body: { detail: { message: 'Invalid schedule', errors: ['e1'] } },
    });
    const component = page();

    await assert.rejects(component.saveConfig(), { message: 'Invalid schedule\nValidation errors: e1' });
    assert.equal(component.saveState, 'idle');
    assert.deepEqual(browser.toasts, []);
});

test('validate() can refuse before anything is sent', async t => {
    const browser = installBrowser();
    t.after(browser.restore);
    const component = page({
        validate() {
            throw new Error('Species validation failed');
        },
    });
    await assert.rejects(component.saveConfig(), { message: 'Species validation failed' });
    assert.deepEqual(browser.calls, []);
});

test('in server mode the config group is part of the URL and the toast comes from the save state', async t => {
    const browser = installBrowser();
    t.after(browser.restore);
    serverModeManager.enabled = true;
    serverModeManager.currentConfigGroup = 'demo';
    const component = page();

    await component.saveConfig();

    assert.equal(browser.calls[0].url, 'http://localhost/api/schedule?config_group=demo');
    // handleSaveConfig's own toast, not the per-page one
    assert.deepEqual(browser.toasts, [{ message: 'Configuration saved successfully', type: 'success' }]);
});

test('Save & Restart saves, restarts the unit and refreshes the status afterwards', async t => {
    const browser = installBrowser({ services: [{ name: 'tsschedule', active: true, enabled: true, status: 'running' }] });
    t.after(browser.restore);
    t.mock.timers.enable({ apis: ['setTimeout'] });
    browser.respond({ match: /api\/schedule/, method: 'PUT', body: { message: 'Schedule updated' } });
    browser.respond({ match: /systemd\/action/, method: 'POST', body: { message: 'tsschedule restarted' } });
    let saved = 0;
    const component = page({ onSaved: () => saved++ });

    await component.saveAndRestartService();

    assert.deepEqual(browser.calls.map(c => `${c.method} ${c.url}`), ['PUT /api/schedule', 'POST /api/systemd/action']);
    assert.deepEqual(browser.calls[1].body, { service: 'tsschedule', action: 'restart' });
    assert.deepEqual(
        browser.toasts.map(toast => toast.message),
        ['Schedule updated', 'Configuration saved and tsschedule restarted']
    );
    assert.equal(saved, 1);
    assert.equal(component.saveRestartState, 'saved');

    t.mock.timers.tick(2000);
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(component.serviceStatus.status, 'running');
});

test('a failing restart reports the API detail and resets the button', async t => {
    const browser = installBrowser();
    t.after(browser.restore);
    browser.respond({ match: /systemd\/action/, method: 'POST', status: 500, body: { detail: 'Unit not found' } });
    const component = page();
    await assert.rejects(component.saveAndRestartService(), { message: 'Unit not found' });
    assert.equal(component.saveRestartState, 'idle');
});

test('the status box shows the unit of the page', async t => {
    const browser = installBrowser({
        services: [
            { name: 'radiotracking', active: false, enabled: false, status: 'dead' },
            { name: 'tsschedule', active: true, enabled: true, status: 'running', uptime: '5m' },
        ],
    });
    t.after(browser.restore);
    const component = page();
    await component.loadServiceStatus();
    assert.deepEqual(component.serviceStatus, { active: true, enabled: true, status: 'running', uptime: '5m' });
    assert.equal(component.serviceStatusLoading, false);
});

test('the status refreshes only on the page\'s own tab, and not in server mode', async t => {
    const browser = installBrowser({
        hash: '#settings/schedule',
        services: [{ name: 'tsschedule', active: true, enabled: true, status: 'running' }],
    });
    t.after(browser.restore);
    t.mock.timers.enable({ apis: ['setInterval', 'setTimeout'] });
    const component = page({}, { ...OPTIONS, refreshSeconds: 10, refreshOn: ['settings', 'schedule'] });
    const statusRequests = () => browser.calls.filter(c => c.url.endsWith('/api/systemd/services')).length;

    await component.startServiceStatusRefresh();
    t.mock.timers.tick(10000);
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(statusRequests(), 1, 'on settings/schedule');

    browser.window.location.hash = '#settings/network';
    component.serviceStatusLoading = false;
    t.mock.timers.tick(10000);
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(statusRequests(), 1, 'not on another subtab');

    browser.window.location.hash = '#settings/schedule';
    serverModeManager.enabled = true;
    t.mock.timers.tick(10000);
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(statusRequests(), 1, 'not in server mode');
});

test('init loads, starts the refresh and reloads when the config group changes; destroy stops all of it', async t => {
    const browser = installBrowser();
    t.after(browser.restore);
    t.mock.timers.enable({ apis: ['setInterval', 'setTimeout'] });
    const component = page();

    const started = component.init();
    t.mock.timers.tick(0);
    await started;
    assert.equal(component.loads(), 1);
    assert.equal(browser.listenerCount('config-group-changed'), 1);

    browser.window.dispatchEvent(new globalThis.CustomEvent('config-group-changed'));
    assert.equal(component.loads(), 2);

    component.destroy();
    assert.equal(browser.listenerCount('config-group-changed'), 0);
    browser.window.dispatchEvent(new globalThis.CustomEvent('config-group-changed'));
    assert.equal(component.loads(), 2);
});

test('a component destroyed while init() was loading starts nothing', async t => {
    const browser = installBrowser();
    t.after(browser.restore);
    t.mock.timers.enable({ apis: ['setInterval', 'setTimeout'] });
    const component = page();

    const started = component.init();
    component.destroy(); // tab left during the start-up pause
    t.mock.timers.tick(0);
    await started;
    assert.equal(browser.listenerCount('config-group-changed'), 0);
});

test('showMessage: error flag, toast type strings, and the event fallback without toasts', t => {
    const browser = installBrowser();
    t.after(browser.restore);
    const component = page({}, { ...OPTIONS, errorTitle: 'Schedule Failed' });

    component.showMessage('fine');
    component.showMessage('broken', true);
    component.showMessage('also broken', 'error');
    component.showMessage('also fine', 'success');
    assert.deepEqual(browser.toasts, [
        { message: 'fine', type: 'success', title: 'Schedule Configuration' },
        { message: 'broken', type: 'error', title: 'Schedule Failed' },
        { message: 'also broken', type: 'error', title: 'Schedule Failed' },
        { message: 'also fine', type: 'success', title: 'Schedule Configuration' },
    ]);

    browser.window.toastManager = undefined;
    component.showMessage('no toasts here', true);
    assert.deepEqual(browser.events, ['show-message']);
});

test('service actions use the page\'s message display', async t => {
    const browser = installBrowser();
    t.after(browser.restore);
    t.mock.timers.enable({ apis: ['setTimeout'] });
    browser.respond({ match: /systemd\/action/, method: 'POST', body: { message: 'restarted' } });
    const component = page();

    await component.performAction('tsschedule', 'restart');

    assert.deepEqual(browser.calls[0].body, { service: 'tsschedule', action: 'restart' });
    assert.equal(browser.toasts[0].message, 'restarted');
    assert.equal(component.getServiceRestartState('tsschedule'), 'restarted');
    assert.equal(component.actionLoading, false);
});

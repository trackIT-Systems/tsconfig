// The cards of the status page: each loads and shows its own data.
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { audioStreamPlayer } from '../../app/static/js/components/audioStreamPlayer.js';
import { powerSummary, recentBlock } from '../../app/static/js/components/powerSummary.js';
import { radiotrackingSummary } from '../../app/static/js/components/radiotrackingSummary.js';
import { servicesList } from '../../app/static/js/components/servicesList.js';
import { soundscapepipeSummary } from '../../app/static/js/components/soundscapepipeSummary.js';
import { statusPage } from '../../app/static/js/components/statusPage.js';
import { serverModeManager } from '../../app/static/js/managers/serverModeManager.js';
import { serviceManager } from '../../app/static/js/managers/serviceManager.js';
import { systemConfigManager } from '../../app/static/js/managers/systemConfigManager.js';
import { formatBytes, formatUptime } from '../../app/static/js/utils/format.js';
import { installBrowser } from './helpers.mjs';

const tick = () => new Promise(resolve => setImmediate(resolve));
const statusRefreshed = availableServices =>
    new globalThis.CustomEvent('status-refreshed', { detail: { availableServices } });

test('format helpers', () => {
    assert.equal(formatUptime(0), 'N/A');
    assert.equal(formatUptime(59 * 60), '59m');
    assert.equal(formatUptime(2 * 3600 + 5 * 60), '2h 5m');
    assert.equal(formatUptime(90061), '1d 1h 1m');
    assert.equal(formatBytes(0), '0 B');
    assert.equal(formatBytes(1536), '1.5 KB');
    assert.equal(formatBytes(NaN), '0 B');
});

test('servicesList shows what serviceManager holds, and follows its updates', async t => {
    const browser = installBrowser({ services: [{ name: 'a', active: true, expert: false }, { name: 'b', active: true, expert: true }] });
    t.after(browser.restore);
    const subscribers = serviceManager.subscribers.size;
    const list = servicesList();
    list.init();
    assert.equal(list.servicesLoading, true);
    assert.equal(serviceManager.subscribers.size, subscribers + 1);

    await serviceManager.getServices(true); // the status page's refresh
    assert.deepEqual(list.services.map(s => s.name), ['a', 'b']);
    assert.equal(list.servicesLoading, false);
    assert.deepEqual(list.getFilteredServices(false).map(s => s.name), ['a']);
    assert.deepEqual(list.getFilteredServices(true).map(s => s.name), ['a', 'b']);

    list.destroy();
    assert.equal(serviceManager.subscribers.size, subscribers, 'unsubscribed');
});

test('servicesList does not spin for ever if the refresh failed', t => {
    const browser = installBrowser();
    t.after(browser.restore);
    const list = servicesList();
    list.init();
    browser.window.dispatchEvent(statusRefreshed([]));
    assert.equal(list.servicesLoading, false);
});

test('servicesList makes no request of its own on a server', t => {
    const browser = installBrowser();
    t.after(browser.restore);
    serverModeManager.enabled = true;
    const list = servicesList();
    list.init();
    assert.equal(list.servicesLoading, false);
    assert.deepEqual(browser.calls, []);
});

test('servicesList: an action refreshes the units from the server, not from the cache', async t => {
    const browser = installBrowser({ services: [{ name: 'a', active: true }] });
    t.after(browser.restore);
    t.mock.timers.enable({ apis: ['setTimeout'] });
    const list = servicesList();
    list.init();
    await serviceManager.getServices(true);
    browser.calls.length = 0;

    await list.performAction('a', 'restart');
    t.mock.timers.tick(1000);
    await tick();

    assert.deepEqual(browser.calls.map(c => `${c.method} ${c.url}`), ['POST /api/systemd/action', 'GET /api/systemd/services']);
    assert.equal(browser.toasts[0].type, 'success');
});

test('summaries load their config when the status page has refreshed and the service exists', async t => {
    const browser = installBrowser();
    t.after(browser.restore);
    browser.respond({ match: /api\/radiotracking$/, method: 'GET', body: { 'rtl-sdr': { device: ['0'] } } });
    const card = radiotrackingSummary();
    card.init();
    assert.equal(card.radiotrackingConfig, null);

    browser.window.dispatchEvent(statusRefreshed(['radiotracking']));
    await tick();
    assert.deepEqual(card.radiotrackingConfig, { 'rtl-sdr': { device: ['0'] } });

    // the service is gone: the card is emptied, nothing is requested
    browser.calls.length = 0;
    browser.window.dispatchEvent(statusRefreshed(['schedule']));
    await tick();
    assert.equal(card.radiotrackingConfig, null);
    assert.deepEqual(browser.calls, []);
});

test('a summary whose config cannot be read is empty', async t => {
    const browser = installBrowser();
    t.after(browser.restore);
    browser.respond({ match: /api\/soundscapepipe$/, method: 'GET', status: 404, body: {} });
    const card = soundscapepipeSummary();
    card.init();
    browser.window.dispatchEvent(statusRefreshed(['soundscapepipe']));
    await tick();
    assert.equal(card.soundscapepipeConfig, null);
    assert.equal(card.soundscapepipeLoading, false);
});

test('summaries stop listening when destroyed', t => {
    const browser = installBrowser();
    t.after(browser.restore);
    const card = soundscapepipeSummary();
    card.init();
    assert.equal(browser.listenerCount('status-refreshed'), 1);
    card.destroy();
    assert.equal(browser.listenerCount('status-refreshed'), 0);
});

test('soundscapepipe summary: detector tasks and groups', t => {
    const browser = installBrowser();
    t.after(browser.restore);
    const card = soundscapepipeSummary();
    assert.deepEqual(card.getDetectorTasks('birdedge'), []);
    assert.deepEqual(card.getRecordingGroups(), []);
    card.soundscapepipeConfig = {
        detectors: { birdedge: { tasks: [{ name: 'dawn' }, {}] }, yolobat: {} },
        groups: { birds: {}, bats: {} },
    };
    assert.deepEqual(card.getDetectorTasks('birdedge'), ['dawn', 'Unnamed']);
    assert.deepEqual(card.getDetectorTasks('yolobat'), []);
    assert.deepEqual(card.getRecordingGroups(), ['birds', 'bats']);
});

test('soundscapepipe summary follows the unit through serviceManager', async t => {
    const browser = installBrowser({ services: [{ name: 'soundscapepipe', active: false }] });
    t.after(browser.restore);
    const card = soundscapepipeSummary();
    card.init();
    await serviceManager.getServices(true);
    assert.equal(card.soundscapepipeService.active, false);
});

test('statusPage announces each refresh, with the available services, even when it failed', async t => {
    const browser = installBrowser();
    t.after(browser.restore);
    browser.respond({ match: /api\/system-status/, method: 'GET', status: 500, body: {} });
    const page = statusPage();
    page.availableServices = ['radiotracking'];
    const announced = [];
    browser.window.addEventListener('status-refreshed', event => announced.push(event.detail));

    await page.refreshStatus();

    assert.deepEqual(announced, [{ availableServices: ['radiotracking'] }]);
    assert.equal(page.loading, false);
    assert.equal(browser.toasts[0].type, 'error');
});

test('statusPage does not start in server mode', async t => {
    const browser = installBrowser();
    t.after(browser.restore);
    serverModeManager.enabled = true;
    await statusPage().initStatus();
    assert.deepEqual(browser.calls, []);
});

test('audio player: checks at start, asks the stream, and stops asking when destroyed', async t => {
    const browser = installBrowser();
    t.after(browser.restore);
    t.mock.timers.enable({ apis: ['setInterval', 'setTimeout'] });
    let inactive = false;
    const player = audioStreamPlayer(() => inactive);

    player.init();
    await tick();
    assert.equal(player.streamAvailable, true);
    assert.equal(player.checkingStream, false);
    assert.equal(browser.calls.filter(c => c.url === '/stream.mp3').length, 1);

    t.mock.timers.tick(5000);
    await tick();
    assert.equal(browser.calls.filter(c => c.url === '/stream.mp3').length, 2);

    // a service known to be inactive is not asked for a stream
    inactive = true;
    t.mock.timers.tick(5000);
    await tick();
    assert.equal(browser.calls.filter(c => c.url === '/stream.mp3').length, 2);
    assert.equal(player.streamAvailable, false);

    player.destroy();
    inactive = false;
    t.mock.timers.tick(15000);
    assert.equal(browser.calls.filter(c => c.url === '/stream.mp3').length, 2);
});

test('audio player: periodic checks do not flash the "checking" state, manual ones do', async t => {
    const browser = installBrowser();
    t.after(browser.restore);
    t.mock.timers.enable({ apis: ['setInterval', 'setTimeout'] });
    const player = audioStreamPlayer();
    player.init();
    await tick();

    t.mock.timers.tick(5000); // periodic
    assert.equal(player.checkingStream, false);
    await tick();

    const manual = player.checkStreamAvailability(true);
    assert.equal(player.checkingStream, true);
    await manual;
    assert.equal(player.checkingStream, false);
});

test('audio player: pausing disconnects after 5 s, playing reconnects', t => {
    const browser = installBrowser();
    t.after(browser.restore);
    t.mock.timers.enable({ apis: ['setTimeout'] });
    const audio = { paused: false, src: '/stream.mp3', pause() { this.paused = true; }, load() {}, play: () => Promise.resolve() };
    const player = audioStreamPlayer();
    player.$refs = { audioPlayer: audio };

    player.handleAudioPause();
    t.mock.timers.tick(4999);
    assert.equal(player.audioConnected, true);
    t.mock.timers.tick(1);
    assert.equal(player.audioConnected, false);

    player.handleAudioPlay(); // the user presses play on the disconnected stream
    assert.equal(player.audioConnected, true);
    assert.match(audio.src, /^\/stream\.mp3\?\d+$/);
});

test('audio player: playing before the 5 s are over keeps the connection', t => {
    const browser = installBrowser();
    t.after(browser.restore);
    t.mock.timers.enable({ apis: ['setTimeout'] });
    const player = audioStreamPlayer();
    player.$refs = { audioPlayer: { pause() {}, load() {}, play: () => Promise.resolve() } };
    player.handleAudioPause();
    t.mock.timers.tick(3000);
    player.handleAudioPlay();
    t.mock.timers.tick(10000);
    assert.equal(player.audioConnected, true);
});

// A smartsolard `cached` reply (as on tsos-vhf-17f33a94): adverts a moment
// ago, GATT 40 s ago, no cable
const value = (name, friendly, unit, v) => ({ name, friendly, unit, value: v });
const smartsolarCached = ({ advertAge = 0.2, gattAge = 40 } = {}) => ({
    id: 1,
    ok: true,
    result: {
        device: 'HQ22294ZGEU',
        blocks: {
            advert: {
                enabled: true, age_s: advertAge, stale: false,
                values: [value('device_state', 'Device state', '', 'off'), value('battery_voltage', 'Battery voltage', 'V', 13.3), value('pv_power', 'PV power', 'W', 0)],
            },
            gatt: {
                enabled: true, age_s: gattAge, stale: false,
                values: [value('device_state', 'Device state', '', 'bulk'), value('battery_voltage', 'Battery voltage', 'V', 13.31), value('panel_power', 'Panel power', 'W', 12), value('firmware_version', 'Firmware version', '', '00640100')],
            },
            vedirect: { enabled: true, age_s: null, stale: true, values: [] },
        },
    },
});

test('a power card asks its daemon for the cached data only', async t => {
    const browser = installBrowser();
    t.after(browser.restore);
    browser.respond({ match: /api\/power\/smartsolar$/, method: 'POST', body: smartsolarCached() });
    const card = powerSummary('smartsolar');
    card.init();
    browser.window.dispatchEvent(statusRefreshed([]));
    await tick();
    assert.deepEqual(browser.calls.map(c => [c.method, c.url, c.body]), [
        ['POST', '/api/power/smartsolar', { cmd: 'cached', args: { block: 'all' } }],
    ]);
});

test('a power card shows the best recent block: GATT before the adverts', async t => {
    const browser = installBrowser();
    t.after(browser.restore);
    browser.respond({ match: /api\/power\/smartsolar$/, method: 'POST', body: smartsolarCached() });
    const card = powerSummary('smartsolar');
    card.init();
    browser.window.dispatchEvent(statusRefreshed([]));
    await tick();
    assert.equal(card.powerBlock.name, 'gatt');
    assert.equal(card.powerDevice, 'HQ22294ZGEU');
    // the groups in their own order, with the GATT name for PV power; empty groups and other values left out
    assert.deepEqual(card.powerGroups.map(g => [g.title, g.values.map(v => v.name)]), [
        ['Battery', ['battery_voltage']],
        ['Solar Panel', ['panel_power']],
        ['Controller', ['device_state', 'device', 'connection']],
    ]);
    const controller = card.powerGroups.at(-1).values;
    assert.deepEqual(controller.slice(1).map(v => [v.friendly, v.value]), [['Serial number', 'HQ22294ZGEU'], ['Connection', 'Bluetooth GATT']]);
});

test('a power card falls back to the adverts when GATT is older than power_recent_s', async t => {
    const browser = installBrowser();
    t.after(browser.restore);
    systemConfigManager.config = { status_refresh_interval: 5, power_recent_s: 30 };
    browser.respond({ match: /api\/power\/smartsolar$/, method: 'POST', body: smartsolarCached({ gattAge: 40 }) });
    const card = powerSummary('smartsolar');
    card.init();
    browser.window.dispatchEvent(statusRefreshed([]));
    await tick();
    assert.equal(card.powerBlock.name, 'advert');
    assert.deepEqual(card.powerGroups.flatMap(g => g.values.map(v => v.name)), ['battery_voltage', 'pv_power', 'device_state', 'device', 'connection']);
    assert.equal(card.powerGroups.at(-1).values.at(-1).value, 'Bluetooth advertisements');
});

test('a power card is empty when its data is old, its daemon is absent or refuses', async t => {
    const browser = installBrowser();
    t.after(browser.restore);
    systemConfigManager.config = { status_refresh_interval: 5, power_recent_s: 300 };
    const card = powerSummary('smartsolar');
    card.init();
    for (const body of [smartsolarCached({ advertAge: 301, gattAge: 400 }), null, { id: 1, ok: false, error: { type: 'x', message: 'y' } }]) {
        card.powerBlock = { name: 'previous', age_s: 0, values: [] };
        browser.respond({ match: /api\/power\/smartsolar$/, method: 'POST', body });
        browser.window.dispatchEvent(statusRefreshed([]));
        await tick();
        assert.equal(card.powerBlock, null);
        assert.equal(card.powerDevice, null);
        assert.equal(card.powerLoading, false);
    }
});

test('refresh has the daemon read the block shown, then reloads the cached data', async t => {
    const browser = installBrowser();
    t.after(browser.restore);
    browser.respond({ match: /api\/power\/smartsolar$/, method: 'POST', body: smartsolarCached() });
    const card = powerSummary('smartsolar');
    card.init();
    browser.window.dispatchEvent(statusRefreshed([]));
    await tick();
    browser.calls.length = 0;

    await card.readNow();
    assert.deepEqual(browser.calls.map(c => c.body), [
        { cmd: 'read', args: { block: 'gatt' } },
        { cmd: 'cached', args: { block: 'all' } },
    ]);
    assert.equal(card.powerReading, false);
    assert.equal(card.powerReadError, null);
});

test('a failed refresh says why, and the card keeps the cached data', async t => {
    const browser = installBrowser();
    t.after(browser.restore);
    const card = powerSummary('smartsolar');
    card.init();
    browser.respond({ match: /api\/power\/smartsolar$/, method: 'POST', body: smartsolarCached() });
    browser.window.dispatchEvent(statusRefreshed([]));
    await tick();

    // the fake answers the read and the reload alike: an error envelope, then the cached data again
    const replies = [{ id: 1, ok: false, error: { type: 'not_connected', message: 'no GATT link' } }, smartsolarCached()];
    const fetch = globalThis.fetch;
    globalThis.fetch = async (url, options) => {
        await fetch(url, options);
        return { ok: true, status: 200, json: async () => replies.shift() };
    };
    await card.readNow();
    assert.equal(card.powerReadError, 'no GATT link');
    assert.equal(card.powerBlock.name, 'gatt');
});

test('no refresh without a block on screen', async t => {
    const browser = installBrowser();
    t.after(browser.restore);
    const card = powerSummary('solarlife');
    await card.readNow();
    assert.deepEqual(browser.calls, []);
});

test('recentBlock: first block with values within the window, in the given order', () => {
    const blocks = { a: { age_s: 50, values: [1] }, b: { age_s: 1, values: [1] }, c: { age_s: 1, values: [] }, d: { age_s: null, values: [1] } };
    assert.equal(recentBlock(blocks, ['c', 'd', 'a', 'b'], 60).name, 'a');
    assert.equal(recentBlock(blocks, ['c', 'd', 'a', 'b'], 10).name, 'b');
    assert.equal(recentBlock(blocks, ['c', 'd'], 60), null);
    assert.equal(recentBlock(undefined, ['a'], 60), null);
});

test('power cards stop listening when destroyed', t => {
    const browser = installBrowser();
    t.after(browser.restore);
    const card = powerSummary('solarlife');
    card.init();
    assert.equal(browser.listenerCount('status-refreshed'), 1);
    card.destroy();
    assert.equal(browser.listenerCount('status-refreshed'), 0);
});

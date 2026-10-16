// The cards of the status page: each loads and shows its own data.
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { audioStreamPlayer } from '../../app/static/js/components/audioStreamPlayer.js';
import { radiotrackingSummary } from '../../app/static/js/components/radiotrackingSummary.js';
import { servicesList } from '../../app/static/js/components/servicesList.js';
import { soundscapepipeSummary } from '../../app/static/js/components/soundscapepipeSummary.js';
import { statusPage } from '../../app/static/js/components/statusPage.js';
import { serverModeManager } from '../../app/static/js/managers/serverModeManager.js';
import { serviceManager } from '../../app/static/js/managers/serviceManager.js';
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

// The live dashboard: the manager that holds what the broker sends, and its three panels,
// against a fake browser, broker and chart.
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { configManager } from '../../app/static/js/components/configManager.js';
import { liveFrequencyUsage } from '../../app/static/js/components/liveFrequencyUsage.js';
import { liveSignalDetail } from '../../app/static/js/components/liveSignalDetail.js';
import { liveSignalPower } from '../../app/static/js/components/liveSignalPower.js';
import { liveTabs } from '../../app/static/js/components/liveTabs.js';
import { liveManager } from '../../app/static/js/managers/liveManager.js';
import { LIVE_TOPICS, WINDOW_MS, colorFor, padRange } from '../../app/static/js/utils/liveSignals.js';
import { installBrowser } from './helpers.mjs';

const HOST = 'tsos-test';
const MATCHED = `${HOST}/radiotracking/matched/json`;
const CALIBRATED = `${HOST}/radiotracking/calibrated/json`;
const rawTopic = device => `${HOST}/radiotracking/device/${device}/json`;

// A message of the station, detected `agoMs` ago
const matched = (agoMs, fields, extra = {}) => JSON.stringify({
    Time: new Date(Date.now() - agoMs).toISOString(), Frequency: 150233515.625, Duration: 0.02, ...fields, ...extra,
});
const rawMessage = (agoMs, device, fields) => JSON.stringify({
    Device: String(device),
    Time: new Date(Date.now() - agoMs).toISOString(),
    Frequency: 150233515.625,
    Duration: 0.04,
    'max (dBW)': -55,
    'snr (dB)': 40,
    ...fields,
});

class FakeClient {
    constructor(url, options) {
        this.url = url;
        this.options = options;
        this.handlers = {};
        this.subscribed = [];
        this.unsubscribed = [];
        this.connected = false;
        this.ended = false;
    }
    on(event, handler) { this.handlers[event] = handler; }
    emit(event, ...args) { this.handlers[event]?.(...args); }
    subscribe(topic) { this.subscribed.push(topic); }
    unsubscribe(topic) { this.unsubscribed.push(topic); }
    end() { this.ended = true; this.connected = false; }
    open() { this.connected = true; this.emit('connect'); }
    send(topic, payload) { this.emit('message', topic, Buffer.from(payload)); }
}

class FakeChart {
    static instances = [];
    constructor(options, data) {
        this.options = options;
        this.data = data;
        this.destroyed = false;
        this.scales = [];
        this.redraws = 0;
        FakeChart.instances.push(this);
    }
    setScale(scale, range) { this.scales.push({ scale, ...range }); }
    redraw() { this.redraws++; }
    setSize() {}
    destroy() { this.destroyed = true; }
    get live() { return FakeChart.instances.filter(chart => !chart.destroyed); }
}

function setup(t) {
    const browser = installBrowser();
    FakeChart.instances = [];
    const clients = [];
    Object.assign(browser.window, {
        location: { hash: '', search: '', protocol: 'http:', host: 'station.local' },
        mqtt: { connect: (url, options) => { const c = new FakeClient(url, options); clients.push(c); return c; } },
        uPlot: FakeChart,
    });
    const saved = { document: globalThis.document, getComputedStyle: globalThis.getComputedStyle };
    globalThis.document = { hidden: false };
    globalThis.getComputedStyle = () => ({ color: 'rgb(0, 0, 0)' });
    liveManager.reset();
    t.after(() => {
        liveManager.reset();
        globalThis.document = saved.document;
        globalThis.getComputedStyle = saved.getComputedStyle;
        browser.restore();
    });

    /** A panel on the page, as Alpine would start it. */
    const mount = async factory => {
        const panel = factory();
        panel.$refs = { chart: { clientWidth: 600 } };
        await panel.init();
        t.after(() => panel.destroy());
        return panel;
    };
    return { browser, clients, mount };
}

const lastChart = () => FakeChart.instances.filter(chart => !chart.destroyed).at(-1);

// --- the manager -------------------------------------------------------------

test('acquire opens one connection to /mqtt on the page\'s host and subscribes to what is wanted', t => {
    const { clients } = setup(t);
    const release = liveManager.acquire(['matched']);
    assert.equal(clients.length, 1);
    assert.equal(clients[0].url, 'ws://station.local/mqtt');
    assert.equal(liveManager.status, 'connecting');
    assert.equal(clients[0].options.resubscribe, false, 'subscribed on every connect by the manager itself');

    clients[0].open();
    assert.equal(liveManager.status, 'connected');
    assert.deepEqual(clients[0].subscribed, [LIVE_TOPICS.matched]);
    release();
});

test('panels share the connection and a topic; it closes with the last of them', t => {
    const { clients } = setup(t);
    const first = liveManager.acquire(['matched', 'calibrated']);
    clients[0].open();
    const second = liveManager.acquire(['matched']);
    assert.equal(clients.length, 1, 'one connection');
    assert.deepEqual(clients[0].subscribed, [LIVE_TOPICS.matched, LIVE_TOPICS.calibrated], 'matched is not subscribed twice');

    first();
    assert.deepEqual(clients[0].unsubscribed, [LIVE_TOPICS.calibrated], 'only what nobody wants any more');
    assert.equal(clients[0].ended, false, 'matched is still wanted');

    first(); // releasing twice does nothing
    second();
    assert.equal(clients[0].ended, true);
    assert.equal(liveManager.status, 'offline');
});

test('a reconnect subscribes to what is wanted by then', t => {
    const { clients } = setup(t);
    const raw = liveManager.acquire(['raw']);
    const matchedOnly = liveManager.acquire(['matched']);
    clients[0].open();
    clients[0].connected = false;
    clients[0].emit('close');
    assert.equal(liveManager.status, 'offline');

    raw(); // while the connection is down
    clients[0].emit('reconnect');
    assert.equal(liveManager.status, 'connecting');
    clients[0].open();
    assert.deepEqual(clients[0].subscribed.slice(-1), [LIVE_TOPICS.matched], 'not the raw signals that nobody wants now');
    matchedOnly();
});

test('ingest sends a message to the window of its kind', t => {
    setup(t);
    assert.equal(liveManager.ingest(MATCHED, matched(0, { 0: -60, 1: -62 })), true);
    assert.equal(liveManager.ingest(CALIBRATED, matched(0, { 0: -97 })), true);
    assert.equal(liveManager.ingest(rawTopic(3), rawMessage(0, 3)), true);
    assert.equal(liveManager.windows.matched.length, 1);
    assert.equal(liveManager.windows.calibrated.length, 1);
    assert.equal(liveManager.windows.raw.length, 1);
    assert.equal(liveManager.windows.raw.events[0].key, '3');
    assert.equal(liveManager.ingest('other/topic', '{}'), false);
});

test('a calibrated message without a calibrated SDR adds nothing, but is counted', t => {
    setup(t);
    assert.equal(liveManager.ingest(CALIBRATED, matched(0, { 0: null, 1: null })), false);
    assert.equal(liveManager.windows.calibrated.length, 0);
    assert.equal(liveManager.received.calibrated, 1);
    assert.equal(liveManager.ingest(MATCHED, 'garbage'), false);
    assert.equal(liveManager.received.matched, 1);
});

test('giving a source back keeps what was read (that is Stop); discard clears what nobody reads', t => {
    const { clients } = setup(t);
    const matchedRelease = liveManager.acquire(['matched']);
    const rawRelease = liveManager.acquire(['raw']);
    clients[0].open();
    clients[0].send(MATCHED, matched(1000, { 0: -60 }));
    clients[0].send(rawTopic(0), rawMessage(1000, 0));
    rawRelease();
    assert.equal(liveManager.windows.raw.length, 1, 'stopping reading keeps what was read');

    liveManager.discard(['matched', 'raw', 'calibrated']);
    assert.equal(liveManager.windows.matched.length, 1, 'matched is still read, and drawn');
    assert.equal(liveManager.windows.raw.length, 0, 'raw is not: gone');
    assert.equal(liveManager.received.raw, 0);

    matchedRelease();
    liveManager.discard(['matched']);
    assert.equal(liveManager.windows.matched.length, 0);
});

test('the libraries are loaded once, and again after a failure', async t => {
    const { browser } = setup(t);
    delete browser.window.mqtt;
    delete browser.window.uPlot;
    const loaded = [];
    let fail = true;
    const saved = globalThis.document;
    const quiet = globalThis.console.error;
    globalThis.document = {
        hidden: false,
        head: { appendChild: element => setImmediate(() => {
            loaded.push(element.src ?? element.href);
            if (fail) return element.onerror?.();
            if (element.src?.includes('uPlot')) browser.window.uPlot = FakeChart;
            if (element.src?.includes('mqtt')) browser.window.mqtt = { connect: () => new FakeClient() };
            element.onload?.();
        }) },
        createElement: () => ({}),
    };
    t.after(() => { globalThis.document = saved; globalThis.console.error = quiet; });

    await assert.rejects(liveManager.loadLibraries());
    fail = false;
    loaded.length = 0;
    await liveManager.loadLibraries();
    await liveManager.loadLibraries();
    assert.equal(loaded.filter(url => url.endsWith('uPlot.iife.min.js')).length, 1);
    assert.equal(loaded.filter(url => url.endsWith('mqtt.min.js')).length, 1);
    assert.ok(loaded.some(url => url.endsWith('uPlot.min.css')));
});

// --- Radiotracking: Signal Power ---------------------------------------------

test('Signal Power reads matched and calibrated, and draws the chosen one', async t => {
    const { clients, mount } = setup(t);
    const panel = await mount(liveSignalPower);
    clients[0].open();
    assert.deepEqual(clients[0].subscribed, [LIVE_TOPICS.matched, LIVE_TOPICS.calibrated]);
    assert.equal(panel.status, 'connected');

    clients[0].send(MATCHED, matched(500, { 0: -60, 1: -70 }));
    clients[0].send(CALIBRATED, matched(500, { 0: -97.4 }));
    panel.tick();
    assert.equal(panel.count, 1);
    assert.deepEqual(panel.legend.map(item => item.label), ['SDR 0', 'SDR 1']);
    assert.equal(panel.yLabel, 'Power (dBW)');
});

test('Signal Power: what was read survives a switch between matched and calibrated', async t => {
    const { clients, mount } = setup(t);
    const panel = await mount(liveSignalPower);
    clients[0].open();
    clients[0].send(MATCHED, matched(500, { 0: -60 }));
    clients[0].send(MATCHED, matched(400, { 0: -61 }));
    clients[0].send(CALIBRATED, matched(300, { 0: -97 }));

    panel.setSource('calibrated');
    assert.equal(panel.yLabel, 'Power (dBm)');
    assert.equal(panel.count, 1, 'the calibrated signal that came while matched was shown');
    assert.deepEqual(panel.legend.map(item => item.label), ['SDR 0']);

    clients[0].send(MATCHED, matched(100, { 0: -62 })); // arrives while calibrated is shown
    panel.setSource('matched');
    assert.equal(panel.count, 3, 'nothing was lost, in either direction');
    assert.equal(clients[0].unsubscribed.length, 0, 'and nothing was unsubscribed');
    assert.equal(FakeChart.instances.length, 3, 'the chart is rebuilt for the other unit');
    assert.match(FakeChart.instances[1].options.axes[1].label, /dBm/);
});

test('Signal Power: switching to something unknown, or to the same, does nothing', async t => {
    const { mount } = setup(t);
    const panel = await mount(liveSignalPower);
    const charts = FakeChart.instances.length;
    panel.setSource('matched');
    panel.setSource('nonsense');
    assert.equal(panel.source, 'matched');
    assert.equal(FakeChart.instances.length, charts);
});

test('Signal Power: the hint for a station without calibration comes after a while of silence', async t => {
    const { clients, mount } = setup(t);
    const panel = await mount(liveSignalPower);
    clients[0].open();
    panel.setSource('calibrated');
    assert.equal(panel.calibratedHint, false, 'too early');
    panel.now = liveManager.subscribedAt.calibrated + 31_000;
    assert.equal(panel.calibratedHint, true);

    clients[0].send(CALIBRATED, matched(0, { 0: -40 }));
    panel.tick();
    assert.equal(panel.calibratedHint, false, 'something arrived');

    panel.setSource('matched');
    panel.now = liveManager.subscribedAt.calibrated + 31_000;
    assert.equal(panel.calibratedHint, false, 'only while the calibrated source is shown');
});

test('Signal Power: the power in the tooltip is in the unit of the source', async t => {
    const { clients, mount } = setup(t);
    const panel = await mount(liveSignalPower);
    clients[0].open();
    clients[0].send(MATCHED, matched(1000, { 0: -60, 1: -70.25 }));
    const event = liveManager.windows.matched.events[0];
    const rows = panel.tipRows(event).map(row => row.text);
    assert.deepEqual(rows, ['Frequency: 150.234 MHz', 'Duration: 20.0 ms', 'SDR 0: -60.0 dBW', 'SDR 1: -70.3 dBW']);
    assert.equal(panel.tipRows(event)[2].color, colorFor('0'));
});

// --- Radiotracking: Frequency Usage --------------------------------------------

test('Frequency Usage draws a dot per matched signal at its frequency, without a legend', async t => {
    const { clients, mount } = setup(t);
    const panel = await mount(liveFrequencyUsage);
    clients[0].open();
    assert.deepEqual(clients[0].subscribed, [LIVE_TOPICS.matched]);
    clients[0].send(MATCHED, matched(1000, { 0: -60, 1: -62 }));
    clients[0].send(MATCHED, matched(500, { 0: -60, 1: -62 }, { Frequency: 150100000 }));
    panel.tick();

    const ys = [];
    for (const event of liveManager.windows.matched.events) panel.forEachDot(event, (key, y) => ys.push(y));
    assert.deepEqual(ys, [150.233515625, 150.1], 'one dot per signal, in MHz, not one per SDR');
    assert.equal(panel.legend.length, 0, 'one series: no legend');
    assert.equal(panel.yLabel, 'Frequency (MHz)');
    const [low, high] = panel.yRange(150.1, 150.2335);
    assert.ok(low < 150.1 && high > 150.2335);
    assert.equal(panel.formatY(150.155000001), '150.155');
});

test('Frequency Usage and Signal Power share the connection and the matched signals', async t => {
    const { clients, mount } = setup(t);
    const power = await mount(liveSignalPower);
    const frequency = await mount(liveFrequencyUsage);
    assert.equal(clients.length, 1, 'one connection for both');
    clients[0].open();
    assert.deepEqual(clients[0].subscribed, [LIVE_TOPICS.matched, LIVE_TOPICS.calibrated], 'matched once');

    clients[0].send(MATCHED, matched(100, { 0: -60 }));
    power.tick();
    frequency.tick();
    assert.equal(power.count, 1);
    assert.equal(frequency.count, 1);

    frequency.destroy();
    assert.equal(clients[0].ended, false, 'closing one panel keeps the other connected');
    assert.deepEqual(clients[0].unsubscribed, [], 'and matched stays subscribed');
});

test('a panel that is left clears what it showed, so coming back does not show a gap', async t => {
    const { clients, mount } = setup(t);
    const first = await mount(liveFrequencyUsage);
    clients[0].open();
    clients[0].send(MATCHED, matched(60_000, { 0: -60 }));
    first.tick();
    assert.equal(first.count, 1);
    first.destroy();
    assert.equal(liveManager.windows.matched.length, 0, 'nothing is held while away');

    const second = await mount(liveFrequencyUsage);
    second.tick();
    assert.equal(second.count, 0, 'it starts empty, and fills from now on');
});

test('what another panel still reads and draws is not cleared when one goes away', async t => {
    const { clients, mount } = setup(t);
    const power = await mount(liveSignalPower);
    const frequency = await mount(liveFrequencyUsage);
    clients[0].open();
    clients[0].send(MATCHED, matched(1000, { 0: -60 }));
    clients[0].send(CALIBRATED, matched(1000, { 0: -97 }));

    frequency.destroy();
    assert.equal(liveManager.windows.matched.length, 1, 'Signal Power still draws matched');
    power.destroy();
    assert.equal(liveManager.windows.matched.length, 0);
    assert.equal(liveManager.windows.calibrated.length, 0, 'both of Signal Power\'s sources');
});

// --- Radiotracking: Signal Detail ----------------------------------------------

test('Signal Detail reads nothing until it is started', async t => {
    const { clients, mount } = setup(t);
    const panel = await mount(liveSignalDetail);
    assert.equal(clients.length, 0, 'no connection for a panel that reads nothing');
    assert.equal(panel.status, 'idle');
    assert.equal(panel.collecting, false);

    panel.toggleCollect();
    assert.equal(panel.collecting, true);
    assert.equal(clients.length, 1);
    assert.equal(panel.status, 'connecting');
    clients[0].open();
    assert.deepEqual(clients[0].subscribed, [LIVE_TOPICS.raw]);
    assert.equal(panel.status, 'connected');

    panel.toggleCollect();
    assert.equal(panel.status, 'idle');
    assert.equal(clients[0].ended, true, 'stopped: the connection is closed');
});

test('Signal Detail plots the chosen value of the raw signals, a series per SDR', async t => {
    const { clients, mount } = setup(t);
    const panel = await mount(liveSignalDetail);
    panel.toggleCollect();
    clients[0].open();
    clients[0].send(rawTopic(0), rawMessage(900, 0, { 'max (dBW)': -55, 'snr (dB)': 41 }));
    clients[0].send(rawTopic(1), rawMessage(800, 1, { 'max (dBW)': -66, 'snr (dB)': 32 }));
    panel.tick();
    assert.equal(panel.count, 2);
    assert.deepEqual(panel.legend.map(item => item.label), ['SDR 0', 'SDR 1']);

    const dots = () => {
        const found = [];
        for (const event of liveManager.windows.raw.events) panel.forEachDot(event, (key, y) => found.push([key, y]));
        return found;
    };
    assert.deepEqual(dots(), [['0', -55], ['1', -66]], 'max (dBW) by default');

    panel.setParam('snr (dB)');
    assert.deepEqual(dots(), [['0', 41], ['1', 32]]);
    assert.equal(panel.yLabel, 'snr (dB)');
    assert.equal(lastChart().options.axes[1].label, 'snr (dB)', 'the axis is relabelled');

    panel.setParam('Duration');
    assert.deepEqual(dots().map(([, y]) => y), [40, 40], 'duration in ms');
    assert.equal(panel.yLabel, 'Duration (ms)');
    panel.setParam('Frequency');
    assert.ok(Math.abs(dots()[0][1] - 150.233515625) < 1e-9, 'frequency in MHz');
});

test('Signal Detail offers the values the signals have, and any the station adds', async t => {
    const { clients, mount } = setup(t);
    const panel = await mount(liveSignalDetail);
    assert.ok(panel.paramOptions.some(option => option.name === 'snr (dB)'), 'the usual ones before any signal');
    assert.ok(panel.paramOptions.some(option => option.label === 'Frequency (MHz)'));

    panel.toggleCollect();
    clients[0].open();
    clients[0].send(rawTopic(0), rawMessage(100, 0, { 'bandwidth (Hz)': 4000 }));
    panel.tick();
    assert.ok(panel.paramOptions.some(option => option.name === 'bandwidth (Hz)'));
    const names = panel.paramOptions.map(option => option.name);
    assert.equal(new Set(names).size, names.length, 'no value twice');
});

test('Signal Detail: the tooltip names the SDR and lists the values of the signal', async t => {
    const { clients, mount } = setup(t);
    const panel = await mount(liveSignalDetail);
    panel.toggleCollect();
    clients[0].open();
    clients[0].send(rawTopic(2), rawMessage(100, 2, { 'max (dBW)': -55.123 }));
    const rows = panel.tipRows(liveManager.windows.raw.events[0]);
    assert.deepEqual(rows.map(row => row.text), [
        'SDR 2', 'Frequency: 150.234 MHz', 'Duration: 40.0 ms', 'max (dBW): -55.12', 'snr (dB): 40.00',
    ]);
    assert.equal(rows[0].color, colorFor('2'));
});

test('Signal Detail keeps collecting when the page is left and opened again, from an empty chart', async t => {
    const { clients, mount } = setup(t);
    const first = await mount(liveSignalDetail);
    first.toggleCollect();
    clients[0].open();
    clients[0].send(rawTopic(0), rawMessage(100, 0));
    first.destroy();

    const second = await mount(liveSignalDetail);
    assert.equal(second.collecting, true, 'what was chosen is kept while the page is open');
    assert.equal(clients.length, 2);
    second.tick();
    assert.equal(second.count, 0, 'the signals are not: they would come back with a gap');
});

test('Signal Detail: Stop keeps what was read on the chart, leaving clears it', async t => {
    const { clients, mount } = setup(t);
    const panel = await mount(liveSignalDetail);
    panel.toggleCollect();
    clients[0].open();
    clients[0].send(rawTopic(0), rawMessage(100, 0));
    clients[0].send(rawTopic(1), rawMessage(100, 1));
    panel.tick();
    assert.equal(panel.count, 2);

    panel.toggleCollect(); // Stop
    panel.tick();
    assert.equal(panel.count, 2, 'stopped, not cleared: it can be looked at');

    panel.destroy(); // leaving, while stopped
    assert.equal(liveManager.windows.raw.length, 0, 'even what was read before Stop');
});

test('Signal Detail cannot be started before the libraries are loaded', t => {
    setup(t);
    const panel = liveSignalDetail();
    panel.toggleCollect();
    assert.equal(panel.collecting, false);
});

// --- what the charts share ---------------------------------------------------

test('the time axis is 24 hours without a date', async t => {
    const { mount } = setup(t);
    await mount(liveFrequencyUsage);
    const axis = lastChart().options.axes[0];
    const time = new Date(2026, 9, 19, 14, 5, 9).getTime() / 1000;
    assert.deepEqual(axis.values(null, [time], 0, 80, 30), ['14:05:09'], 'with seconds for short steps');
    assert.deepEqual(axis.values(null, [time], 0, 80, 60), ['14:05'], 'without for a minute or more');
    assert.doesNotMatch(axis.values(null, [time], 0, 80, 30)[0], /[ap]m|\/|-/i);
});

test('the vertical axis fits the signals, and follows them', async t => {
    const { clients, mount } = setup(t);
    const panel = await mount(liveSignalPower);
    clients[0].open();
    const chart = lastChart();
    assert.deepEqual(chart.options.scales.y.range(), [-100, -20], 'before any signal');

    clients[0].send(MATCHED, matched(100, { 0: -59.152 }));
    panel.tick();
    const y = chart.scales.filter(scale => scale.scale === 'y').at(-1);
    assert.deepEqual([y.min, y.max], [-65, -54], 'one signal still has an axis');

    chart.scales.length = 0;
    panel.tick();
    assert.equal(chart.scales.length, 0, 'not set again when it has not changed');
});

test('the chart is redrawn once a second when there is no animation frame', async t => {
    const { mount } = setup(t);
    const panel = await mount(liveFrequencyUsage);
    const chart = lastChart();
    const before = chart.redraws;
    panel.tick();
    assert.equal(chart.redraws, before + 1);
    globalThis.document.hidden = true;
    panel.tick();
    assert.equal(chart.redraws, before + 1, 'not while the page is hidden');
});

test('the time axis glides with the clock on every frame, instead of jumping once a second', async t => {
    const { browser, mount } = setup(t);
    const frames = [];
    const cancelled = [];
    browser.window.requestAnimationFrame = callback => frames.push(callback) && frames.length;
    browser.window.cancelAnimationFrame = id => cancelled.push(id);
    const realNow = Date.now;
    let now = 1_000_000_000_000;
    Date.now = () => now;
    t.after(() => { Date.now = realNow; });

    const panel = await mount(liveFrequencyUsage);
    const chart = lastChart();
    assert.equal(chart.options.pxAlign, 0, 'not snapped to whole pixels');
    assert.equal(chart.options.legend.show, false, 'the legend is the page\'s');

    const frame = () => frames.splice(0).forEach(callback => callback());
    frame();
    assert.deepEqual(chart.scales.filter(s => s.scale === 'x')[0], { scale: 'x', min: now / 1000 - 300, max: now / 1000 });

    const redraws = chart.redraws;
    panel.tick();
    assert.equal(chart.redraws, redraws, 'the frames draw, the tick does not draw twice');

    now += 16;
    frame();
    assert.equal(chart.scales.filter(s => s.scale === 'x').length, 2, 'every frame moves the axis');

    globalThis.document.hidden = true;
    now += 5000;
    frame();
    assert.equal(chart.scales.filter(s => s.scale === 'x').length, 2, 'nothing is drawn while the page is hidden');

    globalThis.document.hidden = false;
    panel.destroy();
    assert.equal(cancelled.length, 1, 'the loop stops with the tab');
});

test('the dots are drawn per signal and series, their diameter showing the duration', async t => {
    const { clients, mount } = setup(t);
    const panel = await mount(liveSignalPower);
    clients[0].open();
    clients[0].send(MATCHED, matched(1000, { 0: -60 }, { Duration: 0.020 }));
    clients[0].send(MATCHED, matched(2000, { 0: -65, 1: -70 }, { Duration: 0.040 }));
    clients[0].send(MATCHED, matched(3000, { 1: -75 }, { Duration: 0.010 }));
    panel.tick();

    const arcs = [];
    const fills = [];
    const ctx = {
        save() {}, restore() {}, beginPath() {}, rect() {}, clip() {}, moveTo() {},
        arc: (x, y, radius) => arcs.push({ x, y, radius }),
        fill() { fills.push(ctx.fillStyle); },
    };
    const draw = (min, max) => lastChart().options.hooks.draw[0]({
        ctx,
        bbox: { left: 0, top: 0, width: 100, height: 100 },
        scales: { x: { min, max } },
        valToPos: (value, axis) => (axis === 'x' ? value : -value),
    });

    draw(0, Infinity);
    assert.deepEqual(
        arcs.map(a => [a.y, a.radius]).sort((a, b) => a[0] - b[0]),
        [[60, 3], [65, 6], [70, 6], [75, 1.5]],
        'one dot per SDR and signal (y is the power here); radius is half of dotSize(duration)',
    );
    assert.equal(fills.length, 4);
    assert.notEqual(fills[0], fills.at(-1), 'one colour per SDR');

    arcs.length = 0;
    draw(Date.now() / 1000 - 1.5, Infinity); // only the newest signal is in view
    assert.equal(arcs.length, 1, 'dots outside the time axis are not drawn');
});

test('many dots are drawn in one go per series', async t => {
    const { mount } = setup(t);
    const panel = await mount(liveSignalPower);
    const now = Date.now();
    for (let i = 0; i < 1600; i++) {
        liveManager.windows.matched.add({
            at: null, epochMs: now - i, frequency: 150e6, duration: 0.02, key: null, values: { 0: -60, 1: -70 },
        }, now);
    }
    panel.tick();

    let arcs = 0;
    let fills = 0;
    let moves = 0;
    const ctx = {
        save() {}, restore() {}, beginPath() {}, rect() {}, clip() {},
        moveTo() { moves++; },
        arc() { arcs++; },
        fill() { fills++; },
    };
    lastChart().options.hooks.draw[0]({
        ctx,
        bbox: { left: 0, top: 0, width: 100, height: 100 },
        scales: { x: { min: 0, max: Infinity } },
        valToPos: value => value,
    });
    assert.equal(arcs, 3200);
    assert.equal(moves, 3200, 'dots in one path must not be joined by lines');
    assert.equal(fills, 2, 'one fill for each SDR, not one for each dot');
});

test('the tooltip shows the dot under the cursor, and nothing when there is none', async t => {
    const { clients, mount } = setup(t);
    const panel = await mount(liveSignalPower);
    clients[0].open();
    clients[0].send(MATCHED, matched(1000, { 0: -60, 1: -70 }, { Duration: 0.020 }));
    const event = liveManager.windows.matched.events[0];

    const hover = (left, top, over = { offsetLeft: 70, offsetTop: 10, clientWidth: 500 }) =>
        lastChart().options.hooks.setCursor[0]({
            cursor: { left, top },
            scales: { x: { min: 0 } },
            valToPos: (value, axis) => (axis === 'x' ? 100 : (axis === 'y' ? (value === -60 ? 50 : 150) : 0)),
            over,
        });

    hover(102, 52); // on the dot of SDR 0
    assert.deepEqual(panel.tip.rows.map(row => row.text).slice(0, 3), ['Frequency: 150.234 MHz', 'Duration: 20.0 ms', 'SDR 0: -60.0 dBW']);
    assert.equal(panel.tip.left, 172, 'measured from the plot area, placed in the wrapper');
    assert.equal(panel.tip.top, 62);
    assert.equal(panel.tip.flip, false);
    assert.match(panel.tip.title, /^\d\d:\d\d:\d\d\.\d{3}$/, '24 hours');
    assert.equal(panel.tip.title.length, 12);

    hover(102, 100); // between the dots
    assert.equal(panel.tip, null);
    hover(300, 52); // far in x
    assert.equal(panel.tip, null);
    hover(-1, 52); // the cursor left the plot
    assert.equal(panel.tip, null);

    hover(102, 152); // the dot of SDR 1
    assert.ok(panel.tip.rows.length > 0);
    hover(102, 52, { offsetLeft: 70, offsetTop: 10, clientWidth: 150 }); // right half of a narrow plot
    assert.equal(panel.tip.flip, true, 'the tooltip goes left of the dot where there is no room');
    assert.ok(event);
});

test('a station clock that differs is said so, with how much', async t => {
    const { clients, mount } = setup(t);
    const panel = await mount(liveFrequencyUsage);
    clients[0].open();
    clients[0].send(MATCHED, matched(400, { 0: -60 }));
    panel.tick();
    assert.equal(panel.clockOff, false);
    assert.equal(panel.delayText, '0.4 s after its time');

    clients[0].send(MATCHED, matched(-90_000, { 0: -60 })); // detected 90 s in the future
    panel.tick();
    assert.equal(panel.clockOff, true);
    assert.equal(panel.delayText, '1 min 30 s ahead of this browser\'s clock');

    clients[0].send(MATCHED, matched(3_600_000, { 0: -60 })); // an hour behind: not drawn
    panel.tick();
    assert.equal(panel.skipped, 1);
    assert.equal(panel.clockOff, true);
});

test('leaving the tab closes the connection and the chart', async t => {
    const { clients, mount } = setup(t);
    const panel = await mount(liveSignalPower);
    clients[0].open();
    const chart = lastChart();
    panel.destroy();
    assert.equal(clients[0].ended, true);
    assert.equal(chart.destroyed, true);
});

test('leaving the tab while the libraries still load connects nothing', async t => {
    const { browser, clients } = setup(t);
    delete browser.window.mqtt;
    delete browser.window.uPlot;
    const saved = globalThis.document;
    globalThis.document = {
        hidden: false,
        head: { appendChild: element => setImmediate(() => {
            if (element.src?.includes('uPlot')) browser.window.uPlot = FakeChart;
            if (element.src?.includes('mqtt')) browser.window.mqtt = { connect: () => { clients.push(1); return new FakeClient(); } };
            element.onload?.();
        }) },
        createElement: () => ({}),
    };
    t.after(() => { globalThis.document = saved; });

    const panel = liveSignalPower();
    panel.$refs = { chart: {} };
    const loading = panel.init();
    panel.destroy();
    await loading;
    assert.equal(clients.length, 0);
    assert.equal(FakeChart.instances.length, 0);
});

test('a library that cannot be loaded is shown as failed, not as a blank chart', async t => {
    const { browser } = setup(t);
    delete browser.window.mqtt;
    delete browser.window.uPlot;
    const saved = globalThis.document;
    const quiet = globalThis.console.error;
    globalThis.console.error = () => {};
    globalThis.document = {
        hidden: false,
        head: { appendChild: element => setImmediate(() => element.onerror?.()) },
        createElement: () => ({}),
    };
    t.after(() => { globalThis.document = saved; globalThis.console.error = quiet; });

    const panel = liveSignalPower();
    panel.$refs = { chart: {} };
    await panel.init();
    assert.equal(panel.status, 'failed');
    assert.match(panel.error, /libraries/);
});

test('the Live tab needs radiotracking, and is not there in server mode', t => {
    const browser = installBrowser();
    t.after(browser.restore);
    const manager = configManager();
    manager.availableServices = ['radiotracking'];
    manager.serverMode = false;
    assert.equal(manager.isLiveAvailable(), true);

    manager.serverMode = true; // there is no local broker on the server
    assert.equal(manager.isLiveAvailable(), false);

    manager.serverMode = false;
    manager.availableServices = ['soundscapepipe'];
    assert.equal(manager.isLiveAvailable(), false);
});

// --- the time window ----------------------------------------------------------

test('a shorter time window shows less of what is held, and the rest comes back with the longer one', async t => {
    const { clients, mount } = setup(t);
    const panel = await mount(liveFrequencyUsage);
    clients[0].open();
    clients[0].send(MATCHED, matched(10_000, { 0: -60 }, { Frequency: 150200000 }));
    clients[0].send(MATCHED, matched(40_000, { 0: -60 }, { Frequency: 150300000 }));
    clients[0].send(MATCHED, matched(100_000, { 0: -60 }, { Frequency: 150400000 }));
    panel.tick();
    assert.equal(panel.count, 3);
    assert.equal(panel.viewText, '5 minutes');

    liveManager.setView(30_000);
    assert.equal(panel.count, 1, 'a changed window shows at once, not at the next tick');
    assert.equal(panel.viewText, '30 seconds', 'and so does the text, from the panel\'s own data');
    assert.equal(panel.viewMs, 30_000);
    const y = lastChart().scales.filter(scale => scale.scale === 'y').at(-1);
    assert.ok(y.max < 150.25, 'the vertical axis fits what is shown, not what is hidden');

    const [from, to] = lastChart().options.scales.x.range();
    assert.ok(Math.abs(to - from - 30) < 1e-6, 'the time axis spans the window');
    assert.equal(liveManager.windows.matched.length, 3, 'nothing is dropped, only not shown');

    liveManager.setView(WINDOW_MS);
    assert.equal(panel.count, 3, 'the held signals are back');
});

test('only the signals of the time window are drawn, and found by the tooltip', async t => {
    const { clients, mount } = setup(t);
    const panel = await mount(liveFrequencyUsage);
    clients[0].open();
    clients[0].send(MATCHED, matched(10_000, { 0: -60 }));
    clients[0].send(MATCHED, matched(100_000, { 0: -60 }));
    liveManager.setView(30_000);

    const arcs = [];
    const ctx = {
        save() {}, restore() {}, beginPath() {}, rect() {}, clip() {}, moveTo() {}, fill() {},
        arc: (x, y, radius) => arcs.push(x),
    };
    const [min, max] = lastChart().options.scales.x.range();
    lastChart().options.hooks.draw[0]({
        ctx,
        bbox: { left: 0, top: 0, width: 100, height: 100 },
        scales: { x: { min, max } },
        valToPos: value => value,
    });
    assert.equal(arcs.length, 1, 'the signal of 100 s ago is outside 30 s');
    assert.ok(panel.count === 1);
});

test('the window is limited to what is kept, and is kept while the page is open', t => {
    setup(t);
    liveManager.setView(60 * 60 * 1000);
    assert.equal(liveManager.prefs.viewMs, WINDOW_MS, 'not more than is held');
    liveManager.setView(0);
    assert.equal(liveManager.prefs.viewMs, 1000, 'and not nothing');
});

// --- the tabs ----------------------------------------------------------------

function tabsSetup(t, { hash = '', available = ['radiotracking'] } = {}) {
    const browser = installBrowser({ hash });
    const replaced = [];
    browser.window.history = { replaceState: (...args) => replaced.push(args[2]) };
    liveManager.reset();
    t.after(() => {
        liveManager.reset();
        browser.restore();
    });
    const tabs = liveTabs(service => available.includes(service));
    return { browser, tabs, replaced };
}

test('the Live page has a tab for each service that is available', t => {
    const { tabs } = tabsSetup(t);
    assert.deepEqual(tabs.tabs.map(tab => tab.id), ['radiotracking']);

    const none = tabsSetup(t, { available: [] });
    assert.deepEqual(none.tabs.tabs, [], 'no service, no tab');
    none.tabs.init();
    assert.equal(none.tabs.tab, null);
    assert.deepEqual(none.replaced, [], 'and nothing to name in the URL');
});

test('the first tab opens, and the URL names it', t => {
    const { tabs, replaced } = tabsSetup(t, { hash: '#live' });
    tabs.init();
    assert.equal(tabs.tab, 'radiotracking');
    assert.deepEqual(replaced, ['#live/radiotracking']);
    tabs.destroy();
});

test('the tab in the URL opens, if it is one; otherwise the first', t => {
    const known = tabsSetup(t, { hash: '#live/radiotracking' });
    known.tabs.init();
    assert.equal(known.tabs.tab, 'radiotracking');
    assert.deepEqual(known.replaced, [], 'the URL is already right');
    known.tabs.destroy();

    const unknown = tabsSetup(t, { hash: '#live/health' });
    unknown.tabs.init();
    assert.equal(unknown.tabs.tab, 'radiotracking', 'a tab that does not exist (yet)');
    assert.deepEqual(unknown.replaced, ['#live/radiotracking']);
    unknown.tabs.destroy();
});

test('a tab of a service that is not available cannot be opened', t => {
    const { browser, tabs } = tabsSetup(t, { hash: '#live/radiotracking' });
    tabs.init();
    const before = browser.window.location.hash;
    tabs.select('health');
    assert.equal(tabs.tab, 'radiotracking');
    assert.equal(browser.window.location.hash, before);
    tabs.destroy();
});

test('choosing a tab puts it in the URL, and the back button comes back to the tab of the URL', t => {
    const { browser, tabs } = tabsSetup(t, { hash: '#live' });
    tabs.init();
    tabs.select('radiotracking');
    assert.equal(browser.window.location.hash, 'live/radiotracking');

    tabs.tab = null;
    browser.window.location.hash = '#live/radiotracking';
    browser.window.dispatchEvent(new globalThis.CustomEvent('hashchange'));
    assert.equal(tabs.tab, 'radiotracking');

    tabs.destroy();
    assert.equal(browser.listenerCount('hashchange'), 0, 'the listener goes with the tab');
});

test('the time window is chosen on the page and held by the manager', t => {
    const { tabs } = tabsSetup(t);
    assert.deepEqual(tabs.viewOptions.map(option => option.label), ['30 seconds', '1 minute', '2 minutes', '5 minutes']);
    assert.equal(tabs.viewMs, WINDOW_MS, 'all that is kept, by default');

    tabs.setView('60000'); // a select gives text
    assert.equal(liveManager.prefs.viewMs, 60_000);
    assert.equal(tabs.viewMs, 60_000);

    const again = liveTabs(() => true);
    assert.equal(again.viewMs, 60_000, 'kept when the page is left and opened again');
});

// --- the axes the radiotracking configuration gives -------------------------------

const CONFIG = {
    'optional arguments': { verbose: 0 },
    'rtl-sdr': { center_freq: 150155000, sample_rate: 300000, device: ['0'] },
    analysis: { signal_threshold_dbw: -95.0, signal_min_duration_ms: 8.0, signal_max_duration_ms: 50.0 },
};
const near = (actual, expected) => assert.ok(
    actual.every((value, index) => Math.abs(value - expected[index]) < 1e-9),
    `${actual} is not ${expected}`,
);

test('the limits are read from tsconfig\'s radiotracking API when a panel opens', async t => {
    const { browser, mount } = setup(t);
    browser.respond({ match: /\/api\/radiotracking$/, body: CONFIG });
    await mount(liveFrequencyUsage);
    assert.ok(browser.calls.some(call => call.url.endsWith('/api/radiotracking') && call.method === 'GET'));
    assert.equal(liveManager.limits.thresholdDbw, -95);
    assert.equal(liveManager.limits.centerFreqHz, 150155000);
});

test('without the configuration the axes fit the data, and the panel still opens', async t => {
    const { browser, clients, mount } = setup(t);
    browser.respond({ match: /\/api\/radiotracking$/, status: 404, body: {} });
    const panel = await mount(liveFrequencyUsage);
    assert.equal(liveManager.limits, null);
    clients[0].open();
    clients[0].send(MATCHED, matched(100, { 0: -60 }));
    panel.tick();
    const y = lastChart().scales.filter(scale => scale.scale === 'y').at(-1);
    assert.ok(y.min < 150.2335 && y.max > 150.2335 && y.max - y.min < 0.2, 'fitted around the one frequency');
});

test('a failing request for the configuration is no reason to stop', async t => {
    const { browser, mount } = setup(t);
    const quiet = globalThis.console.error;
    globalThis.console.error = () => {};
    t.after(() => { globalThis.console.error = quiet; });
    const fetched = globalThis.fetch;
    globalThis.fetch = async url => {
        if (String(url).endsWith('/api/radiotracking')) throw new Error('offline');
        return fetched(url);
    };
    t.after(() => { globalThis.fetch = fetched; });
    const panel = await mount(liveSignalPower);
    assert.equal(liveManager.limits, null);
    assert.equal(panel.status, 'connecting', 'it goes on');
    assert.ok(browser);
});

test('Signal Power: matched power is limited by the signal threshold and a static -30 dBW', async t => {
    const { browser, clients, mount } = setup(t);
    browser.respond({ match: /\/api\/radiotracking$/, body: CONFIG });
    const panel = await mount(liveSignalPower);
    clients[0].open();
    const expected = padRange([-95, -30]);
    near(lastChart().options.scales.y.range(), expected);

    clients[0].send(MATCHED, matched(100, { 0: -50 }));
    panel.tick();
    near(panel.yRange(-50, -50), expected);
    const y = lastChart().scales.filter(scale => scale.scale === 'y');
    assert.equal(y.length, 0, 'the data does not move it: it was right from the start');
});

test('Signal Power: calibrated power (dBm) is fitted to the data, the configuration does not bound it', async t => {
    const { browser, mount } = setup(t);
    browser.respond({ match: /\/api\/radiotracking$/, body: CONFIG });
    const panel = await mount(liveSignalPower);
    panel.setSource('calibrated');
    const [low, high] = panel.yRange(-100, -90);
    assert.ok(low <= -100 && high >= -90 && high - low < 40);
    assert.notDeepEqual([low, high], padRange([-95, -30]));
});

test('Frequency Usage: the centre frequency plus and minus half the sample rate', async t => {
    const { browser, mount } = setup(t);
    browser.respond({ match: /\/api\/radiotracking$/, body: CONFIG });
    const panel = await mount(liveFrequencyUsage);
    near(panel.yRange(150.2, 150.2), padRange([150.005, 150.305]));
    near(lastChart().options.scales.y.range(), padRange([150.005, 150.305]));
});

test('Signal Detail: max and avg have the axis of Signal Power, frequency that of Frequency Usage, duration the configured limits', async t => {
    const { browser, mount } = setup(t);
    browser.respond({ match: /\/api\/radiotracking$/, body: CONFIG });
    const panel = await mount(liveSignalDetail);

    for (const param of ['max (dBW)', 'avg (dBW)']) {
        panel.param = param;
        near(panel.yRange(-80, -60), padRange([-95, -30]));
    }
    panel.param = 'Frequency';
    near(panel.yRange(150.2, 150.2), padRange([150.005, 150.305]));
    panel.param = 'Duration';
    near(panel.yRange(20, 30), padRange([8, 50]));
});

test('Signal Detail: the other dB values are fitted to the data, but span at least 10 dB', async t => {
    const { browser, mount } = setup(t);
    browser.respond({ match: /\/api\/radiotracking$/, body: CONFIG });
    const panel = await mount(liveSignalDetail);
    for (const param of ['snr (dB)', 'std (dB)', 'noise (dBW)']) {
        panel.param = param;
        const [low, high] = panel.yRange(40, 41);
        assert.ok(Math.abs(high - low - 10) < 1e-9, `${param}: a spread of 1 dB gets 10`);
        assert.ok(low < 40 && high > 41, `${param}: around the data`);
        const [wideLow, wideHigh] = panel.yRange(0, 60);
        assert.ok(wideHigh - wideLow > 60, `${param}: a wide spread is kept`);
    }
});

test('Signal Detail: a value the station adds is fitted to the data', async t => {
    const { browser, mount } = setup(t);
    browser.respond({ match: /\/api\/radiotracking$/, body: CONFIG });
    const panel = await mount(liveSignalDetail);
    panel.param = 'bandwidth (Hz)';
    const [low, high] = panel.yRange(3000, 5000);
    assert.ok(low < 3000 && high > 5000);
    assert.ok(high - low < 3000, 'no 10 dB rule for Hz');
});

test('Signal Detail: without the configuration max and frequency are fitted to the data', async t => {
    const { browser, mount } = setup(t);
    browser.respond({ match: /\/api\/radiotracking$/, status: 500, body: {} });
    const panel = await mount(liveSignalDetail);
    panel.param = 'max (dBW)';
    const [low, high] = panel.yRange(-60, -59);
    assert.ok(Math.abs(high - low - 10) < 1e-9, 'a dB value: at least 10 dB');
    panel.param = 'Frequency';
    const [flow, fhigh] = panel.yRange(150.2, 150.3);
    assert.ok(flow < 150.2 && fhigh > 150.3 && fhigh - flow < 1);
});

test('panels that open together share one request for the configuration', async t => {
    const { browser, mount } = setup(t);
    browser.respond({ match: /\/api\/radiotracking$/, body: CONFIG });
    await Promise.all([mount(liveSignalPower), mount(liveFrequencyUsage), mount(liveSignalDetail)]);
    assert.equal(browser.calls.filter(call => call.url.endsWith('/api/radiotracking')).length, 1);
    assert.equal(liveManager.limits.thresholdDbw, -95);
});

// --- a panel that was left without Alpine telling it ------------------------------

test('a panel whose element has left the page lets go of everything by itself', async t => {
    const { clients, mount } = setup(t);
    const element = { isConnected: true };
    liveManager.windows.matched.add({ at: null, epochMs: Date.now(), frequency: 1, duration: 0.02, key: null, values: { 0: -60 } });
    const panel = liveSignalPower();
    panel.$refs = { chart: { clientWidth: 600 } };
    panel.$el = element;
    await panel.init();
    t.after(() => panel.destroy());
    clients[0].open();
    const chart = lastChart();
    assert.equal(liveManager.wanted.matched, 1);

    panel.tick();
    assert.equal(panel.destroyed, false, 'while it is on the page');

    element.isConnected = false; // Alpine removed it, but did not call destroy()
    panel.tick();
    assert.equal(panel.destroyed, true);
    assert.equal(liveManager.wanted.matched, 0, 'what it read is given back');
    assert.equal(clients[0].ended, true, 'and with the last of them the connection closes');
    assert.equal(chart.destroyed, true);
    assert.equal(liveManager.windows.matched.length, 0, 'and so is what it showed');
    assert.ok(mount);
});

test('leaving and coming back does not add a second set of subscriptions, so Stop stops', async t => {
    const { clients, mount } = setup(t);
    const make = async () => {
        const element = { isConnected: true };
        const panel = liveSignalDetail();
        panel.$refs = { chart: { clientWidth: 600 } };
        panel.$el = element;
        await panel.init();
        t.after(() => panel.destroy());
        return { panel, element };
    };

    const first = await make();
    first.panel.toggleCollect();
    assert.equal(liveManager.wanted.raw, 1);
    first.element.isConnected = false; // the page is left, Alpine does not tell the panel
    first.panel.tick();
    assert.equal(liveManager.wanted.raw, 0);

    const second = await make(); // the page is opened again: collecting was kept
    assert.equal(second.panel.collecting, true);
    assert.equal(liveManager.wanted.raw, 1, 'one subscription, not two');

    second.panel.toggleCollect(); // Stop
    assert.equal(liveManager.wanted.raw, 0, 'and now nothing reads the raw signals');
    assert.equal(clients.at(-1).ended, true);
    assert.ok(mount);
});

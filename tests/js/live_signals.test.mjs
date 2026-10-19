// What the live dashboard does with radiotracking's messages: reading them, the
// rolling window, and the numbers the charts need.
import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
    COLORS,
    EventWindow,
    LIVE_TABS,
    LIVE_TOPICS,
    POWER_CEILING_DBW,
    VIEW_OPTIONS,
    WINDOW_MS,
    brokerUrl,
    colorFor,
    compareKeys,
    dotSize,
    durationLimits,
    firstAtOrAfter,
    formatClock,
    formatClockMs,
    formatDuration,
    formatFrequency,
    formatOffset,
    formatTick,
    formatWindow,
    frequencyLimits,
    frequencyRange,
    padRange,
    parseRaw,
    parseSignal,
    powerLimits,
    powerRange,
    radiotrackingLimits,
    rawParam,
    spreadRange,
    valueRange,
} from '../../app/static/js/utils/liveSignals.js';

const message = fields => JSON.stringify({
    Time: '2026-10-19T13:53:28.605577+02:00',
    Frequency: 150233515.625,
    Duration: 0.026453,
    ...fields,
});

// A message whose Time is `ms`
const at = (ms, fields) => message({ ...fields, Time: new Date(ms).toISOString() });

const raw = fields => JSON.stringify({
    Device: '0',
    Time: '2026-10-19T13:53:28.605577+02:00',
    Frequency: 150233515.625,
    Duration: 0.026453,
    'max (dBW)': -55.8,
    'avg (dBW)': -58.0,
    'std (dB)': 5.6,
    'noise (dBW)': -99.7,
    'snr (dB)': 41.7,
    ...fields,
});

const signal = (ms, values, extra = {}) => ({ at: null, epochMs: ms, frequency: 150e6, duration: 0.02, key: null, values, ...extra });

// --- reading messages -------------------------------------------------------

test('parseSignal: every key besides Time, Frequency and Duration is an SDR', () => {
    const parsed = parseSignal(message({ 0: -59.3, 'rtl-b': -61 }));
    assert.deepEqual(parsed.values, { 0: -59.3, 'rtl-b': -61 });
    assert.equal(parsed.at, '2026-10-19T13:53:28.605577+02:00');
    assert.equal(parsed.frequency, 150233515.625);
    assert.equal(parsed.duration, 0.026453);
    assert.equal(parsed.epochMs, Date.parse('2026-10-19T11:53:28.605Z'), 'Time as milliseconds');
});

test('parseSignal: a Time that is not a time is kept as text, without milliseconds', () => {
    const parsed = parseSignal(message({ 0: -50, Time: 'yesterday' }));
    assert.equal(parsed.at, 'yesterday');
    assert.equal(parsed.epochMs, null);
    assert.equal(parseSignal('{"0": -50}').epochMs, null);
});

test('parseSignal: SDRs without a power (not calibrated) are left out', () => {
    assert.deepEqual(parseSignal(message({ 0: -40.5, 1: null })).values, { 0: -40.5 });
    assert.deepEqual(parseSignal(message({ 0: null })).values, {});
});

test('parseSignal: what is not a signal message is null', () => {
    assert.equal(parseSignal('not json'), null);
    assert.equal(parseSignal('[1, 2]'), null);
    assert.equal(parseSignal('null'), null);
    assert.equal(parseSignal('3'), null);
});

test('parseSignal: values that are not numbers are not powers', () => {
    assert.deepEqual(parseSignal(message({ 0: 'loud', 1: -50, 2: true })).values, { 1: -50 });
    assert.equal(parseSignal('{"0": -50}').frequency, null);
});

test('parseRaw: the device is the key, every number is a value to plot', () => {
    const parsed = parseRaw(raw({ Device: 3 }));
    assert.equal(parsed.key, '3');
    assert.equal(parsed.frequency, 150233515.625);
    assert.equal(parsed.duration, 0.026453);
    assert.deepEqual(Object.keys(parsed.values), [
        'Frequency', 'Duration', 'max (dBW)', 'avg (dBW)', 'std (dB)', 'noise (dBW)', 'snr (dB)',
    ]);
    assert.equal(parsed.values['snr (dB)'], 41.7);
    assert.equal(parsed.epochMs, Date.parse('2026-10-19T11:53:28.605Z'));
});

test('parseRaw: a value the station adds later is offered too, text is not', () => {
    const parsed = parseRaw(raw({ 'bandwidth (Hz)': 4000, label: 'x', gain: null }));
    assert.equal(parsed.values['bandwidth (Hz)'], 4000);
    assert.ok(!('label' in parsed.values) && !('gain' in parsed.values));
});

test('parseRaw: what has no Device is not a raw signal', () => {
    assert.equal(parseRaw('{"Time": "2026-10-19T11:53:28Z", "max (dBW)": -50}'), null);
    assert.equal(parseRaw('garbage'), null);
    assert.equal(parseRaw('[]'), null);
});

test('rawParam: frequency in MHz and duration in ms, anything else as it is', () => {
    assert.deepEqual(rawParam('Frequency'), { label: 'Frequency (MHz)', scale: 1e-6 });
    assert.deepEqual(rawParam('Duration'), { label: 'Duration (ms)', scale: 1e3 });
    assert.deepEqual(rawParam('snr (dB)'), { label: 'snr (dB)', scale: 1 });
});

test('the topics: any station prefix', () => {
    assert.equal(LIVE_TOPICS.matched, '+/radiotracking/matched/json');
    assert.equal(LIVE_TOPICS.calibrated, '+/radiotracking/calibrated/json');
    assert.equal(LIVE_TOPICS.raw, '+/radiotracking/device/+/json');
});

// --- the window ---------------------------------------------------------------

test('EventWindow: keeps the signals in order of their Time, and what they carry', () => {
    const window = new EventWindow();
    const t0 = 1_000_000;
    window.add(parseSignal(at(t0, { 0: -60 })), t0);
    window.add(parseSignal(at(t0 + 1000, { 0: -61, 1: -70 })), t0 + 1000);

    assert.equal(window.length, 2);
    assert.deepEqual(window.events.map(event => event.time), [t0 / 1000, t0 / 1000 + 1]);
    assert.deepEqual(window.events[1].values, { 0: -61, 1: -70 });
    assert.equal(window.events[1].frequency, 150233515.625);
    assert.equal(window.events[1].duration, 0.026453);
    assert.ok(!('epochMs' in window.events[1]), 'time is in seconds, as the chart wants it');
});

test('EventWindow: a signal is placed at its Time, not at its arrival', () => {
    const window = new EventWindow();
    const now = 10_000_000;
    assert.equal(window.add(parseSignal(at(now - 2000, { 0: -60 })), now), true); // arrived two seconds after it was detected
    assert.deepEqual(window.events.map(event => event.time), [(now - 2000) / 1000]);
    assert.equal(window.offsetMs, 2000, 'arrival minus Time');
});

test('EventWindow: signals that arrive out of order are kept in order of their Time', () => {
    const window = new EventWindow();
    const now = 10_000_000;
    window.add(parseSignal(at(now - 1000, { 0: -61 })), now);
    window.add(parseSignal(at(now - 5000, { 0: -65 })), now); // detected earlier, arrived later
    window.add(parseSignal(at(now - 3000, { 0: -63 })), now);
    assert.deepEqual(window.events.map(event => event.time), [(now - 5000) / 1000, (now - 3000) / 1000, (now - 1000) / 1000]);
    assert.deepEqual(window.events.map(event => event.values[0]), [-65, -63, -61]);
});

test('EventWindow: drops what is older than the window, by the browser\'s clock', () => {
    const window = new EventWindow(5 * 60 * 1000);
    const t0 = 10_000_000;
    window.add(parseSignal(at(t0, { 0: -60 })), t0);
    window.add(parseSignal(at(t0 + 4 * 60 * 1000, { 0: -61 })), t0 + 4 * 60 * 1000);
    assert.equal(window.length, 2);

    const later = t0 + 5 * 60 * 1000 + 1;
    window.add(parseSignal(at(later, { 0: -62 })), later);
    assert.deepEqual(window.events.map(event => event.values[0]), [-61, -62], 'the first signal left the window');

    window.trim(t0 + 20 * 60 * 1000); // nothing arrives for a long time
    assert.equal(window.length, 0);
});

test('EventWindow: a station clock that is behind shows as skipped signals, not as a quiet chart', () => {
    const window = new EventWindow();
    const now = 10_000_000_000;
    assert.equal(window.add(parseSignal(at(now - 60 * 60 * 1000, { 0: -60 })), now), false);
    assert.equal(window.length, 0);
    assert.equal(window.skipped, 1);
    assert.equal(window.offsetMs, 60 * 60 * 1000, 'and says by how much');
});

test('EventWindow: a station clock that is a little ahead is drawn, to the right of now', () => {
    const window = new EventWindow();
    const now = 10_000_000_000;
    window.add(parseSignal(at(now + 3 * 60 * 1000, { 0: -60 })), now);
    assert.equal(window.length, 1);
    assert.equal(window.skipped, 0);
    assert.equal(window.offsetMs, -3 * 60 * 1000, 'negative: ahead of the browser');
    assert.equal(window.edge(now), now + 3 * 60 * 1000, 'the window ends at the newest signal');

    // the window follows the station's clock then, so what is old by that clock goes
    window.add(parseSignal(at(now + 9.5 * 60 * 1000, { 0: -61 })), now);
    assert.deepEqual(window.events.map(event => event.values[0]), [-61], 'the first is more than 5 minutes behind the newest');
});

test('EventWindow: a station clock far ahead cannot push the window away from the browser\'s clock', () => {
    const window = new EventWindow();
    const now = 10_000_000_000;
    assert.equal(window.add(parseSignal(at(now + 3 * 24 * 3600 * 1000, { 0: -60 })), now), false);
    assert.equal(window.skipped, 1);
    assert.equal(window.edge(now), now);
    window.add(parseSignal(at(now, { 0: -61 })), now); // the real ones still show
    assert.equal(window.length, 1);
});

test('EventWindow: without a readable Time the arrival is all there is', () => {
    const window = new EventWindow();
    window.add(parseSignal(message({ 0: -60, Time: 'yesterday' })), 5_000_000);
    assert.deepEqual(window.events.map(event => event.time), [5000]);
    assert.equal(window.offsetMs, 0);
});

test('EventWindow: bounds the memory when signals come very fast', () => {
    const window = new EventWindow(5 * 60 * 1000, 3);
    for (let i = 0; i < 10; i++) window.add(signal(1_000_000 + i, { 0: -i }), 1_000_000 + i);
    assert.deepEqual(window.events.map(event => event.values[0]), [-7, -8, -9]);
});

test('EventWindow: clear() forgets the signals and the clock offset too', () => {
    const window = new EventWindow();
    window.add(signal(1000, { 0: -60 }), 1500);
    window.add(signal(1, { 0: -60 }), 1_000_000_000);
    window.clear();
    assert.equal(window.length, 0);
    assert.equal(window.offsetMs, null);
    assert.equal(window.skipped, 0);
});

// --- what the charts need ------------------------------------------------------

test('dotSize: 6 px for 20 ms, proportional to the duration, within bounds', () => {
    assert.equal(dotSize(0.020), 6);
    assert.equal(dotSize(0.040), 12, 'twice as long, twice as wide');
    assert.equal(dotSize(0.010), 3);
    assert.equal(dotSize(0.005), 3, 'a very short signal stays visible');
    assert.equal(dotSize(0.500), 24, 'a very long one does not cover the chart');
    assert.equal(dotSize(null), 6, 'unknown: the default');
    assert.equal(dotSize(0), 6);
    assert.equal(dotSize(NaN), 6);
});

test('colorFor: an SDR has its colour on every chart, other series a fixed one', () => {
    assert.equal(colorFor('0'), colorFor(0));
    assert.notEqual(colorFor('0'), colorFor('1'));
    assert.equal(colorFor('8'), colorFor('0'), 'the palette repeats');
    assert.equal(colorFor('frequency'), colorFor('frequency'));
    assert.match(colorFor('rtl-b'), /^#[0-9A-F]{6}$/i);
});

test('colorFor: the SDRs have the colours of the compass: 0 blue, 1 yellow, 2 red, 3 green', () => {
    // hue of a '#RRGGBB' colour, in degrees
    const hue = hex => {
        const [r, g, b] = [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16) / 255);
        const max = Math.max(r, g, b);
        const d = max - Math.min(r, g, b);
        const h = max === r ? ((g - b) / d) % 6 : max === g ? (b - r) / d + 2 : (r - g) / d + 4;
        return ((h * 60) + 360) % 360;
    };
    const within = (value, low, high) => value >= low && value <= high;
    assert.ok(within(hue(colorFor('0')), 190, 230), 'north: blue');
    assert.ok(within(hue(colorFor('1')), 35, 55), 'east: yellow (amber)');
    assert.ok(within(hue(colorFor('2')), 0, 15) || hue(colorFor('2')) > 350, 'south: red');
    assert.ok(within(hue(colorFor('3')), 150, 170), 'west: green');
    assert.equal(new Set(COLORS.slice(0, 4)).size, 4);
});

test('compareKeys: SDR 2 comes before SDR 10', () => {
    assert.deepEqual(['10', '2', '1'].sort(compareKeys), ['1', '2', '10']);
});

test('powerRange: a usual range before there is data, in the source\'s unit', () => {
    assert.deepEqual(powerRange(null, null, 'dBW'), [-100, -20]);
    assert.deepEqual(powerRange(undefined, undefined, 'dBm'), [-120, -40]);
});

test('powerRange: pads the data, and never gives an axis of no height', () => {
    assert.deepEqual(powerRange(-80, -50, 'dBW'), [-85, -45]);
    const [low, high] = powerRange(-59.152, -59.152, 'dBW'); // one signal
    assert.deepEqual([low, high], [-65, -54]);
    assert.ok(high - low >= 10);
    assert.deepEqual(powerRange(-40.2, -40.1, 'dBm').map(Number.isInteger), [true, true], 'whole dB');
});

test('frequencyRange: padded, at least 50 kHz high, a default before there is data', () => {
    assert.deepEqual(frequencyRange(null, null), [150, 151]);
    const [low, high] = frequencyRange(150.2335, 150.2335); // one frequency
    assert.ok(Math.abs(high - low - 0.05) < 1e-9);
    assert.ok(low < 150.2335 && high > 150.2335);
    const [wideLow, wideHigh] = frequencyRange(150.1, 150.3);
    assert.ok(wideLow < 150.1 && wideHigh > 150.3, 'padded');
});

test('valueRange: padded, and a single value still gets an axis', () => {
    assert.deepEqual(valueRange(null, null), [0, 1]);
    const [low, high] = valueRange(10, 20);
    assert.ok(low < 10 && high > 20);
    const [oneLow, oneHigh] = valueRange(-50, -50);
    assert.ok(oneHigh - oneLow > 0 && oneLow < -50 && oneHigh > -50);
    const [zeroLow, zeroHigh] = valueRange(0, 0);
    assert.ok(zeroHigh - zeroLow > 0);
});

test('formatTick: no float noise', () => {
    assert.equal(formatTick(-60.00000000001, 1), '-60');
    assert.equal(formatTick(150.15500000001), '150.155');
    assert.equal(formatTick(0.25, 2), '0.25');
});

test('formatClock: 24 hours, no date, no am/pm', () => {
    const afternoon = new Date(2026, 9, 19, 14, 5, 9).getTime() / 1000;
    const night = new Date(2026, 9, 19, 3, 7, 0).getTime() / 1000;
    assert.equal(formatClock(afternoon), '14:05');
    assert.equal(formatClock(afternoon, true), '14:05:09');
    assert.equal(formatClock(night, true), '03:07:00');
    assert.doesNotMatch(formatClock(afternoon, true), /[ap]m|\//i);
});

test('formatClockMs: with milliseconds, for the tooltip', () => {
    const ms = new Date(2026, 9, 19, 14, 5, 9, 42).getTime();
    assert.equal(formatClockMs(ms), '14:05:09.042');
});

test('formatFrequency and formatDuration', () => {
    assert.equal(formatFrequency(150233515.625), '150.234 MHz');
    assert.equal(formatFrequency(null), '–');
    assert.equal(formatDuration(0.026453), '26.5 ms');
    assert.equal(formatDuration(undefined), '–');
});

test('formatOffset: a clock difference people can read', () => {
    assert.equal(formatOffset(400), '0.4 s');
    assert.equal(formatOffset(-3200), '3.2 s');
    assert.equal(formatOffset(42_000), '42 s');
    assert.equal(formatOffset(125_000), '2 min 5 s');
    assert.equal(formatOffset(62 * 60 * 1000), '1 h 2 min');
    assert.equal(formatOffset(3 * 24 * 3600 * 1000), '3 days');
});

test('brokerUrl: same host as the page, ws or wss like the page, behind the base URL', () => {
    assert.equal(brokerUrl({ protocol: 'http:', host: 'station.local' }), 'ws://station.local/mqtt');
    assert.equal(brokerUrl({ protocol: 'https:', host: '169.254.0.1' }), 'wss://169.254.0.1/mqtt');
    assert.equal(brokerUrl({ protocol: 'http:', host: 'host:8000' }, '/tsconfig'), 'ws://host:8000/tsconfig/mqtt');
});

test('the tabs: radiotracking, offered with its service', () => {
    assert.deepEqual(LIVE_TABS.map(tab => [tab.id, tab.service]), [['radiotracking', 'radiotracking']]);
    assert.ok(LIVE_TABS.every(tab => tab.label && tab.icon), 'each has what the tab shows');
});

test('the time windows to choose from end at the one that is kept', () => {
    assert.deepEqual(VIEW_OPTIONS, [30_000, 60_000, 120_000, WINDOW_MS]);
    assert.equal(VIEW_OPTIONS.at(-1), 5 * 60 * 1000);
});

test('formatWindow: "30 seconds", "1 minute", "5 minutes"', () => {
    assert.equal(formatWindow(30_000), '30 seconds');
    assert.equal(formatWindow(60_000), '1 minute');
    assert.equal(formatWindow(120_000), '2 minutes');
    assert.equal(formatWindow(WINDOW_MS), '5 minutes');
});

test('firstAtOrAfter: where the signals of a time window start', () => {
    const events = [1, 2, 3, 3, 5].map(time => ({ time }));
    assert.equal(firstAtOrAfter(events, 0), 0);
    assert.equal(firstAtOrAfter(events, 2), 1);
    assert.equal(firstAtOrAfter(events, 3), 2, 'the first of equal times');
    assert.equal(firstAtOrAfter(events, 4), 4);
    assert.equal(firstAtOrAfter(events, 6), 5, 'none');
    assert.equal(firstAtOrAfter([], 1), 0);
});

// --- the axes the radiotracking configuration gives -------------------------------

// What GET /api/radiotracking answers (the sections that matter)
const CONFIG = {
    'rtl-sdr': { center_freq: 150155000, sample_rate: 300000, device: ['0'] },
    analysis: { signal_threshold_dbw: -95.0, signal_min_duration_ms: 8.0, signal_max_duration_ms: 50.0 },
};

test('radiotrackingLimits: reads the signal threshold, frequency and duration limits from the configuration', () => {
    assert.deepEqual(radiotrackingLimits(CONFIG), {
        thresholdDbw: -95, centerFreqHz: 150155000, sampleRateHz: 300000, minDurationMs: 8, maxDurationMs: 50,
    });
});

test('radiotrackingLimits: what the configuration does not have is null, and what is not a configuration is null', () => {
    assert.deepEqual(radiotrackingLimits({}), {
        thresholdDbw: null, centerFreqHz: null, sampleRateHz: null, minDurationMs: null, maxDurationMs: null,
    });
    assert.equal(radiotrackingLimits({ analysis: { signal_threshold_dbw: 'low' } }).thresholdDbw, null);
    assert.equal(radiotrackingLimits(null), null);
    assert.equal(radiotrackingLimits('x'), null);
});

test('powerLimits: from the signal threshold up to the static -30 dBW', () => {
    assert.equal(POWER_CEILING_DBW, -30);
    assert.deepEqual(powerLimits(radiotrackingLimits(CONFIG)), [-95, -30]);
    assert.equal(powerLimits(null), null);
    assert.equal(powerLimits({ thresholdDbw: null }), null);
    assert.equal(powerLimits({ thresholdDbw: -20 }), null, 'a threshold above the ceiling would be an axis upside down');
});

test('frequencyLimits: the centre frequency plus and minus half the sample rate, in MHz', () => {
    const [low, high] = frequencyLimits(radiotrackingLimits(CONFIG));
    assert.ok(Math.abs(low - 150.005) < 1e-9 && Math.abs(high - 150.305) < 1e-9);
    assert.equal(frequencyLimits({ centerFreqHz: 150e6, sampleRateHz: null }), null);
    assert.equal(frequencyLimits({ centerFreqHz: 150e6, sampleRateHz: 0 }), null);
    assert.equal(frequencyLimits(null), null);
});

test('durationLimits: the shortest to the longest signal that is detected, in ms', () => {
    assert.deepEqual(durationLimits(radiotrackingLimits(CONFIG)), [8, 50]);
    assert.equal(durationLimits({ minDurationMs: 50, maxDurationMs: 8 }), null);
    assert.equal(durationLimits({ minDurationMs: 8, maxDurationMs: null }), null);
    assert.equal(durationLimits(undefined), null);
});

test('padRange: a little wider than the limits, so a dot on a limit is not cut in half', () => {
    const [low, high] = padRange([-95, -30]);
    assert.ok(Math.abs(low - (-95 - 1.95)) < 1e-9 && Math.abs(high - (-30 + 1.95)) < 1e-9);
    assert.equal(padRange(null), null);
});

test('spreadRange: fitted to the data, but never less than the minimum spread', () => {
    assert.deepEqual(spreadRange(null, null, 10), [0, 10], 'before there is data');
    assert.deepEqual(spreadRange(null, null, 10, [-100, -90]), [-100, -90]);
    const [low, high] = spreadRange(-60, -59, 10);
    assert.ok(Math.abs(high - low - 10) < 1e-9, 'a spread of 1 dB gets 10');
    assert.ok(low < -60 && high > -59, 'around the data');
    const [wideLow, wideHigh] = spreadRange(0, 100, 10);
    assert.ok(wideHigh - wideLow > 100, 'a wide spread is not touched but padded');
    const [oneLow, oneHigh] = spreadRange(-50, -50, 10);
    assert.deepEqual([oneLow, oneHigh], [-55, -45]);
});

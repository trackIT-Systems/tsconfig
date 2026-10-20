// What the Soundscapepipe tab reads: soundscapepipe's JSON messages and how they are shown.
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { colorFor } from '../../app/static/js/utils/liveSignals.js';
import {
    configuredDetectors,
    levelColor,
    levelLabel,
    levelRange,
    parseDetection,
    parseLevels,
    pcmStateText,
    speciesNames,
} from '../../app/static/js/utils/liveSoundscape.js';

// As tsos-audio-03b92ec4 published them (soundscapepipe 2026.10.1)
const BIRDEDGE = '{"time": "2026-10-20T15:49:18.529886+02:00", "duration": 5.0, "species": "Jynx torquilla", "confidence": 0.8861, "detector": "birdedge", "channel": "mix", "detection": 0.9949}';
const YOLOBAT = '{"time": "2026-10-20T15:49:29.469395+02:00", "duration": 0.0198, "species": "Nnoc", "confidence": 0.4152, "detector": "yolobat", "channel": "mix", "low": 20970, "high": 25517}';
const STATE = '{"time": "2026-10-20T15:50:09.424025+02:00", "state": 3, "channels": 2, "peak": [-0.0007952533512092486, 0.0], "rms": [-21.330325577204782, -22.152945633452315], "snr": [21.32953032385357, 22.152945633452315]}';

test('parseDetection reads a birdedge detection', () => {
    assert.deepEqual(parseDetection(BIRDEDGE), {
        at: '2026-10-20T15:49:18.529886+02:00',
        epochMs: Date.parse('2026-10-20T13:49:18.529Z'),
        duration: null,
        key: 'birdedge',
        values: { confidence: 0.8861 },
        species: 'Jynx torquilla',
        seconds: 5.0,
        channel: 'mix',
        low: null,
        high: null,
        callType: null,
    });
});

test('parseDetection reads the frequencies and call type of a bat', () => {
    const bat = parseDetection(YOLOBAT);
    assert.equal(bat.key, 'yolobat');
    assert.equal(bat.seconds, 0.0198);
    assert.deepEqual([bat.low, bat.high, bat.callType], [20970, 25517, null]);

    const typed = parseDetection(JSON.stringify({ ...JSON.parse(YOLOBAT), call_type: 'FM', ct_conf: 0.5 }));
    assert.equal(typed.callType, 'FM');
});

test('parseDetection: lures and schedules, and what is not a detection', () => {
    const lure = parseDetection('{"time": "2026-10-20T05:00:00+02:00", "duration": 1.0, "species": "Test", "confidence": 0.0, "detector": "lure", "channel": "n/a", "filepath": "/data/lure/a.wav", "record": true}');
    assert.deepEqual([lure.key, lure.values], ['lure', { confidence: 0 }]);

    assert.equal(parseDetection('not json'), null);
    assert.equal(parseDetection('[1, 2]'), null);
    assert.equal(parseDetection('{"time": "2026-10-20T05:00:00+02:00", "detector": "birdedge"}'), null, 'no species');
    assert.equal(parseDetection('{"time": "2026-10-20T05:00:00+02:00", "species": "A b"}'), null, 'no detector');
    const noTime = parseDetection('{"species": "A b", "detector": "birdedge", "confidence": 0.5}');
    assert.equal(noTime.epochMs, null, 'drawn when it arrives');
    assert.deepEqual(parseDetection('{"species": "A b", "detector": "birdedge"}').values, {}, 'nothing to draw');
});

test('parseLevels reads the levels of every channel', () => {
    const state = parseLevels(STATE);
    assert.equal(state.epochMs, Date.parse('2026-10-20T13:50:09.424Z'));
    assert.deepEqual([state.state, state.channels, state.duration, state.key], [3, 2, null, null]);
    assert.deepEqual(Object.keys(state.values), ['peak-0', 'peak-1', 'rms-0', 'rms-1', 'snr-0', 'snr-1']);
    assert.equal(state.values['rms-1'], -22.152945633452315);

    const mono = parseLevels('{"time": "2026-10-20T15:50:09+02:00", "state": 3, "channels": 1, "peak": [-6.0], "rms": [-30.0], "snr": [24.0]}');
    assert.deepEqual(mono.values, { 'peak-0': -6, 'rms-0': -30, 'snr-0': 24 });

    assert.deepEqual(parseLevels('{"time": "2026-10-20T15:50:09+02:00", "peak": [null, "x"]}').values, {});
    assert.equal(parseLevels('nope'), null);
});

test('the colours and labels of the levels', () => {
    assert.equal(new Set(['peak-0', 'rms-0', 'peak-1', 'rms-1'].map(levelColor)).size, 4);
    assert.equal(levelColor('peak-2'), colorFor('peak-2'));
    assert.equal(levelLabel('peak-0'), 'Ch 1 Peak');
    assert.equal(levelLabel('rms-1'), 'Ch 2 RMS');
    assert.equal(pcmStateText(3), 'running (3)');
    assert.equal(pcmStateText(4), 'xrun (4)');
    assert.equal(pcmStateText(42), '42');
    assert.equal(pcmStateText(null), '–');
});

test('levelRange: full scale at the top, at least down to -60 dBFS', () => {
    assert.deepEqual(levelRange(null), [-65, 3]);
    assert.deepEqual(levelRange(-20), [-60, 3]);
    assert.deepEqual(levelRange(-120), [-125, 3]);
});

test('configuredDetectors: the detectors of soundscapepipe.yml, and lure if it has tasks', () => {
    assert.deepEqual(configuredDetectors({ detectors: { birdedge: {}, yolobat: {} }, lure: { tasks: [] } }), ['birdedge', 'yolobat']);
    assert.deepEqual(configuredDetectors({ detectors: { yolobat: {} }, lure: { tasks: [{ species: 'x' }] } }), ['yolobat', 'lure']);
    assert.deepEqual(configuredDetectors(null), []);
    assert.deepEqual(configuredDetectors({ message: 'ok' }), []);
});

test('speciesNames: by scientific name, yolobat by its code', () => {
    const names = speciesNames({
        birdedge: [{ scientific: 'Jynx torquilla', english: 'Eurasian Wryneck', german: 'Wendehals' }],
        yolobat: [{ scientific: 'Nyctalus noctula', english: 'Common Noctule', modelLabel: 'Nnoc' }],
        audioprotopnet: [],
    });
    assert.deepEqual(names, {
        'Jynx torquilla': { scientific: 'Jynx torquilla', english: 'Eurasian Wryneck', german: 'Wendehals' },
        Nnoc: { scientific: 'Nyctalus noctula', english: 'Common Noctule', german: '' },
    });
    assert.deepEqual(speciesNames(null), {});
    assert.deepEqual(speciesNames({ message: 'ok', birdedge: [null, {}] }), {});
});

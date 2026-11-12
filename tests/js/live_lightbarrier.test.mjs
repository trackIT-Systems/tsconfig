// What the Lightbarrier tab reads: lightbarrierd's and envsensed's JSON messages.
import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
    envValueNames,
    orderBy,
    pairLabel,
    parseEnvsense,
    parseLbEvent,
    parseLbState,
    splitUnit,
} from '../../app/static/js/utils/liveLightbarrier.js';

// pylightbarrier's README (tsOS MQTT conventions), as dev-2027-lightjonas published them before (`timestamp`),
// and pyenvsense's README
const EVENT = '{"time": "2026-06-08T16:39:00.123456+02:00", "direction": "OUT_TO_IN", "duration_s": 0.123, "delta_t_s": 0.045, "avg_block_time_s": 0.084, "triggers": "ot+it"}';
const PARTIAL = '{"time": "2026-06-08T16:39:01+02:00", "direction": "PARTIAL_OUT", "duration_s": 0.05, "delta_t_s": null, "avg_block_time_s": 0.05, "triggers": "ot"}';
const STATE = '{"time": "2026-06-08T16:39:00.123456+02:00", "pair": "ot", "ambient": 0.1, "active": 1.1, "diff": 1.0, "duration_avg_s": 0.005, "duration_max_s": 0.006}';
const STATE_OLD = '{"timestamp": "2026-11-12T11:50:07.867227+01:00", "pair": "ib", "ambient": 0.013, "active": 0.261, "diff": 0.246, "duration_avg_s": 0.002, "duration_max_s": 0.005}';
const ENV = '{"_time": "2026-06-01 09:10:21.994007+02:00", "Temperature (°C)": 21.53, "Humidity (%)": 48.2}';

test('parseLbEvent reads a passage, keyed by its direction', () => {
    assert.deepEqual(parseLbEvent(EVENT), {
        at: '2026-06-08T16:39:00.123456+02:00',
        epochMs: Date.parse('2026-06-08T16:39:00.123+02:00'),
        duration: null,
        key: 'OUT_TO_IN',
        values: { duration: 0.123 },
        deltaT: 0.045,
        avgBlock: 0.084,
        triggers: 'ot+it',
    });
    assert.equal(parseLbEvent(PARTIAL).deltaT, null, 'one side only: no time between the sides');
    assert.equal(parseLbEvent('{"time": "2026-06-08T16:39:00+02:00"}'), null);
    assert.equal(parseLbEvent('not json'), null);
});

test('parseLbState reads a pair\'s state, also with the time as `timestamp` of the daemon before tsOS\'s conventions', () => {
    assert.deepEqual(parseLbState(STATE), {
        at: '2026-06-08T16:39:00.123456+02:00',
        epochMs: Date.parse('2026-06-08T16:39:00.123+02:00'),
        duration: null,
        key: 'ot',
        values: { diff: 1, active: 1.1, ambient: 0.1, duration_avg_s: 0.005, duration_max_s: 0.006 },
    });
    const old = parseLbState(STATE_OLD);
    assert.equal(old.key, 'ib');
    assert.equal(old.epochMs, Date.parse('2026-11-12T11:50:07.867+01:00'));
    assert.equal(parseLbState('{"time": "2026-06-08T16:39:00+02:00", "diff": 1}'), null, 'no pair');
});

test('parseEnvsense takes the sensor from the topic and every number of the reading', () => {
    assert.deepEqual(parseEnvsense(ENV, 'station/envsense/inside/json'), {
        at: '2026-06-01 09:10:21.994007+02:00',
        epochMs: Date.parse('2026-06-01T09:10:21.994+02:00'),
        duration: null,
        key: 'inside',
        values: { 'Temperature (°C)': 21.53, 'Humidity (%)': 48.2 },
    });
    assert.equal(parseEnvsense(ENV, 'station/envsense/json'), null, 'no sensor');
    assert.equal(parseEnvsense(ENV), null);
});

test('labels, units and orders', () => {
    assert.equal(pairLabel('ot'), 'Outer top (ot)');
    assert.equal(pairLabel('x'), 'x');
    assert.deepEqual(splitUnit('Temperature (°C)'), { label: 'Temperature', unit: '°C' });
    assert.deepEqual(splitUnit('Pressure'), { label: 'Pressure', unit: '' });
    assert.deepEqual(['ib', 'zz', 'ot', 'aa'].sort(orderBy(['ot', 'ob', 'it', 'ib'])), ['ot', 'ib', 'aa', 'zz']);
    assert.deepEqual(envValueNames([{ values: { 'In1 (V)': 1, 'Humidity (%)': 40 } }, { values: { 'Temperature (°C)': 20 } }]),
        ['Temperature (°C)', 'Humidity (%)', 'In1 (V)']);
});

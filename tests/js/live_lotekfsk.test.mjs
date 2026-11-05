// What the Lotek FSK tab reads: lotekfskd's JSON messages and how they are shown.
import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
    crcSummary,
    formatUptime,
    latestByKey,
    parseFskDaemon,
    parseFskDetection,
    parseFskMatched,
    parseFskState,
    stateClass,
    tagSummary,
} from '../../app/static/js/utils/liveLotekfsk.js';

// As tsos-fsk-7d0bb721 published them (pylotekfsk 0.2.0), and the examples of pylotekfsk's docs/mqtt.md
const DETECTION = '{"time": "2026-11-05T10:54:09.873+01:00", "synced": true, "slot": 3, "receiver_serial": 20260054, "tag_id": 420230102, "tag_id_hex": "0x190C33D6", "rssi_dbm": -47.5, "crc8": 0, "crc8_valid": false, "raw_data": "3FCF938159805A6C6021ECC22E9872"}';
const MATCHED = '{"time": "2026-11-05T10:56:22.039+01:00", "synced": true, "tag_id": 420230102, "tag_id_hex": "0x190C33D6", "receiver_count": 4, "duplicates": 0, "rssi_0": -58.0, "rssi_1": -70.0, "rssi_2": -47.5, "rssi_3": -47.0}';
const MATCHED_PARTLY = '{"time": "2026-09-21T16:13:20.123+02:00", "synced": true, "tag_id": 313210061, "tag_id_hex": "0x12AB34CD", "receiver_count": 2, "duplicates": 1, "rssi_0": -57.5, "rssi_1": -63.0, "rssi_2": null, "rssi_3": null}';
const RUNNING = '{"time": "2026-09-21T16:14:20+02:00", "synced": true, "slot": 0, "receiver_serial": 20260015, "usb_serial": "D2019BW6", "port": "/dev/ttyUSB0", "state": "running", "uptime_s": 3600.456, "detections": 42, "last_detection": "2026-09-21T16:14:15+02:00", "reconnects": 1, "frame_errors": 0, "error": null}';
const DISCONNECTED = '{"time": "2026-09-21T16:14:20+02:00", "synced": true, "slot": 2, "receiver_serial": null, "usb_serial": null, "port": null, "state": "disconnected", "uptime_s": null, "detections": 0, "last_detection": null, "reconnects": 0, "frame_errors": 0, "error": "No receiver with USB serial D2019NA9 found"}';
const DAEMON = '{"time": "2026-09-21T16:14:20+02:00", "synced": true, "version": "0.2.0", "uptime_s": 7200.5, "receivers_connected": 3, "receivers_total": 4, "detections": 1234, "matched": 456, "open_groups": 1, "dropped_invalid_crc": 0, "dropped_events": 0}';

test('parseFskMatched reads a transmission, an RSSI per slot that heard it', () => {
    assert.deepEqual(parseFskMatched(MATCHED), {
        at: '2026-11-05T10:56:22.039+01:00',
        epochMs: Date.parse('2026-11-05T10:56:22.039+01:00'),
        duration: null,
        key: '0x190C33D6',
        values: { 0: -58, 1: -70, 2: -47.5, 3: -47 },
        tag: '0x190C33D6',
        receivers: 4,
        duplicates: 0,
    });
    assert.deepEqual(parseFskMatched(MATCHED_PARTLY).values, { 0: -57.5, 1: -63 }, 'slots that did not hear it are left out');
    assert.equal(parseFskMatched(MATCHED_PARTLY).duplicates, 1);
});

test('parseFskDetection reads a report of one receiver, keyed by its slot', () => {
    assert.deepEqual(parseFskDetection(DETECTION), {
        at: '2026-11-05T10:54:09.873+01:00',
        epochMs: Date.parse('2026-11-05T10:54:09.873+01:00'),
        duration: null,
        key: '3',
        values: { 3: -47.5 },
        tag: '0x190C33D6',
        serial: 20260054,
        crcValid: false,
    });
    const withoutCrc = JSON.parse(DETECTION);
    delete withoutCrc.crc8;
    withoutCrc.crc8_valid = null;
    assert.equal(parseFskDetection(JSON.stringify(withoutCrc)).crcValid, null, 'a receiver that does not report the CRC');
});

test('parseFskState reads a receiver state, also of one that is gone', () => {
    const running = parseFskState(RUNNING);
    assert.equal(running.key, '0');
    assert.equal(running.state, 'running');
    assert.deepEqual(running.values, { slot: 0, receiver_serial: 20260015, uptime_s: 3600.456, detections: 42, reconnects: 1, frame_errors: 0 });
    assert.equal(running.port, '/dev/ttyUSB0');
    assert.equal(running.usbSerial, 'D2019BW6');
    assert.equal(running.lastDetection, '2026-09-21T16:14:15+02:00');
    assert.equal(running.error, null);

    const gone = parseFskState(DISCONNECTED);
    assert.equal(gone.key, '2');
    assert.deepEqual(gone.values, { slot: 2, detections: 0, reconnects: 0, frame_errors: 0 });
    assert.equal(gone.port, null);
    assert.equal(gone.error, 'No receiver with USB serial D2019NA9 found');
});

test('parseFskDaemon reads the daemon status', () => {
    const daemon = parseFskDaemon(DAEMON);
    assert.equal(daemon.version, '0.2.0');
    assert.equal(daemon.key, null);
    assert.equal(daemon.values.receivers_connected, 3);
    assert.equal(daemon.values.matched, 456);
});

test('the parsers take nothing that is not their record', () => {
    for (const payload of ['not json', '[1, 2]', 'null', '{}']) {
        assert.equal(parseFskMatched(payload), null, payload);
        assert.equal(parseFskDetection(payload), null, payload);
        assert.equal(parseFskState(payload), null, payload);
        assert.equal(parseFskDaemon(payload), null, payload);
    }
    assert.equal(parseFskDetection(MATCHED), null, 'a matched record has no slot');
    assert.equal(parseFskMatched('2026-11-05T10:56:22.039+01:00;true;420230102;0x190C33D6;4;0;-58.0;-70.0;-47.5;-47.0'), null, 'CSV');
});

test('tagSummary: per tag the count, the last time, the strongest RSSI per slot and of all, the last heard first', () => {
    const event = (time, tag, values) => ({ time, tag, values });
    const summary = tagSummary([
        event(100, '0xA', { 0: -60, 1: -70 }),
        event(101, '0xB', { 2: -50 }),
        event(102, '0xA', { 0: -55 }),
    ]);
    assert.deepEqual(summary, [
        { tag: '0xA', count: 2, lastMs: 102_000, best: -55, slots: { 0: -55, 1: -70 } },
        { tag: '0xB', count: 1, lastMs: 101_000, best: -50, slots: { 2: -50 } },
    ]);
    assert.deepEqual(tagSummary([]), []);
});

test('crcSummary counts the bad CRC8 of each tag, of the detections that report one', () => {
    assert.deepEqual(crcSummary([
        { tag: '0xA', crcValid: false },
        { tag: '0xA', crcValid: true },
        { tag: '0xA', crcValid: null },
        { tag: '0xB', crcValid: true },
    ]), { '0xA': { bad: 1, checked: 2 }, '0xB': { bad: 0, checked: 1 } });
});

test('latestByKey keeps the last event of each key', () => {
    assert.deepEqual(latestByKey([{ key: '0', n: 1 }, { key: '1', n: 2 }, { key: '0', n: 3 }]),
        { 0: { key: '0', n: 3 }, 1: { key: '1', n: 2 } });
});

test('stateClass: green while connected, red when it is not, grey once stopped', () => {
    assert.equal(stateClass('connected'), 'success');
    assert.equal(stateClass('running'), 'success');
    assert.equal(stateClass('unresponsive'), 'danger');
    assert.equal(stateClass('disconnected'), 'danger');
    assert.equal(stateClass('stopped'), 'secondary');
    assert.equal(stateClass(null), 'secondary');
});

test('formatUptime', () => {
    assert.equal(formatUptime(null), '–');
    assert.equal(formatUptime(42.4), '42 s');
    assert.equal(formatUptime(3599), '59 min');
    assert.equal(formatUptime(3600.456), '1 h 0 min');
    assert.equal(formatUptime(3 * 86400 + 7200), '3 d 2 h');
});

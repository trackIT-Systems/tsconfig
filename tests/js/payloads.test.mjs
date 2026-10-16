// What the pages send. Save and Save & Restart share one payload builder; they
// used to carry a copy each, and the copies had drifted apart.
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { radiotrackingConfig } from '../../app/static/js/components/radiotrackingConfig.js';
import { soundscapepipeConfig } from '../../app/static/js/components/soundscapepipeConfig.js';
import { tsupdateConfig } from '../../app/static/js/components/tsupdateConfig.js';
import { installBrowser } from './helpers.mjs';

test('tsupdate: Save converts every HH:MM field, as Save & Restart does', async t => {
    const browser = installBrowser();
    t.after(browser.restore);
    t.mock.timers.enable({ apis: ['setTimeout'] });
    const component = tsupdateConfig();
    Object.assign(component.config, {
        check_interval_hhmm: '02:00',
        persist_timeout_hhmm: '00:20',
        update_countdown_hhmm: '00:02',
        maintenance_check_interval_hhmm: '00:30',
        github_url: '',
    });

    await component.saveConfig();
    await component.saveAndRestartService();

    const [saved, restartSaved] = browser.calls.filter(c => c.method === 'PUT');
    assert.deepEqual(saved.body, restartSaved.body);
    assert.equal(saved.body.check_interval, 7200);
    assert.equal(saved.body.persist_timeout, 1200);
    assert.equal(saved.body.update_countdown, 120);
    assert.equal(saved.body.maintenance_check_interval, 1800);
    assert.equal(saved.body.github_url, null);
    assert.deepEqual(Object.keys(saved.body).filter(key => key.endsWith('_hhmm')), []);
});

test('tsupdate: optional maintenance fields that are empty are left out', () => {
    const component = tsupdateConfig();
    component.config.maintenance_check_interval = null;
    component.config.maintenance_check_interval_hhmm = '';
    component.config.maintenance_do = '';
    const payload = component.buildPayload();
    assert.equal('maintenance_check_interval' in payload, false);
    assert.equal('maintenance_do' in payload, false);
});

test('radiotracking: the page\'s keys are mapped to the API\'s', () => {
    const component = radiotrackingConfig();
    const payload = component.buildPayload();
    assert.deepEqual(Object.keys(payload), ['optional_arguments', 'rtl_sdr', 'analysis', 'matching', 'publish', 'dashboard']);
    assert.deepEqual(payload.rtl_sdr.device, component.config['rtl-sdr'].device);
    assert.equal(payload.optional_arguments.config, '/boot/firmware/radiotracking.ini');
    assert.equal(payload.optional_arguments.station, null);
});

test('soundscapepipe: only enabled detectors are sent, without their enabled flag and UI fields', () => {
    const component = soundscapepipeConfig();
    component.updateLureTaskTimeString = () => {};
    component.config.detectors = {
        birdedge: { enabled: true, threshold: 0.3, tasks: [{ name: 'night', start: '22:00', stop: '04:00', ui: 'x' }] },
        yolobat: { enabled: false, class_threshold: 0.5 },
    };
    component.config.lure = { tasks: [] };
    component.config.groups = {};

    const payload = component.buildPayload();

    assert.deepEqual(Object.keys(payload.detectors), ['birdedge']);
    assert.equal('enabled' in payload.detectors.birdedge, false);
    assert.deepEqual(payload.detectors.birdedge.tasks, [{ name: 'night', start: '22:00', stop: '04:00' }]);
});

test('soundscapepipe: lure times are synced before the payload is built (also for Save & Restart)', () => {
    const component = soundscapepipeConfig();
    const synced = [];
    component.updateLureTaskTimeString = (task, which) => synced.push(which);
    component.config.detectors = {};
    component.config.lure = { tasks: [{ species: 'owl', paths: ['a.wav'], start: '20:00', stop: '21:00', record: 1 }, { species: ' ' }] };
    component.config.groups = {};

    const payload = component.buildPayload();

    assert.deepEqual(synced, ['start', 'stop', 'start', 'stop']);
    // an incomplete task (no species) is dropped; the rest is normalised
    assert.deepEqual(payload.lure.tasks, [{ species: 'owl', paths: ['a.wav'], start: '20:00', stop: '21:00', record: true }]);
});

test('soundscapepipe: species groups are validated before saving', async t => {
    const browser = installBrowser();
    t.after(browser.restore);
    const component = soundscapepipeConfig();
    component.validateSpeciesGroups = () => ['Group "a": Species "x" is invalid'];

    await assert.rejects(component.saveConfig(), { message: 'Species validation failed' });

    assert.deepEqual(browser.calls, []);
    assert.equal(browser.toasts[0].type, 'error');
    assert.match(browser.toasts[0].message, /Species validation failed:\n• Group "a"/);
});

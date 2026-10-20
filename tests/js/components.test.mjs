// Every component module loads and builds its Alpine data object, with the
// members the templates use. Catches a broken import or a bad composition
// that would otherwise only show in the browser.
import assert from 'node:assert/strict';
import { readdirSync } from 'node:fs';
import { test } from 'node:test';
import { installBrowser } from './helpers.mjs';

const COMPONENTS = new URL('../../app/static/js/components/', import.meta.url);

// component file -> members its templates and the mixins rely on
const EXPECTED = {
    configUpload: ['serverMode', 'showMessage', 'uploadConfig'],
    configManager: ['init', 'activeConfig'],
    liveTabs: ['tabs', 'tab', 'select', 'setView', 'viewOptions', 'viewMs', 'init', 'destroy'],
    liveSignalPower: ['serverMode', 'source', 'setSource', 'calibratedHint', 'unit', 'yLabel', 'status', 'init', 'destroy'],
    liveFrequencyUsage: ['serverMode', 'yLabel', 'status', 'init', 'destroy'],
    liveSignalDetail: ['serverMode', 'param', 'paramOptions', 'setParam', 'collecting', 'toggleCollect', 'status', 'init', 'destroy'],
    liveDetections: ['serverMode', 'detectors', 'isShown', 'toggleDetector', 'status', 'init', 'destroy'],
    liveInputLevels: ['serverMode', 'yLabel', 'status', 'init', 'destroy'],
    logViewer: ['startStreaming'],
    networkConfig: ['serverMode', 'dispatchMessage', 'showMessage', 'streamLogs', 'init', 'destroy', 'every', 'listen'],
    radiotrackingConfig: ['serverMode', 'saveConfig', 'saveAndRestartService', 'deployConfig', 'serviceStatus', 'streamLogs', 'destroy'],
    resetConfig: ['serverMode', 'showMessage', 'executeReset'],
    scheduleConfig: ['serverMode', 'expertMode', 'saveConfig', 'saveAndRestartService', 'deployConfig', 'serviceStatus', 'streamLogs', 'destroy'],
    shellViewer: [],
    soundscapepipeConfig: ['serverMode', 'saveConfig', 'saveAndRestartService', 'deployConfig', 'serviceStatus', 'streamLogs', 'destroy'],
    sshKeysConfig: ['serverMode', 'sortedKeys', 'serviceStatus', 'streamLogs', 'destroy', 'loadKeys', 'addKey'],
    statusPage: ['initStatus', 'refreshStatus', 'rebootSystem', 'getFilteredDisks', 'mountpointLinks', 'isServiceAvailable', 'formatBytes', 'formatUptime', 'formatDateTime', 'showToast', 'destroy'],
    servicesList: ['services', 'servicesLoading', 'getFilteredServices', 'performAction', 'performStartStopAction', 'getServiceRestartState', 'getServiceStartStopState', 'streamLogs', 'streamAllLogs', 'loadServices', 'destroy'],
    geolocationMap: ['geolocation', 'loadGeolocation', 'initGeolocationMap', 'updateDistanceLine', 'getTrackerIcon', 'destroy'],
    radiotrackingSummary: ['radiotrackingConfig', 'radiotrackingLoading', 'destroy'],
    powerSummary: ['title', 'powerDevice', 'powerBlock', 'powerReading', 'powerReadError', 'powerGroups', 'formatPowerValue', 'readNow', 'destroy'],
    soundscapepipeSummary: ['soundscapepipeConfig', 'soundscapepipeLoading', 'soundscapepipeService', 'getDetectorTasks', 'getRecordingGroups', 'destroy'],
    audioStreamPlayer: ['streamAvailable', 'checkingStream', 'audioConnected', 'checkStreamAvailability', 'handleAudioPause', 'handleAudioPlay', 'disconnectAudio', 'reconnectAudio', 'destroy'],
    tsupdateConfig: ['serverMode', 'saveConfig', 'saveAndRestartService', 'deployConfig', 'serviceStatus', 'streamLogs', 'destroy'],
    userconfConfig: ['serverMode', 'showMessage', 'setPassword'],
};

test('the list above covers every component', () => {
    const files = readdirSync(COMPONENTS).filter(name => name.endsWith('.js')).map(name => name.slice(0, -3));
    assert.deepEqual(files.sort(), Object.keys(EXPECTED).sort());
});

for (const [name, members] of Object.entries(EXPECTED)) {
    test(`${name} builds and has what the templates use`, async t => {
        const browser = installBrowser();
        t.after(browser.restore);
        const module = await import(new URL(`${name}.js`, COMPONENTS));
        const component = module[name]();
        for (const member of members) {
            assert.ok(member in component, `${name} lacks ${member}`);
        }
    });
}

test('serverMode is a live getter on the pages that use it', async t => {
    const browser = installBrowser();
    t.after(browser.restore);
    const { serverModeManager } = await import('../../app/static/js/managers/serverModeManager.js');
    const { scheduleConfig } = await import(new URL('scheduleConfig.js', COMPONENTS));
    const component = scheduleConfig();
    assert.equal(component.serverMode, false);
    serverModeManager.enabled = true;
    assert.equal(component.serverMode, true);
});

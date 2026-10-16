// Main entry point - imports all components and exposes them for Alpine.js

// Import managers (shared singletons)
import { serverModeManager } from './managers/serverModeManager.js';

// Import utilities
import { parseTimeString, updateTimeString } from './utils/timeUtils.js';

// Import components
import { configManager } from './components/configManager.js';
import { scheduleConfig } from './components/scheduleConfig.js';
import { sshKeysConfig } from './components/sshKeysConfig.js';
import { userconfConfig } from './components/userconfConfig.js';
import { configUpload } from './components/configUpload.js';
import { resetConfig } from './components/resetConfig.js';
import { networkConfig } from './components/networkConfig.js';
import { radiotrackingConfig } from './components/radiotrackingConfig.js';
import { soundscapepipeConfig } from './components/soundscapepipeConfig.js';
import { tsupdateConfig } from './components/tsupdateConfig.js';
import { mqttutilConfig } from './components/mqttutilConfig.js';
import { statusPage } from './components/statusPage.js';
import { logViewer } from './components/logViewer.js';
import { shellViewer } from './components/shellViewer.js';

// Expose components globally for Alpine.js x-data directives
window.configManager = configManager;
window.scheduleConfig = scheduleConfig;
window.sshKeysConfig = sshKeysConfig;
window.userconfConfig = userconfConfig;
window.configUpload = configUpload;
window.resetConfig = resetConfig;
window.networkConfig = networkConfig;
window.radiotrackingConfig = radiotrackingConfig;
window.soundscapepipeConfig = soundscapepipeConfig;
window.tsupdateConfig = tsupdateConfig;
window.mqttutilConfig = mqttutilConfig;
window.statusPage = statusPage;
window.logViewer = logViewer;
window.shellViewer = shellViewer;

// Expose what templates use: the server mode manager (config group links in the navbar)
window.serverModeManager = serverModeManager;

// Expose utility functions that may be called from templates
window.parseTimeString = parseTimeString;
window.updateTimeString = updateTimeString;

// Signal that modules are ready for Alpine.js
window.appModulesReady = true;
window.dispatchEvent(new CustomEvent('app-modules-ready'));

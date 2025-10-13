// Main entry point - imports all components and exposes them for Alpine.js

// Import managers (shared singletons)
import { systemConfigManager } from './managers/systemConfigManager.js';
import { serviceManager } from './managers/serviceManager.js';

// Import utilities
import { getSystemRefreshInterval } from './utils/systemUtils.js';
import { parseTimeString, updateTimeString } from './utils/timeUtils.js';

// Import mixins
import { saveStateMixin } from './mixins/saveStateMixin.js';

// Import components
import { configManager } from './components/configManager.js';
import { scheduleConfig } from './components/scheduleConfig.js';
import { radiotrackingConfig } from './components/radiotrackingConfig.js';
import { soundscapepipeConfig } from './components/soundscapepipeConfig.js';
import { statusPage } from './components/statusPage.js';
import { logViewer } from './components/logViewer.js';
import { shellViewer } from './components/shellViewer.js';

// Expose components globally for Alpine.js x-data directives
window.configManager = configManager;
window.scheduleConfig = scheduleConfig;
window.radiotrackingConfig = radiotrackingConfig;
window.soundscapepipeConfig = soundscapepipeConfig;
window.statusPage = statusPage;
window.logViewer = logViewer;
window.shellViewer = shellViewer;

// Expose managers globally (they may be accessed from components)
window.systemConfigManager = systemConfigManager;
window.serviceManager = serviceManager;

// Expose utility functions that may be called from templates
window.getSystemRefreshInterval = getSystemRefreshInterval;
window.parseTimeString = parseTimeString;
window.updateTimeString = updateTimeString;
window.saveStateMixin = saveStateMixin;

// Signal that modules are ready for Alpine.js
window.appModulesReady = true;
window.dispatchEvent(new CustomEvent('app-modules-ready'));

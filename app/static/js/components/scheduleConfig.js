import { compose } from '../mixins/compose.js';
import { configPageMixin } from '../mixins/configPageMixin.js';
import { serverModeManager } from '../managers/serverModeManager.js';
import { parseTimeString, updateTimeString } from '../utils/timeUtils.js';

export function scheduleConfig() {
    return compose(configPageMixin({
        service: 'tsschedule',
        apiPath: '/api/schedule',
        title: 'Schedule Configuration',
        failureText: 'Failed to save schedule configuration',
        refreshOn: ['settings'],
    }), {
        config: {
            force_on: false,
            button_delay: "00:00",
            recovery_interval: "00:00",
            recovery_guard: "00:00",
            schedule: []
        },
        _expertMode: false, // Internal reactive property for expert mode

        // Expert mode - reactive property that gets updated from URL
        get expertMode() {
            return this._expertMode;
        },

        updateExpertMode() {
            // Read expert mode from URL parameter (same source as configManager uses)
            const urlParams = new URLSearchParams(window.location.search);
            const newExpertMode = urlParams.get('expert') === 'true';
            if (this._expertMode !== newExpertMode) {
                this._expertMode = newExpertMode;
            }
        },

        async refreshConfig() {
            try {
                // Reset to initial state
                this.config = {
                    force_on: false,
                    button_delay: "00:00",
                    recovery_interval: "00:00",
                    recovery_guard: "00:00",
                    schedule: []
                };
                
                // Clear any existing messages (now handled by toasts)
                
                // Reload the configuration
                await this.loadConfig();
            } catch (error) {
                this.showMessage(error.message, true);
            }
        },

        async loadConfig() {
            try {
                // Build API URL with config_group parameter if in server mode
                const url = serverModeManager.buildApiUrl('/api/schedule');
                const response = await fetch(url);
                if (response.status === 404) {
                    // Set default configuration for schedule
                    this.config = {
                        force_on: false,
                        button_delay: "01:00",
                        recovery_interval: "00:00",
                        recovery_guard: "00:00",
                        schedule: []
                    };
                    this.showMessage("No schedule configuration found. Using default values.", false);
                    return;
                }
                if (!response.ok) {
                    throw new Error('Failed to load schedule configuration');
                }
                const data = await response.json();
                this.config = data;
                this.config.recovery_interval = data.recovery_interval ?? "00:00";
                this.config.recovery_guard = data.recovery_guard ?? "00:00";
                
                // Process schedule entries to add UI helper properties
                this.config.schedule.forEach(entry => {
                    const startParts = parseTimeString(entry.start);
                    entry.startReference = startParts.reference;
                    entry.startSign = startParts.sign;
                    entry.startOffset = startParts.offset;
                    
                    const stopParts = parseTimeString(entry.stop);
                    entry.stopReference = stopParts.reference;
                    entry.stopSign = stopParts.sign;
                    entry.stopOffset = stopParts.offset;
                });
                
                // Load service status only in tracker mode (default mode)
                if (!this.serverMode) {
                    await this.loadServiceStatus();
                }
            } catch (error) {
                this.showMessage(error.message, true);
            }
        },

        async init() {
            // Small delay to prevent simultaneous API calls during page load
            await new Promise(resolve => setTimeout(resolve, 50));

            // Expert mode is stored in the URL; history.replaceState does not
            // trigger events, so poll it as well
            this.updateExpertMode();
            this.every(100, () => this.updateExpertMode());
            this.listen(window, 'popstate', () => this.updateExpertMode());
            this.listen(window, 'hashchange', () => this.updateExpertMode());

            await this.loadData();
            await this.startServiceStatusRefresh();
            this.listen(window, 'config-group-changed', () => this.loadData());
        },

        // Tell other components (e.g. tsupdate) that the schedule changed
        onSaved() {
            window.dispatchEvent(new CustomEvent('schedule-config-saved'));
        },

        parseTimeString,
        updateTimeString,

        addSchedule() {
            this.config.schedule.push({
                name: '',
                start: '00:00',
                stop: '00:00',
                startReference: 'time',
                startOffset: '00:00',
                startSign: '+',
                stopReference: 'time',
                stopOffset: '00:00',
                stopSign: '+'
            });
        },

        checkDuplicateMaintenanceName(name, currentIndex) {
            // Check if another entry (not the current one) already has the name 'maintenance'
            if (name === 'maintenance') {
                const duplicateIndex = this.config.schedule.findIndex((entry, index) => 
                    entry.name === 'maintenance' && index !== currentIndex
                );
                return duplicateIndex !== -1;
            }
            return false;
        },

        isFirstMaintenanceEntry(index) {
            // Check if this is the first entry with name 'maintenance'
            const entry = this.config.schedule[index];
            if (!entry || entry.name !== 'maintenance') {
                return false;
            }
            // Find the first maintenance entry index
            const firstMaintenanceIndex = this.config.schedule.findIndex(e => e.name === 'maintenance');
            return firstMaintenanceIndex === index;
        },

        isMaintenanceEntryProtected(index) {
            // Maintenance entry is only protected if it's the first one AND not in expert mode
            if (!this.isFirstMaintenanceEntry(index)) {
                return false;
            }
            // expertMode is a reactive property that gets updated via polling
            return !this.expertMode;
        },

        removeSchedule(index) {
            const entry = this.config.schedule[index];
            if (this.isMaintenanceEntryProtected(index)) {
                this.showMessage('Cannot delete the "maintenance" schedule entry. Enable expert mode to delete it.', true);
                return;
            }
            this.config.schedule.splice(index, 1);
        },

    });
}

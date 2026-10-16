import { compose } from '../mixins/compose.js';
import { configPageMixin } from '../mixins/configPageMixin.js';
import { serverModeManager } from '../managers/serverModeManager.js';
import { parseTimeString, updateTimeString } from '../utils/timeUtils.js';

export function tsupdateConfig() {
    return compose(configPageMixin({
        service: 'tsupdate',
        apiPath: '/api/tsupdate',
        title: 'Updates Configuration',
        failureText: 'Failed to save tsupdate configuration',
        refreshOn: ['settings'],
    }), {
        config: {
            check_interval: 3600,
            check_interval_hhmm: '01:00', // UI representation in HH:MM format
            include_prereleases: false,
            github_url: null,
            max_releases: 5,
            persist_timeout: 600,
            persist_timeout_hhmm: '00:10', // UI representation in HH:MM format
            update_countdown: 60,
            update_countdown_hhmm: '00:01', // UI representation in HH:MM format
            do: 'nothing',
            maintenance_check_interval: 3600,
            maintenance_check_interval_hhmm: '01:00', // UI representation in HH:MM format
            maintenance_do: 'check'
        },
        
        // Maintenance schedule entry from schedule.yml (read by tsupdate)
        maintenanceSchedule: null,
        maintenanceScheduleLoading: false,

        // Helper function to convert seconds to HH:MM format
        secondsToHHMM(seconds) {
            const hours = Math.floor(seconds / 3600);
            const minutes = Math.floor((seconds % 3600) / 60);
            return `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}`;
        },

        // Helper function to convert HH:MM format to seconds
        hhmmToSeconds(hhmm) {
            if (!hhmm || typeof hhmm !== 'string') {
                return 3600; // Default 1 hour
            }
            const parts = hhmm.split(':');
            if (parts.length !== 2) {
                return 3600; // Default 1 hour
            }
            const hours = parseInt(parts[0], 10) || 0;
            const minutes = parseInt(parts[1], 10) || 0;
            return (hours * 3600) + (minutes * 60);
        },
        
        async loadData() {
            await this.loadConfig();
            await this.loadMaintenanceSchedule();
        },

        subscribe() {
            // Refresh the maintenance schedule display when the schedule was saved
            this.listen(window, 'schedule-config-saved', () => this.loadMaintenanceSchedule());
        },

        // The config as the API wants it: the page edits the intervals as HH:MM
        buildPayload() {
            const payload = { ...this.config };
            payload.check_interval = this.hhmmToSeconds(payload.check_interval_hhmm);
            payload.persist_timeout = this.hhmmToSeconds(payload.persist_timeout_hhmm);
            payload.update_countdown = this.hhmmToSeconds(payload.update_countdown_hhmm);
            if (payload.maintenance_check_interval_hhmm && payload.maintenance_check_interval_hhmm.trim() !== '') {
                payload.maintenance_check_interval = this.hhmmToSeconds(payload.maintenance_check_interval_hhmm);
            }
            // Remove UI-only fields
            delete payload.check_interval_hhmm;
            delete payload.persist_timeout_hhmm;
            delete payload.update_countdown_hhmm;
            delete payload.maintenance_check_interval_hhmm;

            // Remove null/empty optional maintenance fields
            if (payload.maintenance_check_interval === null || payload.maintenance_check_interval === undefined) {
                delete payload.maintenance_check_interval;
            }
            if (payload.maintenance_do === null || payload.maintenance_do === undefined || payload.maintenance_do === '') {
                delete payload.maintenance_do;
            }
            if (payload.github_url === '') {
                payload.github_url = null;
            }
            return payload;
        },

        // Load maintenance schedule entry from schedule.yml
        async loadMaintenanceSchedule() {
            this.maintenanceScheduleLoading = true;
            try {
                // Build API URL with config_group parameter if in server mode
                const url = serverModeManager.buildApiUrl('/api/schedule');
                const response = await fetch(url);
                if (response.status === 404) {
                    this.maintenanceSchedule = null;
                    return;
                }
                if (!response.ok) {
                    console.warn('Failed to load schedule configuration for maintenance interval');
                    this.maintenanceSchedule = null;
                    return;
                }
                const scheduleData = await response.json();
                
                // Find the maintenance entry
                const maintenanceEntry = scheduleData.schedule?.find(entry => entry.name === 'maintenance');
                
                if (maintenanceEntry) {
                    // Process the entry to add UI helper properties (same as scheduleConfig does)
                    const startParts = parseTimeString(maintenanceEntry.start);
                    const stopParts = parseTimeString(maintenanceEntry.stop);
                    
                    this.maintenanceSchedule = {
                        name: maintenanceEntry.name,
                        start: maintenanceEntry.start,
                        stop: maintenanceEntry.stop,
                        startReference: startParts.reference,
                        startSign: startParts.sign,
                        startOffset: startParts.offset,
                        stopReference: stopParts.reference,
                        stopSign: stopParts.sign,
                        stopOffset: stopParts.offset
                    };
                } else {
                    this.maintenanceSchedule = null;
                }
            } catch (error) {
                console.error('Failed to load maintenance schedule:', error);
                this.maintenanceSchedule = null;
            } finally {
                this.maintenanceScheduleLoading = false;
            }
        },
        
        // Expose parseTimeString and updateTimeString for use in template
        parseTimeString,
        updateTimeString,

        async refreshConfig() {
            try {
                // Reset to initial state
                this.config = {
                    check_interval: 3600,
                    check_interval_hhmm: '01:00',
                    include_prereleases: false,
                    github_url: null,
                    max_releases: 5,
                    persist_timeout: 600,
                    persist_timeout_hhmm: '00:10',
                    update_countdown: 60,
                    update_countdown_hhmm: '00:01',
                    do: 'nothing',
                    maintenance_check_interval: 3600,
                    maintenance_check_interval_hhmm: '01:00',
                    maintenance_do: 'check'
                };
                
                // Reload the configuration
                await this.loadConfig();
            } catch (error) {
                this.showMessage(error.message, true);
            }
        },

        async loadConfig() {
            try {
                // Build API URL with config_group parameter if in server mode
                const url = serverModeManager.buildApiUrl('/api/tsupdate');
                const response = await fetch(url);
                if (response.status === 404) {
                    // Set default configuration
                    this.config = {
                        check_interval: 3600,
                        check_interval_hhmm: '01:00',
                        include_prereleases: false,
                        github_url: null,
                        max_releases: 5,
                        persist_timeout: 600,
                        persist_timeout_hhmm: '00:10',
                        update_countdown: 60,
                        update_countdown_hhmm: '00:01',
                        do: 'nothing',
                        maintenance_check_interval: 3600,
                        maintenance_check_interval_hhmm: '01:00',
                        maintenance_do: 'check'
                    };
                    this.showMessage("No tsupdate configuration found. Using default values.", false);
                    return;
                }
                if (!response.ok) {
                    throw new Error('Failed to load tsupdate configuration');
                }
                const data = await response.json();
                // Ensure github_url is null if empty string
                if (data.github_url === '') {
                    data.github_url = null;
                }
                // Convert time fields from seconds to HH:MM format for UI
                data.check_interval_hhmm = this.secondsToHHMM(data.check_interval || 3600);
                data.persist_timeout_hhmm = this.secondsToHHMM(data.persist_timeout || 600);
                data.update_countdown_hhmm = this.secondsToHHMM(data.update_countdown || 60);
                // Convert maintenance_check_interval if present, otherwise use default
                if (data.maintenance_check_interval !== null && data.maintenance_check_interval !== undefined) {
                    data.maintenance_check_interval_hhmm = this.secondsToHHMM(data.maintenance_check_interval);
                } else {
                    data.maintenance_check_interval = 3600;
                    data.maintenance_check_interval_hhmm = '01:00';
                }
                // Ensure do has a default value
                if (!data.do) {
                    data.do = 'nothing';
                }
                // Ensure maintenance_do has a default value
                if (!data.maintenance_do) {
                    data.maintenance_do = 'check';
                }
                this.config = data;
                
                // Load service status only in tracker mode (default mode)
                if (!this.serverMode) {
                    await this.loadServiceStatus();
                }
            } catch (error) {
                this.showMessage(error.message, true);
            }
        },

    });
}

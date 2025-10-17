import { saveStateMixin } from '../mixins/saveStateMixin.js';
import { serviceActionMixin } from '../mixins/serviceActionMixin.js';
import { serviceManager } from '../managers/serviceManager.js';
import { getSystemRefreshInterval } from '../utils/systemUtils.js';
import { apiUrl } from '../utils/apiUtils.js';

export function radiotrackingConfig() {
    return {
        ...saveStateMixin(),
        ...serviceActionMixin(),
        
        // Server mode helper
        get serverMode() {
            return window.serverModeManager?.isEnabled() || false;
        },
        
        config: {
            "rtl-sdr": {
                device: [],
                calibration: [],
                center_freq: 0,
                sample_rate: 0,
                sdr_callback_length: null,
                lna_gain: 0,
                mixer_gain: 0,
                vga_gain: 0,
                gain: 0,
                sdr_max_restart: 0,
                sdr_timeout_s: 0
            },
            "analysis": {
                signal_threshold_dbw: 0,
                snr_threshold_db: 0,
                signal_min_duration_ms: 0,
                signal_max_duration_ms: 0,
                fft_nperseg: 0,
                fft_window: ""
            },
            "matching": {
                matching_timeout_s: 0,
                matching_time_diff_s: 0,
                matching_bandwidth_hz: 0,
                matching_duration_diff_ms: 0
            },
            "publish": {
                path: "",
                mqtt_host: "",
                mqtt_port: 0,
                sig_stdout: false,
                match_stdout: false,
                csv: false,
                export_config: true,
                mqtt: false
            },
            "dashboard": {
                dashboard: false,
                dashboard_host: "",
                dashboard_port: 0,
                dashboard_signals: 0
            },
            "optional arguments": {
                verbose: 0,
                calibrate: false,
                config: "/boot/firmware/radiotracking.ini",
                station: null,
                schedule: []
            }
        },
        configLoaded: false,
        deviceCount: 1,
        isLoading: true,
        // Service status tracking
        serviceStatus: {
            active: false,
            enabled: false,
            status: 'unknown',
            uptime: 'N/A'
        },
        serviceStatusLoading: false,
        refreshInterval: null, // For periodic service status refresh
        actionLoading: false,

        dispatchMessage(message, isError) {
            // Use global toast manager for immediate feedback
            if (window.toastManager) {
                const type = isError ? 'error' : 'success';
                const title = isError ? 'Radio Tracking Error' : 'Radio Tracking';
                window.toastManager.show(message, type, { title });
            } else {
                // Fallback: dispatch to parent component for legacy support
                window.dispatchEvent(new CustomEvent('show-message', {
                    detail: { message, isError }
                }));
            }
        },

        addDevice() {
            // Add a new device with default values
            if (!this.configLoaded) return;
            this.config["rtl-sdr"].device.push('0');
            this.config["rtl-sdr"].calibration.push(0.0);
        },

        removeDevice(index) {
            // Only remove if there's more than one device
            if (!this.configLoaded) return;
            if (this.config["rtl-sdr"].device.length > 1) {
                this.config["rtl-sdr"].device.splice(index, 1);
                this.config["rtl-sdr"].calibration.splice(index, 1);
            }
        },

        updateDeviceList() {
            // Update device list based on deviceCount
            if (!this.configLoaded) return;
            
            const currentLength = this.config["rtl-sdr"].device.length;
            const targetLength = parseInt(this.deviceCount);
            
            if (targetLength > currentLength) {
                // Add new devices
                for (let i = currentLength; i < targetLength; i++) {
                    this.config["rtl-sdr"].device.push(i.toString());
                    this.config["rtl-sdr"].calibration.push(0.0);
                }
            } else if (targetLength < currentLength) {
                // Remove devices
                this.config["rtl-sdr"].device.splice(targetLength);
                this.config["rtl-sdr"].calibration.splice(targetLength);
            }
        },

        async init() {
            // Small delay to prevent simultaneous API calls during page load
            await new Promise(resolve => setTimeout(resolve, 100));
            
            // Load configuration and set up periodic refresh
            await this.loadConfig();
            await this.setupPeriodicRefresh();
            
            // Listen for config group changes in server mode
            window.addEventListener('config-group-changed', async () => {
                await this.loadConfig();
            });
        },

        async setupPeriodicRefresh() {
            // Get the refresh interval from system config
            const refreshIntervalSeconds = await getSystemRefreshInterval();
            
            // Set up periodic refresh for service status
            if (this.refreshInterval) {
                clearInterval(this.refreshInterval);
            }
            
            this.refreshInterval = setInterval(() => {
                // Only refresh if radiotracking tab is active
                const currentHash = window.location.hash.slice(1);
                if (currentHash === 'radiotracking') {
                    this.loadServiceStatus();
                }
            }, refreshIntervalSeconds * 1000);
        },

        cleanup() {
            // Clean up interval when component is destroyed
            if (this.refreshInterval) {
                clearInterval(this.refreshInterval);
                this.refreshInterval = null;
            }
        },

        async loadServiceStatus() {
            this.serviceStatusLoading = true;
            try {
                const services = await serviceManager.getServices();
                const radiotrackingService = services.find(service => service.name === 'radiotracking');
                if (radiotrackingService) {
                    this.serviceStatus = {
                        active: radiotrackingService.active,
                        enabled: radiotrackingService.enabled,
                        status: radiotrackingService.status,
                        uptime: radiotrackingService.uptime || 'N/A'
                    };
                }
            } catch (error) {
                console.error('Failed to load service status:', error);
            } finally {
                this.serviceStatusLoading = false;
            }
        },

        async loadConfig() {
            try {
                this.isLoading = true;
                this.configLoaded = false;
                // Build API URL with config_group parameter if in server mode
                const url = window.serverModeManager?.buildApiUrl('/api/radiotracking') || '/api/radiotracking';
                const response = await fetch(url);
                if (response.status === 404) {
                    this.dispatchMessage("No radio tracking configuration found. Please create a configuration file first.", true);
                    this.isLoading = false;
                    return;
                }
                if (!response.ok) {
                    throw new Error('Failed to load radio tracking configuration');
                }
                const data = await response.json();
                // Update the existing config object instead of replacing it
                Object.assign(this.config, data);
                this.configLoaded = true;
                this.deviceCount = this.config["rtl-sdr"].device.length;
                
                // Load service status only in tracker mode (default mode)
                if (!this.serverMode) {
                    await this.loadServiceStatus();
                }
            } catch (error) {
                this.dispatchMessage(error.message, true);
            } finally {
                this.isLoading = false;
            }
        },

        // Add computed property for center frequency in MHz
        get centerFreqMHz() {
            if (!this.configLoaded) return 0;
            return this.config["rtl-sdr"].center_freq / 1000000;
        },
        set centerFreqMHz(value) {
            if (!this.configLoaded) return;
            this.config["rtl-sdr"].center_freq = Math.round(value * 1000000);
        },

        // Add method to update center frequency
        updateCenterFreq() {
            // Ensure the value is within valid ranges
            const value = this.centerFreqMHz;
            if (value < 24) {
                this.centerFreqMHz = 24;
            } else if (value > 1766) {
                this.centerFreqMHz = 1766;
            }
        },

        // Add computed property for sample rate in kHz
        get sampleRateKHz() {
            if (!this.configLoaded) return 0;
            return Math.round(this.config["rtl-sdr"].sample_rate / 1000);
        },
        set sampleRateKHz(value) {
            if (!this.configLoaded) return;
            this.config["rtl-sdr"].sample_rate = value * 1000;
        },

        // Add computed property for bandwidth in kHz
        get bandwidthKHz() {
            if (!this.configLoaded) return 0;
            return this.config["matching"].matching_bandwidth_hz / 1000;
        },
        set bandwidthKHz(value) {
            if (!this.configLoaded) return;
            this.config["matching"].matching_bandwidth_hz = Math.round(value * 1000);
        },

        updateSampleRate() {
            // Ensure the value is within valid ranges
            const value = this.sampleRateKHz;
            if (value < 230 || (value > 300 && value < 900) || value > 3200) {
                // If outside valid ranges, set to nearest valid value
                if (value < 230) {
                    this.sampleRateKHz = 230;
                } else if (value > 300 && value < 900) {
                    this.sampleRateKHz = 300;
                } else if (value > 3200) {
                    this.sampleRateKHz = 3200;
                }
            }
        },

        updateBandwidth() {
            // Ensure the value is valid (minimum 0.1 kHz)
            const value = this.bandwidthKHz;
            if (value < 0.1) {
                this.bandwidthKHz = 0.1;
            }
        },

        async resetConfig() {
            // Reset by reloading from file
            await this.loadConfig();
        },

        async saveConfig() {
            const configSaveFunction = async () => {
                // Convert the frontend config format to the API format
                const apiConfig = {
                    optional_arguments: {
                        verbose: this.config["optional arguments"].verbose,
                        calibrate: this.config["optional arguments"].calibrate,
                        config: "/boot/firmware/radiotracking.ini", // Required field
                        station: this.config["optional arguments"].station || null,
                        schedule: this.config["optional arguments"].schedule || []
                    },
                    rtl_sdr: {
                        device: this.config["rtl-sdr"].device,
                        calibration: this.config["rtl-sdr"].calibration,
                        center_freq: this.config["rtl-sdr"].center_freq,
                        sample_rate: this.config["rtl-sdr"].sample_rate,
                        sdr_callback_length: this.config["rtl-sdr"].sdr_callback_length,
                        gain: this.config["rtl-sdr"].gain,
                        lna_gain: this.config["rtl-sdr"].lna_gain,
                        mixer_gain: this.config["rtl-sdr"].mixer_gain,
                        vga_gain: this.config["rtl-sdr"].vga_gain,
                        sdr_max_restart: this.config["rtl-sdr"].sdr_max_restart,
                        sdr_timeout_s: this.config["rtl-sdr"].sdr_timeout_s
                    },
                    analysis: {
                        fft_nperseg: this.config["analysis"].fft_nperseg,
                        fft_window: this.config["analysis"].fft_window,
                        signal_threshold_dbw: this.config["analysis"].signal_threshold_dbw,
                        snr_threshold_db: this.config["analysis"].snr_threshold_db,
                        signal_min_duration_ms: this.config["analysis"].signal_min_duration_ms,
                        signal_max_duration_ms: this.config["analysis"].signal_max_duration_ms
                    },
                    matching: {
                        matching_timeout_s: this.config["matching"].matching_timeout_s,
                        matching_time_diff_s: this.config["matching"].matching_time_diff_s,
                        matching_bandwidth_hz: this.config["matching"].matching_bandwidth_hz,
                        matching_duration_diff_ms: this.config["matching"].matching_duration_diff_ms
                    },
                    publish: {
                        sig_stdout: this.config["publish"].sig_stdout,
                        match_stdout: this.config["publish"].match_stdout,
                        path: this.config["publish"].path,
                        csv: this.config["publish"].csv,
                        export_config: this.config["publish"].export_config || true,
                        mqtt: this.config["publish"].mqtt,
                        mqtt_host: this.config["publish"].mqtt_host,
                        mqtt_port: this.config["publish"].mqtt_port
                    },
                    dashboard: {
                        dashboard: this.config["dashboard"].dashboard,
                        dashboard_host: this.config["dashboard"].dashboard_host,
                        dashboard_port: this.config["dashboard"].dashboard_port,
                        dashboard_signals: this.config["dashboard"].dashboard_signals
                    }
                };

                // Build API URL with config_group parameter if in server mode
                const url = window.serverModeManager?.buildApiUrl('/api/radiotracking') || '/api/radiotracking';
                const response = await fetch(url, {
                    method: 'PUT',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify(apiConfig)
                });
                if (!response.ok) {
                    const error = await response.json();
                    let errorMessage = error.detail?.message || 'Failed to save radio tracking configuration';
                    if (error.detail?.errors) {
                        errorMessage += '\nValidation errors: ' + error.detail.errors.join(', ');
                    }
                    if (error.detail?.validation_errors) {
                        const validationErrors = error.detail.validation_errors.map(err => 
                            `${err.loc.join('.')}: ${err.msg}`
                        ).join(', ');
                        errorMessage += '\nValidation errors: ' + validationErrors;
                    }
                    throw new Error(errorMessage);
                }
                const data = await response.json();
                this.dispatchMessage(data.message, false);  // false means not an error, so it will be success
            };
            
            await this.handleSaveConfig(configSaveFunction);
        },

        async saveAndRestartService() {
            const configSaveFunction = async () => {
                // Convert the frontend config format to the API format
                const apiConfig = {
                    optional_arguments: {
                        verbose: this.config["optional arguments"].verbose,
                        calibrate: this.config["optional arguments"].calibrate,
                        config: "/boot/firmware/radiotracking.ini", // Required field
                        station: this.config["optional arguments"].station || null,
                        schedule: this.config["optional arguments"].schedule || []
                    },
                    rtl_sdr: {
                        device: this.config["rtl-sdr"].device,
                        calibration: this.config["rtl-sdr"].calibration,
                        center_freq: this.config["rtl-sdr"].center_freq,
                        sample_rate: this.config["rtl-sdr"].sample_rate,
                        sdr_callback_length: this.config["rtl-sdr"].sdr_callback_length,
                        gain: this.config["rtl-sdr"].gain,
                        lna_gain: this.config["rtl-sdr"].lna_gain,
                        mixer_gain: this.config["rtl-sdr"].mixer_gain,
                        vga_gain: this.config["rtl-sdr"].vga_gain,
                        sdr_max_restart: this.config["rtl-sdr"].sdr_max_restart,
                        sdr_timeout_s: this.config["rtl-sdr"].sdr_timeout_s
                    },
                    analysis: {
                        fft_nperseg: this.config["analysis"].fft_nperseg,
                        fft_window: this.config["analysis"].fft_window,
                        signal_threshold_dbw: this.config["analysis"].signal_threshold_dbw,
                        snr_threshold_db: this.config["analysis"].snr_threshold_db,
                        signal_min_duration_ms: this.config["analysis"].signal_min_duration_ms,
                        signal_max_duration_ms: this.config["analysis"].signal_max_duration_ms
                    },
                    matching: {
                        matching_timeout_s: this.config["matching"].matching_timeout_s,
                        matching_time_diff_s: this.config["matching"].matching_time_diff_s,
                        matching_bandwidth_hz: this.config["matching"].matching_bandwidth_hz,
                        matching_duration_diff_ms: this.config["matching"].matching_duration_diff_ms
                    },
                    publish: {
                        sig_stdout: this.config["publish"].sig_stdout,
                        match_stdout: this.config["publish"].match_stdout,
                        path: this.config["publish"].path,
                        csv: this.config["publish"].csv,
                        export_config: this.config["publish"].export_config || true,
                        mqtt: this.config["publish"].mqtt,
                        mqtt_host: this.config["publish"].mqtt_host,
                        mqtt_port: this.config["publish"].mqtt_port
                    },
                    dashboard: {
                        dashboard: this.config["dashboard"].dashboard,
                        dashboard_host: this.config["dashboard"].dashboard_host,
                        dashboard_port: this.config["dashboard"].dashboard_port,
                        dashboard_signals: this.config["dashboard"].dashboard_signals
                    }
                };

                // Build API URL with config_group parameter if in server mode
                const url = window.serverModeManager?.buildApiUrl('/api/radiotracking') || '/api/radiotracking';
                const response = await fetch(url, {
                    method: 'PUT',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify(apiConfig)
                });
                if (!response.ok) {
                    const error = await response.json();
                    let errorMessage = error.detail?.message || 'Failed to save radio tracking configuration';
                    if (error.detail?.errors) {
                        errorMessage += '\nValidation errors: ' + error.detail.errors.join(', ');
                    }
                    if (error.detail?.validation_errors) {
                        const validationErrors = error.detail.validation_errors.map(err => 
                            `${err.loc.join('.')}: ${err.msg}`
                        ).join(', ');
                        errorMessage += '\nValidation errors: ' + validationErrors;
                    }
                    throw new Error(errorMessage);
                }
                const data = await response.json();
                this.dispatchMessage(data.message, false);  // false means not an error, so it will be success
            };
            
            const restartFunction = async () => {
                // Then restart the radiotracking service
                const response = await fetch(apiUrl('/api/systemd/action'), {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({
                        service: 'radiotracking',
                        action: 'restart'
                    })
                });
                
                const data = await response.json();
                
                if (!response.ok) {
                    throw new Error(data.detail || 'Failed to restart radiotracking service');
                }
                
                this.dispatchMessage(`Configuration saved and ${data.message}`, false);
                
                // Refresh service status after restart
                setTimeout(async () => {
                    await this.loadServiceStatus();
                }, 2000);
            };
            
            await this.handleSaveAndRestartConfig(configSaveFunction, restartFunction);
        }
    }
}


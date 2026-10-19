import { compose } from '../mixins/compose.js';
import { configPageMixin } from '../mixins/configPageMixin.js';
import { serverModeManager } from '../managers/serverModeManager.js';

export function radiotrackingConfig() {
    return compose(configPageMixin({
        service: 'radiotracking',
        apiPath: '/api/radiotracking',
        title: 'Radio Tracking',
        failureText: 'Failed to save radio tracking configuration',
        refreshOn: ['radiotracking'],
        initDelayMs: 100,
    }), {
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

        async loadConfig() {
            try {
                this.isLoading = true;
                this.configLoaded = false;
                // Build API URL with config_group parameter if in server mode
                const url = serverModeManager.buildApiUrl('/api/radiotracking');
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
            // Ensure the value is within valid ranges (230-300 kHz or 900-3200 kHz)
            // Only validate on blur to allow typing intermediate values (e.g. "1" when typing "1000")
            const value = this.sampleRateKHz;
            const num = Number(value);
            if (Number.isNaN(num) || num < 230 || (num > 300 && num < 900) || num > 3200) {
                const originalValue = Number.isNaN(num) ? 'empty' : `${num}`;
                let clampedValue;
                if (Number.isNaN(num) || num < 230) {
                    clampedValue = 230;
                } else if (num > 300 && num < 900) {
                    clampedValue = 300;
                } else {
                    clampedValue = 3200;
                }
                this.sampleRateKHz = clampedValue;
                if (window.toastManager) {
                    const msg = originalValue === 'empty'
                        ? `Sample rate adjusted to ${clampedValue} kHz (valid: 230-300 or 900-3200 kHz)`
                        : `Sample rate ${originalValue} kHz is invalid. Adjusted to ${clampedValue} kHz (valid: 230-300 or 900-3200 kHz)`;
                    window.toastManager.show(msg, 'warning', { title: 'Sample Rate' });
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

        // The config in the API's format (the page's keys differ from it)
        buildPayload() {
            return {
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
                }
            };
        },
    });
}


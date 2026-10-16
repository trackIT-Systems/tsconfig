import { compose } from '../mixins/compose.js';
import { configPageMixin } from '../mixins/configPageMixin.js';
import { serverModeManager } from '../managers/serverModeManager.js';
import { parseTimeString, updateTimeString } from '../utils/timeUtils.js';
import { apiUrl } from '../utils/apiUtils.js';

export function soundscapepipeConfig() {
    return compose(configPageMixin({
        service: 'soundscapepipe',
        apiPath: '/api/soundscapepipe',
        title: 'Soundscapepipe',
        failureText: 'Failed to save soundscapepipe configuration',
        refreshOn: ['soundscapepipe'],
        refreshSeconds: 30,
        initDelayMs: 150,
        savedMessage: 'Soundscapepipe configuration saved successfully!',
    }), {
        config: {
            stream_port: 5001,
            input_device_match: "trackIT Analog Frontend",
            sample_rate: 384000,
            input_length_s: 0.1,
            channels: 2,
            detectors: {
                birdedge: {
                    detection_threshold: 0.0,
                    class_threshold: 0.3,
                    model_path: "",
                    channel_strategy: "mix",
                    tasks: []
                },
                yolobat: {
                    class_threshold: 0.3,
                    model_path: "",
                    channel_strategy: "mix",
                    tasks: []
                },
                audioprotopnet: {
                    class_threshold: 0.3,
                    vocalization_threshold: 0.3,
                    model_path: "",
                    channel_strategy: "mix",
                    tasks: []
                },
                schedule: {
                    enabled: false,
                    tasks: []
                }
            },
            output_device_match: "bcm2835 Headphones",
            speaker_enable_pin: 27,
            highpass_freq: 100,
            lure: {
                tasks: []
            },
            ratio: 0.0,
            length_s: 5,
            soundfile_limit: 5,
            soundfile_format: "flac",
            maximize_confidence: false,
            groups: {},
            disk_reserve_mb: 512,
            frontend_gain: "low",
            frontend_gain_pin_a: 23,
            frontend_gain_pin_b: 13
        },
        configLoaded: false,
        audioDevices: {},
        loadingDevices: false,
        modelFiles: {},
        loadingModels: false,
        lureFiles: {},
        loadingLureFiles: false,
        speciesData: {},
        loadingSpecies: false,
        diskInfo: [],

        async init() {
            // Small delay to prevent simultaneous API calls during page load
            await new Promise(resolve => setTimeout(resolve, 150));
            
            // Load model files first to ensure dropdown options are available
            await this.loadModelFiles();
            
            // Load disk info early for slider configuration
            await this.loadDiskInfo();
            
            // Then load config and other data
            await this.loadConfig();
            if (!this.serverMode) {
                this.loadServiceStatus();
            }
            this.loadAudioDevices();
            this.loadLureFiles();
            this.loadSpeciesData();
            
            // Set up watchers for detector enabled state changes
            this.setupDetectorWatchers();
            
            await this.startServiceStatusRefresh();

            // Server mode: another config group was chosen
            this.listen(window, 'config-group-changed', () => this.loadConfig());
        },

        async loadConfig() {
            try {
                // Build API URL with config_group parameter if in server mode
                const url = serverModeManager.buildApiUrl('/api/soundscapepipe');
                const response = await fetch(url);
                if (response.ok) {
                    const data = await response.json();
                    // Handle backward compatibility for detectors - add enabled flags if missing
                    const detectors = data.detectors || {};
                    
                    // BirdEdge detector - enabled if present in config, disabled if not present
                    if (detectors.birdedge) {
                        detectors.birdedge.enabled = detectors.birdedge.enabled !== undefined ? detectors.birdedge.enabled : true;
                        detectors.birdedge.tasks = detectors.birdedge.tasks || [];
                        // Ensure tasks is always an array, not null
                        if (!Array.isArray(detectors.birdedge.tasks)) {
                            detectors.birdedge.tasks = [];
                        }
                        // Parse existing task time strings into UI components
                        detectors.birdedge.tasks.forEach(task => {
                            this.parseDetectorTaskTimeString(task, task.start, 'start');
                            this.parseDetectorTaskTimeString(task, task.stop, 'stop');
                        });
                        // Ensure channel_strategy exists with default value
                        if (detectors.birdedge.channel_strategy === undefined) {
                            detectors.birdedge.channel_strategy = "mix";
                        }
                    } else {
                        detectors.birdedge = { enabled: false, detection_threshold: 0.0, class_threshold: 0.3, model_path: "", channel_strategy: "mix", tasks: [] };
                    }
                    
                                        // YOLOBat detector - enabled if present in config, disabled if not present
                    if (detectors.yolobat) {
                        detectors.yolobat.enabled = detectors.yolobat.enabled !== undefined ? detectors.yolobat.enabled : true;
                        detectors.yolobat.tasks = detectors.yolobat.tasks || [];
                        // Ensure tasks is always an array, not null
                        if (!Array.isArray(detectors.yolobat.tasks)) {
                            detectors.yolobat.tasks = [];
                        }
                        // Parse existing task time strings into UI components
                        detectors.yolobat.tasks.forEach(task => {
                            this.parseDetectorTaskTimeString(task, task.start, 'start');
                            this.parseDetectorTaskTimeString(task, task.stop, 'stop');
                        });
                        // Use class_threshold if available, otherwise use detection_threshold for backwards compatibility
                        if (detectors.yolobat.class_threshold === undefined && detectors.yolobat.detection_threshold !== undefined) {
                            detectors.yolobat.class_threshold = detectors.yolobat.detection_threshold;
                        }
                        // Ensure class_threshold exists with default value
                        if (detectors.yolobat.class_threshold === undefined) {
                            detectors.yolobat.class_threshold = 0.3;
                        }
                        // Ensure channel_strategy exists with default value
                        if (detectors.yolobat.channel_strategy === undefined) {
                            detectors.yolobat.channel_strategy = "mix";
                        }
 
                    } else {
                        detectors.yolobat = { enabled: false, class_threshold: 0.3, model_path: "", channel_strategy: "mix", tasks: [] };
                    }

                    // AudioProtoPNet detector - enabled if present in config, disabled if not present
                    if (detectors.audioprotopnet) {
                        detectors.audioprotopnet.enabled = detectors.audioprotopnet.enabled !== undefined ? detectors.audioprotopnet.enabled : true;
                        detectors.audioprotopnet.tasks = detectors.audioprotopnet.tasks || [];
                        if (!Array.isArray(detectors.audioprotopnet.tasks)) {
                            detectors.audioprotopnet.tasks = [];
                        }
                        detectors.audioprotopnet.tasks.forEach(task => {
                            this.parseDetectorTaskTimeString(task, task.start, 'start');
                            this.parseDetectorTaskTimeString(task, task.stop, 'stop');
                        });
                        if (detectors.audioprotopnet.class_threshold === undefined) {
                            detectors.audioprotopnet.class_threshold = 0.3;
                        }
                        if (detectors.audioprotopnet.vocalization_threshold === undefined) {
                            detectors.audioprotopnet.vocalization_threshold = 0.3;
                        }
                        if (detectors.audioprotopnet.channel_strategy === undefined) {
                            detectors.audioprotopnet.channel_strategy = "mix";
                        }
                    } else {
                        detectors.audioprotopnet = { enabled: false, class_threshold: 0.3, vocalization_threshold: 0.3, model_path: "", channel_strategy: "mix", tasks: [] };
                    }
                    
                    // Static detector (schedule) - enabled if present in config, disabled if not present
                    if (detectors.schedule) {
                        detectors.schedule.enabled = detectors.schedule.enabled !== undefined ? detectors.schedule.enabled : true;
                        detectors.schedule.tasks = detectors.schedule.tasks || [];
                        // Ensure tasks is always an array, not null
                        if (!Array.isArray(detectors.schedule.tasks)) {
                            detectors.schedule.tasks = [];
                        }
                        // Parse existing task time strings into UI components
                        detectors.schedule.tasks.forEach(task => {
                            this.parseDetectorTaskTimeString(task, task.start, 'start');
                            this.parseDetectorTaskTimeString(task, task.stop, 'stop');
                        });
                    } else {
                        detectors.schedule = { enabled: false, tasks: [] };
                    }

                    // Handle lure tasks - parse existing time strings into UI components
                    const lure = data.lure || { tasks: [] };
                    // Ensure tasks is always an array, not null
                    if (!Array.isArray(lure.tasks)) {
                        lure.tasks = [];
                    }
                    if (lure.tasks && lure.tasks.length > 0) {
                        lure.tasks.forEach(task => {
                            this.parseLureTaskTimeString(task, task.start, 'start');
                            this.parseLureTaskTimeString(task, task.stop, 'stop');
                            // Ensure the time strings are properly synchronized after parsing
                            this.updateLureTaskTimeString(task, 'start');
                            this.updateLureTaskTimeString(task, 'stop');
                            // Ensure paths is always an array
                            if (!Array.isArray(task.paths)) {
                                task.paths = task.paths ? [task.paths] : [''];
                            }
                            // Ensure record is boolean
                            task.record = Boolean(task.record);
                        });
                    }

                    this.config = {
                        // Ensure all sections exist with defaults
                        stream_port: data.stream_port || 5001,
                        input_device_match: data.input_device_match || "trackIT Analog Frontend",
                        input_length_s: data.input_length_s || 0.1,
                        channels: data.channels || 2,
                        sample_rate: data.sample_rate || 384000,
                        detectors: detectors,
                        output_device_match: data.output_device_match !== undefined && data.output_device_match !== null ? data.output_device_match : "none",
                        speaker_enable_pin: data.speaker_enable_pin || 27,
                        highpass_freq: data.highpass_freq || 100,
                        lure: lure,
                        ratio: data.ratio || 0.0,
                        length_s: data.length_s || 5,
                        soundfile_limit: data.soundfile_limit || 5,
                        soundfile_format: data.soundfile_format || "flac",
                        maximize_confidence: data.maximize_confidence || false,
                        groups: data.groups || {},
                        disk_reserve_mb: Math.min(data.disk_reserve_mb || 512, this.getDataDiskSize()),
                        frontend_gain: data.frontend_gain || "low",
                        frontend_gain_pin_a: data.frontend_gain_pin_a || 23,
                        frontend_gain_pin_b: data.frontend_gain_pin_b || 13
                    };
                    
                    // Initialize group ratios and recording lengths with global values if not set
                    const globalRatio = this.config.ratio;
                    const globalLength = this.config.length_s;
                    Object.keys(this.config.groups).forEach(groupName => {
                        const group = this.config.groups[groupName];
                        if (group.ratio === undefined || group.ratio === null) {
                            group.ratio = globalRatio;
                        }
                        if (group.length_s === undefined || group.length_s === null) {
                            group.length_s = globalLength;
                        }
                        // Ensure maximize_confidence is always a boolean
                        if (group.maximize_confidence === undefined || group.maximize_confidence === null) {
                            group.maximize_confidence = false;
                        }
                    });
                    this.configLoaded = true;
                } else if (response.status === 404) {
                    // No configuration found, use defaults
                    this.configLoaded = true;
                    this.showMessage("No soundscapepipe configuration found. Using default values.", false);
                } else {
                    const error = await response.json();
                    throw new Error(error.detail || 'Failed to load soundscapepipe configuration');
                }
                
                // Auto-select default models for enabled detectors without a model
                this.autoSelectDefaultModels();
            } catch (error) {
                this.showMessage(error.message, true);
                this.configLoaded = true; // Allow user to see form even if loading failed
                
                // Auto-select default models even if config loading failed
                this.autoSelectDefaultModels();
            }
        },

        resetConfig() {
            this.loadConfig();
        },

        // Species groups must be valid before anything is sent
        validate() {
            const speciesErrors = this.validateSpeciesGroups();
            if (speciesErrors.length > 0) {
                this.showMessage('Species validation failed:\n• ' + speciesErrors.join('\n• '), true);
                throw new Error('Species validation failed');
            }
        },

        // The config as the API wants it: without disabled detectors, UI-only fields and empty entries
        buildPayload() {
            // Create a copy of the config and filter out disabled detectors
            const configToSave = { ...this.config };
            
            // Filter detectors to only include enabled ones
            configToSave.detectors = {};
            
            if (this.config.detectors.birdedge && this.config.detectors.birdedge.enabled) {
                configToSave.detectors.birdedge = { ...this.config.detectors.birdedge };
                delete configToSave.detectors.birdedge.enabled; // Remove the enabled flag from saved config
                // Clean up tasks - convert UI components back to time strings
                if (configToSave.detectors.birdedge.tasks) {
                    configToSave.detectors.birdedge.tasks = configToSave.detectors.birdedge.tasks.map(task => {
                        const cleanTask = { name: task.name, start: task.start, stop: task.stop };
                        return cleanTask;
                    });
                }
            }
            
            if (this.config.detectors.yolobat && this.config.detectors.yolobat.enabled) {
                configToSave.detectors.yolobat = { ...this.config.detectors.yolobat };
                delete configToSave.detectors.yolobat.enabled; // Remove the enabled flag from saved config
                // Set both thresholds to the same value from class_threshold
                configToSave.detectors.yolobat.detection_threshold = this.config.detectors.yolobat.class_threshold;
                // Remove schedule if it exists (YoloBat doesn't use schedule property)
                delete configToSave.detectors.yolobat.schedule;
                // Clean up tasks - convert UI components back to time strings
                if (configToSave.detectors.yolobat.tasks) {
                    configToSave.detectors.yolobat.tasks = configToSave.detectors.yolobat.tasks.map(task => {
                        const cleanTask = { name: task.name, start: task.start, stop: task.stop };
                        return cleanTask;
                    });
                }
            }

            if (this.config.detectors.audioprotopnet && this.config.detectors.audioprotopnet.enabled) {
                configToSave.detectors.audioprotopnet = { ...this.config.detectors.audioprotopnet };
                delete configToSave.detectors.audioprotopnet.enabled;
                if (configToSave.detectors.audioprotopnet.tasks) {
                    configToSave.detectors.audioprotopnet.tasks = configToSave.detectors.audioprotopnet.tasks.map(task => {
                        const cleanTask = { name: task.name, start: task.start, stop: task.stop };
                        return cleanTask;
                    });
                }
            }
            
            if (this.config.detectors.schedule && this.config.detectors.schedule.enabled) {
                configToSave.detectors.schedule = { ...this.config.detectors.schedule };
                delete configToSave.detectors.schedule.enabled; // Remove the enabled flag from saved config
                // Clean up tasks - convert UI components back to time strings
                if (configToSave.detectors.schedule.tasks) {
                    configToSave.detectors.schedule.tasks = configToSave.detectors.schedule.tasks.map(task => {
                        const cleanTask = { name: task.name, start: task.start, stop: task.stop };
                        return cleanTask;
                    });
                }
            }

            // Ensure all lure task time strings are synced with UI components before saving
            if (this.config.lure && this.config.lure.tasks) {
                this.config.lure.tasks.forEach(task => {
                    this.updateLureTaskTimeString(task, 'start');
                    this.updateLureTaskTimeString(task, 'stop');
                });
            }

            // Clean up lure tasks - convert UI components back to time strings and filter out empty tasks
            if (configToSave.lure && configToSave.lure.tasks) {
                // Filter out tasks that don't have a species name (empty/incomplete tasks)
                const validTasks = configToSave.lure.tasks.filter(task => 
                    task && task.species && task.species.trim() !== ''
                );
                
                // Create a new array to avoid modifying the original that UI is bound to
                configToSave.lure = {
                    ...configToSave.lure,
                    tasks: validTasks.map(task => {
                        const cleanTask = { 
                            species: task.species, 
                            paths: task.paths || [''],
                            start: task.start || '00:00', 
                            stop: task.stop || '00:00',
                            record: Boolean(task.record)
                        };
                        return cleanTask;
                    })
                };
            } else if (configToSave.lure) {
                // Ensure tasks is always an empty array, not null
                configToSave.lure.tasks = [];
            }

            // Clean up groups - remove null/undefined values and filter empty species
            if (configToSave.groups) {
                configToSave.groups = {};
                Object.keys(this.config.groups).forEach(groupName => {
                    const group = this.config.groups[groupName];
                    const cleanGroup = {
                        species: (group.species || []).filter(species => species && species.trim() !== '')
                    };
                    
                    // Only include ratio if it's not null/undefined
                    if (group.ratio !== null && group.ratio !== undefined) {
                        cleanGroup.ratio = group.ratio;
                    }
                    
                    // Only include length_s if it's not null/undefined
                    if (group.length_s !== null && group.length_s !== undefined) {
                        cleanGroup.length_s = group.length_s;
                    }
                    
                    // Always include maximize_confidence as a boolean
                    cleanGroup.maximize_confidence = Boolean(group.maximize_confidence);
                    
                    configToSave.groups[groupName] = cleanGroup;
                });
            }

            return configToSave;
        },


        async loadAudioDevices() {
            this.loadingDevices = true;
            try {
                const response = await fetch(apiUrl('/api/soundscapepipe/audio-devices'));
                if (response.ok) {
                    this.audioDevices = await response.json();
                } else {
                    const error = await response.json();
                    this.showMessage(`Failed to load audio devices: ${error.detail}`, true);
                }
            } catch (error) {
                this.showMessage(`Failed to load audio devices: ${error.message}`, true);
            } finally {
                this.loadingDevices = false;
            }
        },

        updateInputDeviceFromSelection(deviceName, selectedOption) {
            if (deviceName) {
                // Extract just the device name part (before the colon if it exists)
                // e.g. "trackIT Analog Frontend: Audio (hw:2,0)" -> "trackIT Analog Frontend"
                
                const cleanDeviceName = deviceName.split(':')[0].trim();
                this.config.input_device_match = cleanDeviceName;
                
                // Set sample rate from selected device
                if (selectedOption && selectedOption.dataset.sampleRate) {
                    this.config.sample_rate = Math.round(parseFloat(selectedOption.dataset.sampleRate));
                }

                // Set channels from selected device
                if (selectedOption && selectedOption.dataset.maxChannels) {
                    this.config.channels = Math.min(parseInt(selectedOption.dataset.maxChannels), 2);
                }
            }
        },

        updateOutputDeviceFromSelection(deviceName, selectedOption) {
            if (deviceName && deviceName !== "none") {
                // Extract just the device name part (before the colon if it exists)
                // e.g. "USB AUDIO DEVICE: Audio (hw:3,0)" -> "USB AUDIO DEVICE"
                const cleanDeviceName = deviceName.split(':')[0].trim();
                this.config.output_device_match = cleanDeviceName;
            } else {
                this.config.output_device_match = "none";
            }
        },

        async loadModelFiles() {
            this.loadingModels = true;
            try {
                const response = await fetch(apiUrl('/api/soundscapepipe/model-files'));
                if (response.ok) {
                    this.modelFiles = await response.json();
                    // Auto-select default models after loading model files
                    this.autoSelectDefaultModels();
                } else {
                    const error = await response.json();
                    this.showMessage(`Failed to load model files: ${error.detail}`, true);
                }
            } catch (error) {
                this.showMessage(`Failed to load model files: ${error.message}`, true);
            } finally {
                this.loadingModels = false;
            }
        },

        // Helper function to select default model when enabling a detector
        selectDefaultModel(detectorType) {
            if (!this.modelFiles || !this.modelFiles[detectorType]) {
                return;
            }

            const availableModels = this.modelFiles[detectorType];
            if (availableModels.length === 0) {
                return;
            }

            let selectedModel = null;

            // Define preferred model patterns
            const preferredPatterns = {
                'yolobat': 'yolobat11_2025.3.2',
                'birdedge': 'MarBird_EFL0_GER.onnx',
                'audioprotopnet': 'audioprotopnet_trackit_nano_23m.onnx'
            };

            const preferredPattern = preferredPatterns[detectorType];
            if (preferredPattern) {
                // Look for preferred model first
                selectedModel = availableModels.find(model => 
                    model.toLowerCase().includes(preferredPattern.toLowerCase())
                );
            }

            // Fallback to first available model if no preferred model found
            if (!selectedModel) {
                selectedModel = availableModels[0];
            }

            // Set the selected model
            this.config.detectors[detectorType].model_path = selectedModel;
            
            // For yolobat, also load the labels
            if (detectorType === 'yolobat') {
                this.loadYoloBatLabels();
            }
        },



        // Auto-select default models for enabled detectors without a model
        autoSelectDefaultModels() {
            if (!this.configLoaded || !this.modelFiles) {
                return;
            }

            // Check each detector type
            ['birdedge', 'yolobat', 'audioprotopnet'].forEach(detectorType => {
                const detector = this.config.detectors[detectorType];
                if (detector && detector.enabled && !detector.model_path) {
                    this.selectDefaultModel(detectorType);
                }
            });
        },

        // Set up watchers for detector enabled state changes
        setupDetectorWatchers() {
            // Watch for birdedge detector enabled state changes
            this.$watch('config.detectors.birdedge.enabled', (enabled, oldEnabled) => {
                if (enabled) {
                    // If no model path or model path doesn't exist in available models, select default
                    const shouldAutoSelect = !this.config.detectors.birdedge.model_path || 
                                           (this.modelFiles.birdedge && 
                                            !this.modelFiles.birdedge.includes(this.config.detectors.birdedge.model_path));
                    
                    if (shouldAutoSelect) {
                        this.selectDefaultModel('birdedge');
                    }
                }
            });

            // Watch for yolobat detector enabled state changes
            this.$watch('config.detectors.yolobat.enabled', (enabled, oldEnabled) => {
                if (enabled) {
                    // If no model path or model path doesn't exist in available models, select default
                    const shouldAutoSelect = !this.config.detectors.yolobat.model_path || 
                                           (this.modelFiles.yolobat && 
                                            !this.modelFiles.yolobat.includes(this.config.detectors.yolobat.model_path));
                    
                    if (shouldAutoSelect) {
                        this.selectDefaultModel('yolobat');
                    }
                }
            });

            // Watch for audioprotopnet detector enabled state changes
            this.$watch('config.detectors.audioprotopnet.enabled', (enabled, oldEnabled) => {
                if (enabled) {
                    const shouldAutoSelect = !this.config.detectors.audioprotopnet.model_path || 
                                           (this.modelFiles.audioprotopnet && 
                                            !this.modelFiles.audioprotopnet.includes(this.config.detectors.audioprotopnet.model_path));
                    
                    if (shouldAutoSelect) {
                        this.selectDefaultModel('audioprotopnet');
                    }
                }
            });
        },

        async loadLureFiles() {
            this.loadingLureFiles = true;
            try {
                const response = await fetch(apiUrl('/api/soundscapepipe/lure-files'));
                if (response.ok) {
                    this.lureFiles = await response.json();
                } else {
                    console.error('Failed to load lure files');
                    this.lureFiles = { directories: [], files: [] };
                }
            } catch (error) {
                console.error('Error loading lure files:', error);
                this.lureFiles = { directories: [], files: [] };
            } finally {
                this.loadingLureFiles = false;
            }
        },

        async loadSpeciesData() {
            this.loadingSpecies = true;
            try {
                const url = serverModeManager.buildApiUrl('/api/soundscapepipe/species');
                const response = await fetch(url);
                if (response.ok) {
                    this.speciesData = await response.json();
                } else {
                    console.error('Failed to load species data');
                    this.speciesData = { birdedge: [], yolobat: [], audioprotopnet: [] };
                }
                
                // Load YoloBat labels if a model is selected
                await this.loadYoloBatLabels();
            } catch (error) {
                console.error('Error loading species data:', error);
                this.speciesData = { birdedge: [], yolobat: [], audioprotopnet: [] };
            } finally {
                this.loadingSpecies = false;
            }
        },

        async loadDiskInfo() {
            // Don't load disk info in server mode
            if (this.serverMode) {
                this.diskInfo = [];
                return;
            }
            
            try {
                const response = await fetch(apiUrl('/api/system-status'));
                if (response.ok) {
                    const data = await response.json();
                    this.diskInfo = data.disk || [];
                } else {
                    console.error('Failed to load disk info');
                    this.diskInfo = [];
                }
            } catch (error) {
                console.error('Error loading disk info:', error);
                this.diskInfo = [];
            }
        },

        getDataDiskSize() {
            if (!this.diskInfo || !Array.isArray(this.diskInfo) || this.diskInfo.length === 0) {
                return 2048; // Default fallback of 2GB in MB
            }
            
            // Find the disk that contains /data, prioritizing exact matches
            let dataDisk = this.diskInfo.find(disk => disk.mountpoint === '/data');
            
            // Fallback to root filesystem if /data not found
            if (!dataDisk) {
                dataDisk = this.diskInfo.find(disk => disk.mountpoint === '/');
            }
            
            // Fallback to any disk that might contain /data
            if (!dataDisk) {
                dataDisk = this.diskInfo.find(disk => 
                    disk.mountpoint && disk.mountpoint.includes('/data')
                );
            }
            
            // Use the first available disk as last resort
            if (!dataDisk && this.diskInfo.length > 0) {
                dataDisk = this.diskInfo[0];
            }
            
            if (dataDisk && dataDisk.total && dataDisk.total > 0) {
                // Convert from bytes to megabytes and round down
                const sizeInMB = Math.floor(dataDisk.total / (1024 * 1024));
                // Ensure we have at least 1GB available for the slider
                return Math.max(sizeInMB, 1024);
            }
            
            return 2048; // Default fallback of 2GB in MB
        },

        async loadYoloBatLabels() {
            try {
                // Get the selected YoloBat model path
                const modelPath = this.config.detectors.yolobat?.model_path;
                if (!modelPath) {
                    // No model selected, keep existing yolobat labels (from fallback JSON)
                    // Don't clear it - preserve fallback data
                    return;
                }

                const url = serverModeManager.buildApiUrl(`/api/soundscapepipe/yolobat-labels?model_path=${encodeURIComponent(modelPath)}`);
                const response = await fetch(url);
                if (response.ok) {
                    const data = await response.json();
                    // Use enhanced labels if available, otherwise fallback to basic labels
                    if (data.enhanced_labels && data.enhanced_labels.length > 0) {
                        // Map enhanced labels to include the original model label for storage
                        this.speciesData.yolobat = data.enhanced_labels.map((enhanced, index) => ({
                            scientific: enhanced.scientific,
                            english: enhanced.english,
                            german: enhanced.german,
                            display: enhanced.display,
                            searchable: enhanced.searchable,
                            modelLabel: data.labels[index] // Store the original model label (e.g., "Ppip")
                        }));
                    } else if (data.labels && data.labels.length > 0) {
                        // Fallback to basic labels if no enhanced data available
                        this.speciesData.yolobat = data.labels.map(label => ({
                            scientific: label,
                            english: "",
                            german: "",
                            display: label,
                            searchable: label.toLowerCase(),
                            modelLabel: label
                        }));
                    }
                    // If response is ok but no labels, preserve existing data (from fallback JSON)
                } else {
                    // Failed to load from model metadata - preserve existing fallback data
                    console.warn('Failed to load YoloBat labels from model metadata, using fallback data');
                    // Don't clear this.speciesData.yolobat - keep the fallback from loadSpeciesData()
                }
            } catch (error) {
                // Error loading from model metadata - preserve existing fallback data
                console.warn('Error loading YoloBat labels from model metadata, using fallback data:', error);
                // Don't clear this.speciesData.yolobat - keep the fallback from loadSpeciesData()
            }
        },

        // Detector task management methods
        updateDetectorTaskTimeString(detectorName, task, type) {
            const reference = task[`${type}Reference`];
            const sign = task[`${type}Sign`];
            const offset = task[`${type}Offset`];
            
            if (reference === 'time') {
                task[type] = offset;
            } else {
                task[type] = `${reference}${sign}${offset}`;
            }
        },

        addDetectorTask(detectorName) {
            if (!this.config.detectors[detectorName].tasks) {
                this.config.detectors[detectorName].tasks = [];
            }
            
            this.config.detectors[detectorName].tasks.push({
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

        removeDetectorTask(detectorName, index) {
            this.config.detectors[detectorName].tasks.splice(index, 1);
        },

        parseDetectorTaskTimeString(task, timeStr, type) {
            if (timeStr.includes('sunrise')) {
                task[`${type}Reference`] = 'sunrise';
                task[`${type}Sign`] = timeStr.includes('-') ? '-' : '+';
                task[`${type}Offset`] = timeStr.replace('sunrise', '').replace('+', '').replace('-', '').trim();
            } else if (timeStr.includes('sunset')) {
                task[`${type}Reference`] = 'sunset';
                task[`${type}Sign`] = timeStr.includes('-') ? '-' : '+';
                task[`${type}Offset`] = timeStr.replace('sunset', '').replace('+', '').replace('-', '').trim();
            } else if (timeStr.includes('dawn')) {
                task[`${type}Reference`] = 'dawn';
                task[`${type}Sign`] = timeStr.includes('-') ? '-' : '+';
                task[`${type}Offset`] = timeStr.replace('dawn', '').replace('+', '').replace('-', '').trim();
            } else if (timeStr.includes('dusk')) {
                task[`${type}Reference`] = 'dusk';
                task[`${type}Sign`] = timeStr.includes('-') ? '-' : '+';
                task[`${type}Offset`] = timeStr.replace('dusk', '').replace('+', '').replace('-', '').trim();
            } else {
                // Assume it's a clock time
                task[`${type}Reference`] = 'time';
                task[`${type}Sign`] = '+';
                task[`${type}Offset`] = timeStr;
            }
        },

        // Lure task management methods
        updateLureTaskTimeString(task, type) {
            const reference = task[`${type}Reference`];
            const sign = task[`${type}Sign`];
            const offset = task[`${type}Offset`];
            
            if (reference === 'time') {
                task[type] = offset;
            } else {
                task[type] = `${reference}${sign}${offset}`;
            }
        },

        addLureTask() {
            if (!this.config.lure) {
                this.config.lure = { tasks: [] };
            }
            if (!this.config.lure.tasks) {
                this.config.lure.tasks = [];
            }
            
            this.config.lure.tasks.push({
                species: '',
                paths: [''],
                start: '00:00',
                stop: '00:00',
                record: false,
                startReference: 'time',
                startOffset: '00:00',
                startSign: '+',
                stopReference: 'time',
                stopOffset: '00:00',
                stopSign: '+'
            });
        },

        removeLureTask(index) {
            this.config.lure.tasks.splice(index, 1);
        },

        parseLureTaskTimeString(task, timeStr, type) {
            if (timeStr.includes('sunrise')) {
                task[`${type}Reference`] = 'sunrise';
                task[`${type}Sign`] = timeStr.includes('-') ? '-' : '+';
                task[`${type}Offset`] = timeStr.replace('sunrise', '').replace('+', '').replace('-', '').trim();
            } else if (timeStr.includes('sunset')) {
                task[`${type}Reference`] = 'sunset';
                task[`${type}Sign`] = timeStr.includes('-') ? '-' : '+';
                task[`${type}Offset`] = timeStr.replace('sunset', '').replace('+', '').replace('-', '').trim();
            } else if (timeStr.includes('dawn')) {
                task[`${type}Reference`] = 'dawn';
                task[`${type}Sign`] = timeStr.includes('-') ? '-' : '+';
                task[`${type}Offset`] = timeStr.replace('dawn', '').replace('+', '').replace('-', '').trim();
            } else if (timeStr.includes('dusk')) {
                task[`${type}Reference`] = 'dusk';
                task[`${type}Sign`] = timeStr.includes('-') ? '-' : '+';
                task[`${type}Offset`] = timeStr.replace('dusk', '').replace('+', '').replace('-', '').trim();
            } else {
                // Assume it's a clock time
                task[`${type}Reference`] = 'time';
                task[`${type}Sign`] = '+';
                task[`${type}Offset`] = timeStr;
            }
        },

        addLureTaskPath(taskIndex) {
            if (!this.config.lure.tasks[taskIndex].paths) {
                this.config.lure.tasks[taskIndex].paths = [];
            }
            this.config.lure.tasks[taskIndex].paths.push('');
        },

        removeLureTaskPath(taskIndex, pathIndex) {
            this.config.lure.tasks[taskIndex].paths.splice(pathIndex, 1);
        },

        // Species group management methods
        addGroup() {
            if (!this.config.groups) {
                this.config.groups = {};
            }
            
            // Create group with empty name - user will provide their own name
            const groupName = '';
            this.config.groups[groupName] = {
                ratio: 0.0,
                maximize_confidence: false,
                length_s: this.config.length_s || 5,
                species: ['']
            };
        },

        removeGroup(groupName) {
            if (this.config.groups && this.config.groups[groupName]) {
                delete this.config.groups[groupName];
            }
        },

        renameGroup(oldName, newName) {
            if (!newName || newName === oldName || !this.config.groups || !this.config.groups[oldName]) {
                return;
            }
            
            // Check if new name already exists
            if (this.config.groups[newName]) {
                this.showMessage(`Group name "${newName}" already exists!`, true);
                return;
            }
            
            // Create new group with new name
            this.config.groups[newName] = { ...this.config.groups[oldName] };
            
            // Delete old group
            delete this.config.groups[oldName];
        },

        addSpeciesToGroup(groupName) {
            if (!this.config.groups || !this.config.groups[groupName]) {
                return;
            }
            
            if (!this.config.groups[groupName].species) {
                this.config.groups[groupName].species = [];
            }
            
            this.config.groups[groupName].species.push('');
        },

        removeSpeciesFromGroup(groupName, speciesIndex) {
            if (!this.config.groups || !this.config.groups[groupName] || !this.config.groups[groupName].species) {
                return;
            }
            
            this.config.groups[groupName].species.splice(speciesIndex, 1);
        },

        updateSpeciesInput(event, groupName, speciesIndex) {
            // Simply store whatever the user typed - no automatic conversion
            // The datalist will provide the correct values for selection
            const inputValue = event.target.value;
            this.config.groups[groupName].species[speciesIndex] = inputValue;
        },

        findSpeciesByAnyName(searchTerm) {
            if (!this.speciesData.birdedge || !searchTerm) {
                return null;
            }
            
            const term = searchTerm.toLowerCase();
            return this.speciesData.birdedge.find(species => 
                species.scientific.toLowerCase() === term ||
                (species.english && species.english.toLowerCase() === term) ||
                (species.german && species.german.toLowerCase() === term)
            );
        },

        getSpeciesDisplayText(species) {
            if (!species) return '';
            
            let display = species.scientific;
            
            // Add common names in parentheses if available
            const commonNames = [];
            if (species.english) commonNames.push(species.english);
            if (species.german) commonNames.push(species.german);
            
            if (commonNames.length > 0) {
                display += ` (${commonNames.join(' / ')})`;
            }
            
            return display;
        },

        getConsistentSpeciesDisplayText(species, classifier) {
            if (!species) return '';
            
            // Format: "Scientific name, English name, German name (Classifier)"
            let parts = [species.scientific];
            
            if (species.english) {
                parts.push(species.english);
            }
            
            if (species.german) {
                parts.push(species.german);
            }
            
            let display = parts.join(', ');
            display += ` (${classifier})`;
            
            return display;
        },

        /**
         * Returns a merged, deduplicated species list for the species group dropdown.
         * BirdEdge and AudioProtoPNet both use scientific names and share many species,
         * so we merge duplicates and show which detectors support each species.
         * YoloBat uses modelLabel (bat abbreviations) and has no overlap.
         */
        getMergedSpeciesList() {
            const byValue = new Map(); // value -> { value, display, types[] }
            const detectorDisplayNames = { birdedge: 'BirdEdge', yolobat: 'YoloBat', audioprotopnet: 'AudioProtoPNet' };

            const addOrMerge = (value, display, detectorType) => {
                if (byValue.has(value)) {
                    const entry = byValue.get(value);
                    if (!entry.types.includes(detectorType)) {
                        entry.types.push(detectorType);
                        const typeLabels = entry.types.map(t => detectorDisplayNames[t] || t);
                        entry.display = entry.display.replace(/ \([^)]+\)$/, ` (${typeLabels.join(', ')})`);
                    }
                } else {
                    byValue.set(value, { value, display, types: [detectorType] });
                }
            };

            if (this.config.detectors.birdedge?.enabled && this.speciesData.birdedge) {
                this.speciesData.birdedge.forEach(s => {
                    addOrMerge(s.scientific, this.getConsistentSpeciesDisplayText(s, 'BirdEdge'), 'birdedge');
                });
            }
            if (this.config.detectors.yolobat?.enabled && this.speciesData.yolobat) {
                this.speciesData.yolobat.forEach(s => {
                    addOrMerge(s.modelLabel, this.getConsistentSpeciesDisplayText(s, 'YoloBat'), 'yolobat');
                });
            }
            if (this.config.detectors.audioprotopnet?.enabled && this.speciesData.audioprotopnet) {
                this.speciesData.audioprotopnet.forEach(s => {
                    addOrMerge(s.scientific, this.getConsistentSpeciesDisplayText(s, 'AudioProtoPNet'), 'audioprotopnet');
                });
            }

            return Array.from(byValue.values()).map(({ value, display }) => ({ value, display }));
        },

        validateSpeciesGroups() {
            const errors = [];
            
            if (!this.config.groups) {
                return errors;
            }
            
            // Get valid species lists for enabled detectors
            const validBirdEdgeSpecies = new Set();
            const validYoloBatSpecies = new Set();
            const validAudioProtoPNetSpecies = new Set();
            
            if (this.config.detectors.birdedge?.enabled && this.speciesData.birdedge) {
                this.speciesData.birdedge.forEach(species => {
                    validBirdEdgeSpecies.add(species.scientific);
                });
            }
            
            if (this.config.detectors.yolobat?.enabled && this.speciesData.yolobat) {
                this.speciesData.yolobat.forEach(species => {
                    validYoloBatSpecies.add(species.modelLabel);
                });
            }
            
            if (this.config.detectors.audioprotopnet?.enabled && this.speciesData.audioprotopnet) {
                this.speciesData.audioprotopnet.forEach(species => {
                    validAudioProtoPNetSpecies.add(species.scientific);
                });
            }
            
            // Check each group
            for (const [groupName, group] of Object.entries(this.config.groups)) {
                if (!group.species || !Array.isArray(group.species)) {
                    continue;
                }
                
                group.species.forEach((species, index) => {
                    if (!species || species.trim() === '') {
                        errors.push(`Group "${groupName}": Species at position ${index + 1} cannot be empty. Please enter a species label or remove the empty entry.`);
                        return;
                    }
                    
                    const isValidBirdEdge = validBirdEdgeSpecies.has(species);
                    const isValidYoloBat = validYoloBatSpecies.has(species);
                    const isValidAudioProtoPNet = validAudioProtoPNetSpecies.has(species);
                    
                    if (!isValidBirdEdge && !isValidYoloBat && !isValidAudioProtoPNet) {
                        // Check which detectors are enabled to provide helpful error message
                        const enabledDetectors = [];
                        if (this.config.detectors.birdedge?.enabled) enabledDetectors.push('BirdEdge');
                        if (this.config.detectors.yolobat?.enabled) enabledDetectors.push('YoloBat');
                        if (this.config.detectors.audioprotopnet?.enabled) enabledDetectors.push('AudioProtoPNet');
                        
                        if (enabledDetectors.length === 0) {
                            errors.push(`Group "${groupName}": Species "${species}" is invalid because no detectors are enabled.`);
                        } else {
                            errors.push(`Group "${groupName}": Species "${species}" is not valid for enabled detectors (${enabledDetectors.join(', ')}).`);
                        }
                    }
                });
            }
            
            return errors;
        }
    });
}

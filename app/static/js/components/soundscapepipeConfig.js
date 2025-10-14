import { saveStateMixin } from '../mixins/saveStateMixin.js';
import { serviceManager } from '../managers/serviceManager.js';
import { parseTimeString, updateTimeString } from '../utils/timeUtils.js';

export function soundscapepipeConfig() {
    return {
        ...saveStateMixin(),
        
        // Server mode helper
        get serverMode() {
            return window.serverModeManager?.isEnabled() || false;
        },
        
        config: {
            stream_port: 5001,
            lat: 50.85318,
            lon: 8.78735,
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
            disk_reserve_mb: 512
        },
        configLoaded: false,
        serviceStatus: {
            active: false,
            enabled: false,
            status: 'unknown',
            uptime: 'N/A'
        },
        refreshInterval: null,
        map: null,
        marker: null,
        mapInitialized: false,
        audioDevices: {},
        loadingDevices: false,
        modelFiles: {},
        loadingModels: false,
        lureFiles: {},
        loadingLureFiles: false,
        speciesData: {},
        loadingSpecies: false,
        actionLoading: false,
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
            
            // Auto-refresh service status every 30 seconds when tab is active
            this.refreshInterval = setInterval(() => {
                // Only refresh if soundscapepipe tab is active
                const currentHash = window.location.hash.slice(1);
                if (currentHash === 'soundscapepipe') {
                    this.loadServiceStatus();
                }
            }, 30000);
            
            // Listen for config group changes in server mode
            window.addEventListener('config-group-changed', async () => {
                await this.loadConfig();
            });
        },

        cleanup() {
            if (this.refreshInterval) {
                clearInterval(this.refreshInterval);
                this.refreshInterval = null;
            }
        },

        async loadServiceStatus() {
            try {
                const services = await serviceManager.getServices();
                const soundscapepipeService = services.find(service => service.name === 'soundscapepipe');
                if (soundscapepipeService) {
                    this.serviceStatus = {
                        active: soundscapepipeService.active,
                        enabled: soundscapepipeService.enabled,
                        status: soundscapepipeService.status,
                        uptime: soundscapepipeService.uptime || 'N/A'
                    };
                }
            } catch (error) {
                console.error('Failed to load service status:', error);
            }
        },

        async toggleEnable(serviceName, currentlyEnabled) {
            this.actionLoading = true;
            
            try {
                // Determine the action based on current state
                const action = currentlyEnabled ? 'disable' : 'enable';
                
                const response = await fetch('/api/systemd/action', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({
                        service: serviceName,
                        action: action
                    })
                });
                
                const data = await response.json();
                
                if (!response.ok) {
                    throw new Error(data.detail || `Failed to ${action} service`);
                }
                
                this.showMessage(data.message, false);
                
                // Refresh service status after action
                setTimeout(async () => {
                    await this.loadServiceStatus();
                }, 1000);
                
            } catch (err) {
                this.showMessage(err.message, true);
                console.error(`Service toggle error:`, err);
            } finally {
                this.actionLoading = false;
            }
        },

        async loadConfig() {
            try {
                // Build API URL with config_group parameter if in server mode
                const url = window.serverModeManager?.buildApiUrl('/api/soundscapepipe') || '/api/soundscapepipe';
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
                        lat: data.lat || 50.85318,
                        lon: data.lon || 8.78735,
                        input_device_match: data.input_device_match || "trackIT Analog Frontend",
                        input_length_s: data.input_length_s || 0.1,
                        channels: data.channels || 2,
                        sample_rate: data.sample_rate || 384000,
                        detectors: detectors,
                        output_device_match: data.output_device_match || "bcm2835 Headphones",
                        speaker_enable_pin: data.speaker_enable_pin || 27,
                        highpass_freq: data.highpass_freq || 100,
                        lure: lure,
                        ratio: data.ratio || 0.0,
                        length_s: data.length_s || 5,
                        soundfile_limit: data.soundfile_limit || 5,
                        soundfile_format: data.soundfile_format || "flac",
                        maximize_confidence: data.maximize_confidence || false,
                        groups: data.groups || {},
                        disk_reserve_mb: Math.min(data.disk_reserve_mb || 512, this.getDataDiskSize())
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
                
                // Initialize map after config is loaded, or update if already initialized
                if (!this.mapInitialized) {
                    setTimeout(() => this.initMap(), 200);
                } else {
                    this.updateMarkerFromInputs();
                }
            } catch (error) {
                this.showMessage(error.message, true);
                this.configLoaded = true; // Allow user to see form even if loading failed
                
                // Auto-select default models even if config loading failed
                this.autoSelectDefaultModels();
                
                // Initialize map with defaults even if config loading failed
                if (!this.mapInitialized) {
                    setTimeout(() => this.initMap(), 200);
                }
            }
        },

        async saveConfig() {
            const configSaveFunction = async () => {
                // Validate species groups first
                const speciesErrors = this.validateSpeciesGroups();
                if (speciesErrors.length > 0) {
                    this.showMessage('Species validation failed:\n• ' + speciesErrors.join('\n• '), true);
                    throw new Error('Species validation failed');
                }

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



                // Build API URL with config_group parameter if in server mode
                const url = window.serverModeManager?.buildApiUrl('/api/soundscapepipe') || '/api/soundscapepipe';
                const response = await fetch(url, {
                    method: 'PUT',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify(configToSave)
                });
                
                if (response.ok) {
                    this.showMessage('Soundscapepipe configuration saved successfully!', false);
                } else {
                    const error = await response.json();
                    let errorMessage = error.detail?.message || 'Failed to save soundscapepipe configuration';
                    if (error.detail?.errors) {
                        errorMessage += '\nErrors: ' + error.detail.errors.join(', ');
                    }
                    throw new Error(errorMessage);
                }
            };
            
            await this.handleSaveConfig(configSaveFunction);
        },

        async saveAndRestartService() {
            const configSaveFunction = async () => {
                // First save the configuration (custom logic to avoid interfering with regular save button)
                const speciesErrors = this.validateSpeciesGroups();
                if (speciesErrors.length > 0) {
                    this.showMessage('Species validation failed:\n• ' + speciesErrors.join('\n• '), true);
                    throw new Error('Species validation failed');
                }

                // Create a copy of the config and filter out disabled detectors (same logic as saveConfig)
                const configToSave = { ...this.config };
                
                // Filter detectors to only include enabled ones
                configToSave.detectors = {};
                
                if (this.config.detectors.birdedge && this.config.detectors.birdedge.enabled) {
                    configToSave.detectors.birdedge = { ...this.config.detectors.birdedge };
                    delete configToSave.detectors.birdedge.enabled;
                    if (configToSave.detectors.birdedge.tasks) {
                        configToSave.detectors.birdedge.tasks = configToSave.detectors.birdedge.tasks.map(task => {
                            const cleanTask = { name: task.name, start: task.start, stop: task.stop };
                            return cleanTask;
                        });
                    }
                }
                
                if (this.config.detectors.yolobat && this.config.detectors.yolobat.enabled) {
                    configToSave.detectors.yolobat = { ...this.config.detectors.yolobat };
                    delete configToSave.detectors.yolobat.enabled;
                    configToSave.detectors.yolobat.detection_threshold = this.config.detectors.yolobat.class_threshold;
                    delete configToSave.detectors.yolobat.schedule;
                    if (configToSave.detectors.yolobat.tasks) {
                        configToSave.detectors.yolobat.tasks = configToSave.detectors.yolobat.tasks.map(task => {
                            const cleanTask = { name: task.name, start: task.start, stop: task.stop };
                            return cleanTask;
                        });
                    }
                }
                
                if (this.config.detectors.schedule && this.config.detectors.schedule.enabled) {
                    configToSave.detectors.schedule = { ...this.config.detectors.schedule };
                    delete configToSave.detectors.schedule.enabled;
                    if (configToSave.detectors.schedule.tasks) {
                        configToSave.detectors.schedule.tasks = configToSave.detectors.schedule.tasks.map(task => {
                            const cleanTask = { name: task.name, start: task.start, stop: task.stop };
                            return cleanTask;
                        });
                    }
                }

                // Clean up lure and groups (same logic as saveConfig)
                if (this.config.lure && this.config.lure.tasks) {
                    this.config.lure.tasks.forEach(task => {
                        this.updateLureTaskTimeString(task, 'start');
                        this.updateLureTaskTimeString(task, 'stop');
                    });
                }

                if (configToSave.lure && configToSave.lure.tasks) {
                    const validTasks = configToSave.lure.tasks.filter(task => 
                        task && task.species && task.species.trim() !== ''
                    );
                    
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
                    configToSave.lure.tasks = [];
                }

                if (configToSave.groups) {
                    configToSave.groups = {};
                    Object.keys(this.config.groups).forEach(groupName => {
                        const group = this.config.groups[groupName];
                        const cleanGroup = {
                            species: (group.species || []).filter(species => species && species.trim() !== '')
                        };
                        
                        if (group.ratio !== null && group.ratio !== undefined) {
                            cleanGroup.ratio = group.ratio;
                        }
                        
                        if (group.length_s !== null && group.length_s !== undefined) {
                            cleanGroup.length_s = group.length_s;
                        }
                        
                        cleanGroup.maximize_confidence = Boolean(group.maximize_confidence);
                        
                        configToSave.groups[groupName] = cleanGroup;
                    });
                }

                // Save the configuration
                const saveResponse = await fetch('/api/soundscapepipe', {
                    method: 'PUT',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify(configToSave)
                });
                
                if (!saveResponse.ok) {
                    const error = await saveResponse.json();
                    let errorMessage = error.detail?.message || 'Failed to save soundscapepipe configuration';
                    if (error.detail?.errors) {
                        errorMessage += '\nErrors: ' + error.detail.errors.join(', ');
                    }
                    throw new Error(errorMessage);
                }
                
                if (!saveResponse.ok) {
                    const error = await saveResponse.json();
                    let errorMessage = error.detail?.message || 'Failed to save soundscapepipe configuration';
                    if (error.detail?.errors) {
                        errorMessage += '\nErrors: ' + error.detail.errors.join(', ');
                    }
                    throw new Error(errorMessage);
                }
            };
            
            const restartFunction = async () => {
                // Then restart the soundscapepipe service
                const response = await fetch('/api/systemd/action', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({
                        service: 'soundscapepipe',
                        action: 'restart'
                    })
                });
                
                const data = await response.json();
                
                if (!response.ok) {
                    throw new Error(data.detail || 'Failed to restart soundscapepipe service');
                }
                
                this.showMessage(`Configuration saved and ${data.message}`, false);
                
                // Refresh service status after restart
                setTimeout(async () => {
                    await this.loadServiceStatus();
                }, 2000);
            };
            
            await this.handleSaveAndRestartConfig(configSaveFunction, restartFunction);
        },

        resetConfig() {
            this.loadConfig();
        },

        showMessage(message, isError = false) {
            // Dispatch to parent component
            this.$dispatch('message', { message, error: isError });
        },

        streamLogs(serviceName) {
            // Show the log modal directly
            const modal = new bootstrap.Modal(document.getElementById('logModal'));
            // Get the log viewer instance and start streaming
            const logViewerEl = document.getElementById('logModal');
            if (logViewerEl && logViewerEl._x_dataStack) {
                const logViewerInstance = logViewerEl._x_dataStack[0];
                logViewerInstance.startStreaming(serviceName);
            }
            modal.show();
            
            // Scroll to bottom after modal is shown and content is rendered
            setTimeout(() => {
                const container = document.getElementById('logContainer');
                if (container) {
                    container.scrollTop = container.scrollHeight;
                }
            }, 250);
        },

        initMap() {
            // Only initialize if not already done and container is visible
            if (this.mapInitialized || !document.getElementById('soundscapeMap')) {
                return;
            }

            // Wait a bit to ensure the container is properly rendered
            setTimeout(() => {
                if (!document.getElementById('soundscapeMap') || this.mapInitialized) {
                    return;
                }

                // Initialize map with loaded coordinates
                this.map = L.map('soundscapeMap', {
                    center: [this.config.lat, this.config.lon],
                    zoom: 13,
                    zoomControl: true
                });
                
                // Add Mapbox satellite streets layer
                L.tileLayer('https://api.mapbox.com/styles/v1/mapbox/satellite-streets-v12/tiles/{z}/{x}/{y}?access_token=pk.eyJ1IjoidHJhY2tpdHN5c3RlbXMiLCJhIjoiY21iaHEwbXcwMDEzcTJqc2JhNzdobDluaSJ9.NLRmiJEDHQgPJEyceCA57g', {
                    attribution: '© Mapbox © OpenStreetMap',
                    maxZoom: 19
                }).addTo(this.map);

                // Add locate control
                L.control.locate({
                    position: 'topleft',
                    strings: {
                        title: "Show my location"
                    },
                    flyTo: true,
                    keepCurrentZoomLevel: true,
                    locateOptions: {
                        enableHighAccuracy: true
                    }
                }).addTo(this.map);

                // Add marker
                this.marker = L.marker([this.config.lat, this.config.lon], {
                    draggable: true
                }).addTo(this.map);

                // Update coordinates when marker is dragged
                this.marker.on('dragend', (e) => {
                    const position = e.target.getLatLng();
                    this.config.lat = parseFloat(position.lat.toFixed(8));
                    this.config.lon = parseFloat(position.lng.toFixed(8));
                });

                // Update marker when map is clicked
                this.map.on('click', (e) => {
                    const position = e.latlng;
                    this.marker.setLatLng(position);
                    this.config.lat = parseFloat(position.lat.toFixed(8));
                    this.config.lon = parseFloat(position.lng.toFixed(8));
                });

                // Handle location found event
                this.map.on('locationfound', (e) => {
                    this.config.lat = parseFloat(e.latlng.lat.toFixed(8));
                    this.config.lon = parseFloat(e.latlng.lng.toFixed(8));
                    this.updateMarkerFromInputs();
                });

                this.mapInitialized = true;
                
                // Force a resize to ensure tiles load properly
                setTimeout(() => {
                    if (this.map) {
                        this.map.invalidateSize();
                    }
                }, 100);
            }, 100);
        },

        ensureMapVisible() {
            // Call this when the soundscapepipe tab becomes active
            if (this.map && this.mapInitialized) {
                setTimeout(() => {
                    this.map.invalidateSize();
                    this.map.setView([this.config.lat, this.config.lon], 13);
                }, 50);
            } else if (!this.mapInitialized) {
                this.initMap();
            }
        },

        updateMarkerFromInputs() {
            if (this.marker && this.mapInitialized) {
                // Ensure coordinates are within bounds and have proper precision
                const lat = Math.min(Math.max(parseFloat(this.config.lat), -90), 90);
                const lon = Math.min(Math.max(parseFloat(this.config.lon), -180), 180);
                
                // Update the marker and map view
                this.marker.setLatLng([lat, lon]);
                this.map.setView([lat, lon]);
                
                // Update the input values with properly formatted numbers
                this.config.lat = parseFloat(lat.toFixed(8));
                this.config.lon = parseFloat(lon.toFixed(8));
            }
        },

        async loadAudioDevices() {
            this.loadingDevices = true;
            try {
                const response = await fetch('/api/soundscapepipe/audio-devices');
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
            if (deviceName) {
                // Extract just the device name part (before the colon if it exists)
                // e.g. "USB AUDIO DEVICE: Audio (hw:3,0)" -> "USB AUDIO DEVICE"
                const cleanDeviceName = deviceName.split(':')[0].trim();
                this.config.output_device_match = cleanDeviceName;
            }
        },

        async loadModelFiles() {
            this.loadingModels = true;
            try {
                const response = await fetch('/api/soundscapepipe/model-files');
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
                'birdedge': 'MarBird_EFL0_GER.onnx'
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
            ['birdedge', 'yolobat'].forEach(detectorType => {
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
        },

        async loadLureFiles() {
            this.loadingLureFiles = true;
            try {
                const response = await fetch('/api/soundscapepipe/lure-files');
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
                const response = await fetch('/api/soundscapepipe/species');
                if (response.ok) {
                    this.speciesData = await response.json();
                } else {
                    console.error('Failed to load species data');
                    this.speciesData = { birdedge: [], yolobat: [] };
                }
                
                // Load YoloBat labels if a model is selected
                await this.loadYoloBatLabels();
            } catch (error) {
                console.error('Error loading species data:', error);
                this.speciesData = { birdedge: [], yolobat: [] };
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
                const response = await fetch('/api/system-status');
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
                    // No model selected, keep yolobat labels empty
                    this.speciesData.yolobat = [];
                    return;
                }

                const response = await fetch(`/api/soundscapepipe/yolobat-labels?model_path=${encodeURIComponent(modelPath)}`);
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
                    } else {
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
                } else {
                    console.error('Failed to load YoloBat labels');
                    this.speciesData.yolobat = [];
                }
            } catch (error) {
                console.error('Error loading YoloBat labels:', error);
                this.speciesData.yolobat = [];
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

        validateSpeciesGroups() {
            const errors = [];
            
            if (!this.config.groups) {
                return errors;
            }
            
            // Get valid species lists for enabled detectors
            const validBirdEdgeSpecies = new Set();
            const validYoloBatSpecies = new Set();
            
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
                    
                    if (!isValidBirdEdge && !isValidYoloBat) {
                        // Check which detectors are enabled to provide helpful error message
                        const enabledDetectors = [];
                        if (this.config.detectors.birdedge?.enabled) enabledDetectors.push('BirdEdge');
                        if (this.config.detectors.yolobat?.enabled) enabledDetectors.push('YoloBat');
                        
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
    }
}


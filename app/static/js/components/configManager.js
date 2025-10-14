export function configManager() {
    return {
        activeConfig: 'status',  // Default to status page
        message: '',
        error: false,
        warning: false,
        expertMode: false,  // Add expert mode state
        availableServices: [],  // List of services with config files available
        servicesLoaded: false,  // Track if services have been loaded
        hostname: 'Loading...',  // Dynamic hostname loaded via AJAX
        serverMode: false,  // Server mode state
        configGroups: [],  // Available config groups in server mode
        currentConfigGroup: null,  // Current config group in server mode
        initialized: false,  // Track if initialization is complete

        // URL parameter utilities
        getUrlParams() {
            return new URLSearchParams(window.location.search);
        },

        updateUrlParams(params) {
            const currentUrl = new URL(window.location);
            Object.entries(params).forEach(([key, value]) => {
                if (value === null || value === undefined || value === false) {
                    currentUrl.searchParams.delete(key);
                } else {
                    currentUrl.searchParams.set(key, value);
                }
            });
            
            // Update URL without reloading the page
            window.history.replaceState({}, '', currentUrl);
        },

        async init() {
            // Initialize server mode manager first
            await window.serverModeManager.initialize();
            this.serverMode = window.serverModeManager.isEnabled();
            this.configGroups = window.serverModeManager.getConfigGroups();
            this.currentConfigGroup = window.serverModeManager.getCurrentConfigGroup();
            
            // Load available services and hostname
            await Promise.all([
                this.loadAvailableServices(),
                this.loadHostname()
            ]);
            
            // Read expert mode from URL parameter
            const urlParams = this.getUrlParams();
            this.expertMode = urlParams.get('expert') === 'true';

            // Set initial active config based on URL hash
            // In server mode, hash format is: config_group/tab_name
            // In normal mode, hash format is: tab_name
            const hash = window.location.hash.slice(1);
            let tabName = hash;
            
            if (this.serverMode) {
                // Extract tab name from hash (config_group/tab_name)
                const parts = hash.split('/');
                if (parts.length >= 2) {
                    tabName = parts[1];
                } else {
                    tabName = ''; // Will use default
                }
            }
            
            if (tabName === 'radiotracking' && this.isServiceAvailable('radiotracking')) {
                this.activeConfig = 'radiotracking';
            } else if (tabName === 'schedule' && this.isServiceAvailable('schedule')) {
                this.activeConfig = 'schedule';
            } else if (tabName === 'soundscapepipe' && this.isServiceAvailable('soundscapepipe')) {
                this.activeConfig = 'soundscapepipe';
            } else if (tabName === 'status' && !this.serverMode) {
                this.activeConfig = 'status';
            } else {
                // Default to first available service in server mode, status otherwise
                if (this.serverMode) {
                    if (this.isServiceAvailable('schedule')) {
                        this.activeConfig = 'schedule';
                    } else if (this.isServiceAvailable('radiotracking')) {
                        this.activeConfig = 'radiotracking';
                    } else if (this.isServiceAvailable('soundscapepipe')) {
                        this.activeConfig = 'soundscapepipe';
                    }
                } else {
                    this.activeConfig = 'status';
                }
            }

            // Watch for expert mode changes and update URL
            this.$watch('expertMode', (value, oldValue) => {
                // Only update URL if this isn't the initial load
                if (oldValue !== undefined) {
                    this.updateUrlParams({ expert: value || null });
                }
            });
            
            // Watch for currentConfigGroup changes and update serverModeManager
            this.$watch('currentConfigGroup', (value, oldValue) => {
                // Keep serverModeManager in sync (but avoid infinite loops)
                if (this.serverMode && value && value !== oldValue && oldValue !== undefined) {
                    window.serverModeManager.currentConfigGroup = value;
                }
            });

            // Update URL hash when active config changes
            this.$watch('activeConfig', (value) => {
                // In server mode, preserve config group in hash
                if (this.serverMode && this.currentConfigGroup) {
                    window.location.hash = `${encodeURIComponent(this.currentConfigGroup)}/${value}`;
                } else {
                    window.location.hash = value;
                }
                
                // Ensure map is properly initialized when switching to schedule tab
                if (value === 'schedule') {
                    setTimeout(() => {
                        const scheduleComponent = this.$el.querySelector('[x-data*="scheduleConfig"]');
                        if (scheduleComponent && scheduleComponent._x_dataStack) {
                            const scheduleData = scheduleComponent._x_dataStack[0];
                            if (scheduleData && scheduleData.ensureMapVisible) {
                                scheduleData.ensureMapVisible();
                            }
                        }
                    }, 150);
                }
                
                // Ensure map is properly initialized when switching to soundscapepipe tab
                if (value === 'soundscapepipe') {
                    setTimeout(() => {
                        const soundscapeComponent = this.$el.querySelector('[x-data*="soundscapepipeConfig"]');
                        if (soundscapeComponent && soundscapeComponent._x_dataStack) {
                            const soundscapeData = soundscapeComponent._x_dataStack[0];
                            if (soundscapeData && soundscapeData.ensureMapVisible) {
                                soundscapeData.ensureMapVisible();
                            }
                        }
                    }, 150);
                }
            });

            // Listen for hash changes
            window.addEventListener('hashchange', () => {
                const hash = window.location.hash.slice(1);
                if (hash === 'status' || 
                    (hash === 'radiotracking' && this.isServiceAvailable('radiotracking')) ||
                    (hash === 'schedule' && this.isServiceAvailable('schedule')) ||
                    (hash === 'soundscapepipe' && this.isServiceAvailable('soundscapepipe'))) {
                    this.activeConfig = hash;
                }
            });

            // Listen for browser navigation changes (back/forward buttons)
            window.addEventListener('popstate', () => {
                const urlParams = this.getUrlParams();
                const newExpertMode = urlParams.get('expert') === 'true';
                
                // Update expert mode if it changed via browser navigation
                if (this.expertMode !== newExpertMode) {
                    this.expertMode = newExpertMode;
                }
                
                // Update active config based on hash
                const hash = window.location.hash.slice(1);
                if (hash === 'status' || 
                    (hash === 'radiotracking' && this.isServiceAvailable('radiotracking')) ||
                    (hash === 'schedule' && this.isServiceAvailable('schedule')) ||
                    (hash === 'soundscapepipe' && this.isServiceAvailable('soundscapepipe'))) {
                    this.activeConfig = hash;
                }
            });

            // Listen for message events from child components
            window.addEventListener('show-message', (event) => {
                this.showMessage(event.detail.message, event.detail.isError);
            });
            
            // Listen for Alpine.js custom events from child components (like soundscapepipe)
            this.$el.addEventListener('message', (event) => {
                this.message = event.detail.message;
                this.error = event.detail.error;
                this.warning = false;
            });
            
            // Set up periodic hostname refresh (every 30 seconds)
            setInterval(() => {
                this.loadHostname();
            }, 30000);
            
            // Listen for hash changes (browser back/forward or manual URL change)
            window.addEventListener('hashchange', () => {
                const hash = window.location.hash.slice(1);
                let tabName = hash;
                
                if (this.serverMode) {
                    // Extract tab name from hash (config_group/tab_name)
                    const parts = hash.split('/');
                    if (parts.length >= 2) {
                        const configGroup = decodeURIComponent(parts[0]);
                        tabName = parts[1];
                        
                        // Update current config group if changed
                        if (configGroup !== this.currentConfigGroup) {
                            this.currentConfigGroup = configGroup;
                            window.serverModeManager.currentConfigGroup = configGroup;
                            // Reload services and hostname for new config group
                            this.loadAvailableServices();
                            this.loadHostname();
                        }
                    }
                }
                
                // Update activeConfig if the tab changed
                const knownTabs = ['schedule', 'radiotracking', 'soundscapepipe', 'status'];
                if (knownTabs.includes(tabName) && tabName !== this.activeConfig) {
                    this.activeConfig = tabName;
                }
            });
            
            // Mark initialization as complete
            this.initialized = true;
        },

        showMessage(message, isError) {
            this.message = message;
            this.error = isError;
            this.warning = !isError && message.includes("No configuration found");
        },

        async loadAvailableServices() {
            try {
                // Build URL with config_group parameter if in server mode
                let url = '/api/available-services';
                if (this.serverMode && this.currentConfigGroup) {
                    url += `?config_group=${encodeURIComponent(this.currentConfigGroup)}`;
                }
                
                const response = await fetch(url);
                if (response.ok) {
                    const data = await response.json();
                    this.availableServices = data.available_services || [];
                    this.servicesLoaded = true;
                } else {
                    console.error('Failed to load available services');
                    this.availableServices = [];
                    this.servicesLoaded = true;
                }
            } catch (error) {
                console.error('Error loading available services:', error);
                this.availableServices = [];
                this.servicesLoaded = true;
            }
        },

        async loadHostname() {
            // In server mode, display config group name instead of hostname
            if (this.serverMode) {
                this.hostname = this.currentConfigGroup || 'Server Mode';
                return;
            }
            
            try {
                const response = await fetch('/api/system-status');
                if (response.ok) {
                    const data = await response.json();
                    this.hostname = data.hostname || 'Unknown';
                } else {
                    console.error('Failed to load hostname from system status');
                    this.hostname = 'Unknown';
                }
            } catch (error) {
                console.error('Error loading hostname:', error);
                this.hostname = 'Unknown';
            }
        },

        isServiceAvailable(serviceName) {
            return this.availableServices.includes(serviceName);
        },

        streamLogs(serviceName) {
            // Show the log modal
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
        }
    }
}


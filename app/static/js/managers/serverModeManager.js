// Server mode manager - handles server mode state and config groups
export const serverModeManager = {
    enabled: false,
    configGroups: [],
    configRoot: null,
    currentConfigGroup: null,
    initialized: false,

    async initialize() {
        if (this.initialized) {
            return this;
        }

        try {
            const response = await fetch('/api/server-mode');
            if (response.ok) {
                const data = await response.json();
                this.enabled = data.enabled || false;
                this.configGroups = data.config_groups || [];
                this.configRoot = data.config_root || null;

                // Try to get current config group from URL hash
                // URL format in server mode: #config_group_name/tab_name
                this.currentConfigGroup = this._parseConfigGroupFromHash();
                
                if (this.enabled && !this.currentConfigGroup && this.configGroups.length > 0) {
                    // Default to first config group if none specified
                    this.setCurrentConfigGroup(this.configGroups[0], 'schedule');
                } else if (this.enabled && this.currentConfigGroup) {
                    // Validate that the config group exists
                    if (!this.configGroups.includes(this.currentConfigGroup)) {
                        console.warn(`Config group '${this.currentConfigGroup}' not found`);
                        if (this.configGroups.length > 0) {
                            this.setCurrentConfigGroup(this.configGroups[0], 'schedule');
                        }
                    }
                }
            } else {
                console.error('Failed to fetch server mode configuration');
                this.enabled = false;
            }
        } catch (error) {
            console.error('Error fetching server mode configuration:', error);
            this.enabled = false;
        }

        this.initialized = true;
        return this;
    },

    _parseConfigGroupFromHash() {
        const hash = window.location.hash.slice(1); // Remove '#'
        if (!hash) return null;
        
        // Format: config_group_name/tab_name or just config_group_name
        const parts = hash.split('/');
        if (parts.length >= 2) {
            // First part is config group, second is tab
            return decodeURIComponent(parts[0]);
        } else if (parts.length === 1 && parts[0]) {
            // Check if it's a tab name (schedule, radiotracking, soundscapepipe, status)
            const knownTabs = ['schedule', 'radiotracking', 'soundscapepipe', 'status'];
            if (knownTabs.includes(parts[0])) {
                // It's just a tab name, no config group
                return null;
            } else {
                // It's a config group name without a tab
                return decodeURIComponent(parts[0]);
            }
        }
        return null;
    },

    _getTabFromHash() {
        const hash = window.location.hash.slice(1);
        if (!hash) return null;
        
        const parts = hash.split('/');
        if (parts.length >= 2) {
            return parts[1];
        } else if (parts.length === 1) {
            const knownTabs = ['schedule', 'radiotracking', 'soundscapepipe', 'status'];
            if (knownTabs.includes(parts[0])) {
                return parts[0];
            }
        }
        return null;
    },

    isEnabled() {
        return this.enabled;
    },

    getConfigGroups() {
        return this.configGroups;
    },

    getCurrentConfigGroup() {
        return this.currentConfigGroup;
    },

    setCurrentConfigGroup(groupName, tab = null) {
        if (this.enabled && this.configGroups.includes(groupName)) {
            this.currentConfigGroup = groupName;
            
            // Update URL hash to include config group and optionally tab
            // Format: #config_group_name/tab_name
            const currentTab = tab || this._getTabFromHash() || 'schedule';
            const newHash = `${encodeURIComponent(groupName)}/${currentTab}`;
            
            if (window.location.hash !== `#${newHash}`) {
                window.location.hash = newHash;
            }
            
            // Trigger event for components to reload
            window.dispatchEvent(new CustomEvent('config-group-changed'));
            return true;
        }
        return false;
    },

    // Build API URL with config_group parameter if in server mode
    buildApiUrl(baseUrl) {
        if (!this.enabled || !this.currentConfigGroup) {
            return baseUrl;
        }

        const url = new URL(baseUrl, window.location.origin);
        url.searchParams.set('config_group', this.currentConfigGroup);
        return url.toString();
    }
};


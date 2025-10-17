// Server mode manager - handles server mode state and config groups
import { apiUrl } from '../utils/apiUtils.js';

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
            const response = await fetch(apiUrl('/api/server-mode'));
            if (response.ok) {
                const data = await response.json();
                this.enabled = data.enabled || false;
                this.configGroups = data.config_groups || [];
                this.configRoot = data.config_root || null;

                // In server mode, get config group from query parameter
                // URL format: /tsconfig/?config_group=groupname#tab_name or #tab_name/subtab
                this.currentConfigGroup = this._parseConfigGroupFromQuery();
                
                if (this.enabled && !this.currentConfigGroup && this.configGroups.length > 0) {
                    // Default to first config group if none specified
                    // Redirect to proper query parameter URL
                    const firstGroup = this.configGroups[0];
                    const currentTab = this._getTabFromHash() || 'settings/schedule';
                    const baseUrl = window.BASE_URL || '';
                    window.location.href = `${baseUrl}/?config_group=${encodeURIComponent(firstGroup)}#${currentTab}`;
                } else if (this.enabled && this.currentConfigGroup) {
                    // Validate that the config group exists
                    if (!this.configGroups.includes(this.currentConfigGroup)) {
                        console.warn(`Config group '${this.currentConfigGroup}' not found`);
                        if (this.configGroups.length > 0) {
                            // Redirect to valid config group
                            const firstGroup = this.configGroups[0];
                            const currentTab = this._getTabFromHash() || 'settings/schedule';
                            const baseUrl = window.BASE_URL || '';
                            window.location.href = `${baseUrl}/?config_group=${encodeURIComponent(firstGroup)}#${currentTab}`;
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

    _parseConfigGroupFromQuery() {
        // In server mode, config group is in the query parameter: ?config_group=groupname
        // Extract it from window.location.search
        const urlParams = new URLSearchParams(window.location.search);
        const configGroup = urlParams.get('config_group');
        
        if (configGroup) {
            return decodeURIComponent(configGroup);
        }
        
        return null;
    },

    _getTabFromHash() {
        // Hash format: tab_name or tab_name/subtab (e.g., #settings/sshkeys)
        const hash = window.location.hash.slice(1);
        if (!hash) return null;
        
        // Extract main tab (before any slash)
        const mainTab = hash.split('/')[0];
        const knownTabs = ['settings', 'radiotracking', 'soundscapepipe', 'status'];
        if (knownTabs.includes(mainTab)) {
            return hash; // Return full hash to preserve subtab
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
            // In server mode, navigate to proper query parameter URL
            // Format: /tsconfig/?config_group=groupname#tab_name or #tab_name/subtab
            const currentTab = tab || this._getTabFromHash() || 'settings/schedule';
            const baseUrl = window.BASE_URL || '';
            const newUrl = `${baseUrl}/?config_group=${encodeURIComponent(groupName)}#${currentTab}`;
            
            // Use full page navigation to change config group (updates query parameter)
            window.location.href = newUrl;
            return true;
        }
        return false;
    },

    // Build API URL with config_group parameter if in server mode
    buildApiUrl(path) {
        // First apply the base URL prefix
        const fullPath = apiUrl(path);
        
        if (!this.enabled || !this.currentConfigGroup) {
            return fullPath;
        }

        const url = new URL(fullPath, window.location.origin);
        url.searchParams.set('config_group', this.currentConfigGroup);
        return url.toString();
    }
};


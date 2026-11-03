// Server mode manager - handles server mode state and config groups
import { apiUrl, getServerModeInfo } from '../utils/apiUtils.js';

export const serverModeManager = {
    enabled: false,
    configGroups: [],  // Config group keys: "{project}/{group}"
    configRoot: null,
    currentProject: null,
    currentConfigGroup: null,  // Config group key: "{project}/{group}"
    initialized: false,

    async initialize() {
        if (this.initialized) {
            return this;
        }

        try {
            // In server mode, get project and config group from query parameters
            // URL format: /tsconfig/?project=pid&config_group=groupname#tab_name or #tab_name/subtab
            // getServerModeInfo() lists only that project's config groups
            this.currentProject = this._parseProjectFromQuery();
            const data = await getServerModeInfo();
            if (data) {
                this.enabled = data.enabled || false;
                this.configGroups = data.config_groups || [];
                this.configRoot = data.config_root || null;

                this.currentConfigGroup = this._parseConfigGroupFromQuery();
                
                if (this.enabled && this.currentProject && !this.currentConfigGroup && this.configGroups.length > 0) {
                    // Default to the project's first config group if none specified
                    // Without a project, no group is picked; the page lists all groups by project
                    // Redirect to proper query parameter URL
                    window.location.href = this._configGroupUrl(this.configGroups[0]);
                } else if (this.enabled && this.currentConfigGroup) {
                    // Validate that the config group exists
                    if (!this.configGroups.includes(this.currentConfigGroup)) {
                        console.warn(`Config group '${this.currentConfigGroup}' not found`);
                        if (this.configGroups.length > 0) {
                            // Redirect to valid config group of the same project
                            window.location.href = this._configGroupUrl(this.configGroups[0]);
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

    _parseProjectFromQuery() {
        const urlParams = new URLSearchParams(window.location.search);
        return urlParams.get('project') || null;
    },

    _parseConfigGroupFromQuery() {
        // In server mode, config group is in the query parameters: ?project=pid&config_group=groupname
        // Extract it from window.location.search and return the "{project}/{group}" key
        // A group without project is ambiguous and is ignored
        const urlParams = new URLSearchParams(window.location.search);
        const configGroup = urlParams.get('config_group');
        
        if (configGroup && !this.enabled) {
            // Tracker mode: unchanged, not used for routing
            return decodeURIComponent(configGroup);
        }

        if (configGroup && this.currentProject) {
            return `${this.currentProject}/${configGroup}`;
        }
        
        return null;
    },

    _configGroupUrl(groupKey, tab = null) {
        // Format: /tsconfig/?project=pid&config_group=groupname#tab_name or #tab_name/subtab
        const [project, group] = groupKey.split('/');
        const currentTab = tab || this._getTabFromHash() || 'settings/schedule';
        const baseUrl = window.BASE_URL || '';
        return `${baseUrl}/?project=${encodeURIComponent(project)}&config_group=${encodeURIComponent(group)}#${currentTab}`;
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

    // Config groups grouped by project: [{ project, groups: ["{project}/{group}", ...] }, ...]
    getConfigGroupsByProject() {
        const byProject = [];
        for (const key of this.configGroups) {
            const project = key.split('/')[0];
            const last = byProject[byProject.length - 1];
            if (last && last.project === project) {
                last.groups.push(key);
            } else {
                byProject.push({ project, groups: [key] });
            }
        }
        return byProject;
    },

    getCurrentConfigGroup() {
        return this.currentConfigGroup;
    },

    setCurrentConfigGroup(groupName, tab = null) {
        if (this.enabled && this.configGroups.includes(groupName)) {
            // In server mode, navigate to proper query parameter URL
            const newUrl = this._configGroupUrl(groupName, tab);
            
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


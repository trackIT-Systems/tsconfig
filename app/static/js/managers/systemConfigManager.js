// Shared System Config Manager - prevents duplicate API calls
export const systemConfigManager = {
    config: null,
    fetchPromise: null,
    
    async getSystemConfig() {
        // If there's already a fetch in progress, return that promise
        if (this.fetchPromise) {
            return this.fetchPromise;
        }
        
        // Return cached config if available
        if (this.config) {
            return this.config;
        }
        
        // Create and store the fetch promise
        this.fetchPromise = this.fetchSystemConfig();
        
        try {
            const config = await this.fetchPromise;
            this.config = config;
            return config;
        } finally {
            this.fetchPromise = null;
        }
    },
    
    async fetchSystemConfig() {
        // Don't fetch system config in server mode
        if (window.serverModeManager?.isEnabled()) {
            return { services: [] };
        }
        
        const response = await fetch('/api/systemd/config/system');
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }
        return await response.json();
    }
};


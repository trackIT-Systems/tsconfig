import { systemConfigManager } from '../managers/systemConfigManager.js';

export async function getSystemRefreshInterval() {
    try {
        const data = await systemConfigManager.getSystemConfig();
        return data.status_refresh_interval || 30;
    } catch (err) {
        console.warn('Failed to load system config, using default refresh interval:', err);
        return 30; // Default fallback
    }
}


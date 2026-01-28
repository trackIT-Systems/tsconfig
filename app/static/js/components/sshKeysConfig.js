import { apiUrl } from '../utils/apiUtils.js';
import { serviceActionMixin } from '../mixins/serviceActionMixin.js';
import { serviceManager } from '../managers/serviceManager.js';
import { getSystemRefreshInterval } from '../utils/systemUtils.js';
import { triggerDeployment, formatDeploymentMessage } from '../utils/deploymentUtils.js';

export function sshKeysConfig() {
    return {
        ...serviceActionMixin(),
        keys: [],
        newKey: '',
        githubUsername: '',
        launchpadUsername: '',
        loading: false,
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

        // Server mode helper
        get serverMode() {
            return window.serverModeManager?.isEnabled() || false;
        },

        // Sorted keys: server keys first, then user keys
        get sortedKeys() {
            const serverKeys = this.keys.filter(key => key.source === 'server');
            const userKeys = this.keys.filter(key => key.source !== 'server');
            return [...serverKeys, ...userKeys];
        },

        async init() {
            // Small delay to prevent simultaneous API calls during page load
            await new Promise(resolve => setTimeout(resolve, 50));
            
            await this.loadKeys();
            await this.setupPeriodicRefresh();
            
            // Listen for config group changes in server mode
            window.addEventListener('config-group-changed', async () => {
                await this.loadKeys();
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
                // Only refresh if settings tab is active and not in server mode
                const currentHash = window.location.hash.slice(1);
                const mainTab = currentHash.split('/')[0];
                if (mainTab === 'settings' && !this.serverMode) {
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
                const sshService = services.find(service => service.name === 'ssh');
                if (sshService) {
                    this.serviceStatus = {
                        active: sshService.active,
                        enabled: sshService.enabled,
                        status: sshService.status,
                        uptime: sshService.uptime || 'N/A'
                    };
                }
            } catch (error) {
                console.error('Failed to load service status:', error);
            } finally {
                this.serviceStatusLoading = false;
            }
        },

        async loadKeys() {
            try {
                // Build API URL with config_group parameter if in server mode
                const url = window.serverModeManager?.buildApiUrl('/api/authorized-keys') || '/api/authorized-keys';
                const response = await fetch(url);
                
                if (!response.ok) {
                    // If file doesn't exist, that's okay - start with empty list
                    if (response.status === 404) {
                        this.keys = [];
                        return;
                    }
                    throw new Error(`Failed to load SSH keys: ${response.status}`);
                }
                
                const data = await response.json();
                this.keys = data.keys || [];
                
                // Load service status only in tracker mode (default mode)
                if (!this.serverMode) {
                    await this.loadServiceStatus();
                }
            } catch (error) {
                // Don't show error message if it's just a missing file
                if (!error.message.includes('404')) {
                    this.showMessage('Failed to load SSH keys: ' + error.message, 'error');
                }
                this.keys = [];
            }
        },

        async addKey() {
            if (!this.newKey.trim()) {
                this.showMessage('Please enter an SSH key', 'error');
                return;
            }

            this.loading = true;

            try {
                // Build API URL with config_group parameter if in server mode
                const url = window.serverModeManager?.buildApiUrl('/api/authorized-keys') || '/api/authorized-keys';
                
                const response = await fetch(url, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({
                        key: this.newKey.trim()
                    })
                });

                if (!response.ok) {
                    const errorData = await response.json();
                    throw new Error(errorData.detail?.message || errorData.detail || 'Failed to add SSH key');
                }

                const data = await response.json();
                this.keys = data.keys || [];
                this.newKey = '';
                this.showMessage('SSH key added and saved successfully', 'success');
                
                // Trigger deployment in server mode
                await this.triggerDeploymentIfServerMode();
            } catch (error) {
                this.showMessage(error.message || 'Failed to add SSH key', 'error');
            } finally {
                this.loading = false;
            }
        },

        async removeKey(key) {
            if (!confirm('Are you sure you want to remove this SSH key? This will prevent access using this key.')) {
                return;
            }

            // Find the index of the key in the original keys array
            const index = this.keys.findIndex(k => 
                k.key_data === key.key_data && k.source === key.source
            );

            if (index === -1) {
                this.showMessage('Failed to remove SSH key: key not found', 'error');
                return;
            }

            this.loading = true;

            try {
                // Build API URL with config_group parameter if in server mode
                // Use buildApiUrl with the full path including the index
                const url = window.serverModeManager?.buildApiUrl(`/api/authorized-keys/${index}`) || `/api/authorized-keys/${index}`;
                
                const response = await fetch(url, {
                    method: 'DELETE'
                });

                if (!response.ok) {
                    const errorData = await response.json();
                    throw new Error(errorData.detail || 'Failed to remove SSH key');
                }

                const data = await response.json();
                this.keys = data.keys || [];
                this.showMessage('SSH key removed successfully', 'success');
                
                // Trigger deployment in server mode
                await this.triggerDeploymentIfServerMode();
            } catch (error) {
                this.showMessage(error.message || 'Failed to remove SSH key', 'error');
            } finally {
                this.loading = false;
            }
        },

        async importFromGitHub() {
            await this.importKeys('github', this.githubUsername);
            this.githubUsername = '';
        },

        async importFromLaunchpad() {
            await this.importKeys('launchpad', this.launchpadUsername);
            this.launchpadUsername = '';
        },

        async importKeys(platform, username) {
            if (!username.trim()) {
                this.showMessage(`Please enter a ${platform} username`, 'error');
                return;
            }

            this.loading = true;

            try {
                // Build API URL with config_group parameter if in server mode
                const url = window.serverModeManager?.buildApiUrl('/api/authorized-keys/import') 
                            || '/api/authorized-keys/import';
                
                const response = await fetch(url, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({
                        platform: platform,
                        username: username.trim()
                    })
                });

                if (!response.ok) {
                    const errorData = await response.json();
                    throw new Error(errorData.detail?.message || errorData.detail || `Failed to import SSH keys from ${platform}`);
                }

                const data = await response.json();
                this.keys = data.keys || [];
                
                // Show detailed message with counts
                this.showMessage(data.message || 'SSH keys imported successfully', 'success');
                
                // Trigger deployment in server mode
                await this.triggerDeploymentIfServerMode();
            } catch (error) {
                this.showMessage(error.message || `Failed to import SSH keys from ${platform}`, 'error');
            } finally {
                this.loading = false;
            }
        },

        showMessage(msg, type = 'success') {
            // Use global toast manager
            if (window.toastManager) {
                const title = type === 'success' ? 'SSH Keys - Success' : 
                             type === 'error' ? 'SSH Keys - Error' : 'SSH Keys';
                window.toastManager.show(msg, type, { title });
            }
        },

        async triggerDeploymentIfServerMode() {
            // Only trigger deployment in server mode
            if (!this.serverMode) {
                return;
            }

            const configGroup = window.serverModeManager?.getCurrentConfigGroup();
            if (!configGroup) {
                console.warn('No config group available for deployment');
                return;
            }

            try {
                const deploymentResult = await triggerDeployment(configGroup);
                const deploymentMessage = formatDeploymentMessage(deploymentResult);
                
                if (window.toastManager) {
                    window.toastManager.success(deploymentMessage);
                }
            } catch (deployError) {
                console.error('Deployment error:', deployError);
                if (window.toastManager) {
                    window.toastManager.error(`Deployment failed: ${deployError.message}`);
                }
            }
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
    };
}


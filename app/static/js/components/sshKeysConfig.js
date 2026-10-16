import { compose } from '../mixins/compose.js';
import { configPageMixin } from '../mixins/configPageMixin.js';
import { serverModeManager } from '../managers/serverModeManager.js';
import { deployIfServerMode } from '../utils/deploymentUtils.js';

export function sshKeysConfig() {
    return compose(configPageMixin({
        service: 'ssh',
        title: 'SSH Keys - Success',
        errorTitle: 'SSH Keys - Error',
        refreshOn: ['settings'],
    }), {
        keys: [],
        newKey: '',
        githubUsername: '',
        launchpadUsername: '',
        loading: false,
        // Sorted keys: server keys first, then user keys
        get sortedKeys() {
            const serverKeys = this.keys.filter(key => key.source === 'server');
            const userKeys = this.keys.filter(key => key.source !== 'server');
            return [...serverKeys, ...userKeys];
        },

        async loadData() {
            await this.loadKeys();
        },

        async loadKeys() {
            try {
                // Build API URL with config_group parameter if in server mode
                const url = serverModeManager.buildApiUrl('/api/authorized-keys');
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
                const url = serverModeManager.buildApiUrl('/api/authorized-keys');
                
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
                await deployIfServerMode();
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
                const url = serverModeManager.buildApiUrl(`/api/authorized-keys/${index}`);
                
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
                await deployIfServerMode();
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
                const url = serverModeManager.buildApiUrl('/api/authorized-keys/import');
                
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
                await deployIfServerMode();
            } catch (error) {
                this.showMessage(error.message || `Failed to import SSH keys from ${platform}`, 'error');
            } finally {
                this.loading = false;
            }
        },

    });
}

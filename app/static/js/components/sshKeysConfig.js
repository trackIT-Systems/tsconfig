import { apiUrl } from '../utils/apiUtils.js';

export function sshKeysConfig() {
    return {
        keys: [],
        newKey: '',
        loading: false,

        // Server mode helper
        get serverMode() {
            return window.serverModeManager?.isEnabled() || false;
        },

        async init() {
            await this.loadKeys();
        },

        async loadKeys() {
            try {
                // Build API URL with config_group parameter if in server mode
                const url = window.serverModeManager?.buildApiUrl('/api/authorized-keys') || '/api/authorized-keys';
                console.log('Loading SSH keys from:', url);
                const response = await fetch(url);
                
                if (!response.ok) {
                    // If file doesn't exist, that's okay - start with empty list
                    if (response.status === 404) {
                        console.log('No authorized_keys file found, starting with empty list');
                        this.keys = [];
                        return;
                    }
                    const errorText = await response.text();
                    console.error('Failed to load SSH keys:', response.status, errorText);
                    throw new Error(`Failed to load SSH keys: ${response.status}`);
                }
                
                const data = await response.json();
                console.log('Loaded SSH keys:', data);
                this.keys = data.keys || [];
            } catch (error) {
                console.error('Error loading SSH keys:', error);
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
                console.log('Adding SSH key to:', url);
                
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
                    console.error('Failed to add SSH key:', response.status, errorData);
                    throw new Error(errorData.detail?.message || errorData.detail || 'Failed to add SSH key');
                }

                const data = await response.json();
                console.log('SSH key added successfully:', data);
                this.keys = data.keys || [];
                this.newKey = '';
                this.showMessage('SSH key added and saved successfully', 'success');
            } catch (error) {
                console.error('Error adding SSH key:', error);
                this.showMessage(error.message || 'Failed to add SSH key', 'error');
            } finally {
                this.loading = false;
            }
        },

        async removeKey(index) {
            if (!confirm('Are you sure you want to remove this SSH key? This will prevent access using this key.')) {
                return;
            }

            this.loading = true;

            try {
                // Build API URL with config_group parameter if in server mode
                // Use buildApiUrl with the full path including the index
                const url = window.serverModeManager?.buildApiUrl(`/api/authorized-keys/${index}`) || `/api/authorized-keys/${index}`;
                console.log('Removing SSH key from:', url);
                
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
            } catch (error) {
                console.error('Error removing SSH key:', error);
                this.showMessage(error.message || 'Failed to remove SSH key', 'error');
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
            } else {
                // Fallback to console if toast manager not available
                console.log(`[SSH ${type.toUpperCase()}] ${msg}`);
            }
        }
    };
}


import { triggerDeployment, formatDeploymentMessage } from '../utils/deploymentUtils.js';

export function userconfConfig() {
    return {
        inputMode: 'password', // 'password' | 'hash'
        password: '',
        passwordConfirm: '',
        content: '',
        loading: false,

        // Server mode helper
        get serverMode() {
            return window.serverModeManager?.isEnabled() || false;
        },

        apiUrl(path) {
            return window.serverModeManager?.buildApiUrl(path) || path;
        },

        async setPassword() {
            let content;

            if (this.inputMode === 'password') {
                if (!this.password) {
                    this.showMessage('Please enter a password', 'error');
                    return;
                }
                if (this.password !== this.passwordConfirm) {
                    this.showMessage('Passwords do not match', 'error');
                    return;
                }

                this.loading = true;
                try {
                    const hashResponse = await fetch(this.apiUrl('/api/userconf/hash'), {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ password: this.password }),
                    });
                    const hashData = await hashResponse.json();
                    if (!hashResponse.ok) {
                        throw new Error(hashData.detail || 'Failed to hash password');
                    }
                    content = hashData.content;
                } catch (error) {
                    this.showMessage(error.message || 'Failed to hash password', 'error');
                    this.loading = false;
                    return;
                }
            } else {
                if (!this.content.trim()) {
                    this.showMessage('Please paste a userconf.txt line (pi:$6$...)', 'error');
                    return;
                }
                content = this.content.trim();
                this.loading = true;
            }

            try {
                const response = await fetch(this.apiUrl('/api/userconf'), {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ content }),
                });

                const data = await response.json();
                if (!response.ok) {
                    const detail = data.detail;
                    const message = (detail && (detail.errors?.join(', ') || detail.message)) || 'Failed to set operator account password';
                    throw new Error(message);
                }

                this.password = '';
                this.passwordConfirm = '';
                this.content = '';
                this.showMessage(
                    this.serverMode
                        ? 'Operator account password saved to this config group'
                        : 'Operator account password saved - takes effect on next reboot',
                    'success'
                );

                await this.triggerDeploymentIfServerMode();
            } catch (error) {
                this.showMessage(error.message || 'Failed to set operator account password', 'error');
            } finally {
                this.loading = false;
            }
        },

        showMessage(msg, type = 'success') {
            if (window.toastManager) {
                const title = type === 'success' ? 'Operator Account Password - Success' : 'Operator Account Password - Error';
                window.toastManager.show(msg, type, { title });
            }
        },

        async triggerDeploymentIfServerMode() {
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
    };
}

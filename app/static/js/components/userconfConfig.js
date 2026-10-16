import { compose } from '../mixins/compose.js';
import { serverModeMixin } from '../mixins/serverModeMixin.js';
import { toastMixin } from '../mixins/toastMixin.js';
import { serverModeManager } from '../managers/serverModeManager.js';
import { deployIfServerMode } from '../utils/deploymentUtils.js';

export function userconfConfig() {
    return compose(serverModeMixin(), toastMixin('Operator Account Password - Success', 'Operator Account Password - Error'), {
        inputMode: 'password', // 'password' | 'hash'
        password: '',
        passwordConfirm: '',
        content: '',
        loading: false,

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
                    const hashResponse = await fetch(serverModeManager.buildApiUrl('/api/userconf/hash'), {
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
                const response = await fetch(serverModeManager.buildApiUrl('/api/userconf'), {
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

                await deployIfServerMode();
            } catch (error) {
                this.showMessage(error.message || 'Failed to set operator account password', 'error');
            } finally {
                this.loading = false;
            }
        },

    });
}

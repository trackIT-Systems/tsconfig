import { apiUrl } from '../utils/apiUtils.js';

export function resetConfig() {
    return {
        resetConfig: true,
        wipeOverlay: false,
        executing: false,
        result: null,
        error: null,

        get serverMode() {
            return window.serverModeManager?.isEnabled() || false;
        },

        init() {
            // Initialize component
        },

        get canExecute() {
            return this.resetConfig || this.wipeOverlay;
        },

        get confirmationMessage() {
            const parts = [];
            if (this.resetConfig) {
                parts.push('Reset original configuration (remove WireGuard config)');
            }
            if (this.wipeOverlay) {
                parts.push('Wipe overlay filesystem at /media/root-rw');
            }
            if (parts.length === 0) return '';
            const rebootNote =
                this.resetConfig || this.wipeOverlay ? '\n\nThe system will reboot if needed.' : '';
            return `You are about to:\n${parts.map((p) => `• ${p}`).join('\n')}${rebootNote}\n\nThis cannot be undone. Continue?`;
        },

        async executeReset() {
            if (!this.canExecute) {
                this.showMessage('Select at least one step to execute', 'error');
                return;
            }

            if (!confirm(this.confirmationMessage)) {
                return;
            }

            this.executing = true;
            this.result = null;
            this.error = null;

            try {
                const url = apiUrl('/api/system-reset');
                const response = await fetch(url, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({
                        reset_config: this.resetConfig,
                        wipe_overlay: this.wipeOverlay,
                    }),
                });

                const data = await response.json();

                if (!response.ok) {
                    throw new Error(data.detail || 'System reset failed');
                }

                this.result = data;

                let message = data.message || 'System reset completed';
                if (data.reboot_initiated) {
                    message += '\n\nSystem is rebooting. The page will become unavailable shortly.';
                }
                this.showMessage(message, 'success');
            } catch (error) {
                console.error('Error during system reset:', error);
                this.error = error.message;
                this.showMessage(error.message || 'System reset failed', 'error');
            } finally {
                this.executing = false;
            }
        },

        showMessage(msg, type = 'success') {
            if (window.toastManager) {
                const title =
                    type === 'success'
                        ? 'System Reset - Success'
                        : type === 'error'
                          ? 'System Reset - Error'
                          : 'System Reset';
                window.toastManager.show(msg, type, { title });
            } else {
                console.log(`[System Reset ${type.toUpperCase()}] ${msg}`);
            }
        },
    };
}

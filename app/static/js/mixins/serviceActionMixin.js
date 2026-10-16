import { serviceAction } from '../utils/apiUtils.js';

/**
 * Mixin for service action management (start, stop, restart)
 * Provides common functionality for controlling systemd services
 */
export function serviceActionMixin() {
    return {
        // Service restart states (track individual service restart states)
        serviceRestartStates: {}, // serviceName -> 'idle' | 'restarting' | 'restarted'
        // Service start/stop states (track individual service start/stop states)
        serviceStartStopStates: {}, // serviceName -> 'idle' | 'starting' | 'stopping' | 'started' | 'stopped'

        setServiceRestartState(serviceName, state) {
            this.serviceRestartStates[serviceName] = state;
            if (state === 'restarted') {
                setTimeout(() => {
                    this.serviceRestartStates[serviceName] = 'idle';
                }, 5000);
            }
        },

        getServiceRestartState(serviceName) {
            return this.serviceRestartStates[serviceName] || 'idle';
        },

        setServiceStartStopState(serviceName, state) {
            this.serviceStartStopStates[serviceName] = state;
            
            // Auto-reset after 5 seconds for completed states
            if (state === 'started' || state === 'stopped') {
                setTimeout(() => {
                    this.serviceStartStopStates[serviceName] = 'idle';
                }, 5000);
            }
        },

        getServiceStartStopState(serviceName) {
            return this.serviceStartStopStates[serviceName] || 'idle';
        },

        async performAction(serviceName, action) {
            // Only restarts show their progress on the button
            await this._runServiceAction(
                serviceName,
                action,
                (name, state) => this.setServiceRestartState(name, state),
                action === 'restart' ? { pending: 'restarting', done: 'restarted' } : {}
            );
        },

        async performStartStopAction(serviceName, action) {
            await this._runServiceAction(
                serviceName,
                action,
                (name, state) => this.setServiceStartStopState(name, state),
                { pending: action === 'start' ? 'starting' : 'stopping', done: action === 'start' ? 'started' : 'stopped' }
            );
        },

        async _runServiceAction(serviceName, action, setState, states) {
            this.actionLoading = true;
            if (states.pending) {
                setState(serviceName, states.pending);
            }

            try {
                const data = await serviceAction(serviceName, action);
                // Use component's message display method
                this._displaySuccessMessage(data.message);
                if (states.done) {
                    setState(serviceName, states.done);
                }
                // Refresh service status after action
                setTimeout(async () => {
                    await this._refreshServiceStatus();
                }, 1000);
            } catch (err) {
                this._displayErrorMessage(err.message);
                console.error(`Service ${action} error:`, err);
                if (states.pending) {
                    setState(serviceName, 'idle');
                }
            } finally {
                this.actionLoading = false;
            }
        },

        // Default implementations - components can override these
        _displaySuccessMessage(message) {
            if (this.showMessage) {
                // For components with showMessage method (scheduleConfig, soundscapepipeConfig)
                this.showMessage(message, false);
            } else if (this.dispatchMessage) {
                // For components with dispatchMessage method (radiotrackingConfig)
                this.dispatchMessage(message, false);
            } else if (this.showToast) {
                // For components with showToast method (statusPage)
                this.showToast(message, 'success', { title: 'Service Action' });
            } else {
                console.log('[SUCCESS]', message);
            }
        },

        _displayErrorMessage(message) {
            if (this.showMessage) {
                // For components with showMessage method (scheduleConfig, soundscapepipeConfig)
                this.showMessage(message, true);
            } else if (this.dispatchMessage) {
                // For components with dispatchMessage method (radiotrackingConfig)
                this.dispatchMessage(message, true);
            } else if (this.showToast) {
                // For components with showToast method (statusPage)
                this.showToast(message, 'error', { title: 'Service Action Failed' });
            } else {
                console.error('[ERROR]', message);
            }
        },

        _refreshServiceStatus() {
            if (this.loadServiceStatus) {
                // For config components
                return this.loadServiceStatus();
            } else if (this.loadServices) {
                // For statusPage
                return this.loadServices();
            }
            return Promise.resolve();
        }
    };
}


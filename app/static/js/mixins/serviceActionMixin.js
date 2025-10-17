import { apiUrl } from '../utils/apiUtils.js';

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
            this.actionLoading = true;
            
            // For restart actions, set the service-specific state
            if (action === 'restart') {
                this.setServiceRestartState(serviceName, 'restarting');
            }
            
            try {
                const response = await fetch(apiUrl('/api/systemd/action'), {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({
                        service: serviceName,
                        action: action
                    })
                });
                
                const data = await response.json();
                
                if (!response.ok) {
                    throw new Error(data.detail || `Failed to ${action} service`);
                }
                
                // Use component's message display method
                this._displaySuccessMessage(data.message);
                
                // For restart actions, set the service-specific state
                if (action === 'restart') {
                    this.setServiceRestartState(serviceName, 'restarted');
                }
                
                // Refresh service status after action
                setTimeout(async () => {
                    await this._refreshServiceStatus();
                }, 1000);
                
            } catch (err) {
                this._displayErrorMessage(err.message);
                console.error(`Service ${action} error:`, err);
                
                // For restart actions, reset the service-specific state
                if (action === 'restart') {
                    this.setServiceRestartState(serviceName, 'idle');
                }
            } finally {
                this.actionLoading = false;
            }
        },

        async performStartStopAction(serviceName, action) {
            this.actionLoading = true;
            
            // Set the service-specific state
            if (action === 'start') {
                this.setServiceStartStopState(serviceName, 'starting');
            } else if (action === 'stop') {
                this.setServiceStartStopState(serviceName, 'stopping');
            }
            
            try {
                const response = await fetch(apiUrl('/api/systemd/action'), {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({
                        service: serviceName,
                        action: action
                    })
                });
                
                const data = await response.json();
                
                if (!response.ok) {
                    throw new Error(data.detail || `Failed to ${action} service`);
                }
                
                // Use component's message display method
                this._displaySuccessMessage(data.message);
                
                // Set the service-specific state to completed
                if (action === 'start') {
                    this.setServiceStartStopState(serviceName, 'started');
                } else if (action === 'stop') {
                    this.setServiceStartStopState(serviceName, 'stopped');
                }
                
                // Refresh service status after action
                setTimeout(async () => {
                    await this._refreshServiceStatus();
                }, 1000);
                
            } catch (err) {
                this._displayErrorMessage(err.message);
                console.error(`Service ${action} error:`, err);
                
                // Reset the service-specific state on error
                this.setServiceStartStopState(serviceName, 'idle');
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


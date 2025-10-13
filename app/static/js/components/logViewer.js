import { saveStateMixin } from '../mixins/saveStateMixin.js';

export function logViewer() {
    return {
        ...saveStateMixin(),
        currentService: '',
        logs: [],
        isStreaming: false,
        streamError: null,
        autoScroll: true,
        eventSource: null,
        maxLogs: 1000, // Limit to prevent memory issues
        restartState: 'idle', // 'idle', 'restarting', 'restarted'

        init() {
            // Listen for modal close event to cleanup
            const modal = document.getElementById('logModal');
            modal.addEventListener('hidden.bs.modal', () => {
                this.stopStreaming();
            });
            
            // Listen for modal shown event to scroll to bottom
            modal.addEventListener('shown.bs.modal', () => {
                setTimeout(() => {
                    const container = document.getElementById('logContainer');
                    if (container) {
                        container.scrollTop = container.scrollHeight;
                    }
                }, 100);
            });
        },

        startStreaming(serviceName) {
            this.currentService = serviceName;
            this.logs = [];
            this.streamError = null;
            this.isStreaming = true;

            try {
                // Create event source for server-sent events
                this.eventSource = new EventSource(`/api/systemd/logs/${encodeURIComponent(serviceName)}`);
                
                this.eventSource.onmessage = (event) => {
                    const logLine = event.data;
                    // Only add non-empty lines to avoid empty lines at the start
                    if (logLine && logLine.trim()) {
                        this.logs.push(logLine);
                        
                        // Keep only the last maxLogs entries to prevent memory issues
                        if (this.logs.length > this.maxLogs) {
                            this.logs = this.logs.slice(-this.maxLogs);
                        }
                        
                        // Auto-scroll to bottom for new log entries
                        if (this.autoScroll) {
                            this.$nextTick(() => {
                                const container = document.getElementById('logContainer');
                                if (container) {
                                    container.scrollTop = container.scrollHeight;
                                }
                            });
                        }
                    }
                };

                this.eventSource.onerror = (error) => {
                    console.error('Log stream error:', error);
                    this.streamError = 'Connection to log stream failed';
                    this.isStreaming = false;
                    if (this.eventSource) {
                        this.eventSource.close();
                        this.eventSource = null;
                    }
                };
            } catch (error) {
                console.error('Error starting log stream:', error);
                this.streamError = 'Failed to start log streaming';
                this.isStreaming = false;
            }
        },

        stopStreaming() {
            if (this.eventSource) {
                this.eventSource.close();
                this.eventSource = null;
            }
            this.isStreaming = false;
        },

        clearLogs() {
            // Keep the last log line if there are any logs
            if (this.logs.length > 0) {
                this.logs = [this.logs[this.logs.length - 1]];
            }
        },

        async restartService() {
            if (!this.currentService) return;
            
            const restartFunction = async () => {
                // Call the systemd API directly
                const response = await fetch('/api/systemd/action', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({
                        service: this.currentService,
                        action: 'restart'
                    })
                });
                
                const data = await response.json();
                
                if (!response.ok) {
                    throw new Error(data.detail || `Failed to restart ${this.currentService} service`);
                }
                
                // Show success message if there's a way to display it
                // Try to find any component with showMessage method
                const configComponent = document.querySelector('[x-data*="Config"]');
                if (configComponent && configComponent._x_dataStack && configComponent._x_dataStack[0].showMessage) {
                    configComponent._x_dataStack[0].showMessage(`${data.message}`, false);
                }
            };
            
            try {
                this.restartState = 'restarting';
                await restartFunction();
                this.restartState = 'restarted';
                
                // Reset to idle state after 5 seconds
                setTimeout(() => {
                    this.restartState = 'idle';
                }, 5000);
                
            } catch (error) {
                this.restartState = 'idle';
                console.error('Failed to restart service:', error);
                
                // Show error message if there's a way to display it
                const configComponent = document.querySelector('[x-data*="Config"]');
                if (configComponent && configComponent._x_dataStack && configComponent._x_dataStack[0].showMessage) {
                    configComponent._x_dataStack[0].showMessage(error.message, true);
                } else {
                    // Fallback to alert
                    alert(`Failed to restart service: ${error.message}`);
                }
            }
        }
    };
}


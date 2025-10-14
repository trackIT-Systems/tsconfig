import { apiUrl } from '../utils/apiUtils.js';

export function shellViewer() {
    return {
        isConnected: false,
        isConnecting: false,
        connectionError: null,
        terminal: null,
        websocket: null,
        sessionId: null,
        fitAddon: null,
        resizeObserver: null,
        resizeHandler: null,

        init() {
            // Generate a unique session ID
            this.sessionId = 'shell_' + Math.random().toString(36).substr(2, 9);
            
            // Handle modal show event
            document.getElementById('shellModal').addEventListener('shown.bs.modal', () => {
                // Generate a new session ID each time the modal is opened
                this.sessionId = 'shell_' + Math.random().toString(36).substr(2, 9);
                
                // Reset connection state
                this.isConnected = false;
                this.isConnecting = false;
                this.connectionError = null;
                
                this.$nextTick(() => {
                    this.initTerminal();
                    setTimeout(() => {
                        this.connect();
                    }, 200); // Give terminal more time to initialize
                });
            });

            // Handle modal hide event
            document.getElementById('shellModal').addEventListener('hidden.bs.modal', () => {
                this.disconnect();
                this.destroyTerminal();
                
                // Reset all state
                this.isConnected = false;
                this.isConnecting = false;
                this.connectionError = null;
                this.websocket = null;
            });
        },

        initTerminal() {
            if (this.terminal) {
                this.destroyTerminal();
            }

            // Ensure terminal container is ready
            const terminalContainer = document.getElementById('terminal');
            if (!terminalContainer) {
                return;
            }

            // Create xterm.js terminal instance
            this.terminal = new Terminal({
                cursorBlink: true,
                fontSize: 14,
                fontFamily: 'Consolas, "Liberation Mono", Menlo, Courier, monospace',
                theme: {
                    background: '#000000',
                    foreground: '#ffffff',
                    cursor: '#ffffff',
                    selection: '#ffffff40'
                },
                convertEol: true,
                disableStdin: false,
                allowProposedApi: true
            });

            // Create and load the fit addon
            this.fitAddon = new FitAddon.FitAddon();
            this.terminal.loadAddon(this.fitAddon);

            // Open terminal in the container
            this.terminal.open(terminalContainer);

            // Show the terminal container
            terminalContainer.style.display = 'block';

            // Handle terminal input
            this.terminal.onData((data) => {
                if (this.websocket && this.websocket.readyState === WebSocket.OPEN) {
                    this.websocket.send(JSON.stringify({
                        type: 'input',
                        data: data
                    }));
                }
            });

            // Initial fit after a small delay to ensure container is properly sized
            this.$nextTick(() => {
                setTimeout(() => {
                    this.fitTerminal();
                }, 100);
            });

            // Handle window resize
            this.resizeHandler = () => {
                this.fitTerminal();
            };
            window.addEventListener('resize', this.resizeHandler);

            // Handle modal resize events
            const modal = document.getElementById('shellModal');
            this.resizeObserver = new ResizeObserver(() => {
                this.fitTerminal();
            });
            this.resizeObserver.observe(modal);
        },

        fitTerminal() {
            if (this.terminal && this.fitAddon) {
                try {
                    // Use the fit addon for proper terminal sizing
                    this.fitAddon.fit();
                    
                    // Get the new dimensions
                    const dimensions = this.fitAddon.proposeDimensions();
                    if (dimensions) {
                        // Send resize information to the backend PTY
                        if (this.websocket && this.websocket.readyState === WebSocket.OPEN) {
                            this.websocket.send(JSON.stringify({
                                type: 'resize',
                                cols: dimensions.cols,
                                rows: dimensions.rows
                            }));
                        }
                    }
                } catch (error) {
                    console.warn('Error fitting terminal:', error);
                    // Fallback to manual calculation
                    this.manualFitTerminal();
                }
            }
        },

        manualFitTerminal() {
            if (this.terminal) {
                const container = document.getElementById('terminal');
                if (container) {
                    const rect = container.getBoundingClientRect();
                    
                    // Create a temporary element to measure character dimensions
                    const measureElement = document.createElement('div');
                    measureElement.style.fontFamily = this.terminal.options.fontFamily;
                    measureElement.style.fontSize = this.terminal.options.fontSize + 'px';
                    measureElement.style.position = 'absolute';
                    measureElement.style.visibility = 'hidden';
                    measureElement.style.whiteSpace = 'pre';
                    measureElement.textContent = 'W'.repeat(10); // Use 'W' as it's typically the widest character
                    
                    document.body.appendChild(measureElement);
                    
                    const charWidth = measureElement.offsetWidth / 10;
                    const charHeight = measureElement.offsetHeight;
                    
                    document.body.removeChild(measureElement);
                    
                    // Calculate dimensions with some padding
                    const padding = 16; // Account for container padding
                    const cols = Math.floor((rect.width - padding) / charWidth);
                    const rows = Math.floor((rect.height - padding) / charHeight);
                    
                    if (cols > 0 && rows > 0) {
                        this.terminal.resize(cols, rows);
                        
                        // Send resize information to the backend
                        if (this.websocket && this.websocket.readyState === WebSocket.OPEN) {
                            this.websocket.send(JSON.stringify({
                                type: 'resize',
                                cols: cols,
                                rows: rows
                            }));
                        }
                    }
                }
            }
        },

        connect() {
            if (this.isConnecting || this.isConnected) {
                return;
            }

            this.isConnecting = true;
            this.connectionError = null;

            // Create WebSocket connection
            const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            const baseUrl = window.BASE_URL || '';
            const wsUrl = `${protocol}//${window.location.host}${baseUrl}/api/shell/ws/${this.sessionId}`;
            
            this.websocket = new WebSocket(wsUrl);

            this.websocket.onopen = () => {
                this.isConnecting = false;
                this.isConnected = true;
                this.connectionError = null;
                
                if (this.terminal) {
                    this.terminal.clear();
                    this.terminal.write('\x1b[32mTerminal connected successfully!\x1b[0m\r\n');
                    this.terminal.write('\x1b[90mUsing your default shell...\x1b[0m\r\n');
                    
                    // Ensure terminal is properly sized after connection
                    setTimeout(() => {
                        this.fitTerminal();
                    }, 100);
                }
            };

            this.websocket.onmessage = (event) => {
                try {
                    const data = JSON.parse(event.data);
                    
                    if (data.type === 'output' && this.terminal) {
                        this.terminal.write(data.data);
                    } else if (data.type === 'error') {
                        if (this.terminal) {
                            this.terminal.write(`\x1b[31mError: ${data.data}\x1b[0m\r\n`);
                        }
                        this.connectionError = data.data;
                    } else if (data.type === 'exit') {
                        if (this.terminal) {
                            this.terminal.write(`\x1b[33m\r\n${data.data}\x1b[0m\r\n`);
                            this.terminal.write('\x1b[90mConnection will close automatically...\x1b[0m\r\n');
                        }
                        // Mark as disconnected
                        this.isConnected = false;
                        this.isConnecting = false;
                    }
                } catch (e) {
                    console.error('Error parsing WebSocket message:', e);
                }
            };

            this.websocket.onerror = (error) => {
                this.connectionError = 'WebSocket connection failed';
                this.isConnecting = false;
                this.isConnected = false;
                
                if (this.terminal) {
                    this.terminal.write('\x1b[31mConnection failed. Try closing and reopening the modal.\x1b[0m\r\n');
                }
            };

            this.websocket.onclose = (event) => {
                this.isConnecting = false;
                this.isConnected = false;
                
                if (this.terminal && !this.connectionError) {
                    this.terminal.write('\x1b[33m\r\nConnection closed\x1b[0m\r\n');
                }
            };
        },

        disconnect() {
            if (this.websocket) {
                this.websocket.close();
                this.websocket = null;
            }
            this.isConnected = false;
            this.isConnecting = false;
            this.connectionError = null;
        },

        reconnect() {
            this.disconnect();
            
            // Generate a new session ID for reconnection
            this.sessionId = 'shell_' + Math.random().toString(36).substr(2, 9);
            
            this.$nextTick(() => {
                // Ensure terminal is properly fitted before connecting
                if (this.terminal) {
                    setTimeout(() => {
                        this.fitTerminal();
                        // Wait a bit more to ensure terminal is fully ready
                        setTimeout(() => {
                            this.connect();
                        }, 100);
                    }, 100);
                } else {
                    this.connect();
                }
            });
        },

        clearTerminal() {
            if (this.terminal) {
                this.terminal.clear();
            }
        },

        destroyTerminal() {
            // Clean up resize observers and handlers
            if (this.resizeObserver) {
                this.resizeObserver.disconnect();
                this.resizeObserver = null;
            }
            
            if (this.resizeHandler) {
                window.removeEventListener('resize', this.resizeHandler);
                this.resizeHandler = null;
            }
            
            // Clean up terminal and addon carefully
            if (this.terminal) {
                try {
                    // Only dispose if the terminal is still valid and not already disposed
                    if (this.terminal.element && this.terminal.element.parentNode) {
                        this.terminal.dispose();
                    }
                } catch (error) {
                    // Silently handle disposal errors - they're usually harmless
                }
                this.terminal = null;
            }
            
            // Clear addon reference
            this.fitAddon = null;
            
            const terminalElement = document.getElementById('terminal');
            if (terminalElement) {
                terminalElement.style.display = 'none';
                terminalElement.innerHTML = '';
            }
        }
    };
}


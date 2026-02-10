// Authentication utilities for OIDC

import { apiUrl } from './utils/apiUtils.js';

/**
 * Authentication manager for handling OIDC authentication
 */
export const authManager = {
    user: null,
    initialized: false,
    refreshInterval: null,
    isRefreshing: false,

    /**
     * Initialize authentication and fetch user info if authenticated
     */
    async initialize() {
        if (this.initialized) {
            return;
        }

        try {
            // Check if we're in server mode with OIDC enabled
            const serverModeResponse = await fetch(apiUrl('/api/server-mode'));
            if (!serverModeResponse.ok) {
                this.initialized = true;
                return;
            }

            const serverModeData = await serverModeResponse.json();
            if (!serverModeData.enabled || !serverModeData.oidc_enabled) {
                // OIDC not enabled, skip authentication
                this.initialized = true;
                return;
            }

            // Try to get current user info
            const authStatusResponse = await fetch(apiUrl('/auth/status'), {
                credentials: 'same-origin',
            });

            if (authStatusResponse.ok) {
                const authStatus = await authStatusResponse.json();
                if (authStatus.authenticated) {
                    this.user = authStatus.user;
                    // Start proactive token refresh
                    this.startTokenRefresh();
                }
            }
        } catch (error) {
            console.error('Failed to initialize authentication:', error);
        }

        this.initialized = true;
    },

    /**
     * Start proactive token refresh timer
     * Refreshes token every 4 minutes (before typical 5-minute expiration)
     */
    startTokenRefresh() {
        // Clear any existing interval
        if (this.refreshInterval) {
            clearInterval(this.refreshInterval);
        }

        // Refresh token every 4 minutes (240 seconds)
        // This is before the typical 5-minute (300s) token expiration
        this.refreshInterval = setInterval(async () => {
            if (this.isAuthenticated() && !this.isRefreshing) {
                await this.refreshToken();
            }
        }, 4 * 60 * 1000); // 4 minutes
    },

    /**
     * Refresh the authentication token
     * @returns {Promise<boolean>} True if refresh succeeded
     */
    async refreshToken() {
        if (this.isRefreshing) {
            return false;
        }

        this.isRefreshing = true;
        
        try {
            const response = await fetch(apiUrl('/auth/refresh'), {
                method: 'POST',
                credentials: 'same-origin',
            });

            if (response.ok) {
                const data = await response.json();
                if (data.user) {
                    this.user = data.user;
                }
                return true;
            } else {
                console.warn('Token refresh failed, redirecting to login');
                // Clear interval before redirect
                if (this.refreshInterval) {
                    clearInterval(this.refreshInterval);
                    this.refreshInterval = null;
                }
                // Redirect to login
                window.location.href = apiUrl('/auth/login?return_to=' + encodeURIComponent(window.location.href));
                return false;
            }
        } catch (error) {
            console.error('Token refresh error:', error);
            return false;
        } finally {
            this.isRefreshing = false;
        }
    },

    /**
     * Check if user is authenticated
     * @returns {boolean} True if user is authenticated
     */
    isAuthenticated() {
        return this.user !== null;
    },

    /**
     * Get current user information
     * @returns {object|null} User information or null if not authenticated
     */
    getUser() {
        return this.user;
    },

    /**
     * Logout the current user
     * Redirects to the logout endpoint which handles OIDC logout
     */
    logout() {
        // Clear refresh interval
        if (this.refreshInterval) {
            clearInterval(this.refreshInterval);
            this.refreshInterval = null;
        }
        window.location.href = apiUrl('/auth/logout');
    },
};

// Initialize authentication on page load
document.addEventListener('DOMContentLoaded', async () => {
    await authManager.initialize();
});

// Global fetch interceptor for automatic token refresh on 401 errors
(function() {
    const originalFetch = window.fetch;
    const requestsInRetry = new WeakSet();

    window.fetch = async function(...args) {
        let response = await originalFetch(...args);
        
        // If we get a 401 and user is authenticated, try to refresh and retry
        if (response.status === 401 && authManager.isAuthenticated() && !requestsInRetry.has(args[0])) {
            // Don't intercept auth endpoints - they handle their own flow
            const url = typeof args[0] === 'string' ? args[0] : args[0].url;
            if (url && url.includes('/auth/')) {
                return response;
            }

            
            // Mark this request to prevent infinite retry loops
            requestsInRetry.add(args[0]);
            
            try {
                // Attempt to refresh the token
                const refreshed = await authManager.refreshToken();
                
                if (refreshed) {
                    // Retry the original request with the new token
                    response = await originalFetch(...args);
                }
            } catch (error) {
                console.error('Error during token refresh retry:', error);
            } finally {
                // Clean up the retry marker after a delay
                setTimeout(() => requestsInRetry.delete(args[0]), 5000);
            }
        }
        
        return response;
    };
})();


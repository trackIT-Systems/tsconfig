// Authentication utilities for OIDC

import { apiUrl } from './utils/apiUtils.js';

/**
 * Authentication manager for handling OIDC authentication
 */
export const authManager = {
    user: null,
    initialized: false,

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
                }
            }
        } catch (error) {
            console.error('Failed to initialize authentication:', error);
        }

        this.initialized = true;
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
        window.location.href = apiUrl('/auth/logout');
    },
};

// Initialize authentication on page load
document.addEventListener('DOMContentLoaded', async () => {
    await authManager.initialize();
    authManager.displayUserInfo();
});


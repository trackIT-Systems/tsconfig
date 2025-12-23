// API URL utilities for handling base URL prefix

/**
 * Get the configured base URL from window.BASE_URL
 * @returns {string} The base URL (empty string if not configured)
 */
export function getBaseUrl() {
    return window.BASE_URL || '';
}

/**
 * Construct a full API URL with the base URL prefix
 * @param {string} path - The API path (should start with /)
 * @returns {string} The full URL with base URL prefix
 */
export function apiUrl(path) {
    const baseUrl = getBaseUrl();
    return baseUrl + path;
}

/**
 * Fetch with authentication support.
 * Includes credentials (cookies) and handles 401 responses by redirecting to login.
 * 
 * @param {string} url - The URL to fetch
 * @param {object} options - Fetch options
 * @returns {Promise<Response>} The fetch response
 */
export async function fetchWithAuth(url, options = {}) {
    // Include credentials (cookies) in the request
    const fetchOptions = {
        ...options,
        credentials: 'same-origin',
    };

    try {
        const response = await fetch(url, fetchOptions);

        // Handle 401 Unauthorized
        if (response.status === 401) {
            // Check if we're in server mode and OIDC is enabled
            const serverModeResponse = await fetch(apiUrl('/api/server-mode'));
            if (serverModeResponse.ok) {
                const serverModeData = await serverModeResponse.json();
                if (serverModeData.enabled && serverModeData.oidc_enabled) {
                    // Redirect to login page with return URL
                    const returnUrl = encodeURIComponent(window.location.href);
                    window.location.href = apiUrl(`/auth/login?return_to=${returnUrl}`);
                    // Throw error to prevent further processing
                    throw new Error('Authentication required - redirecting to login');
                }
            }
            // If not in OIDC mode, just throw the error
            throw new Error('Unauthorized');
        }

        return response;
    } catch (error) {
        // Re-throw the error for caller to handle
        throw error;
    }
}

/**
 * Check if OIDC authentication is enabled
 * @returns {Promise<boolean>} True if OIDC is enabled
 */
export async function isOidcEnabled() {
    try {
        const response = await fetch(apiUrl('/api/server-mode'));
        if (response.ok) {
            const data = await response.json();
            return data.enabled && data.oidc_enabled;
        }
    } catch (error) {
        console.error('Failed to check OIDC status:', error);
    }
    return false;
}


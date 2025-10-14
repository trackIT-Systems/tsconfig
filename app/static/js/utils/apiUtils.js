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


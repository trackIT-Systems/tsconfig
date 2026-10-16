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

const sharedRequests = new Map();

/**
 * GET `url` and parse its JSON, sharing the request with other callers of the
 * same URL within `maxAgeMs`. Components that load together (the navbar and the
 * status page) then make one request instead of one each.
 * @param {string} url - The full URL (use apiUrl())
 * @param {number} maxAgeMs - How long a started request is reused
 * @returns {Promise<{ok: boolean, status: number, statusText: string, data: any}>}
 *     `data` is the parsed JSON body, or null if the response was not ok
 */
export function getJsonShared(url, maxAgeMs = 3000) {
    const now = Date.now();
    const cached = sharedRequests.get(url);
    if (cached && now - cached.time < maxAgeMs) {
        return cached.promise;
    }
    const promise = fetch(url).then(async response => ({
        ok: response.ok,
        status: response.status,
        statusText: response.statusText,
        data: response.ok ? await response.json() : null,
    }));
    sharedRequests.set(url, { time: now, promise });
    promise.catch(() => sharedRequests.delete(url));
    return promise;
}

let serverModeInfo = null;

/**
 * The /api/server-mode answer, fetched once per page load and shared by all
 * callers (auth, server mode manager, ...), which used to fetch it each.
 * @returns {Promise<object|null>} The parsed answer, or null if it could not be fetched
 */
export function getServerModeInfo() {
    if (!serverModeInfo) {
        serverModeInfo = fetch(apiUrl('/api/server-mode'))
            .then(response => (response.ok ? response.json() : null))
            .catch(error => {
                console.error('Failed to fetch server mode configuration:', error);
                return null;
            });
    }
    return serverModeInfo;
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
            const serverModeData = await getServerModeInfo();
            if (serverModeData) {
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
    const data = await getServerModeInfo();
    return Boolean(data && data.enabled && data.oidc_enabled);
}

/**
 * The text of a failed save, from the `detail` of the API's error answer: its
 * message, followed by the field errors it lists (`errors`, or pydantic's
 * `validation_errors` with their location).
 * @param {object|string|undefined} detail - The `detail` of the error answer
 * @param {string} fallback - Used if the answer has no message
 * @returns {string}
 */
export function formatApiError(detail, fallback) {
    let message = detail?.message || fallback;
    if (detail?.errors) {
        message += '\nValidation errors: ' + detail.errors.join(', ');
    }
    if (detail?.validation_errors) {
        const validationErrors = detail.validation_errors.map(err => `${err.loc.join('.')}: ${err.msg}`).join(', ');
        message += '\nValidation errors: ' + validationErrors;
    }
    return message;
}

/**
 * Send `body` as JSON and return the parsed answer.
 * @param {string} url - The full URL
 * @param {object} body - Sent as the JSON body
 * @param {string} failureText - Error message if the answer has none
 * @param {string} method - HTTP method (default PUT)
 * @returns {Promise<object>} The parsed JSON answer
 * @throws {Error} With formatApiError()'s text if the response is not ok
 */
export async function sendJson(url, body, failureText, method = 'PUT') {
    const response = await fetch(url, {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    });
    if (!response.ok) {
        const error = await response.json();
        throw new Error(formatApiError(error.detail, failureText));
    }
    return response.json();
}

/**
 * Perform a systemctl action (start, stop, restart) on a service.
 * @param {string} service - The systemd unit
 * @param {string} action - 'start' | 'stop' | 'restart'
 * @returns {Promise<{message: string}>} The parsed answer
 * @throws {Error} With the API's `detail` if the response is not ok
 */
export async function serviceAction(service, action) {
    const response = await fetch(apiUrl('/api/systemd/action'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ service, action }),
    });
    const data = await response.json();
    if (!response.ok) {
        throw new Error(data.detail || `Failed to ${action} ${service} service`);
    }
    return data;
}

/**
 * `showToast(message, type, options)` for components that choose the toast
 * type themselves (info, success, warning, error). The title defaults to the
 * type's name, `System Status` for info; `options.title` overrides it.
 * (toastMixin is for pages whose every message has the page's title.)
 */
export function showToastMixin() {
    return {
        showToast(message, type = 'info', options = {}) {
            if (window.toastManager) {
                const defaultTitle =
                    type === 'success' ? 'Success' :
                    type === 'error' ? 'Error' :
                    type === 'warning' ? 'Warning' : 'System Status';
                window.toastManager.show(message, type, { title: defaultTitle, ...options });
            } else {
                // Fallback to console if toast manager not available
                console.log(`[STATUS ${type.toUpperCase()}] ${message}`);
            }
        },
    };
}

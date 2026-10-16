/**
 * Toasts for a component's messages, titled with the component's name.
 *
 * showMessage(message, isError) takes the error flag as a boolean; some
 * callers pass the toast type ('error' / 'success') instead.
 *
 * @param {string} title - Title of success toasts
 * @param {string} [errorTitle] - Title of error toasts, default `${title} Error`
 */
export function toastMixin(title, errorTitle = `${title} Error`) {
    return {
        showMessage(message, isError = false) {
            const failed = isError === true || isError === 'error';
            if (window.toastManager) {
                window.toastManager.show(message, failed ? 'error' : 'success', {
                    title: failed ? errorTitle : title,
                });
            } else {
                // No toasts: the page's message handler shows it
                window.dispatchEvent(new CustomEvent('show-message', { detail: { message, isError: failed } }));
            }
        },

        dispatchMessage(message, isError) {
            this.showMessage(message, isError);
        },
    };
}

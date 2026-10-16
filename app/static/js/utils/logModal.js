/**
 * Open the log modal and stream a service's journal into it.
 * @param {string} serviceName - systemd unit, or 'all' / 'kernel'
 */
export function showServiceLogs(serviceName) {
    const logViewerEl = document.getElementById('logModal');
    const modal = new bootstrap.Modal(logViewerEl);
    // The log viewer is an Alpine component on the modal element
    if (logViewerEl && logViewerEl._x_dataStack) {
        logViewerEl._x_dataStack[0].startStreaming(serviceName);
    }
    modal.show();

    // Scroll to bottom after modal is shown and content is rendered
    setTimeout(() => {
        const container = document.getElementById('logContainer');
        if (container) {
            container.scrollTop = container.scrollHeight;
        }
    }, 250);
}

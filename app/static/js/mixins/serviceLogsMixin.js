import { showServiceLogs } from '../utils/logModal.js';

/** `streamLogs(service)` for the "Logs" buttons: opens the log modal on that service's journal. */
export function serviceLogsMixin() {
    return {
        streamLogs(serviceName) {
            showServiceLogs(serviceName);
        },
    };
}

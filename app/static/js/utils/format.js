/** Formatting for the status page. */

/** 90061 -> "1d 1h 1m" */
export function formatUptime(seconds) {
    if (!seconds) return 'N/A';

    const days = Math.floor(seconds / (24 * 3600));
    const hours = Math.floor((seconds % (24 * 3600)) / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);

    if (days > 0) {
        return `${days}d ${hours}h ${minutes}m`;
    } else if (hours > 0) {
        return `${hours}h ${minutes}m`;
    } else {
        return `${minutes}m`;
    }
}

/** An ISO timestamp in the browser's locale. */
export function formatDateTime(isoString) {
    if (!isoString) return 'N/A';

    try {
        const date = new Date(isoString);
        return date.toLocaleString();
    } catch (err) {
        return isoString;
    }
}

/** 1536 -> "1.5 KB" */
export function formatBytes(bytes) {
    if (!bytes || bytes === 0 || isNaN(bytes) || !isFinite(bytes)) return '0 B';

    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));

    const formattedValue = parseFloat((bytes / Math.pow(k, i)).toFixed(2));

    // Additional safety check for the final result
    if (isNaN(formattedValue) || !isFinite(formattedValue)) return '0 B';

    return formattedValue + ' ' + sizes[i];
}

/**
 * Alpine calls destroy() when a component's element is removed (tab left).
 * Timers and window listeners started in init() must stop then, or every
 * visit to a tab would add another set that keeps running (and requesting)
 * for a component nobody sees. Register them through listen() and every()
 * and destroy() takes care of it (onDestroy() for anything else that must be
 * let go); `destroyed` tells an init() that was still loading when the tab
 * was left to stop.
 */
export function lifecycleMixin() {
    return {
        destroyed: false,
        _cleanups: [],

        /** Add a listener to `target` (window, an element) until the component is destroyed. */
        listen(target, event, handler) {
            if (this.destroyed) return;
            target.addEventListener(event, handler);
            this._cleanups.push(() => target.removeEventListener(event, handler));
        },

        /** setInterval until the component is destroyed. */
        every(milliseconds, handler) {
            if (this.destroyed) return;
            const id = setInterval(handler, milliseconds);
            this._cleanups.push(() => clearInterval(id));
        },

        /** Run `cleanup` when the component is destroyed (unsubscribe, remove a map, ...). */
        onDestroy(cleanup) {
            this._cleanups.push(cleanup);
        },

        destroy() {
            this.destroyed = true;
            for (const cleanup of this._cleanups) {
                cleanup();
            }
            this._cleanups = [];
        },
    };
}

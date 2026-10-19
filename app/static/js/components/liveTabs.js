import { liveManager } from '../managers/liveManager.js';
import { compose } from '../mixins/compose.js';
import { lifecycleMixin } from '../mixins/lifecycleMixin.js';
import { LIVE_TABS, VIEW_OPTIONS, formatWindow } from '../utils/liveSignals.js';

/**
 * The Live page: its tabs (`LIVE_TABS`), each offered when its service is available, and
 * the time window all of its charts show. Like the settings page, the tab is part of the
 * URL (`#live/radiotracking`), so a link or a reload comes back to it.
 *
 * `isServiceAvailable` is the page's own, passed in from the template:
 * `x-data="liveTabs((service) => isServiceAvailable(service))"`.
 */
export function liveTabs(isServiceAvailable) {
    return compose(lifecycleMixin(), {
        tab: null, // the id of the tab shown
        viewOptions: VIEW_OPTIONS.map(ms => ({ ms, label: formatWindow(ms) })),
        viewMs: liveManager.prefs.viewMs, // kept while the page is open

        /** The tabs to offer: those whose service is available. */
        get tabs() {
            return LIVE_TABS.filter(tab => isServiceAvailable(tab.service));
        },

        init() {
            this.tab = this.tabFromHash();
            this.listen(window, 'hashchange', () => {
                const tab = this.tabFromHash();
                if (tab !== this.tab) this.tab = tab;
            });
            // The URL names the tab it shows: #live becomes #live/radiotracking
            if (this.tab && window.location.hash.slice(1).split('/')[1] !== this.tab) {
                window.history?.replaceState(null, '', `#live/${this.tab}`);
            }
        },

        /** The tab in `#live/<tab>`, or the first one that is available. */
        tabFromHash() {
            const [page, tab] = window.location.hash.slice(1).split('/');
            const available = this.tabs;
            return (page === 'live' && available.find(item => item.id === tab)?.id) || available[0]?.id || null;
        },

        select(id) {
            if (!this.tabs.some(tab => tab.id === id)) return;
            this.tab = id;
            window.location.hash = `live/${id}`;
        },

        /** Show the last `ms` of what is held, on every chart. */
        setView(ms) {
            liveManager.setView(Number(ms));
            this.viewMs = liveManager.prefs.viewMs;
        },
    });
}

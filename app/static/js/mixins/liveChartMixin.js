import { liveManager } from '../managers/liveManager.js';
import {
    CLOCK_TOLERANCE_MS,
    WINDOW_MS,
    colorFor,
    compareKeys,
    dotSize,
    firstAtOrAfter,
    formatClock,
    formatClockMs,
    formatOffset,
    formatWindow,
} from '../utils/liveSignals.js';

const HEIGHT = 320;

// With more dots than this one path is filled per series, not one dot at a time
const BATCH_ABOVE = 1500;

// How close (px) the cursor must be to a dot, beyond its radius, to show its tooltip
const HIT_SLACK_PX = 6;

/**
 * What the charts of the live dashboard share: dots over a 24-hour time axis,
 * one colour per series, the diameter showing the duration, and the data of
 * `liveManager` (held there, not here: the panels of a tab share it, and it survives a
 * switch of the source or a Stop; but a panel that goes away discards what it showed).
 *
 * A panel (a component built with `compose`) adds which signals it shows and how:
 *
 * - `liveSources()`: what to read from the broker while the panel is open (`matched`, ...)
 * - `liveWindows()`: which of the manager's windows it draws, to discard when it goes away
 * - `eventWindow()`: the `EventWindow` it draws
 * - `forEachDot(event, add)`: `add(key, y)` for every dot of a signal; the key picks the colour
 * - `yLabel`, `yRange(min, max)`, `formatY(value)`: the vertical axis
 * - `legendLabel(key)`: the legend's text for a series, null for none
 * - `tipRows(event, hit)`: the rows of the tooltip, `{text, color?}`
 *
 * The broker connection, chart and signals are kept out of the component's
 * data: Alpine would wrap them in proxies, and the arrays grow.
 */
export function liveChartMixin() {
    let chart = null;
    let release = null; // gives back what liveSources() acquired
    let yBounds = null;
    let gliding = false;

    return {
        status: 'idle', // connecting | connected | offline | failed, or idle: reading nothing
        error: '',
        now: Date.now(),
        count: 0, // signals in the window
        offsetMs: null, // the last signal's arrival minus its Time
        skipped: 0, // signals whose Time is too far from this browser's clock to be drawn
        tip: null, // {left, top, flip, title, rows[]}
        legend: [], // [{key, label, color}]
        ready: false, // the libraries are loaded and the chart is built
        viewMs: liveManager.prefs.viewMs, // the time window shown (the manager's, copied at each tick for Alpine to see it)

        /** How long the signals are kept, in minutes. */
        get windowMinutes() {
            return WINDOW_MS / 60000;
        },

        /** The time window that is shown ("5 minutes"), chosen on the page: all of what is kept, or less. */
        get viewText() {
            return formatWindow(this.viewMs);
        },

        /** The last signal arrived this long after its Time (negative: its Time is ahead of this browser's clock). */
        get delayText() {
            if (this.offsetMs === null) return '';
            const text = formatOffset(this.offsetMs);
            return this.offsetMs < 0 ? `${text} ahead of this browser's clock` : `${text} after its time`;
        },

        /** The station's clock and this browser's clock are not the same, or detections come in late. */
        get clockOff() {
            return this.offsetMs !== null && Math.abs(this.offsetMs) > CLOCK_TOLERANCE_MS;
        },

        // --- what a panel overrides -------------------------------------------------

        liveSources() {
            return [];
        },
        liveWindows() {
            return this.liveSources();
        },
        formatY(value) {
            return String(value);
        },
        legendLabel(key) {
            return String(key);
        },
        tipRows() {
            return [];
        },
        /** Once a second, with the signals of the window, for a panel that has more to refresh. */
        onTick() {},

        // --- the lifecycle ----------------------------------------------------------

        async init() {
            try {
                // The limits only fall back to fitting the data, so they do not hold the page up if they fail
                await Promise.all([liveManager.loadLibraries(), liveManager.loadLimits()]);
            } catch (error) {
                this.status = 'failed';
                this.error = 'The chart libraries could not be loaded.';
                console.error(error);
                return;
            }
            if (this.destroyed) return;

            this.onDestroy(liveManager.onChange(() => {
                this.refreshStatus();
                if (this.ready) this.tick(); // a changed time window shows at once
            }));
            this.onDestroy(() => {
                release?.();
                release = null;
                // The time axis moves on while the panel is away: what was held would come back with a gap
                liveManager.discard(this.liveWindows());
                chart?.destroy();
                chart = null;
            });

            this.buildChart();
            this.useSources(this.liveSources());
            this.tick();
            this.every(1000, () => this.tick());
            this.glide();
            this.listen(window, 'resize', () => this.resizeChart());
            this.ready = true;
        },

        /** Read these sources from the broker instead of what was read before. */
        useSources(sources) {
            const next = sources.length > 0 ? liveManager.acquire(sources) : null;
            release?.(); // after acquiring, so what stays wanted stays connected
            release = next;
            this.refreshStatus();
        },

        /** The connection's status; `idle` for a panel that is not reading anything (so it has no connection to report). */
        refreshStatus() {
            const reading = release !== null;
            this.status = reading ? liveManager.status : 'idle';
            this.error = reading ? liveManager.error : '';
        },

        /**
         * Once a second: forget what is too old, and draw what arrived.
         *
         * Alpine does not call destroy() on a component inside an x-if that is inside another x-if
         * when the outer one goes (the page is left): the panel would stay subscribed, and every
         * visit would add one more. So a panel whose element has left the page destroys itself.
         */
        tick() {
            if (this.$el && !this.$el.isConnected) {
                this.destroy();
                return;
            }
            this.now = Date.now();
            this.viewMs = liveManager.prefs.viewMs;
            const signals = this.eventWindow();
            signals.trim(this.now);
            this.count = signals.length - this.firstShown(signals, this.now);
            this.offsetMs = signals.offsetMs;
            this.skipped = signals.skipped;
            this.refreshLegend(signals);
            this.fitY(signals);
            this.onTick(signals);
            if (chart && !gliding && !document.hidden) chart.redraw();
        },

        /** Where in the signals what is shown starts: the chosen time window ending at the window's edge. */
        firstShown(signals, now) {
            const edge = signals.edge(now);
            return firstAtOrAfter(signals.events, (edge - liveManager.prefs.viewMs) / 1000);
        },

        refreshLegend(signals) {
            const keys = new Set();
            const events = signals.events;
            for (let i = this.firstShown(signals, Date.now()); i < events.length; i++) {
                this.forEachDot(events[i], key => keys.add(key));
            }
            const legend = [...keys].sort(compareKeys)
                .map(key => ({ key, label: this.legendLabel(key), color: colorFor(key) }))
                .filter(item => item.label !== null);
            if (JSON.stringify(legend) !== JSON.stringify(this.legend)) this.legend = legend;
        },

        // --- the chart --------------------------------------------------------------

        /** The vertical axis that fits what is in the window. */
        computeYBounds(signals) {
            let min = null;
            let max = null;
            const events = signals.events;
            for (let i = this.firstShown(signals, Date.now()); i < events.length; i++) {
                this.forEachDot(events[i], (key, y) => {
                    if (!Number.isFinite(y)) return;
                    if (min === null || y < min) min = y;
                    if (max === null || y > max) max = y;
                });
            }
            return this.yRange(min, max);
        },

        fitY(signals) {
            const bounds = this.computeYBounds(signals);
            if (yBounds && bounds[0] === yBounds[0] && bounds[1] === yBounds[1]) return;
            yBounds = bounds;
            chart?.setScale('y', { min: bounds[0], max: bounds[1] });
        },

        buildChart() {
            const element = this.$refs.chart;
            if (!element || !window.uPlot) return;
            chart?.destroy();

            const text = getComputedStyle(element).color;
            const grid = { stroke: 'rgba(128,128,128,0.2)', width: 1 };
            yBounds = this.computeYBounds(this.eventWindow());

            chart = new window.uPlot({
                width: element.clientWidth || 300,
                height: HEIGHT,
                // Lines and dots are not snapped to whole pixels: the axis moves continuously
                pxAlign: 0,
                // Signals are events: dots, not a line between them. uPlot's own points have one
                // size per series, but the size shows the duration, so drawDots() draws them,
                // and the legend is the page's.
                legend: { show: false },
                series: [{}],
                scales: {
                    x: {
                        time: true,
                        range: () => {
                            const edge = this.eventWindow().edge(Date.now());
                            return [(edge - liveManager.prefs.viewMs) / 1000, edge / 1000];
                        },
                    },
                    y: { auto: false, range: () => yBounds },
                },
                axes: [
                    {
                        stroke: text,
                        grid,
                        ticks: grid,
                        space: 80,
                        // 24 hours and no date: this is live, and a date collided with the times
                        values: (u, splits, axis, space, increment) => splits.map(split => formatClock(split, increment < 60)),
                    },
                    {
                        stroke: text,
                        grid,
                        ticks: grid,
                        label: this.yLabel,
                        size: 64,
                        values: (u, splits) => splits.map(split => this.formatY(split)),
                    },
                ],
                cursor: { drag: { x: false, y: false }, points: { show: false } },
                hooks: { setCursor: [u => this.showTip(u)], draw: [u => this.drawDots(u)] },
            }, [[]], element);
        },

        /** Build the chart again, for a vertical axis that now means something else. */
        rebuildChart() {
            this.tip = null;
            this.buildChart();
            this.tick();
        },

        resizeChart() {
            const element = this.$refs.chart;
            if (chart && element) chart.setSize({ width: element.clientWidth || 300, height: HEIGHT });
        },

        /**
         * Slide the time axis along with the clock on every animation frame, or
         * it would move in visible steps (a pixel is about 0.4 s). That is cheap
         * for a handful of dots, and uPlot is told not to snap to whole pixels
         * (`pxAlign: 0` above), so the movement is smooth, not 1 px at a time.
         * Nothing is drawn while the page is hidden.
         */
        glide() {
            if (!window.requestAnimationFrame) return;
            gliding = true;
            let frame = window.requestAnimationFrame(() => step());
            const step = () => {
                frame = window.requestAnimationFrame(step);
                if (!chart || document.hidden) return;
                const edge = this.eventWindow().edge(Date.now()) / 1000;
                chart.setScale('x', { min: edge - liveManager.prefs.viewMs / 1000, max: edge });
            };
            this.onDestroy(() => {
                gliding = false;
                window.cancelAnimationFrame?.(frame);
            });
        },

        /** The signals as dots: the series' colour, the diameter showing the duration (`dotSize`). */
        drawDots(u) {
            const { ctx, bbox } = u;
            const ratio = window.uPlot?.pxRatio || globalThis.devicePixelRatio || 1;
            const { min, max } = u.scales.x;
            const events = this.eventWindow().events;
            const batch = events.length - firstAtOrAfter(events, min) > BATCH_ABOVE;

            const perKey = new Map(); // key -> [x, y, radius, ...] while batching
            let event = null;
            const add = (key, y) => {
                if (!Number.isFinite(y)) return;
                const x = u.valToPos(event.time, 'x', true);
                const py = u.valToPos(y, 'y', true);
                const radius = dotSize(event.duration) / 2 * ratio;
                if (batch) {
                    let list = perKey.get(key);
                    if (!list) perKey.set(key, list = []);
                    list.push(x, py, radius);
                } else {
                    ctx.fillStyle = colorFor(key);
                    ctx.beginPath();
                    ctx.arc(x, py, radius, 0, 2 * Math.PI);
                    ctx.fill();
                }
            };

            ctx.save();
            ctx.beginPath();
            ctx.rect(bbox.left, bbox.top, bbox.width, bbox.height);
            ctx.clip();
            ctx.globalAlpha = 0.8; // dots on top of each other stay countable
            for (let i = firstAtOrAfter(events, min); i < events.length; i++) {
                event = events[i];
                if (event.time > max) break;
                this.forEachDot(event, add);
            }
            for (const [key, list] of perKey) {
                ctx.fillStyle = colorFor(key);
                ctx.beginPath();
                for (let i = 0; i < list.length; i += 3) {
                    ctx.moveTo(list[i] + list[i + 2], list[i + 1]);
                    ctx.arc(list[i], list[i + 1], list[i + 2], 0, 2 * Math.PI);
                }
                ctx.fill();
            }
            ctx.restore();
        },

        /** The tooltip of the dot under the cursor. */
        showTip(u) {
            const { left, top } = u.cursor;
            if (left === null || left === undefined || left < 0) {
                this.tip = null;
                return;
            }
            let best = null;
            let bestDistance = Infinity;
            let event = null;
            const consider = (key, y) => {
                if (!Number.isFinite(y)) return;
                const distance = Math.hypot(u.valToPos(event.time, 'x') - left, u.valToPos(y, 'y') - top);
                if (distance <= dotSize(event.duration) / 2 + HIT_SLACK_PX && distance < bestDistance) {
                    bestDistance = distance;
                    best = { event, key, y };
                }
            };
            const events = this.eventWindow().events;
            for (let i = firstAtOrAfter(events, u.scales.x.min); i < events.length; i++) {
                event = events[i];
                if (Math.abs(u.valToPos(event.time, 'x') - left) > 40) continue; // the dots can't be this wide
                this.forEachDot(event, consider);
            }
            if (!best) {
                this.tip = null;
                return;
            }
            this.tip = {
                // The cursor is measured from the plot area; the tooltip sits in the chart's wrapper
                left: left + (u.over?.offsetLeft ?? 0),
                top: top + (u.over?.offsetTop ?? 0),
                // right of the point, or left of it where there is no room
                flip: left > (u.over?.clientWidth ?? Infinity) / 2,
                title: formatClockMs(best.event.time * 1000),
                rows: this.tipRows(best.event, best),
            };
        },
    };
}

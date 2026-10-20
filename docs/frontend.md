# Frontend structure

The web UI is Alpine.js components (`app/static/js/components/`) on server-rendered Jinja
pages (`app/templates/`). `app.js` imports the components and exposes them on `window` for the
templates' `x-data="scheduleConfig()"`; it also exposes `serverModeManager` (config group links in
the navbar) and the time helpers. Components import everything else.

## Layers

| | |
|---|---|
| `components/` | One Alpine component per page or card. What differs between pages lives here: the config's shape, its form logic, `buildPayload()`. |
| `mixins/` | Behaviour components share (below). |
| `managers/` | Singletons that dedupe requests: `serviceManager` (systemd units), `systemConfigManager` (refresh interval), `serverModeManager` (server mode and the chosen config group). |
| `utils/` | Plain functions: `apiUtils.js` (URLs, shared GETs, `sendJson`, `serviceAction`, `formatApiError`), `deploymentUtils.js`, `logModal.js`, `timeUtils.js`. |

## Mixins

Components are built with `compose(...)` (`mixins/compose.js`), not object spread: spread evaluates
getters once, `compose` keeps `get serverMode()` live. Later parts win, so a component overrides a
mixin's method by defining its own.

- `lifecycleMixin`: `listen(target, event, fn)`, `every(ms, fn)` and `onDestroy(fn)`. Alpine
  calls `destroy()` when a tab is left; everything registered through these stops then. Never call
  `setInterval` or `addEventListener` on `window` directly in a component: every visit to a tab
  would add another timer that keeps requesting for a component nobody sees. `destroyed` is true after
  that, for an `init()` that was still loading.
- `serverModeMixin`: `serverMode`, for `x-show="!serverMode"`.
- `toastMixin(title, errorTitle)`: `showMessage(message, isError)`.
- `serviceLogsMixin`: `streamLogs(unit)` for the Logs buttons.
- `saveStateMixin`, `serviceActionMixin`: the button states of Save, Save & Restart, Deploy, and
  Start / Stop / Restart of a unit.
- `configPageMixin(options)`: all of the above for the settings page of a service, plus the status
  box of its systemd unit (refreshed while the tab is shown), `saveConfig()`,
  `saveAndRestartService()`, `deployConfig()` and the default `init()`.

## A settings page

```js
export function scheduleConfig() {
    return compose(configPageMixin({
        service: 'tsschedule',              // unit of the status box, restarted by Save & Restart
        apiPath: '/api/schedule',
        title: 'Schedule Configuration',    // toast title
        failureText: 'Failed to save schedule configuration',
        refreshOn: ['settings'],            // refresh the status while this hash path is shown
    }), {
        config: { /* ... */ },
        async loadConfig() { /* GET serverModeManager.buildApiUrl('/api/schedule') into this.config */ },
        buildPayload() { return this.config; },   // what Save sends; Save & Restart sends the same
        onSaved() { /* after a save */ },
    });
}
```

`init()` pauses briefly, calls `loadData()` (default `loadConfig()`), starts the status refresh and
reloads when another config group is chosen (server mode). Override `init()`/`loadData()` where the
start-up differs, and register extra listeners in `subscribe()`.

Save and Save & Restart must send the same data, so there is one `buildPayload()`. Before the mixin
each had a copy, and the copies had drifted apart (tsupdate's Save did not convert two of its
HH:MM fields).

## The status page

`statusPage` only does what is the page's own: the system's state (uptime, load, memory, disks, time,
network, modem), the refresh that keeps it current and the reboot. The cards that load and show
their own data are components inside its template, each with its own `x-data`:

| Card | Component | Follows the refresh through |
|---|---|---|
| System Services | `servicesList` | `serviceManager` (it subscribes; the refresh fetches the units) |
| Radiotracking | `radiotrackingSummary` | the `status-refreshed` event |
| Soundscapepipe | `soundscapepipeSummary` | the `status-refreshed` event and `serviceManager` |
| Location | `geolocationMap` | loads once |
| the live audio player (status page and soundscapepipe settings) | `audioStreamPlayer` | its own 5-second check |

After every refresh `statusPage` dispatches `status-refreshed` on `window`, with the available
services as its detail; a card reloads its config if its service is among them. A new card needs a
component and that line in the template, nothing in `statusPage`.

Components inside `x-data` can read the outer data in their *templates* (`expertMode`, `loading`), but
their *methods* cannot: a component defined in a module has no access to the page's scope. What a
method needs from outside is passed to the factory as a closure, which the template creates in its
own scope: `x-data="geolocationMap(() => systemInfo?.os_release?.NAME)"`.

A `<template x-if>` renders only its first element. Two buttons in one template show one button: the
status page's Restart button was missing for that reason.

## The live dashboard

The **Live** tab (`live.html`, tracker mode, if radiotracking or soundscapepipe is available: `isLiveAvailable()`)
reads MQTT in the browser. There is no server-side part: tsconfig subscribes to nothing and stores
nothing. The page connects to the local broker over WebSockets at `/mqtt` on its own host (`ws:` or
`wss:` like the page). The broker only listens on loopback; tsOS-base's Caddy serves the path behind
the login (see tsOS-base docs/security.md, "MQTT over WebSocket"), so the page needs no credentials
of its own. A socket that is open stays connected after logout.

The page is `liveTabs` (`components/liveTabs.js`): a tab for each entry of `LIVE_TABS`
(`utils/liveSignals.js`: id, label, icon and the service whose availability enables it), in the URL
as `#live/<tab>` like the settings page (`#live` becomes `#live/<first tab>`). Only the tab that is
shown has its panels, so only it reads from the broker. **A new tab** is an entry in `LIVE_TABS`, a
`partials/live_<id>.html` with its panels, and a line in `live.html` (`<template x-if="tab === '<id>'">`).
The navbar's Live item and `isLiveAvailable()` follow `LIVE_TABS`: shown if any tab is available.
The page's **Time window** selector (30 s, 1, 2 or 5 minutes, `VIEW_OPTIONS`) sets what every chart shows
of what is held, at most all of it (`liveManager.prefs.viewMs`); it only changes what is shown, not
what is kept, so a longer window brings the signals back. Panels copy it into their own `viewMs` at
each tick, because Alpine cannot see the manager's.

| Panel | Component | Reads | Plots |
|---|---|---|---|
| Signal Power | `liveSignalPower` | `matched` and `calibrated` | the power of each SDR (dBW or dBm, chosen) |
| Frequency Usage | `liveFrequencyUsage` | `matched` | the frequency of the signal, one dot per signal |
| Signal Detail | `liveSignalDetail` | `raw`, after Start | a value of the raw signal, chosen, one series per SDR |
| Detections | `liveDetections` | `detections` | the confidence of each detection, one series per species; detectors shown or hidden |
| Input Levels | `liveInputLevels` | `levels` | peak and RMS (dBFS) of every input channel, one dot per report |

The first three are the Radiotracking tab (`partials/live_radiotracking.html`), the last two the
Soundscapepipe tab (`partials/live_soundscapepipe.html`). soundscapepipe publishes each detection and,
every `status_interval` (60 s by default), the input levels as JSON on
`<station>/soundscapepipe/{detection,state}/json` (`utils/liveSoundscape.js` reads them). A detection
is drawn at the start of what was analysed, so birdedge's arrive about 8 s late (a 5 s window plus
the analysis): Detections only warns about the clock beyond 30 s (`clockToleranceMs`). Instead of
radiotracking's limits, its `loadLimits()` reads the configured detectors (`GET /api/soundscapepipe`,
`configuredDetectors()`: those under `detectors`, and `lure` if it has tasks) and the species names
(`GET /api/soundscapepipe/species`, `speciesNames()`: yolobat's codes such as `Nnoc` become scientific
names); without them the panel still works, with the names as detections give them. A button per
detector shows or hides its detections, a detector that is not configured gets one when it sends;
hiding only filters what is drawn and counted (`forEachDot()`, `onTick()`), so what is held comes back
at once, and `liveManager.prefs.hiddenDetectors` keeps the choice while the page is open. The species
(with its English and German name), detector, duration, channel and, for bats, the frequencies are in the tooltip; the levels' tooltip adds the SNR
and the capture device's ALSA state.

Each panel is a thin component (`partials/live_signal_power.html`, ...) over three shared parts:

- **`liveManager`** (`managers/liveManager.js`) owns the one MQTT connection and, for each kind of
  message (`matched`, `calibrated`, `raw`, `detections`, `levels`), an `EventWindow` with the signals of
  the last five minutes.
  Panels `acquire(sources)` what they need and get a function to give it back; the connection opens
  with the first and closes with the last, and a topic wanted by two panels is subscribed once. The
  windows are module state, so what was read **survives a switch between matched and calibrated and
  Stop**, and the window forgets what is older than five minutes. It does **not survive a panel going
  away** (leaving the page or the tab): the time axis keeps moving, so signals held across a visit
  would come back with a hole in them. A panel `discard`s the windows it drew when it is destroyed
  (`liveWindows()`), unless another panel still reads them. Nothing is read in the background: with no
  panel open there is no connection. Signal Power reads both `matched` and `calibrated` while it is shown, so switching
  between them has no gap; the raw signals are many more, so Signal Detail only reads them after Start
  (`liveManager.prefs.collectRaw` remembers that while the page is open). mqtt.js's `resubscribe` is
  off: the manager subscribes to what is wanted on every `connect`, so after a reconnect it is what
  is wanted by then.
- **`EventWindow`** (`utils/liveSignals.js`) holds the signals in order of `Time` (messages can
  arrive out of order, and uPlot needs ascending x). Signals are drawn at the message's own `Time`,
  while the axis follows the browser's clock: the point is to see how far the detections are from the
  wall clock. It keeps the last signal's delay (arrival minus `Time`, shown on the card, with a warning
  beyond the panel's `clockToleranceMs`, 5 s by default) and counts signals not drawn because their `Time` is more than the window behind or 10
  minutes ahead of the browser (so a clock set years ahead cannot push the window away).
- **`liveChartMixin`** (`mixins/liveChartMixin.js`) is what the charts share: the connection to the
  manager, the once-a-second tick (trim, legend, vertical axis), the chart, the glide, the dots and the
  tooltip. A panel only says what is its own: `liveSources()`, `eventWindow()`,
  `forEachDot(event, add)` (`add(key, y)` per dot; the key picks the colour), `yLabel`, `yRange()`,
  `formatY()`, `legendLabel()` and `tipRows()`. It may override `colorOf(key)` (default `colorFor()`),
  `loadLimits()` (default radiotracking's; the Soundscapepipe panels have fixed axes, confidence 0 to
  1 and dBFS from at least -60 to just above 0, and Detections reads soundscapepipe's instead) and `clockToleranceMs`.

How the charts are drawn:

- **Dots, drawn by us.** uPlot's own points have one size per series, so `drawDots()` draws them in a
  `draw` hook (clipped to the plot area and to the time axis; one fill per series when there are many).
  The diameter shows the duration (`dotSize()`): 6 px at 20 ms, proportional to it (40 ms is twice as
  wide), limited to 3-24 px; a signal without a duration (the Soundscapepipe panels') has 6 px. uPlot gets no series (`legend` is the page's, in `live_chart.html`) and no
  data; the vertical axis is fitted to the signals by the mixin (`fitY()`), and the tooltip finds the dot
  under the cursor itself (`showTip()`).
- **The vertical axes come from the radiotracking configuration**, which tsconfig already parses:
  `liveManager.loadLimits()` reads `/api/radiotracking` when a panel opens (`radiotrackingLimits()`).
  Matched power runs from `signal_threshold_dbw` to a static -30 dBW (`powerLimits()`); calibrated
  power (dBm at the antenna port) is not bounded by the configuration and is fitted to the data.
  Frequency runs from the centre frequency minus half the sample rate to plus half
  (`frequencyLimits()`). In Signal Detail max and avg have the power axis, frequency the frequency
  axis and duration `signal_min_duration_ms` to `signal_max_duration_ms` (`durationLimits()`); the
  other dB values are fitted to the data and never span less than 10 dB (`spreadRange()`), and any
  other value the station adds is fitted too. The limits are widened by 3% (`padRange()`) so a dot on
  a limit is not cut in half; the dot size does not depend on the configuration (`dotSize()` is
  static). Without the configuration every axis is fitted to the data (`powerRange()`,
  `frequencyRange()`, `valueRange()`). Signals outside a fixed axis are clipped.
- **A 24-hour time axis without a date.** This is live, and a date collided with the times.
- **Narrow screens.** The vertical axis is only as wide as its tick texts (`yAxisSize()`), and its
  title (`yLabel`) is above the chart, not turned along the axis. On a plot narrower than 500 px the
  times are at least 40 px apart instead of 80 (`timeTickSpace()`): uPlot steps from one minute to
  five, so a phone would show a single time. The legend is a wrapping flex row: Alpine's `x-for`
  leaves no space between its items, so inline items made one line that pushed the page sideways.
  On phones the card body has less padding left and right (`style.css`).
- **The time axis slides on every animation frame** (`glide()`, with uPlot's `pxAlign: 0` so nothing is
  snapped to whole pixels; one pixel is about 0.4 s, so anything slower moves in visible steps). New
  data is drawn once a second, not per message. Nothing is drawn while the page is hidden.
- **A panel destroys itself when its element has left the page.** Alpine does not call `destroy()` on a
  component that sits in an `x-if` inside another `x-if` when the outer one goes (here: the Live page
  is left; the panels are in the tab's `x-if`, the tab in the page's). The panel would stay subscribed
  and every visit would add another one, so Stop in Signal Detail could only give back one of several.
  The mixin's `tick()` checks `$el.isConnected` and destroys the panel if it is gone (at most a second
  late). Keep this in mind for any component that holds something outside the page (a connection, a
  timer on `window`) and is nested like this.
- **The broker connection, chart and signals stay out of Alpine's reactive data** (closure variables
  and module state): Alpine would wrap the growing arrays in proxies.
- **The station prefix is a wildcard** (`+/radiotracking/...`, `+/soundscapepipe/...`): it is the host
  name or the service's `station` setting, which the page does not know.
- **Colours by SDR** (`colorFor()`): the SDR number picks the colour, the same on every chart; the first four are the compass directions of the antennas, 0 blue (north), 1 yellow (east), 2 red (south), 3 green (west). Red and green are hard to tell apart with some kinds of colour blindness; the legend names each SDR, and the tooltip too. On the Soundscapepipe tab the species picks the colour of a detection (`colorFor()`), and
  each input channel has a pair: channel 1 dark and light blue, channel 2 orange and yellow, peak and RMS (`levelColor()`).
- **JSON topics only.** No CBOR or CSV decoding.
- The libraries are loaded when the tab is first opened, not with every page: mqtt.js 5.16.1
  (`vendor/mqtt/js/mqtt.min.js`, global `mqtt`) and uPlot 1.6.32 (`vendor/uplot`), both MIT, taken
  unchanged from the npm packages' `dist/` (`mqtt.min.js`, `uPlot.iife.min.js`, `uPlot.min.css`).

## Server mode

`serverModeManager` is the one place that knows whether this is the server and which config group
is chosen. Components call `serverModeManager.buildApiUrl(path)` (base URL prefix plus the
`config_group` parameter) and `isEnabled()`; `serviceManager` and `systemConfigManager` answer
without a request in server mode.

## Tests

`node --test 'tests/js/*.test.mjs'` (Node 22 or newer, no packages): the mixins against a fake browser
(`tests/js/helpers.mjs`), what each page sends, and that every component builds. The live dashboard is tested against a fake broker and chart (`tests/js/live_signals.test.mjs` for what it does with the messages, `tests/js/live_panels.test.mjs` for the manager and the panels). CI runs them in
the `frontend` job.

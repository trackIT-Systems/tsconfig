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

## Server mode

`serverModeManager` is the one place that knows whether this is the server and which config group
is chosen. Components call `serverModeManager.buildApiUrl(path)` (base URL prefix plus the
`config_group` parameter) and `isEnabled()`; `serviceManager` and `systemConfigManager` answer
without a request in server mode.

## Tests

`node --test 'tests/js/*.test.mjs'` (Node 22 or newer, no packages): the mixins against a fake browser
(`tests/js/helpers.mjs`), what each page sends, and that every component builds. CI runs them in
the `frontend` job.

"""Privileged operations, run as root via pkexec on behalf of `pi`.

This package is the *only* code in tsconfig that is ever invoked with root
privileges on a tracker. tsconfig.service itself runs as `pi`; restarting
units, rebooting and changing network settings are direct, unprivileged
calls authorized by polkit rules, and logs are readable through the
`systemd-journal` group. What has no such native path - writing, deleting
and stat-ing root-owned config files, wiping the overlay, removing GSM
fields from a netplan file - goes through
`pkexec /usr/local/sbin/tsconfig-privileged`, a wrapper (authorized by the
`systems.trackit.tsos.tsconfig-privileged` polkit action) that execs
`python3 -I -m app.privileged`. See tsOS-base's docs/security.md,
"`tsconfig-privileged` via `pkexec`".

Because this runs as root and is reachable by an operator who does not have
general root access, two rules apply everywhere in this package:

1. Keep the import graph small. Only `app.configs` (the config classes and
   the shared registry) and `app.config_loader` are imported here - never
   `app.main`, `app.routers`, or anything that pulls in FastAPI/uvicorn.
   Less code loaded as root means less code that can misbehave as root.
2. Never trust the caller. Every op re-validates its own input from scratch
   (e.g. `write-config` re-runs the target config class's own `validate()`
   before `save()`), even though the unprivileged tsconfig process that
   normally invokes this already validated the same input. This op is the
   real security boundary; the unprivileged side's validation is only there
   for fast user feedback.
"""

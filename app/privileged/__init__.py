"""Privileged operations, meant to run as root via sudo on behalf of `pi`.

This package is the *only* code in tsconfig that is ever invoked with root
privileges in the target (post-hardening) deployment model: tsconfig.service
itself runs as `pi`, and reaches every action that needs root - writing a
boot-partition config file, restarting a select systemd unit, rebooting,
reading logs, changing network settings - by calling `sudo` for an
allowlisted command. For file writes, network changes and system actions
(everything except plain `systemctl restart <unit>`, which sudoers grants
directly), the sudoers allowlist grants exactly one thing: running the
`tsconfig-privileged` wrapper script, which execs
`python3 -I -m app.privileged`.

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

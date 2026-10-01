# ADR 0003: One-command learner journey

- Status: accepted
- Date: 2026-08-11

## Context

The project is intended for learners who want to administer Linux machines, not debug the
lab platform. Requiring users to understand the implementation stack would undermine that
goal and make the README unsuitable as the front door of an open-source project.

## Decision

After installation, `labctl up` is the single supported startup command. It is responsible
for preflight checks, verified image acquisition or building, runtime initialization, service
startup, VM readiness, and presenting the local URL. Repeated execution is safe and converges
on the same running state.

Stable releases will also provide a one-command bootstrap for a clean supported Linux host.
The exact public command will be documented only after its URL and clean-host test are real.
An inspect-before-running installation path will always be documented alongside any piped
shell bootstrap.

## Requirements

- `labctl doctor` reports actionable failures and performs no mutation.
- `labctl up` never weakens libvirt socket permissions.
- Required privilege escalation is explicit and narrowly scoped.
- Downloads use HTTPS, are checksummed, and fail closed.
- Long-running setup reports progress and can resume safely.
- The website is announced only after its API and required VMs pass readiness checks.
- `labctl down` stops project resources without deleting learner progress.
- `labctl reset <scenario>` names the exact destructive target and preserves other sessions.
- Release CI exercises the documented path from a clean supported host.
- The README begins with requirements and quick start; contributor internals live in `docs/`.

## Consequences

Bootstrap, diagnostics, packaging, and clean-host testing are product features. They cannot be
deferred to an untested installation script at the end of development.


## Implementation notes (2026-10)

`labctl doctor` exists as specified: read-only, with an actionable fix for each failure.
Still open: `labctl down`, resumable long-running setup, and release CI that replays the
documented path on a clean host. Until `down` exists, `labctl scenario list` and
`labctl scenario destroy` stop a lab's machines; learner progress is kept either way.

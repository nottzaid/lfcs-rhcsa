# ADR 0004: Durable, revision-checked session state

- Status: accepted
- Date: 2026-08-11

## Context

Provisioning, checking, rebooting, resetting, and destroying real machines are multi-step
operations. The CLI process or host can stop between those steps. In-memory state would make
the remaining resources ambiguous and would prevent a later process from recovering safely.

## Decision

Each session has an explicit persisted state, generation, and monotonically increasing
revision. Transitions use a compare-and-swap update against the preceding revision. Machine
access details and independently verified libvirt ownership records are persisted in the same
private SQLite database.

The composition root is shared by CLI commands and the future web API. A process that starts a
session does not need to remain alive for another process to inspect or destroy that session.

## Consequences

- Interrupted operations remain visible in `provisioning`, `checking`, `rebooting`,
  `resetting`, or `destroying` until reconciliation handles them.
- Concurrent commands fail with an explicit conflict instead of losing an update.
- Destruction still requires the reserved name, embedded libvirt metadata, durable resource
  identity, and hypervisor UUID to agree.
- The state database contains generated console credentials and must remain mode `0600`.

# ADR 0005: Local web lifecycle and durable progress

- Status: accepted
- Date: 2026-08-11

## Context

The intended product is a SadServers-like practical loop: topic collection, scenario task,
real machine access, and state-based grading. QEMU boot and reset operations can take long
enough that executing them inside one HTTP request would make the browser appear hung and
encourage duplicate mutations.

The topic table also needs meaningful local progress. A green icon cannot be derived merely
from whether a VM exists, and it must survive deletion of disposable resources.

## Decision

- `/scenarios/topic/lfcs` is the canonical LFCS collection route.
- Browser mutations submit to one process-local worker and return a pollable job identifier.
- Operations sharing an exact scenario/session resource key cannot run concurrently.
- Pages and JSON call the same application lifecycle used by the CLI and replay tests.
- Every learner-requested check is stored in private SQLite state with scenario version,
  score, timestamp, and pass/error outcome.
- Solved means at least one complete, error-free required pass for the current scenario
  version. Updating a scenario version does not silently inherit its previous solved mark.
- Estimated duration is advisory. Scenarios and mock rehearsals have no countdown or forced teardown.

## Consequences

The current web process must run as a single worker. Durable session state still makes
interrupted infrastructure inspectable, but durable job resumption is a later requirement
before any multi-process mode. Learner progress remains independent of ephemeral VMs and can
support future weakness and mock-rehearsal reporting without coupling progress to VM lifetime.

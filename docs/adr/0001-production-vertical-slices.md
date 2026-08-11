# ADR 0001: Production vertical slices, not throwaway prototypes

- Status: accepted
- Date: 2026-08-11

## Context

The project is intended to demonstrate serious Linux administration engineering and to be
used for certification preparation. A throwaway prototype would create false confidence in
VM provisioning and checkers while accumulating architecture that cannot be verified.

## Decision

The project will grow through narrow but production-quality vertical slices. The first slice
must use the permanent scenario schema, application boundaries, resource-safety rules,
verification lifecycle, and reporting model. Later work adds adapters and scenarios rather
than replacing a temporary implementation.

## Consequences

- Initial visible functionality arrives later than a shell-script demonstration.
- Unit and contract tests can exercise orchestration before expensive VM runs.
- Every released scenario has reproducible evidence that its setup, solution, persistence,
  and reset behavior work.
- A feature that cannot be controlled programmatically is incomplete.


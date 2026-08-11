# ADR 0002: Original domain-first implementation

- Status: accepted
- Date: 2026-08-11

## Context

BrokenOps closely resembles the desired user experience, while dsoxlab has stronger
declarative validation. Neither currently provides the required combination of multi-machine
topologies, strict resource ownership, certification mapping, persistence replay, browser and
CLI parity, and a mature automated test surface.

## Decision

Implement an original application core and treat the audited projects as engineering
references. Reuse of licensed code or CC BY content, if any, will be explicit and attributed.
The core will expose ports for hypervisor, guest, checks and persistence so that real adapters
and deterministic fakes are interchangeable.

## Consequences

- The project does not inherit an immature engine's data model or privilege decisions.
- Up-front domain design and adapter work are required.
- Source provenance remains clear.


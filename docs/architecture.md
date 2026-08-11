# Architecture

## Purpose

The system must support a polished local website while remaining fully operable and
verifiable without a browser. The application core therefore depends on interfaces for
virtualization, guest execution, state checks, persistence, and reporting. Libvirt, SSH,
QEMU guest-agent, SQLite, CLI, and web implementations sit outside that core.

## Boundaries

```text
Web UI ─────┐
            ├── Application services ── Scenario domain
CLI ────────┘             │
                           ├── Hypervisor port ── system-libvirt adapter
Verification harness ─────┼── Guest port ─────── SSH / QEMU-agent adapters
                           ├── Check port ─────── behavioral check providers
                           └── Store port ─────── SQLite adapter
```

The web layer never invokes `virsh`, SSH, Ansible, or checker scripts directly. This makes
the complete lifecycle callable from tests and prevents browser behavior from becoming an
unverifiable second implementation.

## Scenario lifecycle

A session is a state machine:

```text
declared -> provisioning -> ready -> checking -> ready
                              │                    │
                              ├-> rebooting -------┤
                              └-> resetting -> ready
ready -> destroying -> destroyed
failed -> resetting | destroying
```

Web mutations are serialized by one background worker, with duplicate active operations on
the same resource rejected. Session transitions and resource ownership are durable; the
current web job envelope itself is process-local. Updates use revision-checked
compare-and-swap writes, so two processes cannot silently overwrite one another's lifecycle
transition. Durable job resumption remains required before multi-process deployment.

## Resource ownership and host safety

The project will connect to `qemu:///system` but may mutate only resources that meet all of
these conditions:

- the name has the reserved `lal-` prefix;
- the libvirt XML contains project ownership metadata;
- the resource identifier exists in the project's state store;
- a destructive operation names one exact session or image build.

Existing domains such as `rocky10` and `leap16` are out of scope. The implementation must
not loosen permissions on the libvirt socket. Access is inherited from the user's existing
`libvirt` group membership.

## Image and session model

- A golden image is created reproducibly from a verified installation source.
- Golden images are read-only and identified by a content digest plus build manifest.
- Every session disk is a qcow2 overlay whose backing image is immutable.
- Session metadata and generated console credentials are stored in a mode-0600 SQLite file.
- Version-specific check attempts and solved progress remain after disposable VMs are removed.
- Scenario-specific disks are sparse and disposable.
- Setup runs before learner access and is itself verified.
- Reset destroys only session overlays and recreates the same declared topology.

## Checking model

Checks are host-owned. A learner with root inside a guest cannot edit the grading contract.
Check providers include:

- guest state inspection through SSH;
- QEMU guest-agent fallback when networking is intentionally broken;
- black-box network requests from another scenario VM;
- libvirt state inspection for virtualization tasks;
- reboot and reconnect orchestration;
- file, service, process, mount, identity, security and protocol behavior.

Shell checks are supported as an escape hatch, not as the default abstraction. All shell
inputs are data, are executed without an implicit shell where possible, and have explicit
timeouts and output limits.

## Test layers

1. **Static:** formatting, typing, schema validation and objective/source completeness.
2. **Unit:** state machines, ownership rules, scoring and check semantics with fakes.
3. **Contract:** every scenario manifest and adapter satisfies the same behavioral contract.
4. **Live integration:** disposable libvirt networks, domains, disks and guest transports.
5. **Scenario replay:** broken -> repair -> pass -> reboot -> pass -> reset -> broken.
6. **Mutation:** representative incomplete or unsafe repairs must not receive a pass.
7. **Web end-to-end:** the browser drives the same API after the engine is already proven.

## Local web boundary

`labctl up` binds only to loopback and serves the LFCS topic at
`/scenarios/topic/lfcs`. Trusted-host filtering, a non-simple same-origin action header,
content-security policy, and no-store responses protect local mutation and credential-bearing
pages. VM start, check, reset, and destroy requests return background job identifiers; the
browser polls terminal state without keeping an HTTP request open during guest boot.

Scenario and mock-exam durations are descriptive metadata only. Nothing expires a learner
session, and the released local UI does not enforce a countdown.

Fast tests run on every change. Live verification can run locally or on a dedicated KVM-capable
CI runner. `labctl scenario verify` exposes the same replay system outside pytest.

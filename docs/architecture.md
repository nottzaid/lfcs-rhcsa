# Architecture

## Purpose

The system must support a polished local website while remaining fully operable and
verifiable without a browser. The application core therefore depends on interfaces for
virtualization, guest execution, state checks, persistence, and reporting. Libvirt, SSH,
SQLite, CLI, and web implementations sit outside that core.

## Boundaries

```text
Web UI ─────┐
            ├── Application services ── Scenario domain
CLI ────────┘             │
                           ├── Hypervisor port ── system-libvirt adapter
Verification harness ─────┼── Guest port ─────── SSH adapter
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
There are three kinds:

- **command** checks run arguments, or a bash script, in a named scenario VM over SSH;
- **file** checks compare a path's type, owner, mode, and content;
- **service** checks compare a systemd unit's state and boot enablement, and quote the
  unit's own last log line when it failed.

Most requirements are behavioral, so most checks are scripts: they act as the affected user,
request a page from another VM, or measure a cgroup. The host prepends a small library to
every script (`adapters/check_library.sh`: `fail`, `expect_eq`, `retry`, `unit_value`,
`fstab_field`, `sysctl_configured`, `permissive_domain`, ...), and a script reports an unmet
requirement as one learner-facing sentence. Every check has a timeout and output limits.

A learner's check that passes live, for a scenario whose task requires persistence, reboots
the scenario's persistence hosts, waits until their boot has finished (`systemctl
is-system-running --wait`), and checks again. Only a pass after the reboot counts as solved.

## Scenario networks

A scenario can declare isolated networks, and its hosts NICs on them. Each session gets its
own libvirt networks with no addresses, DHCP, or forwarding: the scenario's setup assigns
addresses, so routers, gateways, and firewalls are real hosts. NICs get the names the
manifest declares (a systemd `.link` file per MAC address), and NetworkManager does not
create automatic profiles for them. Both take effect late in first boot, so scenario NICs
start with their virtual cable unplugged and the platform plugs them in once cloud-init has
finished; otherwise NetworkManager would try DHCP on them, and boot would wait a minute for
it. The management NIC stays on libvirt's default network for SSH and is never part of a
task. Before a machine is destroyed it hands that network's DHCP lease back; libvirt would
otherwise hold it for an hour, and frequent resets would exhaust the network's addresses.

## Test layers

1. **Static:** formatting, typing, schema validation and objective/source completeness.
2. **Unit:** state machines, ownership rules, scoring and check semantics with fakes.
3. **Contract:** every scenario manifest and adapter satisfies the same behavioral contract.
4. **Live integration:** disposable libvirt networks, domains, disks and guest transports.
5. **Scenario replay:** broken -> repair -> pass -> reboot -> pass -> reset -> broken.
6. **Mutation:** every scenario ships rejected near-miss repairs, and the replay proves the
   checks refuse each of them, live or after the reboot.

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

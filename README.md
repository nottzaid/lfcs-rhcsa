# Linux Admin Lab

Linux Admin Lab is a completely local, reproducible, self-grading environment for
practical Linux system-administration work. Its first certification profiles are the
current LFCS and RHCSA 10 objectives; a separate professional-operations track covers
workplace skills without misrepresenting them as examination requirements.

This is not a conventional course and it is not an exam-question dump. A scenario gives
the learner one or more real virtual machines in a known state, describes the required
outcome, and checks the resulting behavior after the learner has administered the systems.

## Quick start

The stable release will provide a one-command bootstrap. After installation, the complete
day-to-day startup interface is:

```bash
labctl up
```

It will verify host capabilities, prepare or verify the local base image, start the website
and required scenario machines, and display the local URL. Until the first release is ready,
this section will not advertise an installation command that has not itself been tested on a
clean supported host.

## Non-negotiable properties

- Native QEMU/KVM and system libvirt; active machines remain visible in virt-manager.
- Immutable, checksummed base images and disposable qcow2 overlays.
- Declarative, versioned scenario and curriculum manifests.
- Checks evaluate resulting system behavior, not command history.
- Persistence checks reboot machines when the task requires it.
- Every scenario is replayed from a clean build with a known-good repair.
- Reset behavior is tested as seriously as successful completion.
- Certification scope comes only from the certification vendor's published objectives.
- Technical behavior is sourced from upstream, distribution, and installed documentation.
- The documented quick-start path is replayed on clean supported hosts before release.

## Verification contract

Before a scenario can be included in a release, automation must demonstrate:

1. Its freshly provisioned state does not satisfy the task.
2. Its reference repair satisfies all required checks.
3. The repaired state remains correct after any required reboot.
4. Resetting the scenario recreates the original failing state.

The web application and CLI will call the same application service used by this verification
harness. No scenario logic may exist only in the browser.

## Current status

The repository is establishing its production architecture and executable scenario contract.
Scenario content has begun: `local-account-repair` is the first scenario to pass the complete
live broken → repair → pass → reset → broken contract. It is not yet exposed through the
learner website. The SELinux manifest under `examples/` remains a draft contract fixture and
is not released scenario content.

The infrastructure can acquire and verify the pinned Rocky Linux 10.2 cloud image, create a
disposable QCOW2 overlay and cloud-init seed, boot an ownership-guarded system-libvirt domain,
discover it through DHCP, verify the guest baseline over SSH, and remove only its registered
resources. Session state, machine access details, and resource ownership survive across CLI
processes in a private SQLite database. See [docs/architecture.md](docs/architecture.md) and
the architecture decisions in [docs/adr](docs/adr).

The low-level engineering interface can currently start, inspect, and destroy a single-host
session. It is not yet the learner quick start:

```bash
labctl session start /absolute/path/to/verified-base.qcow2
labctl session status SESSION_UUID
labctl session destroy SESSION_UUID
```

## Development

```bash
uv sync
uv run ruff check .
uv run mypy
uv run pytest --cov
uv run labctl catalog validate examples/scenarios
```

Live KVM verification is deliberately separate from fast tests:

```bash
uv run pytest -m live
```

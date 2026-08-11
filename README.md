# Linux Admin Lab

Linux Admin Lab is a completely local, reproducible, self-grading environment for
practical Linux system-administration work. Its first certification profiles are the
current LFCS and RHCSA 10 objectives; a separate professional-operations track covers
workplace skills without misrepresenting them as examination requirements.

This is not a conventional course and it is not an exam-question dump. A scenario gives
the learner one or more real virtual machines in a known state, describes the required
outcome, and checks the resulting behavior after the learner has administered the systems.

## Quick start

From a development checkout on the current supported host:

```bash
uv sync
uv run labctl up
```

This opens the LFCS scenario collection at `http://127.0.0.1:8787/scenarios/topic/lfcs`.
Choose **Info** to inspect a task or **Run** to acquire/verify its image and launch the
disposable VM in the background. Active machines are visible in virt-manager. Scenario time
values are estimates only: ordinary labs never expire or destroy a VM on a timer.

The stable release target remains the shorter installed command, `labctl up`, plus a
one-command bootstrap tested on a clean supported host. This README will not advertise an
installation URL until that release path is real and replay-verified.

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

The production browser loop is operational. Its `/scenarios/topic/lfcs` page lists the local
LFCS collection; scenario and session pages expose the task, affected machines, virt-manager
domain, SSH/console access, and **Check**, **Reset**, and **Destroy** controls. Slow VM work is
serialized through background jobs, and check attempts, best score, and version-specific
Solved state survive VM destruction and website restarts.

Scenario content has begun: `local-account-repair` is the first scenario to pass both the
complete live broken → repair → pass → reset → broken verification contract and the real
browser Run → fail → repair → pass → destroy lifecycle. The SELinux manifest under
`examples/` remains a draft contract fixture and is not released scenario content.

The infrastructure can acquire and verify the pinned Rocky Linux 10.2 cloud image, create a
disposable QCOW2 overlay and cloud-init seed, boot an ownership-guarded system-libvirt domain,
discover it through DHCP, verify the guest baseline over SSH, and remove only its registered
resources. Session state, machine access details, and resource ownership survive across CLI
processes in a private SQLite database. See [docs/architecture.md](docs/architecture.md) and
the architecture decisions in [docs/adr](docs/adr).

The same learner lifecycle remains available without a browser. One command acquires or
verifies the pinned image, creates the broken machine, and prints its virt-manager domain,
SSH command, console credentials, and task:

```bash
uv run labctl scenario start local-account-repair
```

The returned session UUID drives the remaining operations:

```bash
uv run labctl scenario check SESSION_UUID
uv run labctl scenario status SESSION_UUID
uv run labctl scenario reset SESSION_UUID
uv run labctl scenario destroy SESSION_UUID
```

These commands and the website use the same application services and durable state.

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

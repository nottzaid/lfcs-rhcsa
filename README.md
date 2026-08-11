# Linux Admin Lab

Linux Admin Lab is a completely local, reproducible, self-grading environment for
practical Linux system-administration work. Its released curriculum covers the current
Linux Foundation Certified System Administrator (LFCS) competencies. RHCSA and broader
professional-operations tracks are future additions and will remain visibly separate.

This is not a conventional course and it is not an exam-question dump. A scenario gives
the learner one or more real virtual machines in a known state, describes the required
outcome, and checks the resulting behavior after the learner has administered the systems.

## Quick start

From a checkout on a Linux host with hardware virtualization, QEMU/KVM, system libvirt,
virt-manager, and [`uv`](https://docs.astral.sh/uv/) installed:

```bash
./lab
```

That is the only project command needed. It opens the LFCS scenario collection at
`http://127.0.0.1:8787/scenarios/topic/lfcs`.
Choose **Info** to inspect a task or **Run** to acquire/verify its image and launch the
disposable VM in the background. Active machines are visible in virt-manager. Scenario time
values are advisory estimates; the local lab does not enforce a countdown.

The first launch verifies or downloads the pinned Rocky Linux 10.2 DVD (about 10 GB) and
builds the immutable lab image. Later launches reuse it after validating its provenance and
checksum. The equivalent development command is `uv run labctl up`.

## What is included

- 38 focused exercises, with at least one dedicated scenario for every published LFCS
  competency.
- 7 integrated capstones that combine administration domains into realistic incidents.
- 3 balanced 20-task mock rehearsals following the published domain weights.
- Fresh disposable Rocky Linux 10.2 VMs, including multi-machine, extra-disk, networking,
  LDAP, NFS, iSCSI, container, SELinux, and nested-libvirt labs.
- State-based grading, reset, solution-independent checks, and reboot validation where
  persistence is part of the task.

The scenario names and suggested times are visible before launch. **Info** shows the task,
competency mapping, sources, topology, and affected VM names. **Run** starts the machines in
the background; **Check** grades the observable end state. You remain free to use the VM
console in virt-manager or the displayed SSH command.

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

## Curriculum and evidence

The Linux Foundation's published objectives define scope; books, repositories, and third-party
training material provide exercise ideas only after technical review. The research record,
official weighting, project and book audits, distribution decision, and coverage matrix live in
[`docs/research`](docs/research) and [`curricula`](curricula). Each scenario also records its
competency mapping and upstream, distribution, or man-page sources in its manifest.

All 45 released scenarios pass the permanent disposable-VM acceptance contract against the
pinned Rocky Linux 10.2 image. Maintainers can replay one with:

```bash
uv run labctl scenario verify SCENARIO_ID
```

The verifier proves broken initial state, reference repair, required reboot persistence, clean
reset, and every alternate repair. The browser and CLI call the same application services; no
grader behavior exists only in the UI.

Session state, machine access details, check history, best score, and resource ownership survive
website restarts in a private SQLite database. Only resources registered to a lab session can be
destroyed. See [docs/architecture.md](docs/architecture.md) and [docs/adr](docs/adr).

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
uv run labctl catalog validate scenarios
```

Live KVM verification is deliberately separate from fast tests:

```bash
LAL_RUN_SCENARIO_LIVE=1 \
LAL_BASE_IMAGE=runtime/cache/images/rocky-10.2-lab-v1.qcow2 \
uv run pytest tests/live/test_verified_scenario_replay.py
```

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
disposable VM in the background. Active machines are visible in virt-manager. Scenario times
are estimates. Only a mock rehearsal started as timed has a deadline: checks after it stop
counting, and machines are never destroyed on a timer.

The first launch verifies or downloads the pinned Rocky Linux 10.2 DVD (about 10 GB) and
builds the immutable lab image. Later launches reuse it after validating its provenance and
checksum. The equivalent development command is `uv run labctl up`.

## What is included

- 38 focused scenarios, with at least one for every published LFCS competency, and 7
  capstones: single incidents whose causes cross several domains.
- 5 mock rehearsals of 20 tasks at the published domain weights. Three review the practice
  scenarios. Two are exam rehearsals: 40 tasks found nowhere on the practice path, phrased
  like the exam's and without hints, so each is a first attempt; together they ask for every
  competency. A rehearsal runs untimed or timed (two hours) and is scored against the exam's
  67% pass mark. An unsolved task earns partial credit for the requirements it meets; the
  Linux Foundation does not document partial credit, but candidates report it.
- A practice path that orders scenarios by LFCS domain and difficulty, capstones last.
- Fresh disposable Rocky Linux 10.2 VMs, including multi-machine labs on their own isolated
  networks (routers, partners, clients, directory, file, and time servers), extra disks,
  LDAP, NFS, iSCSI, containers, SELinux, and libvirt guests inside a lab VM.

Every scenario starts from a situation a working administrator would actually meet,
usually a symptom, and teaches through the lab itself:

- **Done means** lists the requirements the checks enforce, in plain words.
- **Hints** go from where to look, to what is wrong, to which mechanism fixes it; they
  name tools and manual pages and never paste the solution.
- **Check** grades the machines' behavior and explains every unmet requirement. When a
  task requires persistence, a passing check reboots the machines and grades them again.
- **Debrief** opens after a solve, or on request: what was wrong, a clean fix and why it
  works, how to verify it yourself, and the fixes that look right but are not.

**Info** shows the task, competency mapping, sources, and topology before launch. **Run**
starts the machines in the background. You remain free to use the VM console in
virt-manager or the displayed SSH command.

## Non-negotiable properties

- Native QEMU/KVM and system libvirt; active machines remain visible in virt-manager.
- Immutable, checksummed base images and disposable qcow2 overlays.
- Declarative, versioned scenario and curriculum manifests.
- Checks evaluate resulting system behavior, not command history.
- Persistence checks reboot machines when the task requires it.
- Every scenario is replayed from a clean build with a known-good repair.
- Every scenario ships near-miss repairs that the checks must refuse.
- Reset behavior is tested as seriously as successful completion.
- Certification scope comes only from the certification vendor's published objectives.
- Technical behavior is sourced from upstream, distribution, and installed documentation;
  every man page a scenario cites is checked against the lab image.

## Verification contract

Before a scenario can be included in a release, automation must demonstrate:

1. Its freshly provisioned state does not satisfy the task.
2. Its reference repair satisfies all required checks.
3. The repaired state remains correct after any required reboot.
4. Resetting the scenario recreates the original failing state.
5. Every alternate repair passes too, so the checks do not demand one particular method.
6. Every rejected near-miss repair fails, live or after the reboot, so the checks tell a
   fix from a workaround.

The web application and CLI call the same application services as this verification
harness. No scenario logic exists only in the browser.

## Curriculum and evidence

The Linux Foundation's published objectives define scope; books, repositories, and third-party
training material provide exercise ideas only after technical review. The research record,
official weighting, project and book audits, distribution decision, and coverage matrix live in
[`docs/research`](docs/research) and [`curricula`](curricula). Each scenario also records its
competency mapping and upstream, distribution, or man-page sources in its manifest.

All 45 practice scenarios and 40 exam tasks pass this contract against the pinned Rocky
Linux 10.2 lab image (`rocky-10.2-lab-v2`). Maintainers can replay one with:

```bash
uv run labctl scenario verify SCENARIO_ID
```

How to write a scenario that meets the standard is in [docs/authoring.md](docs/authoring.md).

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
uv run labctl scenario list
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

The fast tests need no libvirt. Everything they cannot reach is proven against the real
thing by live tests, each kind opted into by its own variable:

| Variable | What it runs on this host |
| --- | --- |
| `LAL_RUN_LIVE=1` | host-safety rules against real libvirt networks; `labctl doctor` |
| `LAL_RUN_DOMAIN_LIVE=1` | boots a guest and checks every man page a debrief cites |
| `LAL_RUN_SCENARIO_LIVE=1` | isolated scenario networks; every scenario's acceptance contract |
| `LAL_RUN_BROWSER_LIVE=1` | a learner's journey through `labctl up` in headless Chromium |
| `LAL_RUN_IMAGE_BUILD=1` | a complete Kickstart install of the lab image (about 20 minutes) |

Guest-booting tests also need `LAL_BASE_IMAGE`. Replaying all 85 contracts takes hours;
`-k` selects scenarios, and `LAL_LIVE_SHARD_TOTAL` with `LAL_LIVE_SHARD_INDEX` splits them
across runs. Fast and live tests together cover every line:

```bash
uv run pytest --cov --cov-report=
LAL_RUN_LIVE=1 LAL_RUN_DOMAIN_LIVE=1 LAL_RUN_SCENARIO_LIVE=1 LAL_RUN_BROWSER_LIVE=1 \
LAL_RUN_IMAGE_BUILD=1 LAL_BASE_IMAGE=runtime/cache/images/rocky-10.2-lab-v2.qcow2 \
uv run pytest tests/live -m live -k "not acceptance_contract or runaway" --cov --cov-append
```

## Troubleshooting

`uv run labctl doctor` checks this host for everything the lab needs, without changing
anything, and prints the fix for each problem it finds: KVM and nested virtualization,
system libvirt and its default network, the commands the lab runs, whether QEMU can reach
the runtime directory, the host firewall, the lab image, and sessions left behind.

- **Guests are slow to start services, or SSSD and NFS time out.** Guests resolve names
  through libvirt's DNS service on `192.168.122.1`. A host firewall that blocks it, for
  example ufw's default incoming policy, makes every lookup wait for a timeout. Allow DNS
  from the libvirt bridge: `sudo ufw allow in on virbr0 to any port 53`.
- **The libvirt labs run slowly.** They start a small guest inside a lab VM. With nested
  KVM enabled on the host (`cat /sys/module/kvm_*/parameters/nested` prints `Y` or `1`) it
  runs at full speed; without it, QEMU emulates the CPU and the guest still works, slowly.
- **A crash left lab VMs behind.** `uv run labctl scenario list` shows every session that
  was not destroyed, and `uv run labctl scenario destroy SESSION_UUID` removes one session's
  domains, disks, and networks; only resources registered to that session are touched. It
  also clears a session whose machines were already deleted, for example in virt-manager.

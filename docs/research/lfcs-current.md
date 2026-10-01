# Current LFCS scope snapshot

- Authority: Linux Foundation Education
- Retrieved: 2026-08-11
- Certification page last modified by publisher: 2026-08-06
- Machine-readable record: [`curricula/lfcs-2026-08.yaml`](../../curricula/lfcs-2026-08.yaml)
- Coverage: every competency has a focused scenario; `tests/contract/test_scenarios.py` derives this from the scenario manifests.

## Published domains

| Domain | Weight | Published competencies |
| --- | ---: | ---: |
| Operations Deployment | 25% | 8 |
| Networking | 25% | 8 |
| Storage | 20% | 7 |
| Essential Commands | 20% | 6 |
| Users and Groups | 10% | 5 |

The competency wording in the YAML snapshot is transcribed from the current
[LFCS certification page](https://training.linuxfoundation.org/certification/linux-foundation-certified-sysadmin-lfcs/).
The page states that the exam is distribution-independent and performance-based.

## Exam-design facts relevant to this lab

The Linux Foundation's current
[LFCS instructions](https://docs.linuxfoundation.org/tc-docs/certification/instructions-lfcs-and-lfce)
specify 17–20 command-line performance tasks, two hours, and a 67% passing score. Tasks name
their designated host. The environment can direct candidates from `node-1` to containers or
remote nodes over SSH; root access is available with `sudo -i`; `node-1` must not be rebooted,
while other nodes may be rebooted. Allowed in-terminal resources include man pages,
distribution-installed documentation, and distribution packages.

The instructions also say candidates should return to `node-1` instead of nesting SSH
connections, must not manipulate the firewall on `node-1`, and must not block TCP ports 8080,
4505, or 4506. Lab scenarios therefore use designated secondary nodes for reboot and firewall
work, and reserve the controller role for access and grading.

The official
[scoring guidance](https://docs.linuxfoundation.org/tc-docs/certification/lf-handbook2/exam-scoring-and-notification)
says performance tasks may have more than one valid method and are judged by the correct
result unless a task specifies otherwise. This directly supports state-based checkers and is
why scenario validation must not require one command history.

## Scope policy

- Only published certification objectives define the LFCS track.
- Internal objective IDs are stable project identifiers; Linux Foundation publishes titles,
  not these IDs.
- Distribution, upstream, and man-page sources establish technical behavior but cannot add an
  item to LFCS scope.
- RHCSA and professional-operation references can enrich a scenario but remain separate tracks.
- The snapshot date is part of the curriculum ID. A future objective change creates a new
  snapshot and an explicit scenario-reference migration rather than silently changing scope.

## Corroboration and confidence

Exa and LinkUp independently rediscovered the same Linux Foundation certification and instruction
pages. Targeted searches for a later 2025–2026 objective revision found no contradictory official
scope, and the certification page itself was modified five days before retrieval. Confidence that
this snapshot represents the published scope on 2026-08-11 is high. This says nothing about secret
exam tasks: the project intentionally derives original exercises only from public competencies and
real administration behavior.

See the [existing-material audit](existing-material-audit.md) for evaluated project and book
references, and the [default-distribution decision](default-distribution.md) for the Rocky 10.2
choice.

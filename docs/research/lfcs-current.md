# Current LFCS scope snapshot

- Authority: Linux Foundation Education
- Retrieved: 2026-08-11
- Machine-readable record: [`curricula/lfcs-2026-08.yaml`](../../curricula/lfcs-2026-08.yaml)

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

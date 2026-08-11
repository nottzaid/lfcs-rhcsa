# Design provenance: local-account-repair

## Why it is in scope

The current `lfcs-2026-08` snapshot includes “Create and manage local user and group
accounts” under Users and Groups. That published competency is the certification basis; the
scenario does not infer LFCS scope from RHCSA, a book, or a third-party repository.

## How the task was constructed

Linux Foundation's public LFCS practice material demonstrates legitimate account tasks by
specifying concrete group, user, home, shell, and membership end states. The scenario uses
that task-oriented style without copying its wording or account specification. Audited RHCSA
material also reinforced that account repair, ownership, and home migration make stronger
hands-on exercises than isolated command recall.

The particular incident—repairing a wrongly provisioned deployment account while preserving
an existing handoff file—is original to this project. The preservation requirement adds a
professional-operations concern without claiming it is a separately published LFCS item.

## Technical authority

- `usermod(8)` defines UID, primary-group, home-move, and shell changes.
- `groupmod(8)` defines changing a local group's GID.
- `passwd(5)` defines the local account record being inspected.
- `getent(1)`, `id(1)`, and `stat(1)` supply state observations.

The checker evaluates UID, GID, primary group, passwd record, directory metadata, and file
content. It has no access to the learner's command history and accepts any repair sequence
that reaches all required end states.

## Acceptance evidence

`tests/live/test_local_account_repair_replay.py` provisions two independent Rocky Linux 10.2
overlays and enforces broken → solved → reset → broken. The public CLI path was also exercised
with a repair entered through ordinary SSH rather than the reference-action runner.

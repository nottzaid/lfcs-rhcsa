# Existing material audit

Audited on 2026-08-11 at the commits shown below. These projects are inspiration and engineering
evidence, not authorities for LFCS scope. Only the Linux Foundation curriculum snapshot can place
a competency in the LFCS track.

## Strongest engineering references

| Project | Audited revision and license | What is worth using | Limits and decision |
| --- | --- | --- | --- |
| [stephrobert/dsoxlab](https://github.com/stephrobert/dsoxlab) | `d8a38bd0fa1a` (2026-07-28), Apache-2.0 | Clean separation between engine and content; shell, Incus and KVM providers; declarative targets; readiness probes; SQLite progress; doctor checks; pytest-testinfra validation; strict typing, CI and fuzzing. | Do not adopt the engine wholesale because this project already has a smaller libvirt-native core and browser lifecycle. Adopt its provider contracts, explicit readiness, and validator discipline. |
| [stephrobert/linux-dsoxlab-training](https://github.com/stephrobert/linux-dsoxlab-training) | `80a4dc89b600` (2026-07-23), CC BY 4.0 | 84 manifests with matching validators and solutions. Its stronger validators independently prove live state, persistent configuration, actual service behavior, security posture, and negative conditions. LDAP/SSSD, LVM growth, NetworkManager, SELinux, firewall/NAT, Podman, autofs, and proxy labs are particularly relevant. | Its tags are not a current-LFCS coverage proof. The LFCS mock says 66 rather than the current 67 passing score and simplifies the environment to one VM. Reuse requires attribution and substantial adaptation; original scenarios are preferred. |
| [firassBenNacib/RHCSA-Simulator](https://github.com/firassBenNacib/RHCSA-Simulator) | `81e85f0e7ba4` (2026-08-01), MIT | The best QA system found: schema and balance audits, target-identity checks, scenario-similarity limits, smoke tests, fresh reset/rebuild behavior, full reference-solution replay, retry handling, persistent-state checks, and optional timers. | PowerShell/Vagrant/VirtualBox and RHCSA-specific content are not a runtime or curriculum fit. Port the replay and audit ideas, not the platform. |
| [HimanM/BrokenOps](https://github.com/HimanM/BrokenOps) | `5842bd9ce400` (2026-08-04), MIT | Native KVM/libvirt, FastAPI/React, 71 troubleshooting lab layouts, and a CI loop that proves the initial checker fails before applying a reference repair and proving it passes. Good incident vocabulary around disk, fstab, LVM, NFS, systemd, routing, and services. | Many graders are binary and configuration-path-specific. One SELinux lab fakes `sestatus`, `getenforce`, and `semanage` on Ubuntu instead of exercising a real LSM; that technique is rejected. Its CI changes the libvirt socket to mode 0666; that technique is also rejected. |

## Useful, narrower references

| Project | Revision/license | Verdict |
| --- | --- | --- |
| [balucio/LFCS-GNS3-Lab](https://github.com/balucio/LFCS-GNS3-Lab) | `fb178261dfc8` (2024-09-11); README declares Apache-2.0 but no root license text | A thoughtful multi-machine topology and question bank, useful for networked task ideas. It has no provisioning source, automated grader, tests, or current-objective coverage proof. Treat as ideas only unless licensing is clarified. |
| [rdbreak/rhcsa8env](https://github.com/rdbreak/rhcsa8env) | `b523d413e0f0` (2022-01-10), MIT | Useful precedent for a reusable client/server/repository environment and Ansible reset playbook. The RHEL 8/VirtualBox/Vagrant stack and installation scripts are obsolete for this project. |
| [aggressiveHiker/rhcsa9](https://github.com/aggressiveHiker/rhcsa9) | `4de24cfd8873` (2024-06-19), no license | Organized objective-based practice lists and exam task ordering. No automated state validation or reusable licensed implementation. |
| [soficx/rhcsa](https://github.com/soficx/rhcsa) | `b18af545414a` (2022-04-11), MIT | Broad RHCSA task vocabulary. It is largely prose with prescribed solutions, contains errors and stale assumptions, and provides no reproducible setup or grader. Low-confidence inspiration only. |
| [tucker-st/rhcsa-ex200](https://github.com/tucker-st/rhcsa-ex200) | `b0c8c3e366d9` (2026-04-03), MIT | Current RHEL 10 lab organization, read-only grading scripts, reset guidance, and explicit separation of automatic from manual validation. Checkers and task depth need individual review; RHCSA scope remains supplemental. |
| [sandervanvugt/rhcsa-labs](https://github.com/sandervanvugt/rhcsa-labs) | `d88a6a607236` (2025-05-19), no license | Shows a trainer's shell-grader structure. Its own README calls it prerelease and not fit for use. Do not copy. |

## Rejected as foundations

| Project | Revision/license | Reason |
| --- | --- | --- |
| [loyality7/lfcs-practice-tool](https://github.com/loyality7/lfcs-practice-tool) | `ca0207412966` (2025-11-29), MIT | Roughly 83 YAML exercises provide breadth, but many validate a learner-created answer file instead of system state. Its advertised AI validation is unfinished, examples have major false-positive paths, and its objective set contains older LFCS material. Catalog inspiration only. |
| [rmcmillan34/sysadmin-sim](https://github.com/rmcmillan34/sysadmin-sim) | `c66fe8e52826` (2025-06-06), no license despite an MIT claim in README | Attractive ticket vocabulary and multi-distro ambitions, but parts are scaffolding: some setup/check scripts are empty, only five tickets reference LFCS, tests validate a small YAML subset, and the quickstart still contains a placeholder repository URL. No reusable basis. |
| [chanchiwai-ray/lfcs-practice-questions](https://github.com/chanchiwai-ray/lfcs-practice-questions) | `d0685c62d358` (2025-04-30), no license | Current objective transcription plus a small LXD/Terraform fixture and a few questions; no grading or comprehensive environment. |
| LabEx LFCS practice exam [01](https://github.com/labex-labs/lfcs-practice-exam-01) / [02](https://github.com/labex-labs/lfcs-practice-exam-02) | `f5c214d2e02d` / `336e3a6c4cfd` (2026-07-01), no license | Forty plausible task titles, but the repositories are promotional READMEs with no setup, validation, implementation, or license. They cannot support quality or reuse claims. |

## SadServers

The public [SadServers repository](https://github.com/SadServers/sadservers), audited at
`64a06f853152` (2026-06-26), is valuable as a scenario-description corpus and architecture
case study. It does not contain the production frontend or provisioning implementation and has
no repository license, so its code/content is not copied. Inspection of the public LFCS-like
[Linux/Bash topic page](https://sadservers.com/scenarios/topic/linux-bash) shows the interaction
to emulate: a compact striped table, short task metadata, `Info`, `Run`, and post-work machine
validation. Our implementation independently recreates that workflow for local libvirt.

## Books available locally

- Sander van Vugt, *Red Hat RHCSA 10 Cert Guide: EX200* (Pearson IT Certification, 2026).
- Sander van Vugt, *Red Hat RHCSA 9 Cert Guide: EX200* (Pearson IT Certification, 2023).

Both contain end-of-chapter labs and full practice exams across users, permissions, networking,
software, processes, systemd, scheduling, logging, storage/LVM, boot/recovery, SSH, web service,
SELinux, firewall, NFS/autofs, time, containers, and troubleshooting. They are strong references
for task composition, prerequisite layering, persistence expectations, and distractors. The 2026
edition is the better source for current enterprise-Linux behavior; the 2023 edition is useful for
additional exercise variation.

The books do not define LFCS scope, and their task text or solutions will not be reproduced.
They also remain claims to verify: for example, the RHCSA 9 guide's explanation of a persistent
timer implies a fixed interval, while upstream `systemd.timer(5)` defines `Persistent=` as catch-up
for missed `OnCalendar=` activations. Scenario behavior follows upstream documentation.
Original scenarios may use the same public Linux administration concepts, verified against
upstream documentation, Rocky documentation, and installed man pages.

## Engineering rules adopted from the audit

1. Prove that the provisioned broken state fails its checker.
2. Prove that a reference solution passes, then prove at least one materially different valid
   solution where alternatives exist.
3. Reset and replay from a clean artifact; never assume setup scripts are idempotent without proof.
4. For persistence claims, reboot the allowed secondary node and rerun the state checks.
5. Grade observable behavior first and persistent configuration second; do not grade command
   history or a learner-authored “answer” file.
6. Include negative invariants: SELinux must remain enforcing, required services must not be
   replaced with dummy processes, and expected data must survive.
7. Keep scenario and mock-exam times advisory; local sessions do not expire on a countdown.

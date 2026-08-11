# Default practice distribution

## Decision

Use **Rocky Linux 10.2** for the default LFCS practice image. Treat `rocky-10.2-base-v1`
as a pinned, checksummed lab artifact rather than silently following the newest minor release.
Task wording and checker contracts remain distribution-neutral unless the competency itself
requires a distribution facility. A later compatibility lane should replay suitable scenarios
on openSUSE Leap 16 and Debian 13; those variants must not alter the LFCS scope.

## Why Rocky 10.2

- The official [Rocky release table](https://docs.rockylinux.org/10/releases/) identifies 10.2
  as the current Rocky 10 release, with active support through 2030-05-31 and security support
  through 2035-05-31. That is a much calmer base for reproducible VM fixtures than a rapid
  distribution release cycle.
- Rocky supplies a coherent, production-relevant stack for the current objectives: systemd,
  DNF/RPM, NetworkManager, firewalld/nftables, enforcing SELinux, Podman, chrony, LVM/XFS,
  and QEMU/KVM/libvirt. Rocky's own
  [libvirt guide](https://docs.rockylinux.org/10/guides/virtualization/libvirt-rocky/)
  covers Rocky 9 and 10 and uses NetworkManager bridge configuration.
- Current [Rocky 10.2 release notes](https://docs.rockylinux.org/10/release_notes/10_2/)
  show a current enterprise kernel and administration toolchain rather than a legacy training
  target. Rocky 10.2 also matches the OS the project already booted and replay-tested locally.
- Native enforcing SELinux matters. “Create and enforce MAC using SELinux” is an explicit LFCS
  competency, so a default where SELinux is real and ordinary avoids simulations or a special
  one-off security image.
- The overlap with RHCSA and enterprise Linux employment is useful, but it does not define the
  curriculum. The Linux Foundation snapshot, not Red Hat objectives, controls which scenarios
  enter the LFCS collection.

## Alternatives considered

| Distribution | Strengths | Why it is not the default |
| --- | --- | --- |
| openSUSE Leap 16 | Stable, modern, enterprise-related, Zypper, strong YaST/SUSE ecosystem, and new installations use SELinux. The official [Leap 16 announcement](https://en.opensuse.org/Release_announcement_16.0) describes annual 16.x releases through 2031. | Its free maintenance and security window is 24 months, requiring more frequent base-image migrations. It is an excellent second implementation because it exercises different package and network tooling. |
| Debian 13 | Conservative, generic, huge package archive, five-year lifecycle through 2030 according to the [Debian 13 release page](https://www.debian.org/releases/stable/). | AppArmor is the conventional default MAC path. SELinux can be installed, but making it central would be less representative of an ordinary Debian server. Its default server networking choices also exercise less of the NetworkManager/firewalld stack used by several objectives. |
| Fedora | Very current kernel, SELinux, NetworkManager, firewalld, Podman, and libvirt. | Fedora publishes a release about every six months and maintains one for roughly 13 months in its [release lifecycle](https://www.fedoraproject.org/wiki/User:Jkurik/Fedora_Release_Life_Cycle). That is excessive churn for pinned teaching fixtures. |
| AlmaLinux 10 | Nearly the same technical fit and lifecycle class as Rocky. | It offers no material advantage for this project's already verified Rocky image. Supporting it later should be inexpensive if demand exists. |

## Guardrails against turning this into a vendor exam lab

- Scenario objectives always reference `lfcs-2026-08` first.
- User-facing tasks say what state is required; they do not prescribe `nmcli`, `dnf`, or another
  particular command unless command familiarity is itself being exercised.
- Checkers inspect kernel, service, protocol, filesystem, identity, and persistence state before
  inspecting a vendor-specific configuration file.
- Distribution-specific sources establish how Rocky implements a requirement; upstream docs and
  man pages establish the underlying Linux behavior.
- RHCSA mappings are additional metadata. They can never make an out-of-scope task count as LFCS.

## Image update policy

Minor-release drift is intentional, reviewed work. A new image ID requires an image checksum,
setup replay, initial-failure proof, reference-solution replay, alternate-solution replay, reset
replay, and reboot replay for persistent scenarios. Existing verified images remain available
until their scenario versions are migrated.

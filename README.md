# Linux Admin Lab

LFCS practice on disposable Rocky Linux VMs, with automatic grading.
RHCSA is not implemented.

Requires Linux, Python 3.12+, [uv](https://docs.astral.sh/uv/), KVM,
QEMU/system libvirt access, virt-manager, virt-install, xorriso, and OpenSSH.
The Python bindings need libvirt development headers, pkg-config, and a C compiler.

The first run downloads a roughly 10 GB ISO and builds the image.

```sh
./lab
```

Open
<http://127.0.0.1:8787/scenarios/topic/lfcs>, choose a scenario, and click
**Run**. Connect through virt-manager or the displayed SSH command.

Closing the website leaves VMs running. Remove them through the site, or:

```sh
uv run labctl scenario list
uv run labctl scenario destroy SESSION_UUID
```

Diagnostics: `uv run labctl doctor`. CLI: `uv run labctl --help`.

[Curriculum](curricula/lfcs-2026-08.yaml) · [Scenario examples](scenarios) ·
[Tests](tests)

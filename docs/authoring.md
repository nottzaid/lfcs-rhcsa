# Writing a scenario

A scenario is only as good as what it teaches and how honestly it grades. This guide is
the standard every released scenario meets. Each rule exists because an earlier version
of this collection broke it.

## What a scenario is made of

```text
scenarios/<id>.yaml                   manifest: story, requirements, hints, debrief, checks
scenarios/actions/<id>.setup.yaml     host-owned actions that build the broken starting state
scenarios/actions/<id>.solution.yaml  the reference fix
scenarios/actions/<id>.alternate.yaml a different valid fix, the way another learner would do it
scenarios/actions/<id>.rejected-*.yaml near-miss fixes that must NOT be accepted
```

## The learner-facing text

**`task`** describes the situation the learner walks into, in Markdown. For a
`troubleshoot` or `fix` scenario it states the symptom and who is affected. It never names
the cause or the remedy. "Clients on node2 get connection refused on port 9090" teaches
diagnosis; "make the service listen on all interfaces" only teaches typing.

**`requirements`** are the acceptance criteria, one observable outcome or constraint per
line. They may name the end state precisely: user IDs, paths, ports, names the rest of an
organization depends on. They do not dictate method unless the method is the competency
("use an autofs map, not an fstab entry").

Every requirement is graded by at least one check, and no check grades anything the
requirements do not state. A check that needs a NetworkManager profile called `lal-bond`
when the task never mentions that name makes the scenario unpassable for anyone who did
not read the solution.

**`hints`** go from where to look, to what is wrong, to which mechanism fixes it. Two to
four is right. Hints name tools and manual pages; they do not paste the solution.

**`debrief`** opens after the learner solves the scenario, or on request. It covers:

- what was broken and how it showed up;
- how to find it, including the commands that reveal it;
- a clean fix and why it works;
- how to verify it yourself;
- the tempting shortcuts that are wrong, and why.

**`summary`** is one line in the catalog. For a troubleshooting scenario it describes the
symptom, never the diagnosis.

Quote real messages, exactly as Rocky Linux prints them: run the setup on a guest and copy
the journal line, the `curl` error, the AVC. A debrief that paraphrases an error teaches
the learner to look for something that never appears. Cite only manual pages the lab image
has; the live boot test checks every cited page against a real guest, and anything else is
an upstream link.

The contract tests enforce the shape: requirements, two to four hints, a debrief with
`## What was wrong` (for `troubleshoot` and `fix` scenarios), `## Check it yourself`, and
`## Tempting but wrong`, at least one rejected solution, and objective titles exactly as
the curriculum words them.

A **capstone** (`difficulty: exam`) is one incident whose causes cross domains, not a list
of unrelated chores: a full disk that also hides a missing fstab entry, a rebuilt host whose
job trips over a stale automount, a mistyped tmpfs, and a drifting clock. Let the learner
meet the faults in the order a real investigation would.

Never leak the platform into the story. There is no "lab controller" in a scenario, and a
single-machine scenario does not tell the learner not to reboot a `node1` that does not
exist.

## An honest starting state

The setup must genuinely produce the symptom the task describes. If the task says a
filesystem cannot create files, setup fills its inodes until creating a file fails with
`No space left on device`. If the task says a filesystem is damaged, setup damages it in a
way `fsck` has to repair, not by flipping one superblock flag. If the task is about trusting
a repository, the repository is signed and the lesson is how to trust it, never how to turn
signature checking off.

Setup actions are host-owned and run over SSH before the learner connects. They must be
safe to rerun on a fresh machine and must not depend on the internet.

## Checks

Checks run from the host over SSH with the lab's own account. A learner with root can
tamper with that, but a learner practising in good faith should always get a truthful
verdict.

1. **Grade behavior first.** Ask the service (`curl`), try the access (`runuser -u alice --
   test -w`), trace the path (`ip route get`, a connection from another machine). A
   behavioral check accepts every valid method at once.
2. **Grade configuration only when the requirement is about configuration**, and then read
   it with the tool that interprets it: `findmnt --fstab`, `systemctl show`, `sshd -T`,
   `nmcli -g`, `systemd-analyze cat-config`. Never `grep` for the exact text the reference
   solution wrote. `net.ipv4.ip_forward=1` and `net.ipv4.ip_forward = 1` are the same
   setting.
3. **Grade persistence by rebooting.** Set `persistence.reboot` and list the hosts. When the
   live state passes, the learner's check reboots them and grades again. Do not add
   configuration-text checks to stand in for a reboot.
4. **Grade the negative requirements.** "Keep other forwarded traffic dropped" needs a check
   that tries forbidden traffic and expects it to fail. "Keep SELinux enforcing" needs
   `getenforce`.
5. **Explain failure.** Script checks run with the host-owned helper library in
   `src/sysadmin_lab/adapters/check_library.sh`. On failure, end with `fail "<what is
   wrong>"` or `expect_eq`, so the learner reads "UID is 4301; expected 4201" instead of
   "exit status 1". A `service` check quotes a failed unit's own last log line by itself.
6. **Grade from the machine that would notice.** In a multi-machine scenario, request the
   page from the client, open the forbidden port from the partner, and log in from the
   runner. A check on the server alone cannot see a firewall or a route.
7. **Give checks realistic time.** A check has ten seconds unless it asks for more.
   `semanage` takes 12 to 18 seconds on a cold cache right after a boot, hashing hundreds
   of megabytes takes longer still, and some state converges only after a moment: IPv6
   duplicate address detection, a bridge's STP delay, libvirt autostart. Wrap those in
   `retry`, and set `timeout_seconds`. Actions get 120 seconds by default; a busy host is
   slower than a developer's idle one.
8. **Prove "never" with an identifier the kernel does not reuse.** The boot ID proves no
   reboot. A mount ID does not prove no remount: Linux hands a freed mount ID to the next
   mount, so an unmount and remount often get the same number back. An ext4 superblock's
   `Last mount time` (`tune2fs -l`) changes on every mount and never on an online resize.

Check `description`s are shown next to every result. Write them as the requirement they
grade.

## Solutions

The replay harness proves the contract on real virtual machines:

- the fresh state fails, with no checker errors;
- the reference solution passes, and still passes after the required reboot;
- a reset rebuilds the failing state;
- every **alternate** solution passes. Alternates are written as a different learner
  would work: different tools, profile names, file names, formatting, order;
- every **rejected** solution is not accepted. Rejected solutions are the tempting mistakes
  the scenario exists to teach against: a runtime-only change where persistence is
  required, `setenforce 0`, a firewall opened wider than asked, a fix applied to the wrong
  host. If a plausible mistake would pass, the checks are wrong.

Run one scenario's contract with:

```bash
uv run labctl scenario verify SCENARIO_ID
```

While writing a scenario, iterate on a live session instead: `labctl scenario start`, try
the setup's symptom and each solution by hand, and `labctl scenario check` after each one.
Verification replays everything from scratch and takes ten to thirty minutes per scenario.

## Networks

Hosts can join isolated scenario networks: plain layer-2 segments with no host address, no
DHCP, and no route anywhere else. Each NIC is named in the manifest, and the guest sees it
under that name on every boot. NetworkManager creates no automatic profile for it, so
addressing is entirely the scenario's or the learner's.

```yaml
topology:
  networks:
    - {name: lan, cidr: 10.70.0.0/24}
  hosts:
    - {name: router, image: rocky-10.2-lab-v2, nics: [{network: lan, name: lan0}]}
```

The management NIC (`enp1s0`) on libvirt's default network carries SSH for the learner and
the checker. Scenarios must not ask the learner to change it.

## Exam tasks

Exam rehearsals hold tasks that appear nowhere on the practice path, so a learner meets each
one for the first time, as on the exam. An exam task is a scenario with
`collection: exam`, listed in exactly one mock of `kind: exam`. It differs from a practice
scenario in a few deliberate ways:

- **The task says exactly what to do, and where**, like the exam's: "On node2, create a
  logical volume `data` of 600 MiB in a new volume group `vgapp` on `/dev/vdb`, format it
  XFS, and mount it at `/srv/app` persistently." It names every name, size, and path the
  checks grade. It never names the command to use, because any valid method counts.
- **No hints.** The exam has none. The debrief is where the teaching happens, so it has
  three sections: `## One way to do it`, `## Check it yourself`, and `## Tempting but wrong`.
- **The setup provides only what the task needs**: a disk, a server, a repository. It does
  not hide a fault unless the task says to fix something.
- Everything else holds: requirements, behavioral checks, persistence proven by reboot, and
  at least one rejected solution that a careless answer would match.

Each exam mock has twenty tasks at the published domain weights, and the exam mocks
together ask for every competency. The contract tests enforce both.

# DEV-23 Sanctuary CPU pool baseline and rollout gate

Status: runner code prepared on `chore/DEV-23-sanctuary-cpu-pools`; production allocation and soak remain pending.

## Topology and allocation

`lscpu -e` on Sanctuary reports one socket, eight physical cores, and sixteen SMT threads. The sibling pairs are `0/8`, `1/9`, `2/10`, `3/11`, `4/12`, `5/13`, `6/14`, and `7/15`.

| Pool | Complete core pairs | Logical CPUs | Purpose |
| --- | --- | --- | --- |
| Host/daemons | `0/8` | 2 | Reserved from the explicit WoW and CI pins to leave host/daemon scheduling capacity; other host services remain schedulable across the machine. |
| WoW production | `1/9`, `2/10`, `3/11` | 6 | Three physical cores for the current ~3.3-CPU worldserver workload; canary health must prove this is sufficient. |
| CI VM slot 1 | `4/12`, `5/13` | 4 | One 4-vCPU ephemeral runner. |
| CI VM slot 2 | `6/14`, `7/15` | 4 | A second disjoint 4-vCPU ephemeral runner when memory allows. |

The VM CPU pins and WoW pin must be deployed as one drained change. The runner helper refuses launches while WoW is unrestricted or an existing runner lacks a verified pin. This trades temporary availability for an explicit no-overlap guarantee during rollout.

## Read-only baseline, 2026-09-22 21:13 UTC

- WoW `tuinstra-realm-world`: running, no healthcheck, zero restarts, unrestricted CPU set; CPU 330.11%, memory 7.788 GiB, 15 processes. TCP 8085 and 3724 listening.
- Host: 32,013 MiB total, 12,843 MiB `MemAvailable`; projected after one 6,144-MiB VM leaves 6,699 MiB, above the 4,096-MiB reserve. A second VM would leave 555 MiB and is correctly rejected.
- CPU pressure: `some avg10=1.00`, `full avg10=0.00`; the WoW process affinity was `0-15`; memory pressure `some avg10=0.00`, `full avg10=0.00`. A brief `mpstat -P ALL 1 2` sample showed 73.70% aggregate idle and 0% steal; it is not a soak or production SLO.
- `/var/lib/ci-runner/overlay` resolves to the main NVMe root, with 70 GiB available. The prior checked-in minimum was 140 GiB. A privileged read on 2026-09-23 confirmed the live manager minimum was 40 GiB. The operator selected 60 GiB as the new minimum, tightening the live guard. Ansible permits only the reviewed 40-to-60-GiB transition (or an already-applied 60-GiB policy) and refuses deployment if the main NVMe has less than 60 GiB available.
- Tracker PR #261's successful CI attempt took about 12 minutes, with a five-second frontend verdict delayed several minutes by host capacity. This is prior evidence, not a matched before/after soak.

## Storage and memory inventory, 2026-09-23

At 04:59 UTC, the 217-GiB NVMe root held 138 GiB used and 70 GiB available; ext4 also had about 10 GiB reserved from unprivileged use. The manager's `shutil.disk_usage(...).free`, Linux `statvfs.f_bavail`, and `df Available` were checked against each other and returned the same 74,418,237,440-byte figure at one sample; the ext4 reserve is excluded from admission. The runner overlay is on this root. A 60-GiB launch threshold leaves only about 10 GiB above the gate at the current idle snapshot, whereas each qcow2 guest has a 120-GiB virtual maximum. No historical overlay high-water or hard per-VM host-space bound has been established, so 60 GiB is an operator-selected policy value, not yet a proven production-safe reserve. A representative CI soak must measure peak overlay growth and protect host free space before rollout. Docker's **current** data root is `/mnt/ssd1000-01/docker/data-root` on a separate 1-TB SSD; pruning that engine's reported 45.95 GiB build cache would not free runner capacity on the NVMe.

Privileged read-only `du` accounts for the full 138 GiB: `/var` 77 GiB, `/home` 37 GiB, `/tmp` 8.4 GiB, and `/usr` 5.7 GiB. The previously unexplained 51 GiB is `/var/lib/docker` on the NVMe. The current Docker engine uses the SSD path above, but the running `console_prod_agent` bind-mounts `/var/lib/docker` read-only at `/host/var/lib/docker`. Do not assume this directory is disposable: establish its contents, owner, last use, and agent dependency before any cleanup or migration. Other NVMe directories include about 20 GiB of three immutable runner images under `/var/lib/ci-runner/images` (the current symlink targets one of them), 20 GiB of corresponding Packer output directories under `/home/mtuinstra/ci-devops/infra/packer`, 8.4 GiB under `/tmp` (including archived build artifacts and test logs), and 4.8 GiB of `/var/log` (3.8 GiB of systemd journal). These are inventory findings, not deletion authorization.

At the same time, Sanctuary had 31 GiB RAM, 19 GiB used, about 8 GiB free and 12 GiB `MemAvailable`, with no swap. The largest containers were WoW worldserver about 7.9 GiB, realm database about 4.25 GiB, and a game server about 1.78 GiB. Other containers, kernel memory and cache make up the remainder. After a 6-GiB CI VM, the 4-GiB host reserve fits; a second VM generally does not at this workload snapshot. The two CPU slots remain disjoint for times when memory permits two, but normal admission may allow only one.

## Preconditions and controlled canary

1. Confirm the reviewed 60-GiB disk threshold still fits the main NVMe at deploy time; the live 40-GiB threshold must be the only prior value. With approximately 70 GiB currently free, a launch has about 10 GiB of admission margin. Do not prune Docker data or change the overlay filesystem as an unreviewed shortcut. Confirm no active `sanctuary-ci-*` domains or overlays and retain a copy of the previous manager/helper configuration for rollback.
2. Measure a representative runner overlay high-water mark under real heavy CI and define a host-space reserve or hard bound that remains safe with the selected 60-GiB admission gate. The virtual 120-GiB qcow2 maximum means the current ~10-GiB margin cannot itself justify production rollout.
3. Establish and review a durable WoW lifecycle owner or managed reconciler that reapplies the CPU set after recreation and host restart. The current container has no Compose labels; the unversioned host override is not proven to own it. A one-off `docker update` does not satisfy persistence.
4. Define an agreed WoW health threshold from existing service metrics before changing its CPU allocation. Record a comparable baseline under a concurrent WoW workload and trusted-heavy CI job (using `ci-cpu-pools` for credential-free per-pool busy/steal samples and the manager audit log for sanitized admission outcomes): queue duration, VM launch latency, per-pool `mpstat` use, CPU pressure, steal/throttle indicators, memory/disk admission, and WoW health.
5. Drain CI, activate the persistent WoW pin, verify the effective container CPU set, then deploy the runner manager/helper/config. Launch one trusted-heavy canary and verify its libvirt `<vcpu cpuset>` and four vCPUs. Only then allow a second canary if memory and disk gates pass. Check that CPU sets do not intersect and required GitHub checks remain intact.
6. Recreate the WoW container through its actual lifecycle owner and reboot only in an agreed maintenance window; verify the pin persists, service endpoints return, and CI can launch. Repeat the same measurements. Stop and roll back on WoW health regression, unexpected CPU pressure, or failed CI isolation.

## Rollback drill

Drain CI and stop new dispatch. Restore the prior manager, helper, and manager TOML from the captured prechange versions; restart the manager and helper socket. Restore the prior WoW lifecycle configuration and recreate the container, or disable the managed pin and clear the CPU set using the reviewed prior configuration. Verify WoW process state, ports 8085/3724, player/service health, and a trusted-heavy runner launch with the prior policy. Record the exact commands and observations in DEV-23 before marking the Story Delivered.

No cache, user files, or production configuration was changed while preparing this evidence.

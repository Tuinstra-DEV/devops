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
- `/var/lib/ci-runner/overlay` resolves to the main NVMe root, with 70 GiB available. The checked-in minimum is 140 GiB. The live manager threshold remains unverified because `/etc/ci-runner/manager.toml` needs interactive sudo. No lower disk threshold is proposed.
- Tracker PR #261's successful CI attempt took about 12 minutes, with a five-second frontend verdict delayed several minutes by host capacity. This is prior evidence, not a matched before/after soak.

## Preconditions and controlled canary

1. Confirm the live manager's non-secret capacity settings and resolve the 70-versus-140-GiB discrepancy without weakening the NVMe storage guard. Confirm no active `sanctuary-ci-*` domains or overlays and retain a copy of the previous manager/helper configuration for rollback.
2. Establish and review a durable WoW lifecycle owner or managed reconciler that reapplies the CPU set after recreation and host restart. The current container has no Compose labels; the unversioned host override is not proven to own it. A one-off `docker update` does not satisfy persistence.
3. Define an agreed WoW health threshold from existing service metrics before changing its CPU allocation. Record a comparable baseline under a concurrent WoW workload and trusted-heavy CI job (using `ci-cpu-pools` for credential-free per-pool busy/steal samples and the manager audit log for sanitized admission outcomes): queue duration, VM launch latency, per-pool `mpstat` use, CPU pressure, steal/throttle indicators, memory/disk admission, and WoW health.
4. Drain CI, activate the persistent WoW pin, verify the effective container CPU set, then deploy the runner manager/helper/config. Launch one trusted-heavy canary and verify its libvirt `<vcpu cpuset>` and four vCPUs. Only then allow a second canary if memory and disk gates pass. Check that CPU sets do not intersect and required GitHub checks remain intact.
5. Recreate the WoW container through its actual lifecycle owner and reboot only in an agreed maintenance window; verify the pin persists, service endpoints return, and CI can launch. Repeat the same measurements. Stop and roll back on WoW health regression, unexpected CPU pressure, or failed CI isolation.

## Rollback drill

Drain CI and stop new dispatch. Restore the prior manager, helper, and manager TOML from the captured prechange versions; restart the manager and helper socket. Restore the prior WoW lifecycle configuration and recreate the container, or disable the managed pin and clear the CPU set using the reviewed prior configuration. Verify WoW process state, ports 8085/3724, player/service health, and a trusted-heavy runner launch with the prior policy. Record the exact commands and observations in DEV-23 before marking the Story Delivered.

No production configuration was changed while preparing this evidence.

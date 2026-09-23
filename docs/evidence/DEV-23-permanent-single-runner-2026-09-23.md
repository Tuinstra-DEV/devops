# DEV-23 permanent single-runner decision and evidence

Marcel selected one long-running `trusted-heavy` runner as the permanent
Sanctuary CI capacity. The failed two-runner / WoW CPU-pin canary was rolled
back after reported ~1-second game click delay. The approved live profile is
one ephemeral 4-vCPU, 6,144-MiB VM, a 4,096-MiB host memory reserve, a 60-GiB
minimum free-space gate on the main NVMe, and WoW on CPU set `0-15`. No
dedicated CPU pools are claimed. The runner manager and root helper both
enforce the one-VM limit; global host load alone is not an admission gate in
the repository policy.

The operator installed the live one-runner policy after draining CI and
recorded the root-owned backup at
`/var/backups/dev23-single-runner-20260923T164122Z`. The WoW CPU-pin timer
remains disabled. This repository change makes the Ansible role and source
contract agree with that permanent profile and removes the pin reconciler on
the next drained role application.

## Observed production behavior

- Tracker PR #263 head `ab9cc46` passed source-policy, gitleaks,
  frontend-static, frontend-browser, browser-integration, backend, and the
  frontend aggregate check on sequential ephemeral runners. The final
  four-second aggregate check waited about 19 minutes 38 seconds for its
  turn. GitHub Actions scheduling can assign an older queued job to a newly
  registered JIT runner; the manager verifies that handoff before cleanup.
- With 2,000 WoW playerbots online and one CI VM running, WoW logged update
  intervals of 101 and 108 ms at 17:17 and 17:22 UTC. Continued samples at
  17:27, 17:32, 17:38, and 17:45 UTC were 113, 102, 101, and 105 ms. Before
  the bot reduction, repeated intervals were about 750–830 ms, including when
  no CI VM ran. This is a correlation, not a proven in-game latency SLO.
- At 17:24 UTC with one CI VM, host `MemAvailable` was about 8.7 GiB and the
  NVMe had 87 GiB free. The short CPU sample showed roughly 58–61% idle and
  negligible I/O wait. WoW used about 6.4 GiB; MySQL used about 3.7 GiB with
  no new buffer-pool or row-lock waits during the sampled interval.
- The user could not yet retest click latency in the game. No reboot or
  complete long-running CI/WoW soak was performed during this decision.

Use `virsh -c qemu:///system` for VM inventory; plain `virsh` uses the
operator's empty `qemu:///session`. Recheck WoW update intervals, host memory,
NVMe availability, and one-VM admission after the next drained Ansible
application. Keep DEV-23's original CPU-pool proposal as historical evidence,
not a live operating procedure.

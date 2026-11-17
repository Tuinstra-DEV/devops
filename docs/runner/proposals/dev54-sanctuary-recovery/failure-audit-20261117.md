# DEV-54 historical Sanctuary failure audit

Read-only evidence narrows the cleanup path but does not identify the first
fault. No capacity, lifecycle or admission change is justified by this audit.

| UTC, 17 November | PR #181, job 133931380577 | PR #182, job 133931391484 |
| --- | --- | --- |
| Runner job started | 09:30:36 | 09:31:23 |
| Assignment verified by manager | 09:31:29 | 09:31:33 |
| Flutter post-cache started | 09:53:38 | 09:59:03 |
| Manager began local cleanup | 09:54:13.992 | 10:00:38.246 |
| GitHub reported failed job completion | 10:03:40 | 10:09:32 |

The cleanup records precede the failed GitHub completion timestamps. Subsequent
GitHub API cleanup errors followed local cleanup; those records do not explain
what first changed the VM's state. The two-hour lease bound cannot explain
cleanup after roughly 24 and 30 minutes. The operator's protected QEMU-log
check returned signal-15 termination/shutdown only, with no recorded host OOM.
The readable libvirtd journal had 30 entries in the bounded 09:25–10:15 window
and no matched storage-full, I/O-error, paused, shutdown or OOM category. These
negative observations are not proof that the guest or backing storage was
healthy; the initiating event may not have been recorded there.

## Exact deployed source examined

- Manager SHA-256: `5404bbbf3d4430f4518afa37c19d7be090d1246c3b5d7cc4580ffb601f999ed3`.
- Helper SHA-256: `fef9343a46c4a67ddf1e12db43aa65c2bbb5256e35bbdac14c9cb8a9ad3f1613`.

The helper's `list_leases()` propagates a failed or timed-out `virsh domstate`
as `HostCommandError`. That aborts inventory rather than silently converting
a transient command failure to a missing domain. A successful non-running
state, including `paused`, is returned verbatim. The manager's `reconcile()`
only considers a domain healthy when its state and recorded phase are both
`running`; its non-healthy cleanup invokes `virsh destroy`.

Launch and reconciliation share a lock, so normal provisioning does not expose
an in-flight provisioning phase to reconciliation. Verified-assignment updates
preserve the phase, and completed dispatch handoffs affect history/cleanup
obligations rather than the lease phase. Assigned-job completion checks require
the exact actual job/run/runner identity and completed status. GitHub's recorded
completion times are later than local cleanup in these cases.

A successfully observed non-running domain is therefore the strongest remaining
code-path explanation. Signal 15 is consistent with destroying a still-existing
paused or otherwise non-running VM. **A storage-induced pause remains a
hypothesis, not a demonstrated root cause.** Neither logs nor source establish
which non-running state was first observed.

## New evidence and next observation

[PR #182 attempt 3](https://github.com/Tuinstra-DEV/sudoku-tessera/actions/runs/43950356975/attempts/3)
passed the complete existing job, including the cache phase. Its 701 libvirt
samples showed no I/O fault; minimum observed backing-volume free space was
2,929,967,104 bytes. This does not prove the earlier issue was repaired.

The single [new caller validation](https://github.com/Tuinstra-DEV/sudoku-tessera/actions/runs/44179767254)
failed on the existing Sanctuary route. Job 134383118242 started at 22:10:26 UTC
after 40:13 created-to-start delay. Its adapter received a shutdown signal at
22:36:48 and exited 143; the job completed failed at 22:37:14. Quality correctly
rejected the missing trusted proof. The run consumed two rounded hosted job
minutes, had 26:48 trusted-job runtime, and 71:15 total elapsed time. No hosted
Flutter fallback or additional rerun was used.

The read-only observer retained 778 follow-up libvirt samples and 1,310
one-second backing-volume samples. Minimum observed free space was 904,007,680
bytes. No paused-I/O or storage-full event was observed. The first non-running
sample was a normal shutoff (`state.reason=1`) at 22:37:17.557 UTC. The manager
began cleanup at 22:37:49, after the guest and job had already stopped. For
this new failure, manager cleanup was therefore subsequent to the initiating
shutdown. Sampling does not establish every guest event between observations.

The follow-up VM and storage JSONL evidence is retained locally with SHA-256
`94c4058a55a00bbab48b468b6adfddda69d6147416c71e0409ed798f13d00073`
and `eb618350dc48551c439f0dd05f20802735644e449d837732b6ad2ff6b16d3b10`.
No raw QEMU command lines, credentials or manager configuration contents were
read or included in this evidence.

Read-only inspection of image `66eded13…b82bc4` confirms that
`ci-runner-job.service` has `RuntimeMaxSec=7200`, disables runner auto-update,
and invokes `systemctl poweroff` from `ExecStopPost`. Persistent randomized
APT maintenance timers and unattended upgrades are enabled. The installed
needrestart apt hook can automatically restart services and does not exclude
the runner unit. An upgrade-induced service restart is a concrete plausible
mechanism for SIGTERM followed by guest poweroff, but the destroyed guest's
APT/journal records are unavailable, so it is not a proven historical cause.
The readable host journal for 22:34–22:38 had no corresponding visible host
APT/libvirt maintenance event; protected records were not read without sudo.
This mechanism is being tested separately in an unregistered disposable VM.

The observer supports one separate `--phase followup` window after a long
queue. Both windows have the same 45-minute maximum and exclusive output-file
creation; existing samples cannot be overwritten. Seven targeted tests include
the regression that preserves the primary samples and rejects overwriting a
follow-up file. A rerun is not initiated by the observer.

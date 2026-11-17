# DEV-54 Sanctuary runner failure diagnostics

The hosted Tessera bypass was withdrawn after the owner's minute-cost review.
Tessera run 44159645801 was cancelled, with 20:51 Flutter runtime (21 rounded
hosted minutes) plus one backend-security minute. Its adapter completed tests,
web and APK builds, but the workflow was cancelled during job cleanup and is
not a passing proof. Provider PR #221 and caller PR #183 were closed unmerged;
the original Gate pilot PRs #220/#444 remain untouched.

Two parallel tasks continue under DEV-54: conservative Tessera CI selection
and the original Gate image/cost pilot. Permanent Sanctuary repair first needs
evidence from the protected QEMU logs for failed jobs 133931380577 and
133931391484. GitHub reports communication loss during post-job Flutter cache
work. Manager logs show cleanup while the GitHub jobs were still busy; that
message alone does **not** identify why a VM was no longer considered healthy.
In particular, paused I/O, stopped/missing domain and other cleanup predicates
must not be conflated without the protected evidence.

## Read-only operator command

The existing admin cannot read the QEMU logs and noninteractive sudo requires
authentication. No passwordless privilege or alternate access is introduced.
Run the wrapper in the existing operator's Terminal:

```sh
bash "/Users/marceltuinstra/Documents/Codex/2026-11-15/new-chat-3/work/ci-rollout/devops-tessera-hosted/docs/runner/proposals/dev54-sanctuary-recovery/run-operator-diagnostics.sh"
```

For the current session a shorter launcher avoids copying wrapped filenames:

```sh
bash /tmp/dev54-check.sh
```

The wrapper stages `diagnose.py` under
`/home/mtuinstra/dev54-runner-diagnostics-20261117/` and runs it with existing
sudo authentication through SSH to `sanctuary.tuinstra.dev`. The Python process
uses isolated mode and standard libraries only. It never restarts services,
changes admission/configuration, reads token files or prints logs/config values.

The checker verifies the known manager/helper hashes before inspecting files,
extracts public helper constants without executing helper code, reads only the
two fixed QEMU-log tails, reports allowlisted fault categories/signals, checks
for host OOM in the fixed failure window, and verifies unchanged policy/code.
The effective configuration is parsed only in memory for two boolean checks;
neither configuration contents nor its digest are returned.

Return only the last JSON line. Never return passwords, tokens, raw QEMU logs,
service environment or `manager.toml` contents. Missing/unreadable files or
changed code are blockers. `diagnostics_verified=true` verifies the read, not a
repair or a particular root cause; fault categories determine the next bounded
fix. The old and new source image, runner group and service config remain
unchanged by this diagnostic.

Six targeted tests cover fault classification with secret sentinels, AST
inspection without executing helper code, stopping before file reads when
operator privilege is missing, distinct paused-I/O/disk-full states and
allowlisted numeric VM statistics. Shell syntax and diff checks pass locally.

## Current evidence and one instrumented canary

The operator result is retained in `operator-result-20261117.json`. The protected
logs show only signal-15 termination/shutdown, no reported host OOM, no short
lease timeout, and unchanged policy. They do not prove disk exhaustion or
another underlying cause, so no capacity or lifecycle change is made on that
evidence alone.

PR #182 run `43950356975`, attempt 3, job `134333682935` is a single diagnostic
repeat on the unchanged Sanctuary route/source. A bounded unprivileged
`watch-vm.py` process queries the existing libvirt API every two seconds for
that domain only: state/reason, block error and numeric allocation/memory
metrics. This introduces no new root privilege and changes no guest, service,
admission, disk or configuration. Samples remain under the existing admin's
private `dev54-vm-watch-134333682935` directory. The monitor stops after the
domain departs or 45 minutes. It prints only allowlisted metadata, not QEMU
command lines, credentials, log text or arbitrary paths. This canary is for
diagnosis, not repeated blind rerunning or a claim of infrastructure repair.

The existing administrative account can also read filesystem **metadata**
through the known bounded-volume path without reading its contents. A second
`--storage-only` watcher records the 24-GiB backing filesystem's free bytes
once per second. It rejects a changed/unmounted filesystem and never changes
ownership, permissions, mounts or data. This complements the 120-GiB virtual
disk capacity exposed by QEMU; virtual capacity is not the physical backing
budget. Storage and libvirt observations remain separate evidence streams.

## Canary result, 17 November

[Attempt 3](https://github.com/Tuinstra-DEV/sudoku-tessera/actions/runs/43950356975/attempts/3)
completed successfully on the unchanged source and existing Sanctuary policy.
Job `134333682935` ran from 20:11:35 to 20:34:10 UTC (22:35). The fixed adapter
passed at 20:30:46; the Flutter post-cache step passed from 20:30:48 to 20:34:04.
`quality` passed at 20:34:16. This is complete successful verification, including
cleanup, unlike the cancelled hosted experiment.

The 701 libvirt samples reported running followed by normal shutoff/removal,
with no paused-I/O or storage-full fault. Maximum observed disk physical size
was 22,356,230,144 bytes. The separate bounded-volume samples recorded a minimum
of 2,929,967,104 free bytes (about 2.73 GiB). Thus this attempt does not reproduce
the previous failure and does not establish disk exhaustion as its cause.
Cached inputs, concurrency and historical volume contents can differ between
attempts; a passing rerun is not evidence that an infrastructure defect was
repaired. No live repair, capacity increase, permission, selector, manager or
runner-group change was performed. The old signal-15 logs remain insufficient
to identify the cause of the two earlier failures.

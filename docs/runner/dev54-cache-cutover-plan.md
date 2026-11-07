# DEV-54 dispatch isolation, cache repair and coordinated cutover — planned only

No host, runner-group or consumer cutover is performed by this change. Existing
consumers continue using `ea86c62eeac615cb292a78bf50995fed81c051ee` until a reviewed
operator transaction and the corresponding consumer commits are ready.

## Evidence and bounded repair

Tessera run `41991411533`, attempt 1, lost runner `1047`
(`sanctuary-127124878858`) after the fixed adapter succeeded at 09:37:41 UTC on
5 November 2026. Gradle's cache-save post-step started at 09:37:42; GitHub marked
the job failed at 09:47:01. Attempt 2 on runner `1057` completed successfully,
including both Gradle and Flutter/pub post-steps and the final route proof.
The operator observed about 22.4 GiB allocated inside the 24 GiB heavy reservation
during that post-step, despite the guest's 120 GiB virtual disk capacity.

The workflow marker only writes `verified=true`. The adapter has no shutdown
command. The guest service powers off after the runner exits; its runtime limit
and the host lease limit are both 7200 seconds. The manager's generic
"completed or missing domain" message also covers non-running states such as
paused, so it does not prove that the domain was already absent. ENOSPC while
building a cache archive is consistent with the evidence; no first-attempt
guest/kernel trace proves the exact initiating error.

The repair changes only trusted Tessera: Gradle uses the exact-key restore-only
action, and Flutter SDK/pub caches are explicitly disabled. Hosted Tessera keeps
its caches. Tool versions, checks, route proofs, authority, resource reservations
and all other consumers' cache behavior remain unchanged. Cold SDK/pub downloads
may cost additional Sanctuary time. No archive-save job or larger reservation is
introduced.

## Role-evidence 403 and dispatch isolation

On 5 November 2026, the operator traced repeated dispatch failures from about
11:14 UTC to Tracker PR #387, run `42017356244`. Its isolated metadata role reached
`_protected_branch_sha` and received HTTP 403 from the default-branch endpoint.
The uncaught discovery exception aborted the merged queue before any repository
could receive a VM. Marcel cancelled that run; this change does not rerun it.

The manager now rejects only the failed role job, or the failed run when its
listing/detail cannot be read. A failure listing candidates for one
repository/route also leaves the other repositories/routes available. Logs expose
only fixed stage, configured repo/role/profile, numeric IDs, exception class and
HTTP status, never the remote body or exception message. Regression fixtures cover
403, malformed evidence, a later job/run in the same repo, other repositories and
routes, and malformed candidate identities. `RateLimited` still propagates to the
existing backoff. Global config/group validation remains mandatory, and the catch
does not cover JIT allocation, durable claims, guest launch or cleanup failures.

The credential documentation previously listed Actions and Pull requests read
but omitted **Contents: read**, which GitHub requires for
[Get a branch](https://docs.github.com/en/rest/branches/branches#get-a-branch).
The role path reads repository metadata before the failing branch request;
successful metadata/Actions reads with a denied branch read are consistent with
missing Contents permission. This is an inference from the code and operator
trace, not an inspection of the protected host credential. Its token type, actual
repository selection and granted permissions have not been independently proven.
Runner-group selection and `org_execution_roles.repositories` do not grant token
or App installation access.

| Consumer | Isolated-role branch lookup | Consequence |
| --- | --- | --- |
| Tracker at `ea86c62…` | Metadata role, and security producer when opted in | The observed 403 rejects its job until permission or caller routing is resolved. |
| Gate quality at `ea86c62…` | Metadata role | Same endpoint and permission requirement; Tracker's denial does not prove Gate is denied. Its frozen `43ead525…` security caller remains hosted. |
| Notify / Status PRs | Neither is in the fixed Gate/Tracker role allowlist | Normal trusted PR discovery resolves PR evidence without this role branch lookup. Other credential/API failures remain possible and are isolated by the manager. |
| Any normal trusted `schedule` / `workflow_dispatch`, including Notify / Status | No role is needed, but `_resolve_trusted_source` calls `_protected_branch_sha` | The same Contents permission is required for the protected default-branch GET. Hosted PR metadata alone does not resolve this scheduled-route requirement. |

Before the cutover, Marcel must choose either narrowly scoped repository access
or temporarily hosted role preflights. For the credential option, confirm the
fine-grained PAT resource owner/repository selection, or the App installation's
repository selection and token restrictions, then approve **Contents: read** for
the role-enabled Gate/Tracker repos and any consumers using trusted scheduled or
manually dispatched verification if absent. Preserve existing required
Actions/Pull requests/runner permissions; do not grant broader access as a shortcut.
Review the effective repository set: adding Contents permission to a shared
credential may grant it across all repositories selected for that credential.
Do not remove existing consumers from its selection to narrow it. If access must
be restricted to the necessary branch-evidence repos, a separate scoped evidence credential needs
its own reviewed implementation rather than an implicit permission expansion.
Check with the **same protected host credential**, privately: default/base branch
GETs must succeed and return protected branches for each admitted role repo.
Claude's local `gh` credential succeeding is not evidence for the host credential.
Record only repo, status and required permission conclusion. Follow GitHub's
[authentication and rate-limit troubleshooting](https://docs.github.com/en/rest/using-the-rest-api/troubleshooting-the-rest-api):
403 can also be rate limiting, so inspect only the allowlisted rate-limit and
`X-Accepted-GitHub-Permissions` response headers privately, never a token or body.

Alternatively, a separately reviewed caller change can set
`request-isolated-role-runner: false` for Tracker's PR metadata calls so they use
the existing hosted preflight. An opted-in security-producer caller needs the same
decision; leaving it isolated still needs branch access. This scheduling option
must retain all existing trust/proof and required-check validation, and does not
authorize unverified jobs or change the heavy verification route. No credential
access, caller workaround, host file or organization setting is changed here.

## One controlled drain for #205 and #206 — replacement operator plan

This section supersedes the former request for a naturally idle window. Preparation
only: no host, runner group, credential, consumer or deployment is changed here.
The implementation PR is stacked on #206; it must be reviewed together with #206.
The standalone operator script is `scripts/runner-controlled-cutover.py`, also
staged at `/private/tmp/runner-controlled-cutover.py`, SHA-256
`5574d262572eb5120a3cf0c416f22961856084abf8e8b961eb6e0d413baa298d`.

### Fixed versions and reconciliation

The new shared workflow pin is **`288dc75d5f07819cc2c41ab456140c3ee14de369`**,
the reviewed #206 head, rather than an unspecified future merge SHA. Keep that
commit reachable; merge #206 with its history preserved. If #206's executable
sources change during review, stop and regenerate this transaction and its hashes.
A documentation-only merge commit does not change the chosen workflow identity.

| Component | Current, read-only verified on 7 November | Combined target from `288dc75d` |
| --- | --- | --- |
| Manager | `5404bbbf3d4430f4518afa37c19d7be090d1246c3b5d7cc4580ffb601f999ed3` | `aab54e656e1acbd261d3f28de16d53ac4a9c500276012c1e9f0897022195c40b` |
| Host helper | `fef9343a46c4a67ddf1e12db43aa65c2bbb5256e35bbdac14c9cb8a9ad3f1613` | `addf16e83af68ecfd99fcc080dfa93f8f04e2c8e57098f091c5688c100be6954` |
| Manager unit | `c99fdf23b9c50971be678565db54e85d5227040ff1108ccb3622a9c2ae4d806d` | Unchanged |

The #205-only playbook's `81aa1f6405f71ebf15740079bbde89c8f4e7d44820110172a6ad1565728591ec`
correctly identifies its historical manager, but **does not match #206's source**.
It will refuse a source checkout at `288dc75d`; after this combined installation
it will also refuse the installed manager. Do not run that playbook before, during
or after this transaction as an extra installation/rollback step. Its standalone
procedure remains historical. This script pins and restores both exact binaries,
combining the approved 60 GiB behavior and discovery isolation in one installation.
The mutable activation tools on #206 already pin `aab54e65…` / `addf16e8…`.
The historical DEV-54 activation script must remain unchanged.

The sudo preflight checks the actual TOML array, not runner-group ordering:

```text
BEFORE trusted, in TOML order:
0 e1a9f09504b903120e9b5297eff64f58a891c907
1 604ad2b9e636798899cb90a7aa8bdfa8d6ad2a4d
2 23c08cf212dd6411ed5076b2ec53a05c1e2e5f3e
3 a8ace2f171d7d127bbfb3a6d6b13f158f461ba98
4 ea86c62eeac615cb292a78bf50995fed81c051ee
metadata role: ea86c62eeac615cb292a78bf50995fed81c051ee
producer role: ea86c62eeac615cb292a78bf50995fed81c051ee

AFTER trusted, in TOML order:
0 ea86c62eeac615cb292a78bf50995fed81c051ee
1 604ad2b9e636798899cb90a7aa8bdfa8d6ad2a4d
2 23c08cf212dd6411ed5076b2ec53a05c1e2e5f3e
3 a8ace2f171d7d127bbfb3a6d6b13f158f461ba98
4 288dc75d5f07819cc2c41ab456140c3ee14de369
metadata role: 288dc75d5f07819cc2c41ab456140c3ee14de369
producer role: 288dc75d5f07819cc2c41ab456140c3ee14de369
```

Both installed validators enforce index 3 = `a8ace2f1…` and the enabled metadata
role matching the last trusted pin. The new order satisfies both. Group 4 remains
exactly **five trusted workflow refs plus one producer workflow ref**; no seventh
entry and no sixth trusted pin, even temporarily. Repository IDs, private-only
selection, all role actors/events/repositories, credentials, capacities, 24/4 GiB
backing allocations, image and unit stay unchanged. Only the three workflow-ref
locations and trusted-array order change in the protected policy. Atomic writes
preserve owner/group/mode and all unrelated bytes.

### Consumers that must move first

Retire **`e1a9f095…`**, keeping the still-used `ea86c62e…` trusted pin. Migrating
Tessera back to `23c08cf2` is not valid: that workflow predates Tessera support.
Keep `604ad2b9…` for WODIQ-102 and `a8ace2f1…` for Gate PR #426.

The fresh, selected-repository inventory at **2026-11-07 21:05:12 UTC** took
172.4 seconds and found these `e1a9f095` callers:

| Repository | Required pre-cutover migration |
| --- | --- |
| `marcel-site` | develop/default and main → `23c08cf212dd6411ed5076b2ec53a05c1e2e5f3e` |
| `tuinstra-site` | develop/default and main → same retained pin |
| `wodiq-site` | develop/default and main → same retained pin |
| `openairco-site` | develop/default and main → same retained pin |
| `notify` | main/default → same retained pin |
| `status` | main/default → same retained pin |
| `wodiq-app` | develop/default → same retained pin |

Reinspect all open PR heads, default/main/develop and nonterminal runs at execution.
Migrate any additional `e1` caller found, including current open-PR heads. Preserve
selector, fixed profile/job key, route proof, required verdict and release order;
verify the affected consumer workflows before merging their migration. The eight fixed CONTRACT blocks/outputs and repository labels for these seven
repositories were executed/compared at `e1a9f095` and `23c08cf2` and match. This preparation
does not claim that any consumer migration PR has run or passed.

Tracker default/develop has since adopted `ea86c62e` (the earlier inventory only
found PR #387). Before changing the metadata role, set the **literal boolean**
`request-isolated-role-runner: false` on every Gate/Tracker trusted call still using
`ea86c62e`, on default/main/develop and open PR heads. This keeps their existing
trusted execution and proof contract while using the existing hosted metadata
preflight. Complete all already queued/running `ea` metadata-role calls. The
collector parses YAML job mappings and distinguishes a real per-job boolean from
a quoted string, comment or another job's option. Missing/dynamic/quoted opt-outs
block cutover. An active run whose immutable source still requests the old role
also blocks it; changing today's branch does not repair yesterday's queued run.

There were no `ea86c62e` producer callers in the inspected branches. If one appears,
migrate it to the still-working hosted producer pin `2ca87c18bddce63379b7a33dc6de5be2f0a1d951`
and finish its old run before retiring that producer role ref. New callers must
not target the unadmitted `288dc75d` until the host/group transaction is applied.
Tessera can remain on admitted `ea` throughout the drain, then adopt the cache
repair at `288dc75d` afterward. The same retained pins keep ordinary queued work
eligible after resume.

Historical completed runs/obsolete branches are not blanket re-enrolled: do not
rerun a retired-pin historical run during or after cutover. Regenerate it from its
migrated source instead. Collectors cover the 13 selected repositories; the earlier
22-repository inventory provides supporting coverage, not a new atomic global lock.
Claude and Marcel must keep other runner-group/config changes and caller edits
frozen between the final collection and resume.

### Drain limits and recent measurement

A fresh GitHub jobs-API measurement covered 449 runs created since 1 November,
482 run attempts and 450 completed Sanctuary jobs, across the selected 13
repositories. Every attempt was re-read, including earlier failed/retried attempts.
Durations use the jobs API started/completed timestamps. Captured 7 November
21:22:03 UTC:

| Longest observed job | Duration | Evidence |
| --- | --- | --- |
| WODIQ API, run `42396069934`, attempt 2 | 2,754 seconds | [job 128490105145](https://github.com/Tuinstra-DEV/wodiq-platform/actions/runs/42396069934/job/128490105145) |
| WODIQ API, run `41911894401`, attempt 3 | 2,610 seconds | [job 126901025593](https://github.com/Tuinstra-DEV/wodiq-platform/actions/runs/41911894401/job/126901025593) |
| WODIQ API, run `42381065046`, attempt 1 | 2,588 seconds | [job 128429884019](https://github.com/Tuinstra-DEV/wodiq-platform/actions/runs/42381065046/job/128429884019) |

`ADMISSION_TIMEOUT_SECONDS=420` bounds acquiring a clean stop boundary: existing
helper mutation timeout 330 seconds plus three 30-second manager poll intervals.
The script acquires the **existing lifecycle flock** and checks the installed
manager's durable dispatch history for any indefinitely quarantined JIT claim.
Every JIT POST creates such a claim under this lock; launch activates it under the
same lock. No outstanding quarantine plus the held lock closes the JIT/stop race.
An unresolved old quarantine, non-running lease or busy helper prevents stopping;
the script neither clears claims nor interrupts provisioning. On this timeout,
service/config/binaries are untouched; keep the new backup and use `continue-drain`
with fresh proof after the cause is resolved.

`DRAIN_TIMEOUT_SECONDS=7500` is a fixed setting, justified by the existing
7,200-second guest/lease limit plus 300 seconds for cleanup. It deliberately covers
more than the observed 2,754-second maximum; it is not a delivery estimate.
After safe admission closure, the manager service runs only a cleanup worker,
with `Restart=no` and the existing service sandbox/credential bindings. Guests
keep running. The worker refuses launch, JIT dispatch, and cleanup of **any**
running, blocked, paused, suspended or unknown-state CI domain. It invokes existing
reconciliation only once every remaining CI domain is already `shut off` or gone.
Completed-domain cleanup removes old definitions/storage/runner registrations;
no running VM is destroyed. Existing guest/host lease timers and job limits remain
in force; the preparation does not extend them or claim to prevent pre-existing
application/runtime failures.

The driver bounds the drain loop and prints only counts/status. Each subprocess
timeout is capped at the lesser of 90 seconds and the remaining shared deadline.
Refusal can lag the configured deadline by one 10-second polling interval; no
sequence of subprocesses receives a new full budget. No loop
restarts the 7,500-second budget without an explicit `continue-drain` action.
At timeout/interruption, config and binaries remain original, admission stays
paused and jobs remain untouched. Claude refreshes BEFORE without PATCHing;
Marcel runs `abort` to resume the original manager, including still-running jobs.
Alternatively the operator can explicitly retry `continue-drain`. No kill,
cancellation, unverified state deletion or forced cleanup is a timeout action.

### Scanner and consumer readiness before moving Gate security

The deployed scanner digest beginning `b20881ca…` does not recognize the `ea86c62e`
caller, and it will not automatically recognize `288dc75d`. Gate's current trusted
PR caller is frozen at `43ead52517b9ab7e3d6f6bb94305be2b98a10fd9`, on
`pull_request` plus the deliberately unsupported `merge_group`. Tracker's current
security producer stays at hosted `2ca87c18…`. Do not replace those with a new SHA
alone and claim Sanctuary scanning works.

Required separate Gate backend/scanner work:

1. Add the exact `reusable-gate-pr-security.yml@288dc75d5f07819cc2c41ab456140c3ee14de369`
   target caller to `CiGateTextScanner::TRUSTED_PR_SECURITY_WORKFLOW_REFS`, preserving
   the existing old callers until their migrations/runs finish. Keep exact trigger,
   concurrency/metadata filter, job keys and permissions checks; add positive and
   altered-ref/shape rejection tests. `ea` needs no new trust grant for this plan.
2. Review Gate's move from the frozen PR caller to the canonical
   `pull_request_target` default-branch controller. Preserve the immutable PR head,
   base/default and execution/run-attempt binding. Do not silently drop required
   merge-group behavior or the existing required nested check. Tracker already uses
   the target route; retain its canonical trigger/metadata filter/permissions.
3. Publish and verify the updated scanner and affected profile/content bindings;
   pin exact new image digests and publication evidence. Update the Gate/Tracker
   producer registration's exact workflow ref/SHA and corresponding scanner binding
   variables through the normal reviewed registration path. `ActionsOidcVerifier`
   checks registration equality; runner-group membership alone grants no publisher
   authority. No wildcard or verifier bypass is part of this migration.
4. After APPLIED, migrate each canonical consumer to `288dc75d`, verify a real
   eligible human PR on `ci-pr-security-producer` / Sanctuary, and verify the
   untrusted/bot fallback and replay/stale-source rejection. The operator transaction
   only admits that fixed role; it does not count as this end-to-end proof.

Resolve/privately verify the existing host credential's protected default/base
Contents-read access as described above. The agent cannot use sudo to inspect
protected files; local gh access is not proof of host credential access. Any genuinely
new credential grant remains Marcel's separate decision. Tracker's published-container
runtime provenance is still absent from source `288dc75d`; it remains a prerequisite
for that consumer feature, not something this host swap implements or proves.

### Exact operator sequence

Review #206 and this stacked preparation PR first. Do not run the separate #205
playbook. No operator command below was executed by the preparation agent.
Use the existing named SSH account and keys; never disable host-key checking.

One-time local staging, in the reviewed preparation checkout:

```sh
cp scripts/runner-controlled-cutover.py /private/tmp/runner-controlled-cutover.py
python3.14 /private/tmp/runner-controlled-cutover.py group-before > /private/tmp/runner-group4-before.json
python3.14 /private/tmp/runner-controlled-cutover.py group-after > /private/tmp/runner-group4-after.json
git show 288dc75d5f07819cc2c41ab456140c3ee14de369:runner/manager/ci_runner_manager.py > /private/tmp/runner-cutover-manager.new
git show 288dc75d5f07819cc2c41ab456140c3ee14de369:runner/host-helper/ci_runner_host_helper.py > /private/tmp/runner-cutover-helper.new
shasum -a 256 /private/tmp/runner-controlled-cutover.py /private/tmp/runner-cutover-manager.new /private/tmp/runner-cutover-helper.new
scp /private/tmp/runner-controlled-cutover.py /private/tmp/runner-cutover-manager.new /private/tmp/runner-cutover-helper.new mtuinstra@sanctuary.tuinstra.dev:
```

Marcel, one interactive SSH session, install reviewed public bytes only:

```sh
ssh -t mtuinstra@sanctuary.tuinstra.dev
sudo install -d -o root -g root -m 0700 /root/runner-cutover-stage
sudo install -o root -g root -m 0755 /home/mtuinstra/runner-controlled-cutover.py /usr/local/sbin/runner-controlled-cutover.py
sudo install -o root -g root -m 0600 /home/mtuinstra/runner-cutover-manager.new /root/runner-cutover-stage/manager.new
sudo install -o root -g root -m 0600 /home/mtuinstra/runner-cutover-helper.new /root/runner-cutover-stage/helper.new
sudo sha256sum /usr/local/sbin/runner-controlled-cutover.py /root/runner-cutover-stage/manager.new /root/runner-cutover-stage/helper.new
```

Compare the script checksum in the preparation handoff and the two target hashes
above; refuse any mismatch. Confirm the existing 2.338.2 activation/pickup receipt
and the private host-credential checks before proceeding. The earlier read-only
2.338.2 receipt used WODIQ job `127122546898` (pickup/version verified, application
job failed); it does not claim a fresh successful application test.

Claude, local GET-only collection after prerequisite consumer migrations:

```sh
python3.14 /private/tmp/runner-controlled-cutover.py collect-before > /private/tmp/runner-cutover-before.json
scp /private/tmp/runner-cutover-before.json mtuinstra@sanctuary.tuinstra.dev:runner-cutover-before.json
```

Marcel, in the interactive session:

```sh
sudo install -o root -g root -m 0600 /home/mtuinstra/runner-cutover-before.json /root/runner-cutover-stage/before.json
sudo /usr/local/sbin/runner-controlled-cutover.py check
sudo /usr/local/sbin/runner-controlled-cutover.py drain
```

Expected: `CHECK-PASS`, periodic `DRAIN-WAIT`, then **`DRAINED`**. Today's unmodified
callers instead yield `REFUSED ref-in-use` or `old-metadata-consumer` before the
first service change. Root verifies the actual order, helper/unit/manager hashes,
role union, complete selected repository IDs, current source/candidate TOML and
state. `/var/backups/wodiq-pin-swap` is never reused or deleted: its journal must
be `applied` or `rolled-back` and its old pause drop-in must be absent. The new
private journal/backups live at `/var/backups/runner-cutover-288dc75d-v1`.

**Do not PATCH yet.** Drain time invalidates pre-drain proof. Claude collects BEFORE
again and transfers it; Marcel installs it with the same `sudo install` command:

```sh
sudo /usr/local/sbin/runner-controlled-cutover.py authorize
```

Expected: **`PATCH-AUTHORIZED`**. This fresh collection must have STARTED after
`DRAINED`. Only now Claude, with the caller/config/group editing freeze held:

```sh
gh api orgs/Tuinstra-DEV/actions/runner-groups/4 > /private/tmp/runner-group4-immediately-before.json
python3.14 /private/tmp/runner-controlled-cutover.py readback-before
gh api --method PATCH orgs/Tuinstra-DEV/actions/runner-groups/4 --input /private/tmp/runner-group4-after.json --silent
python3.14 /private/tmp/runner-controlled-cutover.py readback-after
python3.14 /private/tmp/runner-controlled-cutover.py collect-after > /private/tmp/runner-cutover-after.json
scp /private/tmp/runner-cutover-after.json mtuinstra@sanctuary.tuinstra.dev:runner-cutover-after.json
```

Expected readbacks: `GROUP-BEFORE-MATCH`, then `GROUP-AFTER-MATCH`; PATCH itself is
silent. Readbacks verify the controls, full exact workflow set and repository IDs,
not just one added pin. PATCH changes **only** `selected_workflows`. Manager remains
in cleanup-only mode and the host is empty throughout this temporary mismatch.

Marcel immediately installs the fresh AFTER and commits:

```sh
sudo install -o root -g root -m 0600 /home/mtuinstra/runner-cutover-after.json /root/runner-cutover-stage/after.json
sudo /usr/local/sbin/runner-controlled-cutover.py commit
```

Expected after one 35-second stability window: **`APPLIED; active`** with the exact
new binary hashes. Claude then runs `readback-after` once more and verifies ordinary
queued jobs pick up successfully before migrating Tessera and the remaining
consumer calls to `288dc75d`. No new WODIQ 604 migration is required here.

All evidence expires **300 seconds after collection START**, including the roughly
180 seconds spent collecting; it does not gain a fresh five minutes at completion.
Commit requires at least 45 seconds remaining for installation/resume checks.
Aim for transfer, install and commit within 60 seconds of collector completion.
If that budget is missed, leave admission paused and recollect AFTER; do not rerun
the group PATCH or weaken expiry. Root also checks the collection began after the
relevant drain/authorization timestamp. Evidence is operator-supplied GET readback,
not an independently root-authenticated API connection; root never reads a token.

### Timeout, restart failure and rollback

- **Drain timeout before PATCH:** Claude recollects BEFORE, Marcel stages it and
  runs `abort`. Expected `ABORTED; original admission active`; busy jobs keep running.
  `continue-drain` explicitly retries the bounded drain without deleting backups.
- **Restart fails while idle:** automatic exact manager/helper/config restoration;
  expected `AUTO-RESTORED; admission PAUSED`. Claude PATCHes the full BEFORE payload,
  runs `readback-before`, recollects BEFORE and transfers it. Marcel stages it and
  runs `recover`; expected `ROLLED-BACK; active`. No group auto-PATCH occurs in root.
- **Failure after a new guest picked up:** expected `RECOVERY-DRAIN-REQUIRED`.
  Keep the new files and AFTER group; the script does not restore policy underneath
  that guest. Marcel runs `continue-drain`, waits for `RECOVERY-DRAINED`, then Claude
  restores BEFORE/readback/fresh proof and Marcel runs `recover`. Do not PATCH BEFORE
  or restore binaries before the guest and cleanup finish. An unresolved JIT claim
  blocks the stop and requires diagnosis; the tool never clears it to force progress.
- **Deliberate later rollback:** first migrate every `288dc75d` consumer, including
  isolated metadata/producer calls, back to a still-admitted retained route. Finish
  all immutable old-source/nonterminal `288` runs. Claude collects AFTER, Marcel
  stages it and runs `rollback-drain`. After `ROLLBACK-DRAINED`, Claude PATCHes BEFORE,
  readbacks/collects BEFORE, Marcel stages it and runs `recover`. If rollback draining
  times out, keep AFTER and use `abort-rollback` with fresh AFTER to resume it.

Backups, prior transactions and worktrees are retained. No credentials, policy
contents, child error output or response bodies are printed by operator actions.
Thirty offline fixtures cover real pinned old/new source bytes, busy drain, claim race,
old-role callers, expiry, previous-transaction collision, timeout, failed restart,
late pickup, atomic bytes/modes and full rollback. A real sudo dry run/production
drain is deliberately not claimed; protected preflight is still Marcel's step.

### Preparation verification

- `python3.14 -B -m unittest tests/test_runner_controlled_cutover.py`: 30 pass.
- `make lint`: pass with the existing Python 3.14/Ansible environment.
- Full `make test`: pass locally, 805 Python passes / 1 existing Linux-findmnt
  skip and 41 Node passes. The existing macOS process-group EPERM was reproduced
  on one rerun; its focused rerun and final full suite passed without test changes.
  Optional Packer and the unconfigured cross-repository fleet check were unavailable.
- Read-only `readback-before`: `GROUP-BEFORE-MATCH`. The version-2 collector ran
  in 167.7 seconds (21:13:19 UTC), confirming Tracker's remaining isolated `ea`
  metadata caller; no group PATCH, sudo transaction or consumer run was performed.
- The old-manager fixture is reconstructed from checked-in source plus a reviewed
  reverse patch and required to match the exact deployed SHA. It works in a shallow
  CI checkout without fetching history or reading the protected host configuration.
- No UI changes; no screenshot applies. No Tracker mutation or new Story.

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
GitHub currently marks #206 `CONFLICTING` / `DIRTY`; its integration with main
needs a separate reviewed conflict resolution before any merge. Preserve the
immutable `288dc75d` source identity and WODIQ 604 support; do not silently retarget
this operator transaction to whatever SHA conflict resolution produces.
The standalone operator script is `scripts/runner-controlled-cutover.py`, also
staged at `/private/tmp/runner-controlled-cutover.py`, SHA-256
`b8bad777b33e3d453da1c05d8053ff76e07ceee1034378449e32364c5771d9dc`. The previously distributed `5574d262…` and `c3acd248…` versions are withdrawn;
do not execute it or mix its v1 journal with this v2 protocol.

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
backing allocations, image and base unit stay unchanged. The controlled service
drop-in persists during guarded resume until explicit `finalize`, and runs the reviewed admission wrapper with
normal `Restart=always`; credential bindings and the original service sandbox
remain unchanged. The wrapper/operator SHA is part of its root-owned admission
record until finalization; the root operator remains available for later recovery/rollback. Only the three workflow-ref
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

`DRAIN_TIMEOUT_SECONDS=7500` is an **operator wait budget**, chosen from the
existing 7,200-second runtime/lease TTL plus a 300-second cleanup allowance. It is
not a hard guarantee of natural completion or clean idle by second 7,500. The
observed job maximum does not establish a worst-case runtime bound.

**The existing host expiration timer can destroy a VM at its TTL while admission
is paused.** Guest runtime limits also remain in force. The controller does not
cancel jobs or deliberately destroy running guests; it does not stop, extend or
neutralize those existing timers. A job can therefore still fail at the existing
TTL. The effective completion boundary is 7,200 seconds plus whatever cleanup
actually requires, including timer scheduling, helper work and GitHub/API retries.

After safe admission closure, the manager service runs only a cleanup worker,
with `Restart=no` and its existing sandbox/credential bindings. The worker refuses
launch, JIT dispatch and cleanup of any running, blocked, paused, suspended or
unknown-state CI domain. It invokes reconciliation only once every remaining CI
domain is already `shut off` or gone. Completed-domain cleanup removes definitions,
storage and registrations; it does not terminate an executing guest.

Subprocess timeouts share the remaining 7,500-second wait budget. One polling
interval, scheduler/filesystem delay, or an already-started cleanup worker can
outlast that budget. On expiry/interruption, the command records the interrupted
phase, leaves admission closed and returns without claiming idle or resetting the
budget. A still-running cleanup worker is allowed to finish; `abort` will refuse
while that service remains active. No state, claim or backup is deleted to force
progress. For a job still running, refresh BEFORE and use `abort` to resume the
original manager. For cleanup taking longer, retain the current files/group and
use `continue-drain` (or `reconcile` after a resume failure) for an explicit new
bounded attempt, or diagnose the failed cleanup. Do not patch the group until an
actual `DRAINED` / `RECOVERY-DRAINED` result exists.

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
private transaction container is `/var/backups/runner-cutover-288dc75d-v2/`.
Each attempt has a random `tx-<32 hex>` subdirectory and the root-owned `active.json`
pointer selects it. The script prints its transaction ID. A durable `backing-up`
journal is written before the first copy, and the pointer only after that journal.
Partial copies and a completed backup interrupted before drain intent are
hash-checked and continued without overwrite. After `abort` or
rollback, a new attempt verifies the previous backups and chooses a new ID. An
orphan created before pointer publication remains untouched and is never treated
as a verified rollback source. No previous directory or worktree is deleted.

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

Expected after a service-owned ready receipt (including live group GETs) and
one 35-second stability window: **`APPLIED; active`** with the exact
new binary hashes. Claude then runs `readback-after` once more and verifies ordinary
queued jobs pick up successfully before migrating Tessera and the remaining
consumer calls to `288dc75d`. No new WODIQ 604 migration is required here.

All evidence expires **300 seconds after collection START**, including the measured
191.1 seconds spent collecting (about three minutes). Installation, helper start, override changes, live
GETs, admission, and stability checks use one shared wall/monotonic deadline. The
root controller checks it again immediately before publishing admission permission
and starting the service. The service checks it again after its live GETs and
before dispatch, reserving 35 seconds for stability. The final `enabled` write
flushes/fsyncs its temporary file, checks both deadlines immediately before rename,
and rechecks after directory fsync. Expiry, including after a terminal journal,
records recovery intent and blocks admission; the command does not report APPLIED. Aim to stage and start `commit` within 30 seconds after
collection completes, leaving room for the two GETs, installation and stability.
Expiry refuses safely; recollect AFTER rather than weakening the deadline.

The staged JSON is a **trusted operator declaration**, not signed GitHub evidence,
remote attestation or an atomic cross-repository lock. Root ownership/hash checks
protect the staged copy; they do not prove its assertions were honestly collected.
Marcel explicitly trusts the unprivileged collector/gh session and maintains the
caller/config/group editing freeze. The root process reads no credential and makes
no network request. The existing sandboxed service account independently repeats
GETs of group 4 and its selected repository IDs with its unchanged systemd org
credential before every guarded daemon start until finalization. The only source
is `/run/credentials/ci-runner-manager.service/org_github_token`, in the existing
`ci-runner-manager` service context. The startup probe permits exactly two GETs:
`https://api.github.com/orgs/Tuinstra-DEV/actions/runner-groups/4` and
`https://api.github.com/orgs/Tuinstra-DEV/actions/runner-groups/4/repositories?per_page=100&page=1`.
No redirect, alternate origin/source, token export to argv/env/subprocess/files,
or extra GET is allowed by this probe. The pinned manager validates their
exact policy against the actual config. Failure, rate limiting or mismatch denies
admission. These live GETs strengthen group verification; they do **not** repeat the
full branch/PR/active-source inventory or the required-check inventory. Those remain
operator assertions requiring fresh collection and the freeze. No public/anonymous
GET can verify this private organization policy; no extra credential is requested.

### Durable resume and boot/crash recovery

During resume/recovery the pause override is never unlinked. It changes atomically
between cleanup-only and `/usr/local/libexec/runner-cutover-admission.py`; the latter retains normal `Restart=always`.
`/etc/ci-runner/cutover-admission.json` is a root-owned readable control record
containing transaction ID, nonce, code/config hashes, boot ID and deadlines only.
The protected journal and backups remain private. The wrapper uses the existing
service user, sandbox and credential bindings. It does not broaden access.

Before every risky resume step, the journal becomes `resuming`, with intended
BEFORE/AFTER files and terminal state. A probation ticket is valid only in that
boot and within the shared deadline. The service repeats the live policy GETs,
rechecks the ticket/files and records readiness; the controller then observes 35
seconds of stability. Only after that does it durably write the terminal journal
and publish boot-independent `enabled` permission. A crash between those writes
leaves probation permission, so the next boot still refuses admission. Fully
completed activation/rollback can restart on later boots without reusing an
expired activation proof; it still checks all pinned files and performs fresh
live group GETs. This dependency is temporary: use the explicit finalization below
after the transaction has succeeded and been observed stable.

A runtime guard rejects new JIT POSTs when the root record is blocked/changed,
while an already-started JIT may finish normally. The existing manager's normal
pre-POST rejection handling is retained; the controller never clears an ambiguous
quarantine claim. Resume failure writes `recovery-stopping` **before** attempting a
safe stop. If JIT quarantine prevents that stop, the intent and blocked gate survive;
`RECOVERY-STOP-BLOCKED` explicitly means the current process may still be active.
An inactive manager with an unresolved quarantine also requires diagnosis, not
claim deletion. Do not change group selection in either case.

After an interrupted command or reboot, Marcel uses the reviewed installed tool:

```sh
sudo /usr/local/sbin/runner-controlled-cutover.py reconcile
```

This reads/verifies the active journal, every original backup, the three actual
file hashes and protected config permissions. It validates the real service/unit,
credential binding, command and drop-ins, accepting only the exact known forms,
including a disk override ahead of daemon-reload. It blocks future admission,
closes the current manager at the existing lock/JIT boundary, and drains existing
leases naturally. BEFORE, AFTER and partial installs are supported; no assumption
that new binaries are installed is used. No guest is killed and no claim is cleared.
It is safe to repeat after a timeout. An already completed, coherent, enabled
transaction is checked against actual service/files/leases and returns a no-op.
Unknown file/unit drift fails closed for diagnosis.

Expected once genuinely idle: `RECOVERY-DRAINED`. Only then Claude ensures the
exact BEFORE group (PATCH only when needed), runs `readback-before`, collects and
transfers fresh BEFORE. Marcel stages it and resumes the original files:

```sh
sudo install -o root -g root -m 0600 /home/mtuinstra/runner-cutover-before.json /root/runner-cutover-stage/before.json
sudo /usr/local/sbin/runner-controlled-cutover.py recover
```

Expected: `ROLLED-BACK; active`. Repeating `recover` checks actual files/service/
leases and returns `ALREADY-ROLLED-BACK`; it does not interrupt a healthy guest.
If `recover` first encounters an unfinished `installing`/`resuming` phase or a
missing override, it reconciles/drains first and asks for fresh BEFORE afterward.
Do not manually delete the override, change the journal phase, start the bare
manager, reset claims or reuse an expired file to force admission.

### Finalize: remove the temporary startup dependency

After APPLIED, Claude confirms the exact AFTER readback and normal queued-job pickup.
Marcel then runs:

```sh
sudo /usr/local/sbin/runner-controlled-cutover.py finalize
```

Expected: **`FINALIZED; original service start active; wrapper and override removed; backups retained`**.
`FINALIZE_STABILITY_SECONDS=300` requires five continuous minutes with the same
MainPID/start timestamp, healthy service, zero restarts/exit status and unchanged
manager/helper/config hashes. This is a separate post-activation observation window,
not a renewal of the five-minute evidence validity. It changes no trusted pin,
credential, GitHub policy, capacity or guest lifetime.

Only after that observation does the script persist `finalizing`, close admission
at the same bounded JIT/lock interlock and stop the manager process while leaving
its guests/leases intact. It removes only the reviewed cutover override, reloads
systemd and starts the original daemon command from the unchanged pinned base unit.
After a further 35-second startup check it removes the separate admission-wrapper
file and its public gate record, then records `finalized`. The root operator and
all private journals/backups stay available for rollback; the base service no
longer references them. Subsequent boots use the original service startup and do
not incur the wrapper's two extra GitHub GETs. Existing normal manager API behavior
and credential bindings remain unchanged.

An unstable observation refuses before removal. A restart failure, including pickup
of a guest, keeps `finalizing`, blocks/stops at the safe interlock and retains actual
files and claims. Retry `finalize` after any JIT quarantine settles; do not clear it.
A crash after override removal may boot the normal daemon: that removal was already
authorized by the durable stable-policy intent, with complete verified files.
Repeating `finalize` reconciles the actual service/files and finishes cleanup; an
already finalized healthy service returns `ALREADY-FINALIZED` without interruption.
For this phase use `finalize` rather than manually recreating/removing metadata.
Deliberate `rollback-drain` remains supported after finalization.

### Timeout, failed restart and deliberate rollback

- **Before PATCH:** refresh BEFORE and use `abort` when the cleanup service is
  inactive. The original manager resumes, including still-running guests, and a
  subsequent `drain` starts a new verified transaction. For longer cleanup, use an
  explicit `continue-drain` after checking its status; it does not silently extend
  the previous deadline.
- **Idle resume failure:** the durable recovery intent is recorded first. Exact
  original manager/helper/config bytes are restored and admission remains closed
  (`AUTO-RESTORED`). Claude restores BEFORE if needed and supplies fresh BEFORE;
  Marcel runs `recover`.
- **Failure after old or new manager pickup:** the actual files/group stay in place,
  phase `recovery-draining` records which files are present, and the gate is blocked.
  Use `reconcile`, then the fresh BEFORE/recover sequence above. This applies equally
  to failed commit, failed old-manager recovery, abort and abort-rollback resumes.
  A safe-stop refusal cannot lose the recovery status. Existing TTL timers still
  apply to the guest while draining.
- **Deliberate later rollback:** first migrate every 288 consumer to an admitted
  retained route and complete all nonterminal immutable 288-source runs. Collect
  AFTER, stage it and use `rollback-drain`. After `ROLLBACK-DRAINED`, Claude restores
  BEFORE/readback/fresh collection and Marcel runs `recover`. `abort-rollback` with
  fresh AFTER resumes the new files if the rollback drain must be abandoned.

The seven original review regressions were executed against SHA `5574d262…` before
implementation: all failed (four assertion failures and three transaction errors).
The revised suite includes actual abrupt child-process exits around new/old resume
and the terminal/gate publication boundary; tests also cover JIT-stop refusal,
late old/new pickup, slow helper start at proof age 304, stability/journal overrun, normal
reboot after proof expiry, partial installation/backup and safe retry after abort.
The second-review regressions reproduced the final-write deadline gap, an extra
GET, and the missing finalization action before implementation. The credential
regression exercises both exact pinned manager versions, substitutes only a synthetic
credential/HTTP and the daemon boundary, audits file I/O and argv/env/subprocess
exports, and asserts the exact two authenticated GETs. Finalization tests cover
stability refusal, running guests, failed restart after pickup, abrupt crash after
override removal, idempotence and later rollback.
No production sudo/crash/drain rehearsal is claimed by these offline fixtures.

Fresh consumer and required-check GETs are recorded in
[the exact per-repository inventory](dev54-required-check-inventory.md). No required
context or app binding was changed; actual migration PR verification remains required.

### Preparation verification

- `python3.14 -B -m unittest tests/test_runner_controlled_cutover.py`: 63 pass.
- `make lint`: pass with the existing Python 3.14/Ansible environment.
- Full `make test`: pass locally, 838 Python passes / 1 existing Linux-findmnt
  skip and 41 Node passes. Direct Python `fork()` crash fixtures proved unstable on
  the instrumented macOS runtime; fresh Python child processes now inject actual
  `os._exit(73)` crashes at the same boundaries, without skipping a scenario.
  Optional Packer and the unconfigured cross-repository fleet check were unavailable.
- Earlier read-only `readback-before`: `GROUP-BEFORE-MATCH`. The final version-2 collector ran
  in 191.1 seconds (22:02:42 UTC), confirming Tracker's remaining isolated `ea`
  metadata caller; no group PATCH, sudo transaction or consumer run was performed.
- The old-manager fixture is reconstructed from checked-in source plus a reviewed
  reverse patch and required to match the exact deployed SHA. It works in a shallow
  CI checkout without fetching history or reading the protected host configuration.
- No UI changes; no screenshot applies. No Tracker mutation or new Story.

Review logs: `/private/tmp/runner-cutover-review-red.log`, `runner-cutover-final-deadline-red.log`, `runner-cutover-final-backup-red.log`, `runner-cutover-second-review-red.log`, `runner-cutover-second-review-green.log`, `runner-cutover-second-review-full-test.log`. The operator checksum at the top belongs to this revision, not the withdrawn copy.

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

## One future idle window with the #205 rollout

1. Review and merge dispatch isolation, the cache repair and any prerequisite runtime-provenance
   work. #205 is already merged at `1d4c927c2672c360fd1f525621203a69bb034861`.
   Select **one final immutable SHA** containing all approved changes. A cache-PR
   head is a candidate, not an already-admitted production pin.
2. Prepare consumer diffs for Gate, Tracker, Notify, Status and Tessera. Gate's
   security caller stays at `43ead52517b9ab7e3d6f6bb94305be2b98a10fd9` with its
   frozen trigger/events/concurrency exception. Other shared calls/proof identities
   move together to the final pin after activation is applied.
3. Resolve and privately verify the role credential/caller decision above before
   another isolated metadata job is queued. Before draining, verify no affected open PHP CVE hotfixes and no queued or
   in-progress affected runs. Inventory other callers of `ea86c62…` before retiring
   that ref. Preserve all four legacy trusted refs, role actors/events/repositories,
   selected repository IDs including Tessera `1179209966`, and the five-pin limit.
4. Prepare a reviewed root-owned transaction with precondition hashes, protected
   backups and exact rollback bytes. Marcel runs that transaction interactively
   through **one sudo step**. It drains safely, installs the reviewed #205 changes
   together with this manager's discovery isolation
   and replaces the fifth trusted ref plus `metadata-preflight.workflow_ref` with
   the final SHA. If producer callers migrate too, replace `pr-security-producer`
   with the same SHA in that transaction. Do not append a sixth trusted pin.
5. During its pause, Claude atomically sets runner group 4's full selected-workflow
   list to the four preserved trusted refs, the new trusted ref and the exact
   configured producer ref. Keep selected visibility, restricted workflows and
   public-repository denial. Manager config and GitHub must match exactly;
   an org-only additive change would block dispatch. Marcel resumes only after
   Claude confirms the exact list and then verifies the applied transaction.
6. Only after `applied`, push the prepared consumer commits and use their ordinary
   PR runs as route evidence. Migrate required-check names atomically where needed;
   retain the separate develop/main release ordering. Keep the backup. Rollback
   restores both host files and the exact prior GitHub selection before undraining.

Tracker's existing trusted backend already runs on baked VM PHP. Its published
TRAC-223 lock is unchanged and used by the release-browser route; it is not the
current shared backend's runtime source. The planned reusable cutover must review
running backend quality **inside the published container image in the trusted VM**
as a separate implementation prerequisite, without substituting baked PHP or
expanding registry access silently. This PR does not implement or claim
that provenance guarantee.

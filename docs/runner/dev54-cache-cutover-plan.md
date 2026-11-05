# DEV-54 cache repair and coordinated pin cutover — planned only

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

## One future idle window with the #205 rollout

1. Review and merge the cache repair and any prerequisite runtime-provenance
   work. #205 is already merged at `1d4c927c2672c360fd1f525621203a69bb034861`.
   Select **one final immutable SHA** containing all approved changes. A cache-PR
   head is a candidate, not an already-admitted production pin.
2. Prepare consumer diffs for Gate, Tracker, Notify, Status and Tessera. Gate's
   security caller stays at `43ead52517b9ab7e3d6f6bb94305be2b98a10fd9` with its
   frozen trigger/events/concurrency exception. Other shared calls/proof identities
   move together to the final pin after activation is applied.
3. Before draining, verify no affected open PHP CVE hotfixes and no queued or
   in-progress affected runs. Inventory other callers of `ea86c62…` before retiring
   that ref. Preserve all four legacy trusted refs, role actors/events/repositories,
   selected repository IDs including Tessera `1179209966`, and the five-pin limit.
4. Prepare a reviewed root-owned transaction with precondition hashes, protected
   backups and exact rollback bytes. Marcel runs that transaction interactively
   through **one sudo step**. It drains safely, installs the reviewed #205 changes
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
expanding registry access silently. This cache-only PR does not implement or claim
that provenance guarantee.

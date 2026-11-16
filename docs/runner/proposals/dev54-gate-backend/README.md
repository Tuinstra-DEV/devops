# DEV-54: exact compact Gate backend admission proposal

**Prepared locally only. No push, PR edit/comment/merge, runner setting, host
configuration, credential, VM image or production change has been made.**

## Evidence and limits of the effective-policy check

`gh` confirms runner group **4**, `sanctuary-trusted-verification`, restricts
jobs to the exact six refs in `group-before.json`. Selected repository IDs are
recorded in `repositories-before.json`; Gate is already included.

Read-only SSH to `mtuinstra@sanctuary.tuinstra.dev` confirms the active service:

```text
/usr/local/bin/ci-runner-manager --config /etc/ci-runner/manager.toml daemon --interval 30
service user: ci-runner-manager
deployed source SHA-256: 5404bbbf3d4430f4518afa37c19d7be090d1246c3b5d7cc4580ffb601f999ed3
```

The readable deployed source was copied and examined. It allows at most five
exact legacy trusted-verification refs, separately configured execution roles,
and requires exact equality of the manager's combined refs and the GitHub group.
It currently has no product-CI role. The local branch manager is a different
version; **do not deploy its entire file for this pilot**.

The effective TOML is inaccessible to the administrative account and `sudo -n`
requires a password. Its exact ref order, role configuration and runtime image
settings have **not** been read or inferred as verified fact. The prepared
`read_effective.py` must first run under the existing operator privilege with
fresh group/repository snapshots. It prints selected verification results only,
never the config or credentials. Any mismatch aborts the proposal unchanged.

Read-only operator check (fresh snapshots from the authorized local gh session;
copy only the public policy snapshots and this proposal to Sanctuary):

```bash
GH_PAGER=cat gh api orgs/Tuinstra-DEV/actions/runner-groups/4 > /tmp/dev54-group-state.json
GH_PAGER=cat gh api orgs/Tuinstra-DEV/actions/runner-groups/4/repositories > /tmp/dev54-repositories-state.json
# On Sanctuary, using the existing operator privilege; no credentials are read:
sudo python3 read_effective.py --group-state /tmp/dev54-group-state.json --repositories-state /tmp/dev54-repositories-state.json
```

Snapshots older than five minutes are rejected. After a later approved change,
the same check with `--after --before-config <root-owned-TOML-backup>` verifies
the exact new manager hash, seven refs, and that removing only the proposed role
from the effective TOML yields exactly the previous parsed configuration.

## Exact minimal settings

1. **GitHub:** PATCH only `selected_workflows` on
   `/orgs/Tuinstra-DEV/actions/runner-groups/4` using `group-after.json`.
   Preserve the six existing refs, repository IDs, visibility and workflow
   restriction; add exactly:

   ```text
   Tuinstra-DEV/devops/.github/workflows/reusable-product-ci.yml@9e4a72c0b8ca586cd97af4b6e33e65a919accb43
   ```

2. **Manager code:** apply `manager-effective.patch` to a staged copy of the
   deployed Python source with the exact before hash above. Expected after
   hash: `e9b2f1865fb915f80f01803a81d36476c637d8b655044f5c664b67acfc6f185c`.
   It adds a single role using existing isolated role dispatch/JIT machinery.
   Legacy workflow prefixes, five-pin bound, source checks and other roles
   stay unchanged. The patch also applies to the local branch implementation,
   but the deployment artifact is the patch against the verified live version.
   In an approved operator staging directory, name the copied file
   `ci_runner_manager.py` and apply `patch --batch --forward -p3` from that
   directory using `manager-effective.patch`. Syntax-check and compare the
   exact after hash before replacing `/usr/local/bin/ci-runner-manager`.

3. **Manager TOML:** append only `manager-role.toml`, after confirming the role
   is absent. Its exact fields are:

   ```toml
   [org_execution_roles.gate-backend-pilot]
   workflow_ref = "Tuinstra-DEV/devops/.github/workflows/reusable-product-ci.yml@9e4a72c0b8ca586cd97af4b6e33e65a919accb43"
   repositories = ["Tuinstra-DEV/gate"]
   events = ["pull_request"]
   actors = [{ login = "marcel-tuinstra", type = "User" }]
   ```

   Do not change `org_workflow_refs`, existing roles, repository selection,
   credentials, pool/capacity limits or helper permissions. The combined
   manager set becomes exactly the same seven refs as the GitHub group.

The role's source policy is fixed in code: private same-repository owner PR
**#444**, head `chore/DEV-54-compact-ci-pilot`, protected base `develop`, current
head/base and exact merge parents. Only `ci / Backend CI` is eligible. JIT is
bound to job/run/attempt, PR head and execution merge SHA. Its dedicated label
`ci-gate-backend-pilot` cannot be confused with `trusted-medium` jobs or other
roles. Resource profile remains the existing **medium** pool.

## Workflow and runtime

Workflow pin: `9e4a72c0b8ca586cd97af4b6e33e65a919accb43`.
New trust/baked actions pin: `ac5ef4542cc6f8505f894a3db27f59f38fd1648e`.
These are local commits, **not published or activated**. Gate's prepared local
caller grants Actions/PR **read** only to satisfy the hosted preflight; the
verification job retains Contents read only. No write permissions/secrets.

Bronselectie performs the read-only trust check. Its matrix gives Frontend CI
the literal hosted runner, and Backend CI the verified JSON runner object or
the existing hosted fallback. The same matrix job definition runs exactly one
of two step paths. MariaDB stays in Backend CI. No extra hosted/trusted job
definitions or generic bundle are introduced; conditional **steps** can still
be skipped. When this reusable is invoked, forks, bots, unknown actors and
unrelated events/branches select hosted execution; the existing bounded pilot
caller trigger/if conditions remain unchanged. Malformed/unavailable identity
evidence cannot grant a role.

The trusted path reuses baked PHP 8.3 and the same Gate adapter. It adds a hard
PDO MySQL requirement when MariaDB is selected, without sudo or installation.
The repository's image recipe lists SQLite but does not install PHP 8.3 MySQL.
The active sealed image cannot be verified from the unreadable config. Therefore
`guest-readiness.sh` must pass on that image as the existing runner user before
activation. **If MySQL/Docker readiness is absent, stop; this decision does not
authorize an image rebuild, package installation or expanded privilege.**

The manager's existing host credential must already allow read-only GET of
Gate's protected `develop` branch and candidate merge Git commit. Do not read
or paste credentials, or silently expand token permissions. A 403 is a blocker.

## Local verification

- New role/route tests cover opt-in, forks/bots/unknown actors, exact repo, PR,
  branch/base, protected tip, immutable workflow pin, stale source, merge parents,
  attempts, mixed labels, frontend job confusion, pre-JIT revalidation and group
  equality. Existing role and legacy organization-route regressions also run.
- The new tests reject the unpatched deployed version. The minimal deployed
  patch passes **58 role/route tests**, including existing role regressions.
- Matrix/verdict tests preserve the same three definitions (source, dynamic
  verify matrix, verdict), four selected jobs and fail-closed final verdict.
- Baked-driver tests execute the actual YAML step with synthetic PHP: missing
  PDO MySQL fails, legacy SQLite callers still work, and no privileged install
  path exists. YAML parsing is checked as part of that test.
- `make lint`, complete `make test` and actionlint passed locally. The full
  suite first caught and then verified the repaired action YAML indentation.
  A later run hit the existing macOS killpg permission flake; its isolated
  rerun and subsequent full suite passed without suppressing that test.
- The read-only effective-policy checker passed against synthetic before/after
  TOML with the exact live-source and patched-source hashes; it rejected a
  missing group ref and an unrelated config/capacity change, preserved inputs
  and did not disclose a synthetic redaction sentinel. This is not a read of
  the protected live TOML.
- Existing platform limits: Linux-only findmnt skip on macOS; optional Packer
  format and cross-repository fleet checks remain unavailable/unconfigured.
  No live CI or Sanctuary execution is claimed by local tests.

## After approval: one comparison, no merge

1. Confirm both remote PR heads still match `manifest.json`. Confirm Gate base
   is still `1ca529f02479c4e1d0a14353382a6a7dc9cc74f7`; otherwise stop and reassess
   the comparison. Capture fresh group/repository state with `gh` and run the
   read-only effective TOML/hash check under the existing operator privilege.
2. Verify current image/PDO MySQL/Docker and current credential read access.
   If either fails, report the exact blocker without changing admission.
3. Publish the prepared DevOps commits in one normal push, validate the exact
   workflow pin, and require green provider CI. PR #220 remains open.
4. In an idle window with **zero active leases**, back up the deployed manager,
   effective TOML and six-ref group state. Stage the minimal live-source patch,
   syntax/config-check it and verify its expected hash. Coordinate code, one
   role stanza and the seventh group ref; no admissions during a mismatch.
   Restore/restart the existing manager service and verify exact seven-ref
   equality with unchanged repository/capacity/credential settings.
5. Publish Gate's prepared CI-only caller change once. It starts the compact
   pilot alongside existing required CI on the same new head/merge candidate.
   Do not compare a successful old head with a different-source new run.
6. Verify backend/frontend/adapter tree identities against Gate `188356ba` and
   equality of canonical routing payloads. The expected prior routing digest
   is in `manifest.json`; ZIP archive hashes are not the source comparison.
7. Record actual run links and attempts, four started pilot jobs, **zero skipped
   jobs**, same selected Unit/Integration/Functional and frontend/API checks,
   **1,216 PHPUnit tests / 7,567 assertions** with the known 14 skips/one incomplete/
   one warning, and native MariaDB **12 tests / 99 assertions** with fail-on-skipped
   and fail-on-empty. Investigate any differences rather than accepting a green
   label alone. Required-check names/App bindings/protection stay unchanged.
8. Accept the canary only with **sum ceil(hosted job seconds/60) < 7** and wall
   elapsed time no worse than existing CI on this same candidate. Reference
   run `43845010575` was 11 rounded minutes / 6:04; its concurrent existing CI
   `43845010846` was 7 / 6:39. Backend was 286 s / 5 hosted minutes. Removing
   that hosted job projects **6**, not a proven result; added preflight time
   and runner/platform variability can still cross rounding thresholds.
9. Report Sanctuary job `started_at - created_at` separately. It includes
   manager polling, capacity/admission, VM provisioning and registration; do
   not call it pure runner queue time. Where existing manager audit timestamps
   can be safely correlated by job/run/attempt, split discovery/JIT/VM/assignment
   delays. Otherwise retain only the measured combined delay. No invented
   queue breakdown, monthly savings or causal guarantee from one sample.

## Rollback

Before activation, aborting requires no rollback: all settings are unchanged.
After a trial, stop further pilot triggering, let admitted leases finish, and
return to an idle window. Restore the exact manager/config backups (before
manager SHA above), and PATCH `group-before.json` to restore exactly six refs.
Restart the existing service and verify equality and unchanged repositories.
Restore Gate's caller to provider pin
`91da5fb0faf76231f6af2a3542889f7581afbe40` with a **new normal commit/push**, not
reset/force/amend. No tags, protections, other consumer pins or production
artifacts are involved. If any before-state fingerprint differs, stop rather
than overwriting concurrent changes.

## One decision

Authorize **one conditional, isolated Backend CI canary** with exactly the
seven-ref group and single manager role above, plus the prepared CI-only
caller/read-permission change. Activation is conditional on a directly verified
effective TOML, runtime prerequisites, existing credential rights and idle
leases. Failure of a prerequisite leaves live state unchanged; no additional
access/image change, merge or broader rollout is authorized by this decision.

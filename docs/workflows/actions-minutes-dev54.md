# DEV-54 shared workflow rollout

This release implements the 2 November 2026 policy: bundle light hosted work and
use Sanctuary's ephemeral VMs for fixed heavy verification. Consumer pins are the
single full merge SHA recorded in DEV-54; do not use a moving branch or tag.

## Changes and compatibility

- `pr-checks.yml`: one `lint` job runs both `make lint` and `make test`; obsolete
  PR runs are cancelled. The active Core Branches Default ruleset had no required
  status-check entries when inspected. No ruleset was changed.
- Trusted verification: metadata preflight uses the existing special DEV-14 role
  for Gate/Tracker and independently admitted medium VMs for the other fixed
  private repositories. The role request now defaults to true; explicit false
  retains the old PR-preflight/producer hosted choice. All input names/types and
  previous outputs remain supported.
- Owner schedules/dispatches: the API gate and runner manager both require the
  owner actor and triggering actor, immutable reusable reference, same private
  repository, no PR association, and the current protected default-branch SHA.
  Bundle mode uses the same identity gate. An invalid bundle identity fails;
  an invalid single-job identity retains the existing hosted fallback.
- Gate security: the existing default-branch controller, immutable offline scanner,
  token scopes, source checks and fixed producer-role labels are unchanged. The
  scheduling request defaults true. Untrusted code/actors never get that role;
  the hosted fallback still enforces the scan. No secrets input was added.
- Tessera: `profile: heavy`, `job-key: tessera-flutter`; fixed Node 24, Flutter
  3.47.1, Java 17, Android 37 and repository-specific lockfile caches. The consumer
  implements `.github/ci/sanctuary-ci --strict tessera-flutter`. Its repository ID
  is `1179209966`; admission remains dependent on the operator's exact group policy.
- The obsolete Node example dispatch in this non-Node control-plane repository
  is now `templates/workflows/example-node-ci.yml`.

## Notify: one call, one required verdict

```yaml
verification:
  uses: Tuinstra-DEV/devops/.github/workflows/reusable-trusted-verification.yml@<release-sha>
  with:
    bundle: notify
    selection-json: '{"schemaVersion":1,"jobs":[{"job-key":"notify-quality","plan":{}},{"job-key":"notify-production-images","plan":{}}]}'
```

Do not also pass `profile`, `job-key`, or `plan-json`. A nonempty subset of the two
fixed keys is allowed, without duplicates or nonempty plans. Both trusted groups
run on separate heavy VMs with `sanctuary-notify`. `Notify verification` always
runs after preflight and the selected groups, even on failures. On the hosted
fallback it runs both selected adapters inside that same verdict job. It checks
literal completion proofs, rejects cancelled/failed/missing/ambiguous routes and
sets `notify-proof: true` only after all selected work succeeds.

The example creates the child check `verification / Notify verification`. Confirm
the exact check-run name from the consumer's necessary PR validation. Require it
in place of the old quality and production-images verdicts using one atomic
ruleset update, without an interval without required checks. If retaining a
consumer alias, require both `needs.verification.result == 'success'` and
`needs.verification.outputs.notify-proof == 'true'`. Old single-job callers and
outputs continue to work, but two separate calls lose the consolidation benefit.

## Hosted job/minute accounting

Minute equivalents below use `sum(ceil(job_seconds / 60))`, excluding skipped jobs.
They are not an invoice export; this control-plane repository is public. The new
consumer counts are routing projections until the new pin and manager are active.
No extra benchmark runs were started.

| Invocation | Before hosted jobs / rounded minutes | After hosted jobs / rounded minutes |
| --- | --- | --- |
| DevOps PR checks | 2 / 2 (run 39153956469: lint 6 s, test 51 s) | 1 / ceil(combined seconds / 60), measured in the single PR validation |
| Trusted fixed verification, one call | 1 preflight / 1, plus zero hosted execution | 0 / 0 |
| Owner default-branch schedule/dispatch, single job | 2 / 1 + ceil(adapter seconds / 60) | 0 / 0 |
| Gate security | 1 / 2 (run 39135892684: 113 s) | 0 / 0 for an admitted owner PR; untrusted unchanged |
| Tracker Gate security | 1 / 5 (run 39652478161: 244 s) | 0 / 0 for an admitted owner PR; untrusted unchanged |
| Notify: two fixed groups and required verdict(s) | 2 preflights + 2 consumer verdicts / at least 4, excluding the caller route job | 1 hosted verdict / ceil(verdict seconds / 60) for trusted code |
| Notify: untrusted fallback, both selected | 2 preflights + 2 adapters + consumer verdicts | 2 / 1 + ceil(combined adapters and verdict seconds / 60) |

For monthly projections multiply each **measured consumer** invocation cost by its
October event count; DEV-54's repo agents own that inventory and the organization
sum. Do not infer the organization's 1,500-minute goal from this shared release.

## Other reusable boundaries and exceptions

- `reusable-ci`, `reusable-php-lint`, `reusable-php-security`, `reusable-release-pr`,
  `reusable-release-tag` and `reusable-cd-restricted-deploy` already run one job.
  `reusable-php-test` selects exactly one database job. Keep light caller checks
  together; use fixed trusted adapters for heavy suites rather than granting a
  caller-supplied shell command access to a trusted runner.
- `reusable-ci-docker` has one build/scan and an optional second SARIF uploader.
  Keeping the uploader separate avoids giving repository build code its
  `security-events: write` permission. Heavy PR image callers use fixed image
  adapters in trusted verification; its old arbitrary build inputs stay hosted.
- The PHP/Nuxt/Vite CD workflows run one build plus one selected deploy job (two
  hosted jobs). Environment/credential-gated deploy is deliberately separate from
  build execution. `reusable-release-image` retains its existing opt-in trusted
  release route. No new deployment/secrets path was introduced.
- `reusable-heavy-ci-v2` retains its legacy artifact, fanout and metrics job/output
  contract as an explicit exception to the two-hosted-job limit. Its arbitrary
  entrypoint input cannot become a trusted PR runner selector. Consumers needing
  the new heavy policy migrate to the fixed trusted verification adapters. No
  stage, security check, artifact identity or test coverage was removed to reduce
  job count.
- Untrusted legacy multi-key bundles retain their existing per-key hosted fallback
  isolation; Notify's new fallback is consolidated. Existing consumers must not
  assume these compatibility routes are trusted or free.

## Activation prerequisite and rollback

The base checkout and DEV-50 scheduler PR #191 were not modified. This release
changes admission validation, not queue scheduling. Before consumer cutover an
operator must naturally drain the manager, preserve its config/group/role policies,
back up the installed binary, install the reviewed new manager, and add the exact
new immutable workflow references to both the existing restricted runner group
and matching manager policy. Preserve old pins while consumers migrate; do not
replace the group's workflow list with a new singleton. Keep the DEV-14 metadata
and security producer roles aligned to their exact configured pins. Add Tessera's
private repository ID only within the existing explicit repository allowlist.

Read-only host verification found manager SHA-256
`232d37364c0754bd59aefbf44fee9ba5704def038bbda94514975f857dd61e9f`, active.
`sudo -n true` requires authentication, so this agent could not activate the manager
or inspect the protected config. No privileged host or group change was claimed.
The historical activation tools retain their original fixed rollout preconditions;
updating source digest assertions does not make them a DEV-54 activation command.

Rollback is a normal consumer commit restoring its prior full pin. Restore the
operator's protected manager/config/group backup only after a natural runner drain.
Do not move tags, force-push, drop trusted-role checks, or disable host-key checking.

## Verification

Run `make lint` and `make test` with Python 3.11+ and Ansible available. The new
Notify tests execute the actual verdict for each subset on both routes and reject
failure, cancellation, skipped work, absent proof and simultaneous route proofs.
Schedule tests cover protected/default/current source and independent manager
admission and assignment rejection. Changed workflows pass actionlint 1.7.12.

Full-repository actionlint needs the narrowly scoped compatibility filter below:
that release's type model predates GitHub Cloud's documented `job.workflow_*`
properties in the unchanged legacy heavy workflow. See the official
[context reference](https://docs.github.com/en/actions/reference/workflows-and-actions/contexts#job-context).
This filter does not suppress runner-label or trust-expression diagnostics.

```sh
actionlint -shellcheck= -pyflakes= \
  -ignore 'property "workflow_(ref|sha|repository)" is not defined in object type'
```

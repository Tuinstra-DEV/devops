# DEV-50 job routing rollout

The four-pool host is active. New organization routing remains disabled until a restricted runner group and its separate least-privilege credential have been verified. Existing repository-scoped trusted release runners remain available during migration.

The central reusable workflow accepts `profile`, `job-key`, and an optional `plan-json` selection. Its existing hosted preflight verifies the PR and the caller's immutable central workflow reference. A second step in that same hosted job accepts only the fixed repository/key/profile pairs below, validates the repository-specific JSON schema, and selects toolchains. Both execution routes then check out the current event revision and call `bash .github/ci/sanctuary-ci --strict "$JOB_KEY"`. Adapters must perform their own fixed commands and start required PostgreSQL services through Docker; callers cannot pass commands, service definitions, paths, runner labels, or toolchain versions.

| Repository | Medium keys | Heavy keys | Fixed setup |
| --- | --- | --- | --- |
| wodiq-platform | `wodiq-runtime-build`, `wodiq-core`, `wodiq-migration`, `wodiq-client` | `wodiq-api`, `wodiq-management`, `wodiq-worker` | Node 24, npm; strict WODIQ v2 boolean selection plan |
| gate | `gate-php` | `gate-frontend-static` | PHP 8.3 with ctype/iconv and Composer; Node 24 with pnpm 10.28.2; exact suite/test/build selection enums |
| tracker | `tracker-frontend-static` | `tracker-backend-postgres`, `tracker-container-api`, `tracker-container-web`, `tracker-container-php-prod`, `tracker-container-nginx-prod` | PHP 8.4 with existing extensions and Composer; Node 24.18.0 with pnpm 11.1.1 for frontend; Docker Buildx for image checks |
| notify | — | `notify-quality`, `notify-production-images` | Docker Buildx; image PHP remains inside the container |
| console | `console-php-lint` | `console-php-postgres` | PHP 8.4, intl/pdo_pgsql and Composer; adapter manages PostgreSQL 16 |
| marcel-site, tuinstra-site, wodiq-site, openairco-site | `site-node` | — | Node 24 and npm; each adapter preserves its existing CI command list |
| wodiq-app | — | `wodiq-app-checks` | Node 24 and npm; adapter preserves typecheck, unit and build checks plus fixed test environment |
| status | — | `status-quality` | Docker/Compose and Buildx; adapter preserves PHP/PostgreSQL and image checks inside Docker |
| openairco | — | `openairco-flutter` | Temurin Java 17, Android platform 37, Flutter 3.47.1 stable; Apple and security jobs stay hosted |

The fixed key table defines eligibility, not proof of an activated consumer or measured capacity. Gate, WODIQ and Tracker's `tracker-frontend-static` accept only their exact selection schemas, normalized to a one-line `CI_PLAN` environment value. Tracker's frontend plan has four booleans: web, unit, component and site; unit/component imply web, and at least web or site must run. An omitted Tracker frontend plan selects all four for a safe full run. Other listed keys reject a supplied plan. WODIQ also receives `CI_SOURCE_SHA`, `CI_RUN_ID`, `CI_RUN_ATTEMPT` and `CI_ARTIFACT_DIR`; the same immutable run identity is available to every adapter.

Dependency download caches remain bound to lockfile digests; they do not contain installed dependencies, builds or test results. Node/npm consumers use the root `package-lock.json`. Gate pnpm uses `frontend/pnpm-lock.yaml`; Tracker pnpm uses both `code/web/pnpm-lock.yaml` and `code/site/pnpm-lock.yaml`. PHP download cache keys also include OS, architecture and PHP version, with `backend/composer.lock` for Gate, `code/api/composer.lock` for Tracker, and root `composer.lock` for Console. Tracker's self-hosted PHP route uses its existing runner temporary Composer cache path. OpenAirco's pinned Flutter action caches its SDK and pub downloads by OS, architecture, Flutter version and `flutter/pubspec.lock`; Gradle downloads use the same toolchain identity plus the Flutter lock and fixed Android Gradle property files. Cache restoration never supplies a proof of test success.

The fixed Tracker, Notify and Status Docker keys may restore BuildKit layers through the pinned first-party `actions/cache` action into a job-local `$RUNNER_TEMP/ci-buildkit-cache` directory. The exact key combines repository, fixed job key, OS, architecture, dependency locks, Dockerfiles, and the running Docker/Buildx versions; there are no broad restore keys or release cache consumers. A failed, unsafe or oversized restore is quarantined within the job's temporary directory. After the adapter and any required artifact upload succeed, a cache miss may save at most 2 GiB of local layers. Restore/save errors do not establish or invalidate the test proof. Docker never receives an Actions cache credential; this cache only speeds rebuilding the same checked-out source.

## Proposed destination per repository

These are selected migration targets, not completed changes or measured memory profiles. Retain existing test commands, dependency services and required verdict names when converting consumers. The central workflow is an admission and verification foundation; consumer adapters, toolchain setup and same-run artifact handoff still need repository-specific implementation before jobs can move.

| Repository | Heavy | Medium | Hosted |
| --- | --- | --- | --- |
| wodiq-platform | API, Management, Worker PostgreSQL proofs | shared runtime build, core, migration, client | routing, basic/policy, security, publication |
| gate | existing release images, release scan, browser, frontend static | PHP, API contract | immutable security producer, ARM scanner, policy, deploy |
| tracker | PostgreSQL backend, verification Docker builds | frontend static | security, policy, publication, deploy |
| notify | Docker quality and applicable image validation | none initially | routing/verdict and publication/deploy |
| console | PostgreSQL PHP tests and release Docker verification | PHP lint | security/verdict and deploy |
| wodiq-app | protected Docker verification after separate release review, Linux typecheck/unit build | none initially | certification and deploy |
| marcel-site, tuinstra-site, wodiq-site, openairco-site | Docker verification | Node verification | Docker scan, release/deploy control |
| status | Docker/PostgreSQL CI | none initially | scheduled host monitor |
| openairco | Linux Flutter verification | none initially | macOS Apple checks and security |
| devops | no self-execution on Sanctuary | no self-execution on Sanctuary | all control-plane tests and security |
| agent-lab | no active CI | no active CI | no active CI |

Two heavy slots queue WODIQ's third concurrent PostgreSQL job. Medium jobs have 3 GiB RAM and 4 GiB reserved backing; workload fit needs actual measurements. Docker quality belongs on heavy initially, but its 24 GiB reservation needs a successful guest run before broad activation. The first 12 GiB Gate guest filled its disk; current NVMe free-space admission temporarily serializes heavy jobs.

## Required checks and consumer evidence

Reusable calls add nested job contexts. Preserve existing required check names through always-running hosted verdict jobs. Require a successful reusable-call result plus exactly one route proof matching `preflight-trusted`: `true` requires `trusted-proof=true` and an empty hosted proof; `false` requires `hosted-proof=true` and an empty trusted proof. Each route emits its literal proof only in a normal final step after the fixed adapter succeeds. Missing, duplicate, failed, cancelled or entirely skipped execution cannot satisfy the verdict. Do not replace required names with conditional jobs, or remove branch protections. WODIQ can enforce the same contract in its existing Workspace baseline rather than add a check solely for naming. There is no extra hosted finalizer, which would consume another rounded job minute.

WODIQ runtime output is bound to the synthetic PR merge SHA, run ID and run attempt. The central `wodiq-runtime-build` route uploads the adapter-staged `${{ runner.temp }}/ci-runtime-artifact/` under `ci-runtime-${github.sha}-${github.run_id}-${github.run_attempt}`. Selected API, Management and Worker routes download only that current-run artifact before invoking the adapter; `scripts/ci/runtime-artifact.mjs` checks its receipt, digest and source identity during install. Gate's frontend route uploads `frontend/.output` only after a successful selected build, under `gate-frontend-output-${github.run_id}-${github.run_attempt}` for the existing browser consumer. Missing output fails either route. Preserve all suite commands, selected proof requirements and database services when moving adapters; cross-PR build artifacts and cached test outcomes are forbidden.

## Required organization boundary

Create a new group named `sanctuary-trusted-verification`, with visibility `selected`, public access disabled, `restricted_to_workflows=true`, and initially one selected workflow: `Tuinstra-DEV/devops/.github/workflows/reusable-trusted-verification.yml@<reviewed-full-commit-SHA>`.

The selected private repository IDs are checked in at [org-routing-repositories.json](../runner/policy/org-routing-repositories.json). Public DevOps and inactive Agent Lab are excluded. The group permits only jobs directly defined in the immutable central workflow, not arbitrary PR workflow jobs that copy a runner label. The workflow checks GitHub event and REST identities before queueing a group job; only the human `marcel-tuinstra` author/actor/triggering actor on a same-repository non-fork PR qualifies. Untrusted source retains hosted checks. There is no caller-supplied trust boolean, command string, or arbitrary runs-on input.

GitHub can return an empty `pull_requests` association for a valid PR workflow run. The preflight then requires exactly one open PR for the validated source branch and requires it to be the current event PR, in addition to matching the run's event, head SHA, head branch, actors, private repository, current PR and branch ref, exact merge parents, and pinned reusable workflow. A missing or multiple association, unsupported branch name, or ambiguous branch lookup rejects the trusted route. Stale, mismatched or unavailable identity evidence uses the hosted fallback.

Do not repurpose the existing Default group or assume repository JIT group ID 1 inherits organization restrictions. Use organization JIT registration and organization runner lookup/deletion for the new group. Per-repository labels retain attribution; labels alone are not an admission boundary. The manager must verify the exact group policy, workflow commit and private repository IDs before registering anything.

The separate organization credential needs organization **Self-hosted runners: write**, plus **Actions: read** and **Pull requests: read** for the selected consumer repositories. A fine-grained token or scoped App is preferable to a classic PAT with broad `admin:org`. Keep it out of chat, source, Tracker, logs and PRs; provision it through a root-only host credential and systemd LoadCredential. It is never sent to the guest, which receives only its single-use JIT configuration. Credential creation/access expansion requires Marcel's explicit approval and interactive handling; no scope was changed by preparing this implementation.

The opt-in Ansible variables record the organization group, exact workflow reference and repository-ID map. Enabling them requires the independently provisioned `/etc/ci-runner/org-github.token` to exist as root:root mode 0600; Ansible must never receive or copy its value. A separate persistent unit drop-in adds only the `org_github_token` credential. The existing repository credential and unit stay intact. Do not run the whole runner-host provisioning role merely to activate this route: it also handles images, networks and firewall state. A targeted, recoverable activation must preserve the actual installed four-pool policy and let existing leases drain.

## Rollout order

1. Locally verify the central workflow and manager, independent review, then normal protected PR merge.
2. Prepare the exact full-SHA group configuration and obtain the required access approval. Verify the created group policy; provision the separate host credential privately.
3. Deploy optional org routing in four-pool mode with default disabled; enable only after exact API group evidence passes. Preserve a byte-identical backup and let active jobs drain.
4. Convert the four major consumers with fixed adapters and current-run artifact handoff where required, preserving suite commands and required final-check names. Run local tests before one required PR validation.
5. Convert the remaining active Linux consumers; keep security, publication, macOS and immutable ARM workflows hosted. No product release is started by this rollout.
6. Observe actual assigned jobs, Docker behavior, peak backing usage, memory reserve and successful lease cleanup. Record hosted minutes against the audit baseline; do not claim money saved before billing evidence supports it.

If organization access or a restricted workflow is missing, leave new routing disabled and keep runnable hosted consumers. Never queue medium jobs into the default unrestricted group, silently reuse the repository JIT path, or bypass required checks. Rollback restores consumer workflow pins and disables organization routing after active leases finish; existing release routing remains available.

### PHP in restricted guests

Trusted PHP jobs select `/usr/bin/php8.3` (Gate) or `/usr/bin/php8.4`
(Tracker and Console) through job-local `php` and `composer` wrappers. The
workflow validates the fixed version, required extensions and Composer v2
before dependency installation. It never invokes sudo; the guest service
retains `NoNewPrivileges=yes`. Hosted fallback keeps its existing setup action.

The immutable image must include PHP 8.4 `intl` and `pgsql` at the same pinned
package version as its PHP CLI, in addition to the existing XML, mbstring, zip
and curl packages. The image contract verifies both PHP extension sets.
A workflow change alone cannot repair an older image that lacks these modules.
Activate a replacement image only after existing leases drain; preserve the
previous digest image for rollback and execute real PHP canaries afterward.

During a reviewed workflow revision transition, the manager accepts either
legacy `org_workflow_ref` or `org_workflow_refs` with at most two distinct
full commit references to this same workflow. The GitHub group must match
that exact set, without wildcard or branch references. Each candidate run
must name one accepted central revision with matching SHA; new leases record
that reference and assignment revalidates it. Existing consumers may retain
the previous reviewed revision while PHP consumers adopt the repaired one.
Drain leases before removing their accepted revision; never replace the
repository, human-actor or current-PR admission checks with labels alone.

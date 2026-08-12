# Reusable Heavy CI v2 contract

`reusable-heavy-ci-v2.yml` is the shared build-once/test-many orchestration contract. It centralizes runner routing, dependency-cache isolation, immutable artifact handoff, typed stages, timing, and failure evidence. Repository-specific install, build, and test semantics stay in a caller-owned Bash adapter.

`heavy-ci/v2` is the schema version of this contract. It is not a mutable Git tag and does not change the platform's current v10 release channel. A later platform release must use a new immutable semantic tag and consumers must pin its resolved 40-character commit SHA.

## Execution graph

```text
hosted checkout + preflight decision
  -> bootstrap + build
       -> unit
       -> integration
       -> e2e-prepare + browser
       -> live-smoke
  -> consolidated timing evidence
```

Bootstrap and build execute exactly once. Every enabled fan-out stage downloads the same artifact by the artifact ID returned by the build job. Before extraction it verifies the contract version, source SHA, repository ID, run ID, run attempt, artifact name, archive checksum, and archive paths.

The preflight is the only job eligible to run before the queue decision. It always uses `ubuntu-24.04`, checks out the caller revision with credentials disabled, and validates the caller source and routing context before a build or fan-out job can become eligible.

## Preflight decision contract

The machine-readable `preflight-decision` output is one of `blocked`, `skip`, or `proceed`. The current v2 policy emits `proceed` for a valid full run and `blocked` for a fail-closed validation result. `skip` is reserved in the schema for a future explicit no-work policy; v2 does not infer a skip from changed paths or from disabled optional stages because bootstrap/build semantics are caller-owned.

`preflight-reason-category` is stable within `heavy-ci/preflight-v1`:

| Decision | Reason category | Meaning |
|---|---|---|
| `proceed` | `hosted-requested` | A trusted context explicitly selected hosted execution. |
| `proceed` | `trusted-heavy-approved` | A trusted context passed all gates and may queue the isolated heavy runner. |
| `proceed` | `untrusted-hosted` | A fork, Dependabot, or `pull_request_target` context explicitly selected hosted execution. |
| `proceed` | `untrusted-hosted-fallback` | An untrusted context requested `trusted-heavy`; the complete enabled stage set is routed to hosted instead. |
| `blocked` | `invalid-contract` | An enum, identifier, boolean, or retention input is invalid. |
| `blocked` | `invalid-context` | Repository, run, actor, fork, ref, or workflow identity context is missing or inconsistent. |
| `blocked` | `unsupported-event` | The event is outside the documented allowlist. |
| `blocked` | `immutable-reference-required` | A cross-repository caller did not pin this reusable workflow by a full commit SHA. |
| `blocked` | `missing-entrypoint` | The caller revision does not contain the adapter. |
| `blocked` | `missing-lockfile` | The caller revision does not contain the lockfile. |
| `blocked` | `unsafe-input` | A path is unsafe, accesses `.git`, uses a symlink, or names a forbidden cache. |
| `blocked` | `input-path-conflict` | Cache, artifact, adapter, or lockfile paths overlap unsafely. |

`preflight-evidence` is one JSON object with schema `heavy-ci/preflight-v1`. It records the decision and reason, requested/effective execution class, trust tier, event, enabled-stage count, duration, caller and reusable workflow SHAs, and two queue-cost counters. `planned-expensive-jobs` counts build plus enabled fan-out jobs after approval. `avoided-expensive-jobs` reports that same candidate count when a validation blocks before queueing; it is zero for `proceed`. The evidence is also written to the hosted preflight job summary so a blocked run retains its decision even when workflow-call outputs are unavailable to the caller.

The event allowlist is `push`, `pull_request`, `pull_request_target`, `merge_group`, `workflow_dispatch`, `schedule`, `repository_dispatch`, and `workflow_run`. Unknown events fail closed. Fork, Dependabot, and `pull_request_target` work always uses the isolated cache tier, cannot write caches, and runs the full enabled stage set on hosted when valid. Same-repository `./.github/workflows/...` calls must resolve caller and reusable files from the same commit. Cross-repository calls must use a 40-character SHA that equals the resolved reusable workflow SHA. Both resolved SHAs are validated and included in evidence.

## Adapter interface

The `entrypoint` input names a repository-relative Bash script. The workflow passes exactly one of these fixed stage names; callers cannot inject free-form shell commands:

| Stage | Required behavior |
|---|---|
| `bootstrap` | Install locked dependencies and prepare tool downloads. |
| `build` | Produce `artifact-path`. |
| `unit` | Run unit/static checks against the restored build. |
| `integration` | Run repository-specific integration checks. |
| `e2e-prepare` | Install or prepare the pinned browser/runtime before `browser`. |
| `browser` | Run browser/E2E checks. |
| `live-smoke` | Run caller-owned smoke checks without receiving shared secrets. |

The caller controls whether optional stages run. The shared workflow never encodes WODIQ or Tracker commands, databases, credentials, routes, or product behavior.

## Inputs

| Input | Type | Default | Notes |
|---|---|---|---|
| `contract-id` | string | `default` | Safe identifier; separates two calls in one workflow run. |
| `execution-class` | string | `hosted` | Only `hosted` or `trusted-heavy`. |
| `cache-policy` | string | `restore-only` | `off`, `restore-only`, or `trusted-write`. |
| `cache-path` | string | `.cache/heavy-ci` | Tool download cache only. |
| `cache-schema` | string | `v1` | Explicit invalidation dimension. |
| `toolchain` | string | required | Stable runtime/package-manager identifier. |
| `lockfile` | string | required | Repository-relative lockfile hashed into the cache key. |
| `entrypoint` | string | `.github/ci/heavy-ci` | Repository-owned adapter. |
| `artifact-path` | string | `.heavy-ci/payload` | Build payload packed once and restored by every stage. |
| `run-unit` | boolean | `true` | Enables `unit`. |
| `run-integration` | boolean | `false` | Enables `integration`. |
| `run-browser` | boolean | `false` | Enables `e2e-prepare` and `browser`. |
| `run-live-smoke` | boolean | `false` | Enables `live-smoke`. |
| `artifact-retention-days` | number | `14` | Allowed range 1-30 days. |

All paths must be normalized, repository-relative, distinct from `.`, free of dot segments and control characters, and outside `.git`. The checked-out entrypoint and lockfile must be regular non-symlink files. Cache and artifact paths cannot use symlink components, overlap each other, or contain the adapter or lockfile. Dependency-cache paths cannot contain `node_modules`, Composer `vendor`, `dist`, `build`, or `.output`; those are installed dependencies or build products, not trusted cross-run caches.

## Outputs

The workflow exports `contract-version`, `artifact-name`, `artifact-id`, `artifact-digest`, `payload-sha256`, `effective-execution-class`, `cache-key`, `cache-hit`, `metrics-artifact`, `preflight-decision`, `preflight-reason-category`, `preflight-evidence`, `planned-expensive-jobs`, and `avoided-expensive-jobs`.

Artifact names include contract ID, repository ID, source SHA, run ID, and run attempt. They are unique within retries and cannot overwrite another run's artifact. Failure and timing evidence is uploaded per stage and consolidated as newline-delimited JSON.

## Cache ownership and key dimensions

The shared workflow owns restore/save mechanics and key construction. The consumer owns the contents of `cache-path`, the lockfile, `toolchain`, and `cache-schema` values.

Platform-owned warm content is limited to the versioned runner image, system toolchains, and base/container layers maintained by the runner platform. It never contains repository dependencies, application build output, secrets, or consumer state. The runner image dimension and explicit `toolchain` value invalidate repository caches when that platform content changes.

The exact cache key contains repository ID, contract major and ID, trust tier, runner OS, runner architecture and image, toolchain, cache schema, lockfile hash, and a content hash of the lockfile plus adapter. There are no broad restore prefixes. Cache entries therefore never cross repositories, contract calls, trust tiers, runner images, architectures, toolchains, schemas, or dependency states.

- `off`: no restore and no save.
- `restore-only`: exact-key restore only; never save.
- `trusted-write`: save only on a trusted `push` to the repository's default branch. All other events behave as restore-only.

Forks, Dependabot, and `pull_request_target` are assigned the `untrusted` cache tier and cannot save. A `trusted-heavy` request from those contexts selects the documented hosted/full fallback before any self-hosted job queues. Same-repository trusted pull requests may restore the exact trusted default-branch cache but cannot write it.

The contract test executes the actual hosted preflight script against positive and negative contexts. Cases cover hosted and trusted routing, local same-revision calls, fork/Dependabot/`pull_request_target` fallback, mutable cross-repository workflow refs, malformed identity context, missing or symlinked caller inputs, path conflicts, unsafe cache paths, parent traversal, broad restore prefixes, privileged permissions, secret inheritance, incomplete artifact binding, and non-SHA Action references.

## Security boundaries

- Jobs have only `contents: read`; the workflow has no package, deployment, OIDC, attestation, or security-event write permission.
- No secrets are declared or inherited.
- Artifact downloads use the current run's build output ID; callers cannot provide a run ID or artifact ID.
- Full SHA pinning is mandatory for every external Action.
- Artifact creation rejects symbolic links. Extraction rejects absolute paths, parent traversal, and entries outside `artifact-path`.
- Build payloads are same-run transport, not reusable dependency caches. They are portable only between compatible runner OS/architecture and toolchains.

## WODIQ and Tracker compatibility

The contract fixtures model both shapes without modifying either consumer:

- WODIQ: Node/npm adapter with unit and browser stages.
- Tracker: Node/pnpm-style adapter with unit, integration, and browser stages; backend/database orchestration remains caller-owned.

Both use the same reusable workflow. A future consumer migration changes only its adapter and caller inputs, not the shared contract.

## Rollout and rollback

DEV-6 adds the contract in parallel and migrates no consumer. Validate it in this repository first, then canary a reviewed full commit SHA on hosted runners. Measure bootstrap, build, cache, artifact-transfer, and test durations before claiming an improvement or enabling `trusted-heavy`.

Rollback is a normal consumer commit restoring its last known-good full workflow SHA. Keep the hosted caller path available throughout rollout. Never move a release tag or rewrite history; publish a new immutable patch or major release for a correction.

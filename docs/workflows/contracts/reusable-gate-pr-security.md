# Gate PR Security reusable workflow

DEV-46 supplies a producer for the separate Gate publisher. It prepares exact PR
sources, runs the immutable scanner with no network or credentials, uploads a
bounded artifact and submits its actual ID/digest with a fresh Actions OIDC
identity. Receipt acceptance is not a security pass. Gate publishes the App-owned
`Gate PR Security` check only after independently verifying completed execution.

## Review and enrollment

This delivery is preparation for the Gate/DevOps pilot. It does not enroll a
consumer, create an App, alter branch rules or increase a budget. The scanner's
verified local OCI digest is fixed in `constants.py`. Its proposed private GHCR
package must be published with that same digest and granted Actions read access
per approved consumer before live use. The current local GitHub credential lacks
package-read permission; remote publication/access has not been established.
Do not substitute a mutable tag, rebuild silently, or make this image public.

Before enrollment, verify all of:

- Reviewed immutable workflow, helper-action and scanner image identities.
- Private DevOps action/workflow sharing and package access for the consumer.
- A separate publisher App, registry entry, migrated storage and recovery scheduler.
- Exact provider-visible required producer job name, repository/owner IDs and
  registered `pull_request_target` event mode.
- Green and deliberately blocked live probes, including a fork and bot PR.
- App-specific branch requirements, current base/head reevaluation and strict
  freshness, plus non-passing handling of missing/cancelled/uncertain receipt.

No merge queue is enrolled by this implementation. Unsupported event contexts
fail explicitly; they cannot generate passing evidence. A repository using merge
queues needs an implementation and pilot that bind its merge-group sources
before making this workflow its required queue check.

## Consumer contract

The caller template has no path, actor or documentation filters and supplies no
commands, scanner settings or policy overrides. It uses a full reviewed commit
SHA. It needs only Contents read, Pull requests read, Actions read, Packages read
and OIDC write.
There is no `secrets: inherit`, PAT or publisher App key in the workflow.

`pull_request_target` is deliberate: its workflow runs in trusted default-branch
context while the PR can target another branch. The scanned sources come from
independently fetched PR base/head SHAs, never from the execution `GITHUB_SHA`.
Every ordinary, draft, documentation, fork and bot PR follows the same producer
path. PR files are never checked out and then executed in the privileged job.

The helper action is committed first. The reusable workflow pins that support
commit. The caller template then pins the workflow commit. These are ordinary
commits in one Story branch and one PR; no mutable tags or self-referential SHA
placeholder is used in the delivered caller.

## Source and execution boundary

Preparation reads the fixed GitHub API and creates a fresh bare object store.
Only verified regular Git blobs are materialized. Paths, modes, source sizes,
object identities, symlinks, submodules and LFS pointers are checked against the
strict scanner contract. No repository hooks, filters, scripts, package manager,
plugins, submodule initialization or PR scanner configuration are executed.

The scanner uses the fixed Linux ARM64 image and a non-root host UID/GID so the
host can read its owner-only result. Only the object store and base/head source
mounts are read-only; its output mount and bounded scratch are writable. Root
filesystem is read-only, networking is disabled, capabilities are dropped,
no-new-privileges is set, and memory/CPU/PID/time limits apply. No Docker socket,
runner workspace, authentication directory or environment credentials are passed
into the container. Registry credentials exist only during the trusted pull.

The initial profile proves secrets, PHP, JavaScript/TypeScript, Composer and npm
package-lock coverage. Unsupported pnpm/Yarn, framework, infrastructure and native
scopes remain incomplete. This delivery does not declare full Gate or DevOps
repository coverage merely because the scanner process completed.

## Artifact and receipt

The artifact contains exactly two regular files, with no directory entries:

```text
evidence.json
reports/result.json
```

The inner bytes are unchanged from the scanner. The outer assurance manifest
binds those bytes to repository, exact PR sources, actual run/attempt and reusable
workflow identity. It mirrors outcome, coverage and findings and records trusted
policy/image/dataset identities. No source files, scanner logs or signed URLs
are uploaded. Archive and expanded content limits are eight MiB; uncompressed
upload avoids false rejection by the publisher compression-ratio limit.

Artifact upload uses a unique run/attempt name, refuses overwrite and requires
files. The pinned upload action's returned artifact ID and SHA-256 are sent to
Gate; names alone establish no trust. Only after upload does the submit step
request an OIDC token for the fixed Gate audience. It compares current job/run,
repository, event and immutable workflow claims before sending the six-field
receipt to the fixed HTTPS endpoint. Gate verifies the signature independently.

A valid scanner exit `0`, `1` or `2` corresponds to pass, blocked or incomplete
semantic evidence. After successful upload and durable 202 receipt, the producer
job finishes successfully for all three: Gate interprets evidence once that job
has completed. It does not wait on its own check. Scanner crashes, malformed or
missing reports, stale identities, upload failure or exhausted receipt retries
fail the producer; they never manufacture a clean report.

## Operations and cost

Use the hosted ARM64 runner with the fixed job timeout. Retries are bounded,
reuse the same immutable artifact and preserve receipt identity. GitHub Actions
run/job timings and the job summary provide runner and scan-duration evidence;
no new billing service is introduced. During the pilot, record the actual
runner minutes and artifact retention with the green/blocked probes. Do not
raise budgets automatically or repeat expensive scans while a local failure
remains reproducible.

Raw output, source material and pull credentials stay in the marked temporary
workspace and are removed by the always-run cleanup. An abrupt runner loss is
handled by hosted runner disposal. The uploaded evidence retains fourteen days
of technical review data; longer retention is an explicit operational decision.

Rollback means removing consumer enrollment and restoring a previously reviewed
workflow/image pair with matching policy and evidence. Never reuse a previous
successful App check for a new source or configuration. Preserve publisher
receipt records for reconciliation of uncertain writes.

## Primary references

- [Actions OIDC claims](https://docs.github.com/en/actions/reference/security/oidc).
- [Reusable job workflow identity](https://docs.github.com/en/actions/reference/workflows-and-actions/contexts#job-context).
- [Target-event security](https://docs.github.com/en/actions/reference/security/securely-using-pull_request_target).
- [Private workflow/action sharing](https://docs.github.com/en/actions/how-tos/reuse-automations/share-with-your-organization).
- [Pinned upload action file selection](https://github.com/actions/upload-artifact/blob/043fb46d1a93c77aae656e7c1c64a875d1fc6a0a/src/shared/search.ts).

## Local verification

Run from the existing DevOps checkout:

```sh
make lint
make test
PYTHONDONTWRITEBYTECODE=1 python3 scripts/gate-pr-security-local-integration.py \
  --gate-backend /absolute/path/to/existing/gate/backend \
  --output-dir /absolute/path/to/new/temporary/probe-output
```

The ordinary suite includes 29 producer unit tests and a workflow contract check.
It tests source identities, fork bindings, stale attempts, unsafe paths, output
limits, Docker isolation, cleanup, malformed/unsupported evidence, verified
absence, OIDC binding, artifact submission and bounded retries.

The optional integration command requires the existing Gate backend dependencies,
PHP with ZIP support, Git, Docker with a local Unix socket and the already loaded
reviewed ARM64 scanner image. It does not pull an image or access GitHub/Gate.
It creates only synthetic Git objects and regular source fixtures, never another
application checkout. The scanner runs with the production restrictions and its
actual report is packaged and verified by Gate's real PHP artifact verifier.

The local adapter replaces the fixed registry reference with the exact already
loaded image ID. Thus it proves the scanner/helper/publisher data contract,
without claiming registry distribution, live OIDC authentication or App checks.
Synthetic identities are explicit; generated ZIPs and results stay in the
chosen local output directory. Any unexpected outcome exits nonzero.

The first integration run proved pass, blocked and incomplete outcomes. It also
exposed a publisher mismatch for a complete scope whose last source file was
removed: the producer correctly retained `verified_absence`, while the publisher
still treated every empty input list as incomplete. Delivery requires that
publisher correction and a successful rerun; weakening or rewriting the scanner
report is not an acceptable workaround.

### Acceptance evidence

| Requirement | Attributable evidence |
|---|---|
| Immutable, isolated source execution | Caller → workflow → helper use three normal immutable commits; scanner uses its verified OCI manifest digest. Source tests verify exact blobs, rejected symlinks/LFS/unsafe paths and credential-free container arguments. |
| Trusted evidence and separate identity | Packaging preserves exact scanner bytes and validates policy/source/coverage. Submission tests bind current OIDC claims and actual upload ID/digest. The scanner receives neither repository nor OIDC credentials. |
| Uniform PR path | Target-event caller has no path/actor/branch filters; provider fixtures cover forks, independent head binding and stale attempts. Merge queues are explicitly unsupported and cannot pass. No queue is enrolled. |
| Failures, retries and costs | Bounded processes, report limits, semantic exits, immutable upload, bounded receipt retries, job timings and always-run marked cleanup are tested and documented. |
| Scanner/publisher integration | The optional offline command covers pass, new high finding, untrusted suppression configuration and removal of the last scoped file through the actual image and PHP verifier. Remote identity/App/branch enforcement remains pilot work. |

Full `make lint` and `make test` passed with the existing local verification
virtualenv on PATH. The default host lacks Ansible; the initial `make test`
stopped at that prerequisite, then passed with that existing toolchain. Packer
formatting was skipped because Packer is unavailable. Cross-repository fleet
policy checks were not requested (`DEPENDABOT_FLEET_ROOT` unset). No production
services or deployment credentials were used by these tests.

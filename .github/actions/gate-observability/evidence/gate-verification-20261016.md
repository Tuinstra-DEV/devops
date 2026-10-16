# Gate verification renewal — 16 October 2026

This is fresh **verification-environment** Sentry → Gate Heal evidence for
`aaf0cc9f5d97c9db74caf6bccad4160f90b21a55`. It is not a production event,
a production deployment or a successful automatic fix. The IN-2 runner/Compose
repair and intervening SDK-initialization inputs changed two integration
fingerprints; the unchanged contract requires renewing all three components.

## Actual runtimes and provenance

- Browser/Nitro: protected `develop` workflow
  [38918185832/1](https://github.com/Tuinstra-DEV/gate/actions/runs/38918185832),
  successful, artifact 12052883179. The GitHub archive digest and packaged source,
  run/attempt and runtime checksum were checked before starting its compiled
  output on localhost. Runtime digest:
  `sha256:287a9dcb81e17d96a0366eca595111f990c1b72c7c4147ceb8f70329a9a6ca11`.
  Real browser JavaScript was exercised with Playwright/installed Chrome;
  the actual Nitro fixture returned the expected HTTP 500.
- PHP: exact release-PR build [38916893651/1](https://github.com/Tuinstra-DEV/gate/actions/runs/38916893651),
  successful PHP image job, artifact 12052639481. The outer archive checksum,
  receipt source/tree, OCI index/manifest/config hashes and revision labels were
  verified. The older host Docker could not directly load the OCI archive.
  A format-only conversion verified every compressed blob and all 39 uncompressed
  rootfs layer hashes; the loaded image retained the exact original config ID
  `sha256:a7da562d6636f90a26af40db73cdab0d71358370d69e3f6ac6db7c81bb8ce022`.
  The artifact binding is the verified OCI image index
  `sha256:a10005eac9a541968ae9fcb5cefb5b531aac804e11c4d00780e600bbe07e9543`,
  not a fabricated registry push. The existing SDK was booted with a synthetic
  application secret, in-memory database/transport and only the existing Gate
  Sentry ingest DSN. No production database, OpenAI or GitHub credentials entered
  this container. A networkless inspection passed before the one explicit send.

## Observed chain

| Component | Sentry issue / event | Heal case | Actual first-party frame |
|---|---|---|---|
| browser | 154122932 / `89f5cb91d0df4a18a595b08c58a18964` | `e88951a9fc114a889ed0a97bf190a4bb` | `frontend/app/utils/sentry-verification.ts:3` |
| nitro | 154122878 / `4de2fd2736454f32aa8e420c08ce0b57` | `92639b8677064f72b0a5350534a99e59` | `frontend/server/utils/sentry-verification.ts:3` |
| backend | 154124016 / `098af94ab4d34694b0752d6fbbb25308` | `b5a995c64dd44f58b4ee914b302dee2e` | `backend/src/Service/Heal/HealRunnerFailureReporter.php:42` |

Sentry delivered all three through the existing intake before operator replays.
Each case is `triaged`, with its immutable repository/component/source/artifact
binding, one triage attempt and zero fix attempts. Fresh Sentry API inspections
confirmed zero nonempty user fields, request/breadcrumb entries, frame variables
or processing errors, and the expected filtered static exception values.
Browser/Nitro frames were mapped to the correct TypeScript lines using their
private uploaded maps. No map or raw event payload is included in this evidence.

Each real event was then replayed twice through the existing signed HTTP intake.
These are explicitly operator-controlled duplicate-intake probes, not claims of
additional provider deliveries. Both responses returned HTTP 200 and the same
case, without another attempt or fix. The sanitized observations are retained in
`gate-verification-20261016.json`.

## Synthetic grouping and preservation

The real privacy callbacks discard caller fingerprints. An initial browser and
Nitro sample therefore joined their old synthetic groups; Gate correctly preserved
those original bindings and marked the conflicting source for review. Nothing
was reset or overwritten. Changing the synthetic exception name did not prevent
Sentry's stack-based grouping.

Final samples use an operator-only wrapper around each actual SDK's existing
`beforeSend` callback. It preserves the callback result and adds only a fixed,
component-specific synthetic fingerprint **after redaction**. It does not change
frames, release, environment, privacy or routing. The compiled artifact bytes
remain unchanged. No production SDK setting was changed. These grouping helpers
run only in the isolated verification process/browser session.

The measured source/artifact mappings were enrolled as `verification` entries
before intake. Such ledger entries describe CI/runtime provenance, not a successful
Sentry chain. All 39 previous rows were retained, with protected byte-identical
backups. After a rejected atomic-replacement/worker-refresh proposal, the reviewed
alternative held the existing observability lock and updated the same mounted
inode; host and worker hashes matched. No live worker was stopped or restarted.
The ledger now has 42 rows. Existing source bindings and production receipts
remain intact; partial/unreadable ledger data remains ineligible by existing
validation.

## Scope and remaining deployment checks

Only Gate's proof and approved proof digest change. The validator, coverage,
policy selectors, permissions, Tracker proof and production controls remain
unchanged. No user-facing UI changed; UI screenshots do not apply.

Local `make lint` / `make test`, technical review, protected proof merge and the
actual hosted Gate consumer validation are recorded with the delivery. Publish
an immutable patch tag after that canary, never move an existing tag. A subsequent
Gate release must consume the new exact shared-action SHA and preserve database
version and rollback artifacts. This note alone does not claim that release or
IN-2's positive fix/QA trial has completed.

## Local checks and review, executed 16 October

- `make lint`: passed.
- `make test`: passed using the existing Python 3.14.8 / Ansible 2.21.4 toolchain,
  including all 38 observability assurance/fingerprint/action/symbol tests. The
  first invocation selected macOS Python 3.9 and stopped on missing `tomllib`;
  the same suite passed with the documented existing runtime before publication.
- `git diff --check`: passed.
- A separate read-only technical reviewer recomputed the canonical proof digest
  and all four fingerprints, checked component/source/frame/reference consistency,
  and found no concrete defect. Full candidate release success and the actual
  hosted consumer promotion remain separate gates.

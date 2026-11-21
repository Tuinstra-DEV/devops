# DEV-54 — Gate Release CI integration enrollment, 20 November 2026

This enrolls actual **verification-environment** browser/Nitro integration evidence
for `45bc74769aaa08906262f866f1eedb7fd05aab46`. It does not prove a production
release, protected-main tree identity, candidate image promotion or readiness.

## Exact build and observed chain

The successful protected-develop [SDK build44824942586/1](https://github.com/Tuinstra-DEV/gate/actions/runs/44824942586),
job136595060717, produced artifact14673521962. Its GitHub ZIP digest is
`sha256:7a0a5a3ff599fb6cde2557bcd4a2e3f4dab163b8c5023668349a32848e240f82`;
the packaged runtime digest is
`sha256:350d8ba7053eeff21f6ca7e8a37af286d990ca2b7a73aebaf1a96da15d4df033`.
Run/current attempt, protected source, human actor, job, artifact API metadata,
archive checksum, packaged source/run and runtime checksum were independently
verified before execution. Both Sentry build plugins reported successful private
map upload. The private fixture archive retains maps; it is not evidence that a
production image exposes zero maps.

The unchanged compiled bytes ran with the build's exact Node24.22.3 in an
immutable official container, read-only, capped at512MiB/0.5CPU. Only synthetic
session/backend values and a loopback DSN entered the runtime. No production
DB, GitHub, upload or Sentry API credential entered it. Actual local Chrome ran
the browser fixture; the actual Nitro fixture returned the expected HTTP500.

| Component | Sentry issue / event | Heal case | Actual mapped frame |
|---|---|---|---|
| browser | 162707841 / `945eb41f1ec445f387d223221a8c9be0` | `3af99702543b40a8bb7caf9ae7344a8a` | `frontend/app/utils/sentry-verification.ts:3` |
| nitro | 162707854 / `d1c5154786184979b98a2402f7a2b7e7` | `6479af4205234a3aa97a20c2b91e59b0` | `frontend/server/utils/sentry-verification.ts:3` |

Both real Sentry ingests returned HTTP200 with the exact reserved event IDs.
Sentry API readback confirmed source/component/environment, the mapped TypeScript
frames, sanitized static exception values and zero nonempty user fields, private
request/breadcrumb entries, frame variables or processing errors. Both existing
Heal intake cases are triaged with one occurrence, one triage attempt, zero fix
attempts, no fix PR, and exact repository/component/source/runtime/receipt bindings.
The sanitized observations are in `gate-DEV-54-verification-20261120.json`.

## Wire privacy, bounded transport and history

The previous diagnostic fixture exposed SDK11 metadata `infer_ip=auto`, added
after `beforeSend`. The proxy rejected it before any real send. Gate450's public
`beforeEnvelope` hook now sets the final event metadata to `never`. Both actual
compiled SDK11.7.0 envelopes passed an offline mock transport first, including
retained source, stack frames and debug IDs; mock event IDs are not enrolled here.
The application privacy filter and final SDK hook ran unchanged. Only afterwards
the operator forwarder added a fixed, per-component synthetic grouping fingerprint.
It did not alter compiled bytes, event IDs, source, frames or debug IDs.

The reviewed forwarder durably reserved at most one real send per component
before networking and never retried rejected/uncertain outcomes. A connection
reset during initial runtime startup occurred before any reservation; readiness
and empty state were checked before requesting the actual Nitro fixture. No
additional actual SDK sends were made. The temporary container, forwarder and SSH
tunnel were stopped; only the new fixture container was removed. No production
container or runner service was restarted.

The reviewed ledger helper at DevOps0f6153bae9305c7f0f9e3a8d6504fcef2febcec5
refused a deployment marker under both existing locks. Read-only preview succeeded
before two verification receipts were appended. All80 prior rows remained, with
a durable read-back backup and unchanged mounted inode/owner/mode. Worker readback
matched the host; a subsequent read-only preview added zero rows to the resulting82.
These bindings record verified CI provenance, not production readiness. They stay
in history after sends; no event or existing source binding was reset or relabeled.

## Applicable older evidence and the release boundary

Routing fingerprint remains
`sha256:2d6cb782a92533070e7c36a582bd1b5171a8efc4d05ef68486cf131f9757d243`.
The current Composer manifest/lock, PHP Sentry config, failure reporter, fix handler
and PHP privacy filter are byte-identical to the existing backend witness source
`53e8ee62b46bbc7fcd555682d73c8814fd67dad4`. Its exact backend component record,
original event/source/run/artifact and references are retained unchanged. It is not
claimed as a fresh backend event. Unchanged routing/deduplication evidence remains
attributed to its historical source; this renewal did not replay duplicate intake.

The new policy ID and proof file explicitly enroll the changed SDK/privacy and
reviewed Release CI deployment-symbol selectors. This does not substitute the
fixture runtime or its maps for a release candidate. A real successful two-stage
Release CI must independently bind all candidate artifacts/digests, source/run/
attempt, uploaded private maps and zero exposed production maps. Actual VM resource
peaks and the promotion dry-run are separate live evidence. Protected-main promotion
and production readiness remain required gates; the dry-run cannot authorize them.

The existing assurance validator, required coverage, permissions, Tracker policy,
runner admission and production activation flags are unchanged. Local tests cover
the real enrolled policy/proof pair using explicitly synthetic candidate fixtures;
those tests are contract checks, not live candidate proof. Complete normal Linux
PR validation is required before merging and pinning this enrollment.

The legacy Gate policy is renewed separately with its own current legacy-workflow
deployment fingerprint and the same attributed SDK observations. This keeps the
retained overlap route available; Release CI does not inherit its selector set or
policy identity. Reverting a consumer to its previous immutable action pin retains
that pin's original historical enrollment for the previous source.


## Deployment-only review 21 November

Gate source `34eded55a225f481224a17aee823ce0446144c84` pins Bookworm linux-libc-dev to 6.1.192-2 in both PHP runtime roots and grants Bake writes only to the absolute candidate export directory. Signed APT simulation changes exactly one package; AMD64 installation verifies PHP remains 8.3.36. Full local AMD64 extension compilation on ARM stopped with a cc1 segmentation fault in unchanged Xdebug code; native clean-room builds and mandatory image scans remain required. No CVE exemption or runner-policy change is introduced.

The SDK initialization, privacy and routing fingerprints remain byte-identical, as do all six historical PHP witness inputs. Both route-specific deployment fingerprints and canonical approvals are updated in `gate-DEV-54-header-review-20261121.json`. Existing actual SDK events, original source/run/attempt/artifact digests and historical PHP witness are retained without relabeling. This review is not evidence of a successful candidate build or production activation.


## Private export review 21 November

Native Release CI 44908939279 built all OCI images and private maps, then strict candidate digesting rejected Nitro dependency symlinks. Gate `8fbbb16bd1949e6af591db2937321caaf62c489a` excludes node_modules only from the separate first-party private-symbol export, matching the existing collector policy. The production filesystem and first-party symlink rejection remain intact. The actual filter regression was red then green; 34 related tests and Hadolint pass. An existing local compiled Nuxt tree retains all browser/Nitro debug-ID pairs after filtering (diagnostic validation only). Both deployment fingerprints are updated; actual SDK events and their historical source/run/attempt/artifact identity remain unchanged. A new successful native transfer/verification remains required; this review does not claim it or authorize production.


## Baked GitHub CLI review 21 November

Build 44911609859 on 19c3e3f completed all seven artifacts; Verify stopped before ZIP receipt because the pinned gh 2.45.0-1ubuntu0.3 rejects --allow-escape-sequences. The exact Ubuntu package reproduces that rejection. A local loopback fixture using that CLI confirms the corrected binary file-descriptor stream is byte-exact without real credentials. Gate a8c39d606b4af1507206208273d6ce6d0cbc5fe5 removes only the unsupported option; artifact identities/digests and strict extraction are unchanged. 36 relevant regression tests pass. Only the release-route deployment fingerprint changes; the legacy route and SDK/privacy/routing witnesses remain unchanged and retain their real historical provenance. A fresh successful GitHub Build/Verify remains required; no production authorization is claimed.

## PHP extension transport review — 21 November 2026

Source 37f6530dd146ab38be93e8a76ed6886271a83702 retains PHP 8.3.36, APCu 5.1.28 and Xdebug 3.3.0. The existing PECL downloader returned HTTP 500 while curl fetched the same APCu HTTPS archive with HTTP 200 in the same immutable base. Downloaded archives now require fixed SHA-256 checks and bounded HTTPS retries before local PECL installation. An isolated native AMD64 container compiled, installed and loaded both exact versions; a corrupted archive was rejected before installation. No runner image, admission, SDK initialization, privacy or routing changes. Both deployment selectors change because the shared application PHP Dockerfile changes; their reviewed fingerprints and canonical proof hashes are recorded in gate-DEV-54-pecl-download-review-20261121.json. Original SDK/backend witnesses remain bound to their actual sources and runs. This enrollment is not a new live event, full release proof or production authorization.

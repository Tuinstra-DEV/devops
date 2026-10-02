# Gate assurance contract v1

Owner: DevOps for the shared standard and scanner updates; the application team
for runtime instrumentation and evidence; Gate for trusted check publication and
Heal routing. Introduced by DEV-45. Implementation follows in IN-25, IN-26 and
DEV-46; consumer enrollment is separate. This document does not certify that any
repository currently satisfies the complete standard.

## Three independent decisions

| Check | Applies to | Passing condition |
|---|---|---|
| `Gate PR Security` | All 13 repositories in the inventory | Complete, trusted comparison with no new or worsened high/critical finding after valid policy exceptions |
| `Gate Observability` | The eight applications, at production promotion | Required runtime coverage, exact source/artifacts/symbols, applicable integration evidence and separately verified production configuration |
| `Gate Heal QA` | Automatically generated fix PRs | Independent QA on the current commit, with required CI and Protect evidence; human merge remains required |

The existing `reusable-gate-baseline.yml` checks file/configuration presence. Its
success is not a substitute for these decisions. PR security does not contact
Sentry as part of its verdict. Runtime telemetry failure cannot block application
execution. Automatic fixes remain disabled during this migration.

## Versioned documents

- [Inventory](../../../contracts/gate-assurance/v1/inventory.json): 13 security
  consumers, eight runtime applications, required owned components and planning
  ownership. It grants no access and is not the trusted Sentry routing registry.
- [Schema](../../../contracts/gate-assurance/v1/schema.json): JSON Schema 2020-12,
  `schema_version: 1.0`, `policy_version: gate-assurance-1`.
- [PR example](../../../contracts/gate-assurance/v1/examples/pr-security.incomplete.json)
  [native runtime example](../../../contracts/gate-assurance/v1/examples/observability-native.incomplete.json)
  and [runtime example](../../../contracts/gate-assurance/v1/examples/observability.incomplete.json):
  synthetic, incomplete fixtures, not real GitHub/Sentry/artifact IDs or evidence.
- [Onboarding template](../../../templates/gate/observability.example.json): copy
  into the consumer as `.gate/observability.json`, replace the repository and
  component inventory, and attach real evidence as enrollment proceeds.

Schema validation checks structure only. Semantic validation, policy applicability,
artifact verification, provenance and authenticity remain mandatory. No consumer
is compliant because a JSON file parses. `evidence_mode: fixture` must be rejected
by production publishers and release validators. Unsupported schema/policy versions
cannot pass. Breaking field or interpretation changes require a new major contract;
additive changes require explicitly reviewed compatibility and new fixtures.

IDs are decimal strings where APIs can exceed JavaScript integer precision.
Commits are full lowercase Git SHAs; digests use `sha256:` plus 64 hex characters.
Repository names are display/routing hints: verified numeric repository and owner
IDs determine authorization. Inventory components are stable registration IDs;
renaming one is a reviewed mapping migration, not an event-driven update.

`when-deployed` components still require coverage when installed. Enrollment and
each release record their actual deployment applicability in the deployment
artifact; a caller cannot omit an installed component by choosing that status.
All component inventories must be reconciled with deployment/build manifests.
Tracker includes its independently served MCP API and four worker roles; it does
not need a Node runtime merely because its static browser assets use Nuxt. Status
has no required browser SDK without an owned browser JavaScript runtime. Optional
host monitors remain explicit. OpenAirco has no Tracker Story by agreement.

## Required scanner coverage

The inventory profiles are initial requirements, not an exhaustive file allowlist.
Trusted input discovery must reconcile all owned languages and configuration,
including newly introduced ones; an unmapped required scope is incomplete.
Every repository requires **Gitleaks** secret detection, centrally pinned rules and
fully redacted output. Head-controlled ignore files, inline suppressions or scanner
configuration cannot weaken mandatory coverage. Existing stricter audits, secrets
checks and release-image scans remain enabled.

| Profile | Required source/configuration coverage | Required dependency coverage |
|---|---|---|
| `php` | Semgrep CE PHP security rules; explicit Symfony/config fixtures | OSV on every resolved `composer.lock` |
| `javascript-typescript` | Semgrep CE JS/TS rules, including proven handling of owned Vue/Astro embedded scripts, server routes and security configuration | OSV on every applicable npm/pnpm/yarn lockfile |
| `python` | Semgrep CE Python security rules | OSV on supported resolved locks or fully pinned requirements |
| `shell-infrastructure` | Approved shell security rules and Trivy misconfiguration rules for supported IaC; explicit Actions/Compose/Ansible/systemd coverage where present | Applicable resolved locks; exact image package evidence where required |
| `dart-flutter` | A proven Dart security engine/rules profile; initially unsupported until capability fixtures pass | OSV `pubspec.lock` plus native dependencies |
| `apple` | Candidate Semgrep CE Swift rules plus explicit entitlements, ATS and watch-target rules; capability must be proven | Trivy for `Package.resolved` / `Podfile.lock` when present |
| `android` | Approved Java/Kotlin security rules plus manifest/network-security configuration; Capacitor also needs JS/TS coverage | Applicable Gradle lockfiles and JS dependency locks |
| `containers` | Trivy supported Dockerfile/IaC misconfiguration rules | Image packages only from independently built, exact-SHA trusted artifacts; source scanning cannot claim image coverage |

These are requirements and selected engines, not a claim that today's Gate adapters
implement every profile. IN-25 must prove pinned engine/rule/input support before
registering a capability. Semgrep CE has limited, single-function analysis; it must
not be represented as the paid cross-file product. Swift is community supported,
Bash experimental, and Dart support is not established by the CE matrix. A JS/TS
parser alone proves no Vue/Astro template coverage. Trivy's YAML support proves no
arbitrary YAML semantics. OSV's documented inputs do not establish Swift parsing.

Primary capability references, reviewed 27 September 2026:
[Semgrep CE](https://docs.semgrep.dev/semgrep-ce-languages),
[Semgrep product languages](https://docs.semgrep.dev/supported-languages),
[OSV inputs](https://google.github.io/osv-scanner/supported-languages-and-lockfiles/),
[Trivy Swift](https://trivy.dev/docs/latest/coverage/language/swift/),
[Trivy IaC](https://trivy.dev/docs/latest/coverage/iac/),
[Gitleaks](https://github.com/gitleaks/gitleaks#usage).

For each required scope, record discovered, parsed, scanned and skipped inputs,
with safe reason codes in referenced report payloads. Positive vulnerable, clean,
malformed and unsupported fixtures must establish adapter behavior. Missing tools,
rules, inputs, parsing, advisory snapshot, required scope or deadline completion
produce `incomplete`. Trusted policy may establish non-applicability from verified
absence; a scanner's zero-result exit alone cannot. Unsupported Dart or native
coverage is an enrollment blocker, not permission to label partial scans complete.

No application installs/builds, package scripts, plugins, Gradle execution or
head-controlled commands run inside the read-only source scanner. Any necessary
image build belongs to a separately isolated, credential-free build boundary, with
an exact source/artifact binding. The scanner receives the resulting artifact as
input; Dockerfile scanning never substitutes for scanning its built packages.

## Comparison and outcomes

Both sides use the same frozen scanner image, tools, trusted policy, rules and
advisory dataset. Advisory updates are never downloaded separately between base
and head. The dataset manifest binds all constituent inputs by digest. Cache reuse
requires the complete matching identity. Unknown severity or unparseable findings
make required evidence incomplete, rather than being mapped to a low severity.

- **New:** a head finding has no matching base identity.
- **Worsened:** a matched finding's severity/risk increases and becomes or remains
  high/critical. Medium to high and high to critical block. New high/critical block.
- **Existing debt:** an unchanged or improved matched finding remains visible.
  A changed advisory dataset is applied to both sides, not presented as a new-code
  regression. A new advisory affecting both sides is existing debt for this PR rule.
- **Resolved:** no matching head finding remains, with complete applicable coverage.
- **Identity:** stable rule/category and normalized location or dependency identity;
  raw line numbers, variable package-version text and free-form titles alone are
  insufficient. Use source rename/move evidence to preserve matched findings.
  Ambiguous identity changes require review or conservative blocking, not disappearance.
- **Exceptions:** approved identifiers, scope, reason, owner and expiry come from
  trusted policy. PR-head suppressions are ineffective until separately approved.
  Expiry/revocation is visible and requires review, but does not itself turn old debt
  into a new finding. Apply the same policy to both sides; retain stricter existing
  checks and track debt remediation separately.

`pass` requires complete coverage and no applicable blocking delta. `blocked` means
complete evaluation found a blocking delta. `incomplete` means the verdict cannot
be established; it is non-passing. If execution is incomplete and also has findings,
retain the findings and choose `incomplete`. GitHub neutral/skipped/success must not
represent incomplete required work. No human or AI opinion replaces deterministic
scan evidence for this check.

## PR evidence and trusted publisher

The `pr-security-evidence` document binds numeric repository/owner IDs, PR context,
exact base/head, run ID/attempt, approved reusable workflow ref/SHA, policy digest,
scanner digest, dataset manifest and per-scope coverage. `findings` is a redacted
structured comparison. `payload_files` binds each separately stored report file;
**it does not contain the digest of its own enclosing manifest or archive**.

The publisher independently retrieves the GitHub artifact by platform ID from the
verified run/attempt and records that ID and the downloaded archive digest in its
receipt. Do not trust an artifact name, caller-supplied URL, self-declared digest or
OIDC alone as proof of payload content. Verify producer jobs, completeness, manifest
binding and payload digests. Reject duplicate/colliding artifacts, stale attempts,
path traversal, symlinks, unexpected files, oversized archives and expired evidence.
Artifact retention must outlast the review/rollback period; an expired artifact is
missing evidence and must be reproduced when required.

Use GitHub OIDC with expected issuer/audience and a registered full-SHA reusable
workflow identity (`job_workflow_ref` plus independently verified execution facts).
Verify owner/repository IDs, workflow SHA, run/attempt, event/PR relationship, current
base/head and all required jobs. Caller claims, branch names and arbitrary check
names cannot authorize publication. Source workflows must not supply their own
trusted rules, allowlists, scanner image, App credentials or publisher identity.

A dedicated GitHub App is the only issuer of `Gate PR Security`. Permissions:
Checks write, Pull requests read, Actions read and mandatory Metadata read. Its key
stays server-side. The Heal App remains separate and retains its current access.
Bind required status checks to this App, not merely the check name or Actions App.

The handoff is asynchronous: authenticated receipt may return `202 Accepted`;
it is never approval. The producer waits only for fenced `admissionReady: true`
for that receipt, confirming verified initial nonpassing publication. The final
security result waits for trusted run/jobs completion, avoiding a circular wait.
Retry safely within the finite token-bound admission deadline, bind receipts to PR/base/head/policy
and run attempt, and prevent older deliveries from superseding a newer verdict.
Re-check base/head immediately before final publication. A changed base invalidates
previous evidence even if the head is unchanged; enqueue a replacement and ensure
merge rules invalidate stale success. Queued merges need the exact merge-group SHA.

Workflow failures, cancellation, absent evidence and publisher unavailability must
not leave a reusable green verdict for new context. Branch protection and freshness
behavior are enrollment acceptance tests, not assumptions from the API design.
Forks, Dependabot and other bots receive equivalent coverage on suitable isolated
hosted runners. Do not run their source with trusted-heavy or privileged credentials.

See [GitHub workflow OIDC binding](https://docs.github.com/en/actions/how-tos/secure-your-work/security-harden-deployments/oidc-with-reusable-workflows).

## Runtime manifest and integration evidence

The consumer runtime manifest lists all owned execution units and required source
or symbol artifacts. Browser/server releases bind the exact commit to built artifacts.
Native app version/build/dist remains intact, with immutable CI evidence mapping it
to commit, artifact and symbols. Do not guess from a tag, branch or latest release.
Symbol requirements include source maps, native debug symbols and Dart symbols as
applicable; each native target/architecture must retain its own build identity.
`release.native_builds` is empty only for non-native components. Native evidence
requires one entry per applicable platform/target/architecture, recording app
version, build number, the exact artifact digest and each symbol kind/digest/debug
identifier. The artifact digest must occur in the release's artifact list; CI
provenance must bind that same target, version/build, architecture and symbol set.
Symbol UUIDs/debug IDs or mapping identifiers must match the emitted frames and
binary. Different builds cannot reuse a record merely because the version label
matches. Validate uniqueness of the platform/target/architecture/build tuple;
missing, mismatched or ambiguous mappings make the evidence incomplete. The native
fixture illustrates this shape without claiming any actual upload or symbolication.

Gate's server-controlled registry binds verified Sentry installation and numeric
project ID to repository/configuration. Event tags never authorize access or fixes.
Support multiple projects per repo without assuming that one project's events are
interchangeable with another. `wodiq` remains mapped to `wodiq-app`; Status,
OpenAirco and WODIQ-platform need separate projects. Preserve the working Gate pilot.
Unknown components, missing/conflicting source versions and mapping conflicts go to
`needs_review`. Case and retry jobs keep their original repository, component and
configuration binding. A later event cannot overwrite that binding. A cross-component
issue conflict requires review rather than silently redirecting an existing case.

The `observability-evidence` document represents one controlled verification-environment
probe for one component: source and artifact provenance, verified installation/project,
event/case references, symbolized frames and assertions for redaction, deduplication,
retry binding, invalid/wrong-project rejection, conflict review and nonblocking offline
behavior. `verified` requires every applicable assertion and trusted supporting artifact;
false assertions remain `incomplete`. A reference or boolean alone is not proof.

Only technical, minimal errors leave the app. Exclude user content, tokens, request
bodies, device identity, Replay, screenshots and unbounded/free-form breadcrumbs.
Filter expected user errors narrowly; an external Status probe failure is not itself
an application defect. Apply scrubbing before transmission and again at intake and
artifact publication. Bound buffering, retries, data size and retention. Missing DSN,
network failure or Sentry/Heal outage must not block the application or grow storage
without bound. OpenAirco diagnostics are on by default, with privacy/store disclosures
updated and no new consent flow. WatchOS requires explicit supported error recording
and available symbols; automatic crash capture is not an acceptance condition.

The isolated Heal runner stays without network or credentials. Its supervisor reports
only its own sanitized infrastructure errors; it must not recursively treat findings
from scanned applications as supervisor crashes.

## Release validation, rollout and recovery

Repeat actual component-to-case probes at onboarding and when SDK/initialization,
routing, privacy filters or relevant deployment/symbol configuration changes. Evidence
contains fingerprints for all four integration surfaces. Different application source
commits may reuse evidence only when the validator establishes unchanged integration
surfaces, runtime inventory and applicable artifact/symbol contracts. A caller cannot
assert freshness by copying an old fingerprint. Relevant transitive SDK upgrades count.

Every release independently validates the manifest, exact source/build provenance,
artifacts, symbol availability and applicable integration evidence. This is an artifact
check, not a synchronous Sentry probe on every release. Separately record verification
of the deployed production configuration without emitting a synthetic production fault.
Verification-environment events must never be presented as production event evidence.

Enroll Gate and DevOps first, then each remaining repository as its own delivery. Before
requiring `Gate PR Security` on `main` and `develop` where present, demonstrate clean
pass, controlled new/worsened high/critical rejection, old-debt behavior, scanner failure,
forged evidence, modified workflow, stale SHA and bot/fork behavior. Demonstrate that a
failed or missing trusted check actually prevents merging. Cover merge queues when used.
An inventory stage is bookkeeping, not evidence of enforcement.

For each application, enable mandatory `Gate Observability` production promotion only
after every required component is proven and production configuration is checked. New
applications meet this before their first production release. Sites remain excluded
from this runtime obligation. Rollback uses previously verified exact artifacts and
retained evidence; prove that path. Emergency bypass requires a durable human decision
recording reason, exact source/artifact versions, affected control, scope, responsible
person and follow-up. Never silently disable a check or reinterpret missing evidence.
No automatic merges, extra fix permissions, or budget increases are authorized here.

DEV monitors Actions use during the pilot and owns scanner/rule/advisory updates. An
update is reviewed with capability fixtures and paired base/head rescans before rollout.
Existing debt receives separate prioritized follow-up; high/critical debt is never hidden
by green differential results. Automatic fixes require IN-3 and a separate per-stack
regression → bounded fix → independent QA proof; auth, secrets, infra and migrations
remain excluded from that automatic scope.

# DEV-49 feature verification — 1 October 2026

## Fixed candidate and environment

- Candidate: `cfa97a443fc95687e30cb753d2e6f6b4574a2d97`, clean existing checkout; base `5d7efa9f317acbb89d8763c4dbac3846bec4a1e5`.
- Helper: `99ee0df4a14877fae2327ea29d293b0187ef3271`; reusable workflow: `598636f8f0f4290be95427a0f7670a6b515a2cc5`.
- Actual published Linux ARM64 image `ff504c…`, macOS/Colima, source and root filesystem read-only, no network or credentials, unique synthetic Git-object fixtures. Actual Gate PHP ArtifactVerifier; its blob matches Gate source `85256be32acb7e5c589e97f05170484fe43a9b2d`.
- Actual role: local contributor/CLI operator with ordinary host UID. Repository/PR/run/OIDC identifiers are synthetic. No application or GitHub administrator substitutes for a contributor account; this journey requires no remote account.
- Matrix was recorded before four independent single-depth advisory agents executed the actual documented CLI/API path. No source, Git or Tracker mutation by testers. UI, phone, keyboard, screenshots and browser isolation N/A: workflow/CLI-only delivery.

## Fresh scenario scorecard

| Persona | Task / expected outcome | Observed result and concrete evidence | Verdict |
| --- | --- | --- | --- |
| Product Owner | Documented clean and critical-source journeys | Actual image, packaging and PHP verifier: clean `pass`, 0 findings; synthetic critical `gate.php.untrusted-eval` `blocked`, 1 finding. Both retain all 14 ordinary coverage scopes. | PASS |
| Project Manager | Existing debt, collision refusal and safe retry | Actual image/verifier retain one existing finding while passing. Reused output directory rejected; original result hash `a0583a5ea009ae2030058e7305cecb1be8afa3168aab7ff185e31e23580c5fe2` unchanged. Two new directories preserve identical source/tree IDs, report and evidence entries. ZIP timestamps legitimately differ. | PASS |
| Developer | Follow contributor instructions, resolve pins, remove final PHP input | Actual `git show`: caller → workflow `598636f8…` → helper `99ee0df4…` → image `ff504c…`. Actual image and verifier `pass`; PHP head input empty with exact `verified_absence`, base `safe.php` complete. Raw/staged report and sidecar byte-identical. | PASS |
| Tester | Unsupported Yarn/unknown input and real-report tampering | Actual image/packaging/verifier `incomplete` for both source cases. Fresh production packaging accepted an unchanged real-image control; separately rejected image mismatch, policy mismatch with recomputed checksum, corrupt checksum and exit mismatch with safe error codes and no staging output. | PASS |

The Developer, PM and Tester initial sandboxed commands could not access the local Docker socket and produced no pass. Those failed results remain preserved. Narrowly authorized local socket retries in new directories passed; neither scanner permissions nor isolation were relaxed. PM explicitly tested refusal to overwrite the failed result.

Tester replay errors: `scanner_image_mismatch`, `report_profile_mismatch`, `report_checksum_mismatch`, `scanner_exit_mismatch`. These are fresh production-module journeys using real image output, distinct from unit-test evidence.

## Supporting evidence and limits

- `make lint`, complete `make test` with the existing Ansible toolchain, 39 producer tests and workflow contracts passed on the fixed candidate before remote Actions. Packer unavailable; its optional format check was skipped. Fleet-root checks were not requested.
- [Published-image pre-pin receipt](DEV-49-offline-integration-2026-10-01.json): seven scanner/verifier scenarios including the reproduced then corrected Yarn format. Fourteen ordinary scopes processed; bounded coverage does not promise exhaustive detection.
- This scorecard is an evidence-only follow-up to the fixed candidate. It changes no executable code, caller pin or workflow identity. Hosted CI verifies the final PR head separately.
- Live OIDC, App-owned checks, branch enforcement, full Gate policy-exclusion normalization and production enrollment belong to IN-29. None is claimed by these offline fixtures. Automatic Heal fixes remain disabled.
- All four perspectives PASS for the DEV-49 scope; this is not human acceptance, a merge-policy exemption, a production release, or proof of another stack.

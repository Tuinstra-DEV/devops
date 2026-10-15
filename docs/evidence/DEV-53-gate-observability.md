# DEV-53 delivery evidence

## Gate promotion recovery

Production run 38528473241 reached the shared validator and stopped before image
promotion. The extra Tracker-intake check passed the whole release policy to a
helper that expects only the routing selector. A regression using the shipped
Tracker policy and retained source snapshot reproduced this failure.

The caller now selects `fingerprint['Tuinstra-DEV/tracker'].routing`. The same
test accepts the enrolled source and rejects changed or unreadable intake files.
The complete action entrypoint was also run locally for Gate and Tracker using
their real current source/manifests and synthetic GitHub/image fixtures: both
passed without network or production changes. This is local integration evidence,
not a production canary. `make lint` and `make test` pass (38 focused Node tests).

Scope: shared release assurance for Gate and Tracker only. Existing checkouts, separate repository PRs within this Story. No product UI change; UI screenshots do not apply.

## Shared validator candidate

- Canonical protected proof hash, fixed coverage and all image digests; stale source/run/symbol/integration checks fail closed.
- Actual Gate/Tracker SDK dependency closures and selected workflow inputs successfully fingerprinted. Gate's verified sources `57b129` / `910c53` match the current integration files; intake projection is checked separately against its enrolled snapshot.
- Unit fixtures test success, tampering, missing coverage, incorrect source/component, changed integration inputs, ordinary unrelated changes and public-map leakage. Fixture passes do not claim a new live Sentry event.
- Historic IN-37/TRAC-202 chain records retain actual technical IDs. They are verification-environment evidence; production configuration is recorded separately.

## Local verification

- `make lint`: passed.
- `make test`: passed with the documented Ansible 2.21.4 prerequisite. The macOS sandbox blocks Ansible's local RPC socket; the successful run used the same command outside that sandbox with temporary Ansible directories. No production connection was made.
- Focused Node tests: 37 passed after the actual Gate/Tracker policy-pair test and compiled-tree collector fixes. Happy paths, missing artifacts, stale source, failed builds and tampering are covered.
- Retained compiled Tracker browser files: 114 Sentry debug IDs, no maps. Retained Gate private server maps: 116 IDs/maps; browser: 121 IDs/118 maps, with three generated helper IDs explicitly unmapped. These are reads of earlier artifacts with synthetic collector metadata, not new release/upload proof.

Consumer wiring, remote guard proofs, release/runtime/rollback checks are pending. Do not mark DEV-53 Delivered until complete.

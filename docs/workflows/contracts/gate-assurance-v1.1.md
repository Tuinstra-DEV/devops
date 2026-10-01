# PR security assurance 1.1

IN-29 adds a PR-only outer evidence version for exact source exclusions. Legacy assurance 1.0, observability documents and the scanner paired-result schema remain unchanged. Gate keeps the same schema bytes in `backend/resources/pr-security/assurance-v1.1.schema.json`. Remote schema loading is disabled.

Every 1.1 `pr-security-evidence` includes `exclusions`, an empty list or at most 1,000 records. Each record has exactly `scope`, `side`, `path`, `source_sha256`, and `reason_id: approved_exclusion`. Allowed scopes are `opaque-input`, `unknown-input` and `embedded-code`; side is `base` or `head`.

The immutable producer derives each digest from a regular Git blob in the exact base/head commit and tree. It checks the reviewed scope/path/hash allowlist shipped with the helper, tied to the published policy digest. The publisher separately checks its server-owned allowlist and requires every outer record to match one raw approved-exclusion row. PR contents cannot select enrollment, allowlists or scanner configuration. Missing proof is nonpassing; extra, duplicate, wrong-side or untrusted proof is rejected.

The inner report is copied byte-for-byte. Its legacy policy-exclusion rows list paths under both discovered and scanned with an approved_exclusion reason; they are explicitly classified as excluded rather than analyzed. Gate never counts those raw entries as scanned. Only authenticated, independently authorized records count as proven exclusions. No reason is stripped, no finding is removed, and existing high/critical blocking semantics remain.

Version 1.1 requires an explicit matching server registration. A reason-only legacy 1.0 report cannot become a passing scan through this extension. The exact workflow, image, policy, bundle, dataset, source, run and artifact bindings still apply.

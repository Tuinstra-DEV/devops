# IN-46: remove the expired Gate QA grant

This is a blocking repair within IN-46, linked to [Gate PR #427](https://github.com/Tuinstra-DEV/gate/pull/427). It is prepared for operator review; it does not publish or enroll a scanner.

## Fixed source identities

- Gate scanner/QA source commit: `a12a83a14f5a2bd811c0bda8bfc5e4f96c433620`.
- New Gate policy: `sha256:e85de80c7d15830dbd6b63a8d950c41dbe95e5c6e575f913304cf17d60e159df`.
- Producer action: `4c8e3a44574451dbace09ce1587452bd190dae54`.
- Reusable producer: `f896d68aed9e0c32389e333e5a9b91cca478bc9b`.
- Gate's unchanged exact input-exclusion inventory, rebound to the new policy: `sha256:c0397e8b2c729f3302ed6127aeb72e3ad5ab46206e47849c73b592131c1997c8`.

The QA job changes from `pull_request_target` to read-only `pull_request`, still executing the helper from the exact PR base SHA. It retains current head/base/alias checks, expected QA App identity, assessment binding, bounded visibility recovery and body-edit wake handling. The real text scanner reports complete coverage and no findings for that workflow. The IN-3 content-bound grant is removed, not extended.

## Resolve both immutable dependency cycles

The old base-owned target producer always uses the expired image. Changing only a candidate caller cannot replace that execution. The new Gate caller therefore invokes the exact reviewed reusable through `pull_request`. The existing independent publisher already supports PR OIDC refs and verifies the current merge execution SHA, source head/base, attempt, referenced workflow and actual producer check/artifact.

The scanner must also recognize the exact new caller SHA, while the producer needs the resulting scanner image digest. To avoid a self-referential image/workflow hash, the trusted reusable captures `vars.GATE_GATE_SCANNER_BINDING` in its action step, just as the existing Tracker route captures its own binding. It is not a workflow input and candidate environment cannot replace it. The binding must contain exactly immutable image, policy, bundle and dataset digests; the policy is fixed to the IN-46 hash above, bundle and dataset must match, mutable tags and legacy/Tracker images fail. Source preparation snapshots the validated binding; scan and packaging reuse it even if the environment changes later. The server-owned publisher independently requires the entire same tuple.

Only Gate gains the PR event path; other repositories cannot select it. Tracker's profile, binding validation and routing predicates remain unchanged. PR invocations use the existing hosted fallback because the self-hosted role still requires the full trusted target-event predicate. This repair does not change runner admission or privileges.

The Gate scanner recognizes only the exact new reusable SHA and exact caller structure/permissions for this PR entry point. Unknown immutable refs, extra permissions, secrets or conditional skips retain findings. No generic trusted-repository or arbitrary-SHA grant is added. All existing excluded inputs are preserved; no new content-bound exception is introduced.

## Verification and operator boundary

`make lint` and `make test` passed locally. Focused producer contracts cover schema and duplicate-key rejection, policy/image/profile mismatch, immutable state, exact provider event/run/attempt binding, absence of candidate execution and network/credential isolation. Gate's local CI, scanner and Heal QA-flow evidence is recorded in IN-46 and Gate PR #427.

Before activation Marcel must review the source and exact OCI receipt, publish that archive without changing its digest, set the validated repository binding, and atomically replace only Gate's publisher registration while preserving Tracker and all App/check identities. The Gate runbook contains the concrete commands and rollback identity. Neither image publication, repository-variable activation nor publisher-enrollment replacement has been executed by this agent. Required checks and branch protection remain intact. Fresh authenticated CI must pass before any Gate merge; no old receipt or locally generated success substitutes for that proof.

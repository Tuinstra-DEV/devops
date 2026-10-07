# IN-3: content-bound QA producer compatibility

## Scope

This narrow IN-3 prerequisite transports the scanner's optional content-bound
proof without granting exceptions. The independent Gate publisher owns approval,
coverage, exact source binding and expiry checks. No runner routing or App access
is changed; the 20 existing excluded-input records are unchanged.

The two Gate-only workflow records expire at `2026-10-20T22:00:00Z`.
Changes to either reviewed input cannot receive those grants.

## Protected scanner provenance

- Build/run: https://github.com/Tuinstra-DEV/gate/actions/runs/37607649169
- Clean source: `20fd4f364120f02b0377e035140582a5cdc881a4`.
- Registry manifest: `sha256:6a63e8d1e105f27a083173187cb6b718181953b1dcaa1b0c71d07ba5d648c4ae`.
- OCI archive: `sha256:21d1c38cee4cb559726ec3ec7afccc9bb5223deaa905df763a975a36d8711ec0`.
- Policy: `sha256:a7b3fb9f22ec2342c847ffab8eeee24745d9fbe2da136fcb27cc44388d897f6b`.
- Bundle/data manifest: `sha256:27416c40c66c07bef5a29ddf3d8e455dfa666086f77d3d6f01c51b926003b4c7`.
- Producer action commit: `23b7b60b136528bc8a2e1d8a59d4864a5a65587a`.

Protected build, behavior verification, publication, read-only digest pull and
anonymous access denial all passed. Downloaded archive, source manifest and all
OCI blobs were independently checksum-verified locally.

## Local verification

- Optional-field RED/GREEN contract: 44 tests PASS; original result bytes are
  transported unchanged. Unknown fields and malformed or duplicate bindings reject.
- `make lint`: PASS.
- `make test`: PASS using documented `ansible-core==2.21.4` in a task-local
  temporary environment. Initial missing Ansible and sandbox RPC restrictions
  were resolved before the successful actual local fixture run.
- Canonical image → current unmodified producer → real PHP `ArtifactVerifier`:
  **PASS**. Exact two-binding artifact passes; an unrelated high stays blocked.
  Missing, duplicate and expired proof rejects. Both actual file-drift scans are
  incomplete and the producer rejects their absent coverage without a receipt.
  The producer preserves scanner result bytes; no profile override was used.
  The test uses synthetic Git objects and identity data, offline read-only
  containers, and no live publication. Local receipt directory:
  `/private/tmp/IN3-canonical-chain-37607649169` (not a public artifact link).

## Cutover boundary

Both Gate default-branch callers must use the new immutable reusable workflow
before switching the single server-owned registration. The compiled publisher
is released first. PR #329 then receives fresh checks from the new enrolled
profile. Old receipts cannot prove the updated candidate.

No production activation, positive AI QA trial or billed-minute reduction is
claimed here. No UI changes; screenshot evidence is N/A. Human acceptance of
IN-3 remains required.

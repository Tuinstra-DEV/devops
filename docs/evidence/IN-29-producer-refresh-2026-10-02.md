# IN-29 confirmed producer refresh — 2 October 2026

## Immutable scanner and scope

Gate [run37018917742](https://github.com/Tuinstra-DEV/gate/actions/runs/37018917742) built, verified and privately published source `a93f888518bacbc0952397f90935d57aa75b72c7`. All four jobs passed: scanner fixtures, digest-preserving publication, readonly digest pull and anonymous access denial.

| Binding | Value |
| --- | --- |
| Registry image | `ghcr.io/tuinstra-dev/gate/ci-scanner@sha256:b6a805ea9570188a65c2af54dbf9bf8683bb7f18bd5247c8161d93efdf8bdf7c` |
| Policy | `sha256:bca9dae69e5a93f4f72b3941328abf3e3ab49ab05f9bd99c77c5e96dd3ca19be` |
| Bundle/data | `sha256:b0d3535458f977a26942a1fc8382ee17df3c215fd027165f6c0c9dc95e850169` |
| Exact producer exclusion table | `sha256:561cb956466b087675668769dac63046c76d1a4f0d60e4b9f2fdee77b98db1ae` |
| Downloaded artifact ZIP | `sha256:dbee87c160cea9a709fc928b2906486145304a56da5acced69d02fa3c3b59477` |
| OCI archive | `sha256:a822e343f854bf6290818dd99530a0f31dacb9ddf69622f93c82a1f37f18e34e` |
| Config blob / source-host loaded ID | `sha256:ff40da3c11b4a551ab4539467447cdb1709b885c325f35821a593ad9562b82fd` |
| Runner manifest | `sha256:660efb61cb0ba3ad1de5f1190c8fb47c4b0f0b499d0a4fdaee077fd5eca72ea1` |

The immutable producer retains the prior 17 exclusion records and adds only approved `verify-local.sh` e795 and node-forge patch 73bb records. Patch hunks receive no Semgrep coverage; changed content fails. The raw policy-exclusion row must already be complete and backed by exact blob proofs. No verifier rule turns failed raw coverage green. Both base/head use the same pinned Semgrep 1.178.0 and frozen rule/advisory data. Findings and existing debt remain visible.

OIDC URL admission permits exactly `pipelines.actions.githubusercontent.com` and `run-actions-1-azure-eastus.actions.githubusercontent.com`; HTTPS, no port/userinfo/fragment or preexisting audience, no redirects. Endpoint and audience remain fixed. Separate publisher rights/repository scope are unchanged.

## Fresh local verification before push

- `make lint`: PASS.
- `PATH=/private/tmp/dev48-ansible-venv/bin:$PATH make test`: PASS using the pre-existing installed tool environment; no package installation. Includes 53 producer + 17 OCI tests and workflow contracts.
- Independent canonical run/source verification plus ZIP/archive/runner manifest and every manifest/config/layer descriptor checksum: PASS. The local adapter validates clean-source receipt declarations and ARM64 identity; it does not independently authenticate the receipt source against GitHub. Actual image readback confirms policy/bundle, 19 records and Semgrep1.178.
- Canonical published image against actual Gate candidate `ac54bbea25e4f4c1506873ce203657f019f379b8` (byte-identical tree to merged a93): PASS, all16 coverage rows complete; all27 debt findings identical. Only readonly individual file binds from the existing checkout; no second application checkout.
- Actual published scanner → production producer package → real Gate PHP ArtifactVerifier, five generated small source fixtures: **all PASS**. Clean and verified-absence outcomes pass; new critical outcome blocked; approved exclusions pass with6 outer proofs; changed excluded source incomplete with5 retained proofs. Raw report bytes remain unchanged. These synthetic identities do not prove live OIDC/App checks.
- Docker classic config-ID / containerd manifest-ID mismatch reproduced; focused TDD validates the exact archive-bound candidates and rejects wrong ID/descriptor/platform. The original downloaded archive is unmodified. Its local loading annotation used a fresh tag so the previous local image remained intact; execution uses only the verified immutable ID.

## Delivery boundary

This is one attributable IN-29 delivery in the existing DevOps checkout/branch. No additional clone/worktree or different Story implementation. Root owns pins/Git/docs, one bounded worker owns only local OCI harness and its tests; independent static review is separate from runtime evidence.

Live Gate enrollment, OIDC→durable receipt→App-owned check lineage, actual abandoned receipt recovery, main/develop merge enforcement and four functional personas remain required for IN-29 acceptance. Production is still ade96582 with empty enrollment; automatic Heal fixes remain off. UI/screenshots/phone checks N/A: no UI change. Human acceptance remains separate from a normal merge.

# IN-29 corrected canonical scanner verification — 2 October 2026

The initial full-source pilot safely rejected duplicate public fingerprints. Gate PR #251 preserves all findings and gives a colliding class unique fingerprints derived only from public fields. Private match proof is not exposed. Exception suppression is denied for the entire collision class; high/critical blocking and strict duplicate rejection remain active.

## Fixed candidate

- Protected Gate source: `c9a6c5788eb9d7bf92a54b84f0e30f742b23717f`.
- Canonical [scanner run 37026503691](https://github.com/Tuinstra-DEV/gate/actions/runs/37026503691), attempt 1: all four publication/fixture/access jobs succeeded.
- Image: `ghcr.io/tuinstra-dev/gate/ci-scanner@sha256:64dd14c6d5d1b56dec146105312ded9ce11bbad1571322bea04145ba2acb4487`.
- Policy: `sha256:bca9dae69e5a93f4f72b3941328abf3e3ab49ab05f9bd99c77c5e96dd3ca19be`.
- Bundle: `sha256:b0d3535458f977a26942a1fc8382ee17df3c215fd027165f6c0c9dc95e850169`.
- The nineteen reviewed exclusion records, tool versions, coverage requirements and two exact OIDC hosts are unchanged.

## Newly executed verification

The downloaded artifact ZIP matched the GitHub API SHA-256. The existing OCI verifier checked the complete original archive, platform, descriptors, configuration and layers. The runner manifest matched the fixed source, including the corrected comparator. Docker readback matched the policy and comparator hashes. Only a separate index naming annotation was used to preserve existing local image tags; original archived bytes remain unchanged.

The actual existing Gate checkout was scanned with 885 byte-verified source files at the candidate commit, read-only mounts, no network, no credentials and no application execution. Result: 16 complete required coverage records, 27 existing findings and 27 unique public fingerprints. The strict producer report validator accepted the actual report. Its synthetic execution envelope is not live GitHub identity evidence.

Five isolated synthetic scanner-to-producer-to-real-PHP-artifact-verifier cases passed with this exact image:

| Case | Observed outcome |
| --- | --- |
| Clean input | pass |
| New high finding | blocked |
| Proven absent stack | pass |
| Exact reviewed exclusions | pass |
| Changed excluded input | incomplete |

`make lint` and the complete `make test` suite passed locally using the existing Ansible environment. No tests were delegated to Actions as their first execution.

## Operational boundary

Production source `5402ce67e2672bcce890392aef8313b196021aee` passed deployment and image/schema/queue/key-boundary verification after a real backup and isolated MariaDB restore. Its publisher remains unenrolled at this point. This document proves the corrected canonical package and local producer integration, not a live App check or branch enforcement. Those require subsequent real Gate-only PR trials. No UI changed; screenshot evidence is not applicable.

# DEV-51 PHP deployment host identity

## Execution scope

Bug DEV-51 was approved in Backlog on 5 October 2026; no active Cycle exists. Implementation uses the existing `gate/work/devops-workflows` checkout on `feat/DEV-51-verify-php-deploy-host-key`, based on `49519ad504770e4a07f86d3731047e8228532608`. No additional checkout, worktree or production connection is used.

| Owner | Write scope | Verification |
| --- | --- | --- |
| Workflow worker | PHP reusable workflow, focused functional harness and test entrypoint/contract assertions | Reproduce missing verification before fix; execute actual extracted shell with synthetic keys, scan and remote-action spies; normal local lint/test |
| Root | PHP contract, consumer migration checklist and this evidence document | Review trust boundary/compatibility; record fixed candidate and four independent synthetic caller journeys; protected PR checks afterward |

Every writer verifies root, cwd, branch, base relationship, status and assigned files before editing. NTF-9 source is clean, committed and pushed at `774760b`; its hosted CI and unresolved operational dependencies do not permit delivery or production deployment. Different Stories are implemented sequentially.

## Synthetic environment and scenario matrix

Use temporary synthetic job directories while preserving HOME, generated synthetic ED25519 keys, the actual workflow's extracted shell, a controlled local scan stub and SSH/SCP spies. These are test fixtures, not repository checkouts. No real user account, server or deployment is exercised. No UI change: screenshots N/A. Commit and fresh execution evidence must be recorded before a PASS verdict.

| Persona | Task | Expected result | Observed result / evidence | Verdict |
| --- | --- | --- | --- | --- |
| Product Owner | Synthetic approved caller supplies matching pin and completes admission; mismatched pin explains closed failure | Approved target only; zero remote action on mismatch | Fixed-candidate run pending | BLOCKED |
| Project Manager | Synthetic caller retries and changes port while retaining independent expected pin and failure evidence | Same strict trust path on retry; no stale trust adoption or duplicate scan authorization | Fixed-candidate run pending | BLOCKED |
| Developer | Synthetic consumer configures contract and uses exact workflow SSH/SCP entry point | Correct DNS/port and common verified configuration, understandable missing-pin failure | Fixed-candidate run pending | BLOCKED |
| Tester | Synthetic scanner errors, empty/malformed/duplicate/substituted key, stale existing known_hosts | Every invalid path prevents SSH/SCP; valid path remains usable | Fixed-candidate run pending | BLOCKED |

This matrix records the status before execution. Fixed candidate results are recorded in DEV-51 and its PR with their exact commit attribution; no PASS is inferred from this initial matrix.

Static workflow assertions, red/green regression tests, fresh persona executions and hosted-only proof are separate evidence. Missing hosted Environment permissions/canary evidence remains BLOCKED. Independent personas do not count as CODEOWNERS approval or human acceptance.

## Delivery and migration gates

- Run `make lint` and `make test` locally before push, then observe checks on the exact PR candidate.
- Obtain CODEOWNERS review and normal protected merge. Marcel's standing merge authorization does not bypass required review/checks.
- The mandatory protected `SSH_HOST_ED25519_FINGERPRINT` changes PHP deployment behavior: follow the next immutable major-contract release and hosted-canary procedure. Do not create, move or publish tags as a shortcut.
- NTF-2 separately adopts the reviewed immutable full workflow SHA and approved fingerprint. Notify is not protected before its own caller update and proof.
- No production deployment, key rotation, Vite/Nuxt change, image promotion change or production approval is included. Preserve existing deployments/data while investigating any mismatch; never adopt the scanner's key as the expected pin.

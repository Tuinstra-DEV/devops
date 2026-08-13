# DEV-15 Node.js 24 migration evidence

Date: 2026-08-13

## Decision

Standardize application, CI, browser-tooling, and container build paths on the
Node.js 24 major line. Local validation used the official `node:24-alpine`
image, resolved as Node.js 24.19.0 with npm 11.17.0 and image digest
`sha256:d32cdf619f63fe0471182d08996dd516c6275bb5fd31ae06e55a570bd9e1ad43`.

The six excluded repositories remain out of scope: StintDeck,
eelcos-workbench, finance, klaas, agent-council, and garmin-connect-sdk.

## Repository matrix

| Repository | Classification | Node 24 outcome | Validation | Delivery |
| --- | --- | --- | --- | --- |
| WODIQ | Already aligned | Local version, CI, and Docker paths already use Node 24; no contradictory declaration was found. | Clean `npm ci`; no source change required. | No PR |
| console | Upgraded | Added the Node 24 engine contract and `.nvmrc`; generalized stale action-runtime guidance. | Clean `npm ci`; Playwright 1.61.1 available; repository CI. | [PR 202](https://github.com/Tuinstra-DEV/console/pull/202) (merged) |
| devops | Already aligned | Reusable workflow defaults, templates, and version strategy already use Node 24. This evidence report records the rollout. | `make lint`; `make test`. | This PR |
| gate | Upgraded; delivery blocked | Package engine, local version, Docker stages, agent guidance, and fail-closed CI preflight now require Node 24. | Frozen pnpm install; format, lint, typecheck, 85 tests, build, CI contract tests, and Docker build are green locally; hosted PR jobs pass, trusted-heavy validation is queued. | [PR 187](https://github.com/Tuinstra-DEV/gate/pull/187) (open) |
| marcel-site | Upgraded | Package engine, `.nvmrc`, and Docker builder aligned on Node 24. | Clean `npm ci`; `npm test`; Docker build; repository CI. | [PR 129](https://github.com/Tuinstra-DEV/marcel-site/pull/129) (merged) |
| notify | Upgraded; delivery blocked | Added the engine contract and `.nvmrc`; browser smoke explicitly uses pinned `setup-node` v6.2.0 with Node 24. | Clean `npm ci` and browser script syntax are green locally; hosted quality passes, trusted-heavy validation is queued. | [PR 9](https://github.com/Tuinstra-DEV/notify/pull/9) (open) |
| openairco-site | Upgraded | CI, package engine, `.nvmrc`, and Docker builder aligned on Node 24. | Clean `npm ci`; `npm test`; Docker build; repository CI. | [PR 6](https://github.com/Tuinstra-DEV/openairco-site/pull/6) (merged) |
| tracker | Already aligned | Package engine, `.node-version`, CI, Docker, and documentation already use Node 24.18. | No source change required. | No PR |
| tuinstra-site | Upgraded | CI, documentation, package engine, `.nvmrc`, and Docker builder aligned on Node 24. | Clean `npm ci`; `npm test`; Docker build; repository CI. | [PR 104](https://github.com/Tuinstra-DEV/tuinstra-site/pull/104) (merged) |
| wodiq-site | Upgraded | CI, documentation, package engine, `.nvmrc`, and Docker builder aligned on Node 24. | Clean `npm ci`; `npm test`; Docker build; repository CI. | [PR 45](https://github.com/Tuinstra-DEV/wodiq-site/pull/45) (merged) |
| sudoku-spark-web | Upgraded | CI, documentation, package engine, `.nvmrc`, and Docker builder aligned on Node 24; `setup-node` is commit-pinned. | Clean `npm ci`; 358 tests with coverage; application and Docker builds; repository CI. | [PR 87](https://github.com/marcel-tuinstra/sudoku-spark-web/pull/87) (merged) |

No repository was classified as not applicable. Gate and Notify are upgraded
in source but blocked from delivery by unavailable trusted-heavy runner
capacity. Package managers were retained. The dependency graph did not require
regeneration, so no lockfile changed.

## Runner evidence

The pull-request workflows route the affected jobs to the exact labels
`self-hosted` and `trusted-heavy`, but no ephemeral runner accepted them within
the documented historical queue peak. The hosted portions passed; the following
jobs remained queued without an assigned runner name:

- Gate [frontend-static job 94519986823](https://github.com/Tuinstra-DEV/gate/actions/runs/31721659138/job/94519986823), queued from 2026-08-13 16:38 UTC.
- Notify [browser-smoke job 94519504121](https://github.com/Tuinstra-DEV/notify/actions/runs/31721552565/job/94519504121), queued from 2026-08-13 16:36 UTC.
- Notify [production-images job 94519504168](https://github.com/Tuinstra-DEV/notify/actions/runs/31721552565/job/94519504168), queued from 2026-08-13 16:36 UTC.

Decision: hold Gate PR 187, Notify PR 9, and this report PR. Do not substitute
hosted execution as proof of self-hosted compatibility and do not bypass the
required checks. After Sanctuary is available, require all three jobs to pass
and record their actual runner names before merging.

## Rollback

Each repository can be rolled back independently by reverting its linked PR;
there are no lockfile or dependency-version changes to unwind. Reversion
restores the repository's previous Node declaration and container builder.
After a revert, rerun the repository's clean install, existing checks, and
container build before merging. If the trusted-heavy runner fails while hosted
checks remain healthy, use the reviewed hosted-runner rollback in
`docs/playbooks/ci-runner-host.md`; do not broaden runner trust or action
permissions as a shortcut.

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
| gate | Upgraded | Package engine, local version, Docker stages, agent guidance, and fail-closed CI preflight now require Node 24. | Frozen pnpm install; format, lint, typecheck, 85 tests, build, CI contract tests, Docker build, and hosted plus trusted-heavy PR jobs pass. | [PR 187](https://github.com/Tuinstra-DEV/gate/pull/187) (merged) |
| marcel-site | Upgraded | Package engine, `.nvmrc`, and Docker builder aligned on Node 24. | Clean `npm ci`; `npm test`; Docker build; repository CI. | [PR 129](https://github.com/Tuinstra-DEV/marcel-site/pull/129) (merged) |
| notify | Upgraded | Added the engine contract and `.nvmrc`; browser smoke explicitly uses pinned `setup-node` v6.2.0 with Node 24. | Clean `npm ci`, browser script syntax, hosted quality, and trusted-heavy browser/image jobs pass. | [PR 9](https://github.com/Tuinstra-DEV/notify/pull/9) (merged) |
| openairco-site | Upgraded | CI, package engine, `.nvmrc`, and Docker builder aligned on Node 24. | Clean `npm ci`; `npm test`; Docker build; repository CI. | [PR 6](https://github.com/Tuinstra-DEV/openairco-site/pull/6) (merged) |
| tracker | Already aligned | Package engine, `.node-version`, CI, Docker, and documentation already use Node 24.18. | No source change required. | No PR |
| tuinstra-site | Upgraded | CI, documentation, package engine, `.nvmrc`, and Docker builder aligned on Node 24. | Clean `npm ci`; `npm test`; Docker build; repository CI. | [PR 104](https://github.com/Tuinstra-DEV/tuinstra-site/pull/104) (merged) |
| wodiq-site | Upgraded | CI, documentation, package engine, `.nvmrc`, and Docker builder aligned on Node 24. | Clean `npm ci`; `npm test`; Docker build; repository CI. | [PR 45](https://github.com/Tuinstra-DEV/wodiq-site/pull/45) (merged) |
| sudoku-spark-web | Upgraded | CI, documentation, package engine, `.nvmrc`, and Docker builder aligned on Node 24; `setup-node` is commit-pinned. | Clean `npm ci`; 358 tests with coverage; application and Docker builds; repository CI. | [PR 87](https://github.com/marcel-tuinstra/sudoku-spark-web/pull/87) (merged) |

No repository was classified as blocked or not applicable. Package managers
were retained. The dependency graph did not require regeneration, so no
lockfile changed.

## Runner evidence

The pull-request workflows routed the affected jobs to the exact labels
`self-hosted` and `trusted-heavy`. Sanctuary was reachable throughout, but the
manager recorded a GitHub API rate limit at 16:24 UTC. It recovered without a
service restart and admitted the queued work at 16:58 UTC:

- Gate [frontend-static job 94519986823](https://github.com/Tuinstra-DEV/gate/actions/runs/31721659138/job/94519986823) passed on `sanctuary-94519986823`; its job log confirms Node.js 24.19.0.
- Notify [production-images job 94519504168](https://github.com/Tuinstra-DEV/notify/actions/runs/31721552565/job/94519504168) passed on ephemeral runner `sanctuary-94519504121`.
- Notify [browser-smoke job 94519504121](https://github.com/Tuinstra-DEV/notify/actions/runs/31721552565/job/94519504121) passed on a fresh registration named `sanctuary-94519504121`; its job log confirms Node.js 24.19.0.

The manager remained active with zero restarts and `NoNewPrivileges=yes`, and
cleaned each ephemeral lease after completion. This is actual runner evidence;
hosted fallback was not substituted for the required validation.

## Rollback

Each repository can be rolled back independently by reverting its linked PR;
there are no lockfile or dependency-version changes to unwind. Reversion
restores the repository's previous Node declaration and container builder.
After a revert, rerun the repository's clean install, existing checks, and
container build before merging. If the trusted-heavy runner fails while hosted
checks remain healthy, use the reviewed hosted-runner rollback in
`docs/playbooks/ci-runner-host.md`; do not broaden runner trust or action
permissions as a shortcut.

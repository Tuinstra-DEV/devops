# Gate chain evidence

## Active IN-46 / IN-44 renewal — 3 November 2026

Source `317e086b9fa7299f0394c36cdd1df4b229558984` changes the SDK dependency closure and PHP deployment symbols. Routing and privacy fingerprints remain unchanged. This renewal changes only observed Gate evidence and its approved canonical hash; fingerprint selectors, required coverage, guard code and acceptance rules remain unchanged. Tracker evidence is preserved.

The successful protected [release build 41645284648/1](https://github.com/Tuinstra-DEV/gate/actions/runs/41645284648) supplies the PHP OCI artifact, index `sha256:70ea061384c8ed656bf40c02604ffa4626abd7f68b23d0aa1aabe24f4cbce6ab`. A private format-only conversion retained the image configuration `sha256:d94e79d428b0affd00202aa96bc1c0c1ea38fb193502fcbe2b1ce9a82b9206c7` and all 39 layer diffIDs. Isolated read-only runtime probes verified PHP **8.3.36**, Node **24.21.0** and exact source hashes. No application image was published by this verification.

Exactly one protected [Sentry verification build 41701648819/1](https://github.com/Tuinstra-DEV/gate/actions/runs/41701648819) succeeded on that source and uploaded private maps. Runtime archive `sha256:ea42f4ef9c4c73818429fad92cc16a01d90d95b5ef631bc998136a075a069dfa` ran without changing compiled bytes or receiving production database/API credentials. Operator-only grouping was added after the actual privacy callbacks. Safe SDK metadata was retained; transport validation supports bounded fixed-length and chunked bodies and forwards at most one event per JavaScript component.

| Component | Actual event | Heal case | Mapped source |
| --- | --- | --- | --- |
| backend | `424ab55d82004813bddedcfda52ecb0c` | `61258be6a9024ff38392555ae71a51a7` | `backend/src/Service/Heal/HealRunnerFailureReporter.php:42` |
| browser | `a3d2b445f3d84059a64c0ef053545b41` | `2263f2f206fd4f6cac90a533f2bbd697` | `frontend/app/utils/sentry-verification.ts:3` |
| nitro | `09ab8cba32ab43328c683644cc5ff0ea` | `80555315948a417f8430d383edc860b8` | `frontend/server/utils/sentry-verification.ts:3` |

Actual Sentry readback confirms the exact release, `verification` environment, zero populated user/private fields and zero processing errors. A synthetic private marker was injected into the PHP scope and removed by the real filter. No live private marker was injected into browser/Nitro events. All three matching cases were already triaged before operator replays. Six explicitly operator-signed webhook deliveries, two per event, returned HTTP 200 and retained the same case IDs: one occurrence, one triage, zero fix attempts per component. These repeats are not claimed as extra automatic Sentry alerts.

The ledger grew from 53 to 56 records with every original record and the bind-mounted inode preserved. A private 0600 backup was retained; host and worker hashes both match `eb92f9ce4b44e9abc5edf938aa20e1c7830b3189cbbe6270efc2fb100df3b8de`. No production container was restarted. The temporary JavaScript container and SSH tunnel were stopped.

An initial PHP operator incorrectly checked the SDK's flush Result as a boolean, causing an extra automatic operator-error event in synthetic issue `158402338`. Its case remains intact and excluded. The replay preflight stopped before any delivery against that group. A separately approved corrected probe, first tested offline with the actual SDK and a mock transport, produced the clean backend event above with automatic error integrations disabled. `gate-IN-46-verification.json` records this limitation explicitly.

This is isolated SDK/source/transport and real Sentry/Heal-chain evidence, not production HTTP/job execution. Historical production snapshots below and `gate-production-runtime.json` remain historical. The subsequent ordinary release must generate its own runtime readiness receipt. Local assurance validation uses actual source fingerprints and observed proof with an explicitly synthetic candidate contract fixture; it is not evidence that production images were published. No UI source changed; the browser screenshot is a technical synthetic fixture only.

Local validation: all 38 observability/guard/fingerprint/symbol tests, actual-source fingerprint computation, canonical proof approval and `make lint` pass. `make test` was executed with Python 3.14 and isolated Ansible tooling; the existing macOS process-group test `test_bounded_runner_terminates_the_entire_child_process_group` fails at `os.killpg` with `PermissionError: Operation not permitted`, as also recorded by TRAC-223. No complete local-suite pass is claimed. Required ordinary Linux CI must pass before merge.

## Historical IN-37 evidence — 14 October 2026

Captured 14 October 2026. Actual SDK events passed through Sentry and signed intake into the existing Gate cases. Browser/Nitro use verification source `57b129f787141dd0a476879fd347712aef36757b`, protected build 38475524705/1, archive `sha256:913c2bcdfb2a259de12892d0880ded0894bf55cc33ec377d3132f0d3b03db1b5`. PHP supervisor uses deployed source `910c53cbe27637c8691bd7d6b437779fa0504ec1`, deployment38487479438 and exact PHP digest in the case record. Sources remained unchanged across the later history-only release merge.

Browser and Nitro each repeated into one case, two occurrences, one triage and no fix. PHP supervisor produced one actual event; its repetition behavior is covered by the local intake/dedup regression suite, not a claimed second live event. All cases retain original repository/component/source/artifact binding. Actual events had no populated user data or processing errors. Synthetic data redaction is also covered by SDK/privacy tests.

Attributable local checks: 268 Heal unit tests / 1,218 assertions including wrong webhook, project, source, component, redaction and retry binding; 13 frontend tests; actual credentialless/offline SDK execution and bounded transport. Full local PHP static checks, frontend lint/typecheck/build/map checks passed. Required release/CI checks passed on910c53. Production configuration and database identity were checked separately; verification events are not production events. The isolated runner retains no network or credentials.

This protected versioned technical record preserves the operator-observed chain after short-lived runtime artifacts expire. It does not claim those archives remain available. New integration fingerprints require renewed observed chain evidence. No UI changed. Human Story acceptance remains separate.

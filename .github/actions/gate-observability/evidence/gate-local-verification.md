# Gate chain evidence

## Active Symfony 7.4.23 renewal — 5 November 2026

Gate security [PR #437](https://github.com/Tuinstra-DEV/gate/pull/437) and release [PR #438](https://github.com/Tuinstra-DEV/gate/pull/438) produced image source `53e8ee62b46bbc7fcd555682d73c8814fd67dad4`, tree `da29fee80b41a62ea1d52d6b1f8fcac5d41522a0`. The main merge is `d1fd77b27b2e3c9106a4a56cb45e56bc663edc0b` and has the same tree; the image and event retain the actual release-build source, not the later main SHA. This renewal changes only Gate evidence and the policy's approved canonical hash. Guard code, selectors, routing, process bindings, required coverage and Tracker evidence are unchanged.

### Fingerprint attribution

Using exactly the fingerprint code pinned by production (`b261e4514faa5593ab4b2d1c54eeab7bafe18753`), SDK initialization changes from `sha256:03145dd980432c315d358a5fbf58950f107fce645c902ceb3e5d6989d69a2935` to `sha256:6f17730371d6ed5e0fc4892784d521b38b40aac5878aeb10352257311b1c6f4b`. All four fingerprints at pre-security source `61f38ddbae93f37710ca1ebda9db367ca71f9a9e` exactly match the previously approved proof. On the new release source, substituting only the old `backend/composer.lock` as an in-memory input restores all four old fingerprints exactly. No source files are changed for that counterfactual check.

The Composer closure rooted at `sentry/sentry-symfony` contains 33 packages; the Sentry bundle remains 5.10.0. Only these seven package records in that closure change, including their locked source/dist/support/time metadata:

| Package | Before | After |
|---|---|---|
| symfony/config | 7.4.14 | 7.4.23 |
| symfony/console | 7.4.13 | 7.4.23 |
| symfony/dependency-injection | 7.4.14 | 7.4.23 |
| symfony/filesystem | 7.4.11 | 7.4.23 |
| symfony/http-foundation | 7.4.21 | 7.4.23 |
| symfony/http-kernel | 7.4.18 | 7.4.23 |
| symfony/yaml | 7.4.21 | 7.4.23 |

Routing stays `sha256:2d6cb782a92533070e7c36a582bd1b5171a8efc4d05ef68486cf131f9757d243`; privacy stays `sha256:a0c63875a5808ce06b17689d208c52bc063e5d628118d056c066531957ac3853`; deployment symbols stay `sha256:d2c3d2ad8a61127576752c7ba4a8e71a217316d1d9a207384efd08f5fb1afdd4`. SDK initialization files themselves are unchanged. The fingerprint implementation and Gate policy selectors at this DevOps base are identical to the pinned versions.

### Exact archive and operator verification

Successful protected [release build 42067086339/1](https://github.com/Tuinstra-DEV/gate/actions/runs/42067086339), PHP job `127384288924`, supplies artifact `13434179870`. Receipt identity was independently matched to that successful run, source/tree, release PR, actors and committed workflow blobs. The repository's unchanged `validateReceipt` also passed. Exact identities are:

- OCI index: `sha256:8c55178cb8859b1b1117092efebdb0f344826ef7bea68dc615ed8ed3a1ae5c59`.
- Original image config: `sha256:06000374675b0d696cd71809bcc894f1fd831b42ea1e03a9f81dcfcceee76359`.
- OCI archive checksum: `sha256:a8f2eced2765cb1f77abb54bd1611b29fa99e54d3ee7bf9fcd375bb20a9ba84c`.
- Receipt file checksum: `sha256:f50aeae51e0a612cc2f6c023c6cb2d4f41c8980609390f4e8d952c73a936ab0f`.

[Main deploy 42075609100/1](https://github.com/Tuinstra-DEV/gate/actions/runs/42075609100) correctly stopped at the changed fingerprint before image publication/promotion and before server deployment. The verification uses the accepted private CI archive, not a claimed published registry image or completed production deployment.

The reviewed operator script was executed on Sanctuary as `mtuinstra`, without sudo. It verified the exact archive and receipt checksums, converted only the archive format, and proved the original config bytes plus all 39 uncompressed rootfs digests through a Docker export. The local Docker importer exposed a different runtime ID but exported those exact original bytes; Sanctuary retained the original config ID directly. Isolated read-only, networkless runtime checks passed PHP 8.3.36 and all 11 security-update Symfony packages at v7.4.23. Running production application images were not replaced.

Registration appended exactly one `backend` receipt in environment `verification`, retaining every existing receipt, its order, ledger inode and owner/mode; duplicates remain zero. The backup is `verified-deployments-v7.json.bak-symfony-7423-20261105T180438Z`. The messenger worker had an automatic restart policy, was gracefully refreshed and returned running within 60 seconds with the same image. An earlier non-blocking lock attempt stopped before backup/append; the subsequent explicitly authorized registration succeeded. No registration failure caused a probe.

The first probe attempt stopped before reserving an attempt or sending because its scoped DSN reader rejected Docker's trailing empty line. The approved one-line correction filters empty lines while retaining the exactly-one-DSN check. Final operator SHA256 is `ff68ec71be52e6a627901275217a8fab8d6ddc050584c4dcd11d5c9fdde939a2`; its remote checksum and absent attempt marker were verified before execution. Twelve offline tests and Python/PHP syntax checks cover backup, append, duplicates, rollback, exact config/rootfs validation, DSN rejection and the trailing-newline case. Only the existing ingest DSN was read in memory through a scoped Docker format, never printed or written; no API token or other runtime environment values were read.

### Observed backend chain

Exactly one backend verification event was sent after the mandatory 75-second cooldown, using the actual `HealRunnerFailureReporter` and `SentryEventPrivacyFilter` in the immutable candidate. The real filter removed injected synthetic user/extra markers before operator-only fingerprint `symfony-7423-53e8ee62-backend-verification` was applied. The SDK reported one event and flush SUCCESS. No automatic resend or manual signed intake replay was used.

| Event | Sentry issue | Heal case | Canonical source frame |
|---|---|---|---|
| `d0cd8c26ddce4a24aabd599005648a2b` | GATE-1A / `158924336` | `ee0380d84c15414a838e6546aec0efdf` | `backend/src/Service/Heal/HealRunnerFailureReporter.php:42` |

Claude supplied an attributed read-only Sentry MCP readback: first seen `2026-11-05T18:09:27.382Z`, one occurrence, zero users, component `backend`, environment `verification`, exact release `53e8ee62b46bbc7fcd555682d73c8814fd67dad4`, exception `App\Service\Heal\HealRunnerInfrastructureFailure` and message `[Filtered]`. First-party frames are reporter lines 30 and 42; the additional `Command line code(29)` frame is operator invocation, not a source frame. Only trace context remains; no user/request/extra/private marker is visible. This is not represented as an independent Sentry API read by the backend agent.

Independent read-only `dbal:run-sql` SELECT confirmed the exact event in a repository-bound `tuinstradev/gate` case: `triaged`, reason null, one occurrence, one triage attempt, fix status `not_started`, zero fix attempts, correct source and OCI digest above, and receipt digest `sha256:5c4bfea2367bed2a055b7e06daaec983c27bab1bc876b6eaa93ab7d914bb1cab`. Stored evidence contains only technical IDs, source/frame and immutable binding fields; there is no `missing_deployment_binding`. The only webhook access entry since 18:08Z returned HTTP 202 at `2026-11-05T18:09:38.151709654Z`. Logs contain no issue IDs, so exact-event correlation is established by the Heal record rather than inferred from access logs alone.

The fresh case counts demonstrate one successful intake and no automatic fix. No fresh duplicate-delivery test is claimed; historical deduplication evidence remains historical. These are isolated SDK/source/transport and real provider-to-Heal observations, not a production HTTP request or production job failure.

### Browser and Nitro evidence reuse

Browser and Nitro proof objects and case records are preserved unchanged, attributed to source `317e086b9fa7299f0394c36cdd1df4b229558984`, [verification build 41701648819/1](https://github.com/Tuinstra-DEV/gate/actions/runs/41701648819), archive `sha256:ea42f4ef9c4c73818429fad92cc16a01d90d95b5ef631bc998136a075a069dfa`. No new browser/Nitro execution, event or live-case readback is claimed. The top-level observation date covers the renewed PHP record; reused component observations retain their original attribution below.

Exact Git comparisons from that proof source to the current release found byte-identical browser/server Sentry initialization, shared privacy evidence helper, frontend manifest and pnpm lock, frontend Dockerfile/Nuxt configuration, sourcemap source/helper scripts and both verification fixtures. The selected pnpm SDK closure is therefore unchanged. Routing, privacy and deployment-symbol fingerprints also remain unchanged. The shared SDK fingerprint changes solely through the PHP Composer records above. The existing browser/Nitro proof remains attributable to those unchanged integrations; all PHP process bindings continue to map to the single backend component.

Canonical approvedProofSha256 changes from `sha256:b790bdc1da41d65c0e59f4999ab90a9bc64cc887c8fc67cff1d49e82148d6f3e` to `sha256:f817a114a098b1d16ad6b699bb5307affe8b22f814664c255de6ee798930efde`. The eventual ordinary guarded deployment must still produce its own exact source/image/readiness receipt. No UI changed; screenshots do not apply.

Local verification passed: all 38 focused observability/assurance/fingerprint/symbol tests, `make lint` and full `make test` (exit 0). The full suite used the existing Python 3.14/Ansible tooling outside the sandbox for localhost fixtures. Existing platform/optional skips were the Linux-only findmnt test, unavailable Packer formatting check and unset DEPENDABOT_FLEET_ROOT. Exact-source fingerprints match the renewed proof; the unchanged guard accepts an explicitly synthetic candidate contract fixture with the actual observed proof and rejects the old approved proof on backend.sdk_initialization. This fixture does not claim actual frontend image publication or fresh source-map upload. Canonical proof approval, unchanged browser/Nitro proof objects and case records, unchanged policy selectors, four-file scope and `git diff --check` also passed.

## Historical IN-46 / IN-44 renewal — 3 November 2026

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

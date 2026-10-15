# Tracker chain evidence — TRAC-202

Captured 14 October2026, source `52f16c01726e7b40980dceac00e06e037d5fd8d2`. Browser used protected verification build38505044151/1, artifact11867827427, archive `sha256:0631deb67cb484db27cff241120498785fd6c1db79e7394d57ceccb1f405f7d0`; outer GitHub archive `sha256:71443de1afb509b461c7006029f9f3793e50df427ba3c955eaaa51e35cb8d5f2` verified before extraction. Private source maps resolved the actual SDK frame to app/utils/sentry-verification.ts:24.

API and Sentry worker used the actual deployed PHP SDK, process-local verification environment and pure failing parser calls (no application data or outbound parser request). Deployment38505012394/2 used the same exact PHP digest recorded in both cases. This is SDK/source/transport proof, not a claim that a production HTTP request or production job was exercised.

All three components repeated into the same case: two occurrences, one triage, zero fix attempts. Actual PHP events lost injected synthetic private fields and user data; browser events had no populated user fields but no live private marker was injected. Browser marker removal was tested locally. Existing receipts were appended before events and retained their immutable bindings.

Local attributable checks: 26 related PHP tests/337 assertions, PHPStan/fixer/container lint; 30 browser/Sentry tests including real offline SDK transport, typecheck/lint/format/build; 112 private maps validated and stripped; 9 artifact tests, 34CI-routing contracts, 31 recovery contracts; 14Gate tests/90assertions covering the expanded component routing and verification without automatic fixes. Full required CI38501094674 and deployment succeeded. Missing DSN emits no event; rejecting network remains bounded and nonblocking.

Production configuration was checked separately on API/MCP/four workers/browser, all exact source/image bindings, no public maps, zero tracing and operator fixture disabled. Database image/container unchanged. Verification bugs TRAC203/205/206 were labeled and reversibly withdrawn; no product fix was attempted.

This protected versioned record retains verified source and original artifact identity, without claiming short-lived artifacts remain downloadable. New integration fingerprints require renewed observed chain evidence. No UI changed. Human Story acceptance remains separate.

## TRAC-204 PHP privacy/filter renewal — 15 October 2026

Protected-source PHP build [38703197385/1](https://github.com/Tuinstra-DEV/tracker/actions/runs/38703197385), source `23d6fa5b6b279da03ba5aecc3ef6d47ff37322c6`, produced artifact11957278023. Outer archive SHA256 `3ec932fd70b0ff5ac654b01e74f0d86ae8bbeb8ca38eab4bccc3cdeaeeb9f781`, runtime gzip SHA256 `be99f4c545c91972afa964288fa9514aa0a622a14754a99ecfd3c4d5da247798`, original image config `sha256:a10ef29dc113dcc6ab6b3449d11d4097497219eb5af334686c37edce823a8b28`. The host loaded image identity is `sha256:f01852474cb714c7a5ff046631886879761d8691f3d05c273bf5e93f8cb23852`; saving that immutable identity reproduced the exact original config checksum and identical configuration/history/rootfs layers. This is an operator-loaded CI image, not a claimed registry digest or production deployment.

The operator booted that image with synthetic configuration and only the existing Tracker Sentry ingest DSN, no application database credentials, in environment `verification`. The actual configured callback discarded the original AccessDeniedException under an HTTP request before transport. Actual TypeErrors from both first-party files reached Sentry with correct release/component/frame, zero user/private marker fields and no Sentry processing errors. The immutable Gate runtime ledger was appended before capture; existing27rows were preserved and the graceful worker refresh was verified. No runner profile or fix permissions were expanded.

The original privacy callback intentionally strips scope fingerprints. The first API/worker TypeErrors grouped together, and Gate safely retained the original API binding and marked the component conflict `needs_review` (case9c19369bdd5344689f2863f2036c8f9b). No reset or overwrite was performed. Final synthetic groups used an operator-only fixed fingerprint added **after** the unchanged actual callback; this changes only test grouping, never filtering or source binding. API/worker cases each have one occurrence/triage, zero fixes; repeated signed HTTP intake of the same real event twice returned the same case without another attempt. The API final group was also delivered by Sentry automatically; signed repetitions are identified as operator-controlled intake, not additional provider delivery. All three synthetic issues R/S/T are resolved as completed verification.

The browser proof above is reused attribution from14October. Browser SDK/plugin/privacy/build integration files are byte-identical between its52f16source and the23d6candidate. No fresh browser event is claimed. The shared privacy fingerprint now also covers FeedbackSearchPrivacy.php because request classification depends on it.

Local TDD: eight expected-denial scenarios failed before the fix;48focused tests/400assertions passed after, including actual SDK with mocked transport. Full local PHP2077tests/39368assertions, PHPStan/style/APIcontract passed (2notices,67environment-skips). Web audit/lint/typecheck,1721unit/component tests, build and71real-API browser scenarios passed (1screenshot-onlyskip). Mock browser168/169 passed initially; the Notion navigation timeout passed both desktop and phone focused retries. Site security/check/test/browser/build passed. No UI changed; no screenshots apply. Required CI38701392637 passed before protected PR366 merge. Production promotion follows this refreshed protected proof; the historical production snapshot below remains explicitly historical, not a claim that the new filter was already live.

## Earlier TRAC-204 PHP renewal (HttpFoundation 7.4.19) — 15 October 2026

Tracker PR #371 patched only ExpressionLanguage 7.4.21 and HttpFoundation 7.4.19 and added the missing locked Composer audit as the first local check. The exact audit failed before and passed after, without ignores. Local checks passed: 2,079 backend tests / 39,373 assertions (two existing PHPUnit notices, 67 environment skips), PHPStan, style, API contract, three real API/PostgreSQL browser scenarios and 11 preserved site analytics/contract tests. The production PHP image also built locally; real API/worker SDK boot checks with network disabled passed with zero events. Required CI 38720134448 and security 38720134694 passed before PR #371 was merged.

### Verified artifact

Protected PHP build [38722396534/1](https://github.com/Tuinstra-DEV/tracker/actions/runs/38722396534) used source `b78b53b39dbf55e26b17c1c447c5adf36966788d`.

- Artifact: `11967118376`.
- Outer archive SHA256: `6d92534033390f4d85598f3fde4531c415ad01f0d92f6c4f87b873a5d3b96bad`.
- Runtime gzip SHA256: `7ee5ee3c0737425c52b68a0d7f9f89bd3248ed7f918c88dcd29286070d1178b5`.
- Original image config: `sha256:16904a39a02f962775a5d382bf24c7d69908d1feeb229fa6750c231a14f67632`.
- Loaded host image: `sha256:27114b97abe5ce09e4e9a6af564a054902120d38026fe569af29765395071e6b`.

Saving that immutable loaded image reproduced the original config checksum and identical configuration, history and rootfs. Manifest and archive checksums were checked locally and remotely before use. This is an operator-loaded CI verification image; production has not yet received this source.

### Actual SDK → Sentry → Heal

The existing isolated fixture asserts both patched package versions before SDK boot. The actual configured API callback discarded the expected access denial before transport. One first-party TypeError per API/worker reached Sentry with the correct source, component and frame. User/private test fields were removed; Sentry reported no processing errors. Synthetic grouping is added only after the unchanged privacy callback, as documented above.

Both cases arrived through automatic Sentry delivery before the operator repeated the signed intake twice per event. Repeats retained the same case, one occurrence/triage and zero fixes:

- API: `ab447df90729490481a96eb20aba13a2`.
- Worker: `2fae04c942b4477ab38d2650c0a51da9`.

All 32 existing runtime ledger rows were preserved. Two verification rows were appended with a backup; the gracefully restarted worker read the identical 34-row ledger. Sentry test groups TRACKER-W/X are resolved. Automatic test items TRAC-211/212 were labeled and reversibly withdrawn. Original production issues and historical case bindings were unchanged.

Only the Composer SDK closure fingerprint changed. Browser integration files and its own dependency closure remain unchanged from the attributable `52f16` proof above. That browser proof is reused; no fresh browser event is claimed. The earlier `23d6` PHP proof and production snapshot remain historical. Production promotion still must publish its own source/image receipt through the updated immutable guard.

## Final TRAC-204 PHP renewal (both Symfony packages 7.4.21) — 15 October 2026

Parallel Tracker PR #370 advanced HttpFoundation from 7.4.19 to 7.4.21 after the preceding proof. That upstream release changes runtime request/header/session code, so the earlier proof was retained as historical and the final runtime was verified again. Its CI bundling and package update were preserved through a normal merge. Two stale caller runs were explicitly cancelled; no failed test was relabeled as a pass.

### Local verification before the final caller update

The final 7.4.21 dependency set passed 2,079 local backend tests / 39,373 assertions, with two existing PHPUnit notices and 67 environment skips. The initial broad filtered command omitted the documented memory setting and stopped at the default 128 MB; the complete successful run used the existing 512 MB test setting. This changes no application memory policy. The locked Composer audit, full PHPStan/style/API-contract checks, three real API/PostgreSQL browser scenarios, 50 CI-bundle/routing/preflight tests and the release/recovery contracts passed. The production PHP image built locally; API and worker booted their real SDK with network disabled and zero events. Local source `a8da7d9b22e90f4283ab69d2e76ba8d64b114b32` has identical `code/` files to protected source `eee960198fcaad56994f7ba7cfc368a34918d03b`; its differences are the caller pins and verification document outside that build context.

### Verified final artifact

[Protected PHP build 38728083705/1](https://github.com/Tuinstra-DEV/tracker/actions/runs/38728083705) used source `eee960198fcaad56994f7ba7cfc368a34918d03b`.

- Artifact: `11970040872`.
- Outer archive SHA256: `5eb47757695ab9e7fc712e1c2ab0033b55efdd4b3674245441666f0a03fb944a`.
- Runtime gzip SHA256: `3e71d65c02fc758cbcaceac0df4bc2297c0529f4a19b5c17aab4d7df0a14c03c`.
- Original image config: `sha256:3648479b6e8102799206b5e77b8022d09da336d65c00647d58ff97b2c4947ca2`.
- Loaded host image: `sha256:55cdd164f2e925733a83cfd557508f7fdf581595907bbfbbba835f46d66900e9`.

Local and remote manifest/archive checksums passed. Saving the loaded immutable image reproduced the original config checksum and identical configuration/history/rootfs. The isolated fixture asserted both actual package versions before SDK boot. Production services and the database were unchanged during this verification.

### Final SDK → Sentry → Heal outcome

The actual API callback discarded the expected access denial before transport. One first-party TypeError per API/worker arrived through automatic Sentry delivery, with the correct release, component and source frame, no populated user/private test fields and no Sentry processing errors. As above, the fixture adds only fixed synthetic grouping after the unchanged privacy callback.

- API case: `65f91477b22e4e198837be2ef52453ef`.
- Worker case: `4a925a9b0c0546df82d59fe7478152f5`.

Both cases existed before two operator-signed repeats per event. Repetition retained those exact cases, one occurrence and triage, with zero fixes. The ledger preserved all 34 preceding rows and appended two verified rows; the gracefully restarted worker read the identical 36-row file. Sentry groups TRACKER-Y/Z are resolved. Automatic test items TRAC-213/214 were labeled and reversibly withdrawn; original issues and historical case bindings were preserved.

Only the Composer SDK dependency fingerprint changed to `sha256:02609898363fa607368b77895b74b319157f945d9ca60b76c53a34205cf9abd6`. The unchanged browser integration and dependency closure retain their original attributable proof. The earlier PHP proofs and production snapshot remain historical. Normal production promotion must still verify and publish its own source/image receipt using this final bundle.

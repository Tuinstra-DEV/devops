# Tracker chain evidence — TRAC-202

Captured 14 October2026, source `52f16c01726e7b40980dceac00e06e037d5fd8d2`. Browser used protected verification build38505044151/1, artifact11867827427, archive `sha256:0631deb67cb484db27cff241120498785fd6c1db79e7394d57ceccb1f405f7d0`; outer GitHub archive `sha256:71443de1afb509b461c7006029f9f3793e50df427ba3c955eaaa51e35cb8d5f2` verified before extraction. Private source maps resolved the actual SDK frame to app/utils/sentry-verification.ts:24.

API and Sentry worker used the actual deployed PHP SDK, process-local verification environment and pure failing parser calls (no application data or outbound parser request). Deployment38505012394/2 used the same exact PHP digest recorded in both cases. This is SDK/source/transport proof, not a claim that a production HTTP request or production job was exercised.

All three components repeated into the same case: two occurrences, one triage, zero fix attempts. Actual PHP events lost injected synthetic private fields and user data; browser events had no populated user fields but no live private marker was injected. Browser marker removal was tested locally. Existing receipts were appended before events and retained their immutable bindings.

Local attributable checks: 26 related PHP tests/337 assertions, PHPStan/fixer/container lint; 30 browser/Sentry tests including real offline SDK transport, typecheck/lint/format/build; 112 private maps validated and stripped; 9 artifact tests, 34CI-routing contracts, 31 recovery contracts; 14Gate tests/90assertions covering the expanded component routing and verification without automatic fixes. Full required CI38501094674 and deployment succeeded. Missing DSN emits no event; rejecting network remains bounded and nonblocking.

Production configuration was checked separately on API/MCP/four workers/browser, all exact source/image bindings, no public maps, zero tracing and operator fixture disabled. Database image/container unchanged. Verification bugs TRAC203/205/206 were labeled and reversibly withdrawn; no product fix was attempted.

This protected versioned record retains verified source and original artifact identity, without claiming short-lived artifacts remain downloadable. New integration fingerprints require renewed observed chain evidence. No UI changed. Human Story acceptance remains separate.

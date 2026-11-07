# Consumer and required-check readback — 7 November 2026

GET-only inspection of all 13 selected repositories after the GitHub rate limit reset. No consumer, protection, ruleset or runner-group setting was changed.

Collection: 2026-11-07T21:40:28.055416+00:00 → 2026-11-07T21:41:20.846462+00:00.
Inputs: repository/default branch, main/develop where present, open PR heads, workflow YAML, branch protection, effective branch rules and all nonterminal Actions run statuses. Contexts below are the exact configured strings, deduplicated across legacy contexts/checks and effective required-status-check rules. App bindings were read and retained locally; no binding is changed. No effective required-workflow rule was returned.

This is an inventory, not a new consumer run or a five-minute activation statement. Recollect the operator evidence immediately before execution.

## Exact required status-check contexts

| Repository | Branch | Required contexts |
| --- | --- | --- |
| console | `develop` | None configured; branch unprotected |
| console | `main` | None configured; branch protected |
| gate | `develop` | `Gate Heal QA gate`<br>`Gate PR Security`<br>`Gate Protect`<br>`lint / frontend`<br>`lint / source-policy`<br>`routing`<br>`security / Gate evidence producer`<br>`test / api-contract`<br>`test / php` |
| gate | `main` | `Gate Heal QA gate`<br>`Gate PR Security`<br>`Gate Protect`<br>`lint / frontend`<br>`lint / source-policy`<br>`routing`<br>`security / Gate evidence producer`<br>`test / api-contract`<br>`test / php` |
| marcel-site | `develop` | `ci / ci` |
| marcel-site | `main` | `ci / ci` |
| notify | `main` | None configured; branch unprotected |
| openairco | `develop` | `apple-native-verify`<br>`dependency-review`<br>`flutter-verify`<br>`secret-scan` |
| openairco | `main` | `apple-native-verify`<br>`dependency-review`<br>`flutter-verify`<br>`secret-scan` |
| openairco-site | `develop` | None configured; branch unprotected |
| openairco-site | `main` | None configured; branch unprotected |
| status | `main` | None configured; branch unprotected |
| sudoku-tessera | `develop` | None configured; branch unprotected |
| sudoku-tessera | `main` | None configured; branch unprotected |
| tracker | `develop` | `Gate PR Security`<br>`Gate Protect`<br>`gitleaks`<br>`quality / source-policy`<br>`quality / verify` |
| tracker | `main` | `Gate PR Security`<br>`Gate Protect`<br>`containers / build (api)`<br>`containers / build (web)`<br>`containers / deployment-contract`<br>`containers / production (nginx_prod)`<br>`containers / production (php_prod)`<br>`gitleaks`<br>`quality / backend`<br>`quality / browser-integration`<br>`quality / frontend`<br>`quality / source-policy` |
| tuinstra-site | `develop` | `ci / ci` |
| tuinstra-site | `main` | `ci / ci` |
| wodiq-app | `develop` | None configured; branch unprotected |
| wodiq-app | `main` | None configured; branch unprotected |
| wodiq-platform | `develop` | `Workspace baseline` |
| wodiq-platform | `main` | `Workspace baseline` |
| wodiq-site | `develop` | None configured; branch unprotected |
| wodiq-site | `main` | None configured; branch unprotected |

For the seven e1 migrations, `ci / ci` is enforced on marcel-site and tuinstra-site develop/main; no status-check requirement is configured on Notify, Status, Openairco Site, WODIQ App or WODIQ Site in this readback. Preserve their existing verdict/check jobs anyway. No branch protection is relaxed or added by this work.

Gate’s later shared/security migration must preserve every listed required alias, including `security / Gate evidence producer`; changing only a reusable SHA is not evidence that those aliases still publish. Tracker has different develop/main check lists: do not copy the shorter develop list onto main. WODIQ Platform’s required `Workspace baseline` remains required while the 604 pin stays admitted. Openairco’s four native/security checks and Console’s current policy remain unchanged.

## Current trusted and producer callers

| Repository | Source refs | File / caller job | Immutable reusable identity | Metadata option |
| --- | --- | --- | --- | --- |
| console | [default:develop](https://github.com/Tuinstra-DEV/console/commit/2acff26802eaea6d4181abbfde604044ba66c55b) | `.github/workflows/ci.yml` / `test-postgres-heavy` | `reusable-trusted-verification.yml@23c08cf212dd6411ed5076b2ec53a05c1e2e5f3e` | `default` |
| gate | [default:develop](https://github.com/Tuinstra-DEV/gate/commit/f84662a94bff1d2b8d8a80215c3f4981a3becffe) | `.github/workflows/gate-pr-security.yml` / `security` | `reusable-gate-pr-security.yml@43ead52517b9ab7e3d6f6bb94305be2b98a10fd9` | `default` |
| gate | [default:develop](https://github.com/Tuinstra-DEV/gate/commit/f84662a94bff1d2b8d8a80215c3f4981a3becffe) | `.github/workflows/lints.yml` / `gate-quality` | `reusable-trusted-verification.yml@23c08cf212dd6411ed5076b2ec53a05c1e2e5f3e` | `default` |
| gate | [default:develop](https://github.com/Tuinstra-DEV/gate/commit/f84662a94bff1d2b8d8a80215c3f4981a3becffe) | `.github/workflows/lints.yml` / `frontend-static-legacy` | `reusable-trusted-verification.yml@23c08cf212dd6411ed5076b2ec53a05c1e2e5f3e` | `default` |
| gate | [default:develop](https://github.com/Tuinstra-DEV/gate/commit/f84662a94bff1d2b8d8a80215c3f4981a3becffe) | `.github/workflows/tests.yml` / `php-medium` | `reusable-trusted-verification.yml@23c08cf212dd6411ed5076b2ec53a05c1e2e5f3e` | `default` |
| gate | [main:main](https://github.com/Tuinstra-DEV/gate/commit/f09dd38cc6e5323f0cbb8b19d4d8460658f4b921) | `.github/workflows/gate-pr-security.yml` / `security` | `reusable-gate-pr-security.yml@43ead52517b9ab7e3d6f6bb94305be2b98a10fd9` | `default` |
| gate | [main:main](https://github.com/Tuinstra-DEV/gate/commit/f09dd38cc6e5323f0cbb8b19d4d8460658f4b921) | `.github/workflows/lints.yml` / `gate-quality` | `reusable-trusted-verification.yml@23c08cf212dd6411ed5076b2ec53a05c1e2e5f3e` | `default` |
| gate | [main:main](https://github.com/Tuinstra-DEV/gate/commit/f09dd38cc6e5323f0cbb8b19d4d8460658f4b921) | `.github/workflows/lints.yml` / `frontend-static-legacy` | `reusable-trusted-verification.yml@23c08cf212dd6411ed5076b2ec53a05c1e2e5f3e` | `default` |
| gate | [main:main](https://github.com/Tuinstra-DEV/gate/commit/f09dd38cc6e5323f0cbb8b19d4d8460658f4b921) | `.github/workflows/tests.yml` / `php-medium` | `reusable-trusted-verification.yml@23c08cf212dd6411ed5076b2ec53a05c1e2e5f3e` | `default` |
| gate | [PR#426](https://github.com/Tuinstra-DEV/gate/commit/d8a53c3331e1e7040f33cf848ac720211875011b) | `.github/workflows/gate-isolated-role-canary.yml` / `php-role-canary` | `reusable-trusted-verification.yml@a8ace2f171d7d127bbfb3a6d6b13f158f461ba98` | `True` |
| gate | [PR#426](https://github.com/Tuinstra-DEV/gate/commit/d8a53c3331e1e7040f33cf848ac720211875011b) | `.github/workflows/gate-pr-security.yml` / `security` | `reusable-gate-pr-security.yml@5e06c90db856f501245e433ea10c4e332a79677b` | `default` |
| gate | [PR#426](https://github.com/Tuinstra-DEV/gate/commit/d8a53c3331e1e7040f33cf848ac720211875011b) | `.github/workflows/lints.yml` / `gate-quality` | `reusable-trusted-verification.yml@a8ace2f171d7d127bbfb3a6d6b13f158f461ba98` | `True` |
| gate | [PR#426](https://github.com/Tuinstra-DEV/gate/commit/d8a53c3331e1e7040f33cf848ac720211875011b) | `.github/workflows/lints.yml` / `frontend-static-legacy` | `reusable-trusted-verification.yml@a8ace2f171d7d127bbfb3a6d6b13f158f461ba98` | `True` |
| gate | [PR#426](https://github.com/Tuinstra-DEV/gate/commit/d8a53c3331e1e7040f33cf848ac720211875011b) | `.github/workflows/tests.yml` / `php-medium` | `reusable-trusted-verification.yml@a8ace2f171d7d127bbfb3a6d6b13f158f461ba98` | `True` |
| marcel-site | [default:develop](https://github.com/Tuinstra-DEV/marcel-site/commit/45db2db0d9e6aabc0336cb4d442100d3cc397376) | `.github/workflows/ci.yml` / `verify` | `reusable-trusted-verification.yml@e1a9f09504b903120e9b5297eff64f58a891c907` | `default` |
| marcel-site | [main:main](https://github.com/Tuinstra-DEV/marcel-site/commit/dbbca5a8c3899f47a9e6e35e3fa6e8d16e3d53c2) | `.github/workflows/ci.yml` / `verify` | `reusable-trusted-verification.yml@e1a9f09504b903120e9b5297eff64f58a891c907` | `default` |
| notify | [default:main](https://github.com/Tuinstra-DEV/notify/commit/42485a84a07186907f53d25f140c0c6d0addbe44) | `.github/workflows/ci.yml` / `quality-verification` | `reusable-trusted-verification.yml@e1a9f09504b903120e9b5297eff64f58a891c907` | `default` |
| notify | [default:main](https://github.com/Tuinstra-DEV/notify/commit/42485a84a07186907f53d25f140c0c6d0addbe44) | `.github/workflows/ci.yml` / `production-images-verification` | `reusable-trusted-verification.yml@e1a9f09504b903120e9b5297eff64f58a891c907` | `default` |
| openairco | [PR#4](https://github.com/Tuinstra-DEV/openairco/commit/72388365e1fb013e9bd1273d9b5a079986122e1e) | `.github/workflows/ci.yml` / `flutter-verification` | `reusable-trusted-verification.yml@23c08cf212dd6411ed5076b2ec53a05c1e2e5f3e` | `default` |
| openairco-site | [default:develop](https://github.com/Tuinstra-DEV/openairco-site/commit/4c2a94e9375b8e4c51fcbdc5233959f3b26b8ec6) | `.github/workflows/ci.yml` / `verify` | `reusable-trusted-verification.yml@e1a9f09504b903120e9b5297eff64f58a891c907` | `default` |
| openairco-site | [main:main](https://github.com/Tuinstra-DEV/openairco-site/commit/244cea949162b4604af136d1847d1a144e835801) | `.github/workflows/ci.yml` / `verify` | `reusable-trusted-verification.yml@e1a9f09504b903120e9b5297eff64f58a891c907` | `default` |
| status | [default:main](https://github.com/Tuinstra-DEV/status/commit/28919b43c25e5e6e4acdce96c45bfe12f2206f44) | `.github/workflows/ci.yml` / `status-quality` | `reusable-trusted-verification.yml@e1a9f09504b903120e9b5297eff64f58a891c907` | `default` |
| sudoku-tessera | [default:develop](https://github.com/Tuinstra-DEV/sudoku-tessera/commit/9e2468a80e93e31bc18f6481887c3ca43bbe86c4) | `.github/workflows/ci.yml` / `flutter-verification` | `reusable-trusted-verification.yml@ea86c62eeac615cb292a78bf50995fed81c051ee` | `default` |
| tracker | [default:develop](https://github.com/Tuinstra-DEV/tracker/commit/8e7068d065b2f22e5b0602737a3268fca97202f1) | `.github/workflows/containers.yml` / `tracker-images-bundle` | `reusable-trusted-verification.yml@ea86c62eeac615cb292a78bf50995fed81c051ee` | `default` |
| tracker | [default:develop](https://github.com/Tuinstra-DEV/tracker/commit/8e7068d065b2f22e5b0602737a3268fca97202f1) | `.github/workflows/gate-pr-security.yml` / `security` | `reusable-gate-pr-security.yml@2ca87c18bddce63379b7a33dc6de5be2f0a1d951` | `default` |
| tracker | [default:develop](https://github.com/Tuinstra-DEV/tracker/commit/8e7068d065b2f22e5b0602737a3268fca97202f1) | `.github/workflows/quality.yml` / `tracker-quality-bundle` | `reusable-trusted-verification.yml@ea86c62eeac615cb292a78bf50995fed81c051ee` | `default` |
| tracker | [main:main](https://github.com/Tuinstra-DEV/tracker/commit/95cefcfeac4b546d5b3958ba23d876784c608b2f) | `.github/workflows/containers.yml` / `tracker-images-bundle` | `reusable-trusted-verification.yml@23c08cf212dd6411ed5076b2ec53a05c1e2e5f3e` | `default` |
| tracker | [main:main](https://github.com/Tuinstra-DEV/tracker/commit/95cefcfeac4b546d5b3958ba23d876784c608b2f) | `.github/workflows/gate-pr-security.yml` / `security` | `reusable-gate-pr-security.yml@2ca87c18bddce63379b7a33dc6de5be2f0a1d951` | `default` |
| tracker | [main:main](https://github.com/Tuinstra-DEV/tracker/commit/95cefcfeac4b546d5b3958ba23d876784c608b2f) | `.github/workflows/quality.yml` / `tracker-quality-bundle` | `reusable-trusted-verification.yml@23c08cf212dd6411ed5076b2ec53a05c1e2e5f3e` | `default` |
| tracker | [PR#376](https://github.com/Tuinstra-DEV/tracker/commit/51f6b5118fcc105317a6b2275ad41a73b6596ed5) | `.github/workflows/containers.yml` / `tracker-images-bundle` | `reusable-trusted-verification.yml@23c08cf212dd6411ed5076b2ec53a05c1e2e5f3e` | `default` |
| tracker | [PR#376](https://github.com/Tuinstra-DEV/tracker/commit/51f6b5118fcc105317a6b2275ad41a73b6596ed5) | `.github/workflows/gate-pr-security.yml` / `security` | `reusable-gate-pr-security.yml@2ca87c18bddce63379b7a33dc6de5be2f0a1d951` | `default` |
| tracker | [PR#376](https://github.com/Tuinstra-DEV/tracker/commit/51f6b5118fcc105317a6b2275ad41a73b6596ed5) | `.github/workflows/quality.yml` / `tracker-quality-bundle` | `reusable-trusted-verification.yml@23c08cf212dd6411ed5076b2ec53a05c1e2e5f3e` | `default` |
| tracker | [PR#374](https://github.com/Tuinstra-DEV/tracker/commit/cfe37d073937375d8caeda573c09545c96d2c55e) | `.github/workflows/containers.yml` / `tracker-images-bundle` | `reusable-trusted-verification.yml@23c08cf212dd6411ed5076b2ec53a05c1e2e5f3e` | `default` |
| tracker | [PR#374](https://github.com/Tuinstra-DEV/tracker/commit/cfe37d073937375d8caeda573c09545c96d2c55e) | `.github/workflows/gate-pr-security.yml` / `security` | `reusable-gate-pr-security.yml@2ca87c18bddce63379b7a33dc6de5be2f0a1d951` | `default` |
| tracker | [PR#374](https://github.com/Tuinstra-DEV/tracker/commit/cfe37d073937375d8caeda573c09545c96d2c55e) | `.github/workflows/quality.yml` / `tracker-quality-bundle` | `reusable-trusted-verification.yml@23c08cf212dd6411ed5076b2ec53a05c1e2e5f3e` | `default` |
| tuinstra-site | [default:develop](https://github.com/Tuinstra-DEV/tuinstra-site/commit/17f54dc4aaf3bcde218d2be6ce866bb7cb6e40f3) | `.github/workflows/ci.yml` / `verify` | `reusable-trusted-verification.yml@e1a9f09504b903120e9b5297eff64f58a891c907` | `default` |
| tuinstra-site | [main:main](https://github.com/Tuinstra-DEV/tuinstra-site/commit/76301ae52ab6cfe5fb5fe2bcd24bbc0c956c4e21) | `.github/workflows/ci.yml` / `verify` | `reusable-trusted-verification.yml@e1a9f09504b903120e9b5297eff64f58a891c907` | `default` |
| wodiq-app | [default:develop](https://github.com/Tuinstra-DEV/wodiq-app/commit/20127e7f85039fdb95ab87b8f36206e41655e218) | `.github/workflows/checks.yml` / `client-checks` | `reusable-trusted-verification.yml@e1a9f09504b903120e9b5297eff64f58a891c907` | `default` |
| wodiq-platform | [default:develop, PR#152](https://github.com/Tuinstra-DEV/wodiq-platform/commit/9e39bbc1f4ae95f756a29def6b213182a3e4e667) | `.github/workflows/ci.yml` / `bundle` | `reusable-trusted-verification.yml@23c08cf212dd6411ed5076b2ec53a05c1e2e5f3e` | `default` |
| wodiq-platform | [PR#157](https://github.com/Tuinstra-DEV/wodiq-platform/commit/aa256ef22ab413ecb37eba069c0d29c05e96a827) | `.github/workflows/ci.yml` / `bundle` | `reusable-trusted-verification.yml@23c08cf212dd6411ed5076b2ec53a05c1e2e5f3e` | `default` |
| wodiq-site | [default:develop](https://github.com/Tuinstra-DEV/wodiq-site/commit/e8ac01e31e141d6c2b377f5521fea1f704b43089) | `.github/workflows/ci.yml` / `verify` | `reusable-trusted-verification.yml@e1a9f09504b903120e9b5297eff64f58a891c907` | `default` |
| wodiq-site | [main:main](https://github.com/Tuinstra-DEV/wodiq-site/commit/a17803cec12a6567ac9e2a6414fdb5680fa54591) | `.github/workflows/ci.yml` / `verify` | `reusable-trusted-verification.yml@e1a9f09504b903120e9b5297eff64f58a891c907` | `default` |

The same seven e1 consumer repositories still need migration. Tracker develop still requests the default isolated ea metadata preflight; Gate PR #426 still uses a8. Tessera still needs ea trusted until its eventual 288 migration. WODIQ Platform’s inspected source uses 23; that does not authorize retiring the explicitly retained 604 slot.

The scan found 2 nonterminal run records. Each immutable source must be rechecked by the fixed collector before any ref retirement; the status readback is not an atomic cross-repository lock. No old run was cancelled or rerun.

## Required consumer validation before rollout

- Keep the seven e1 callers’ selector, adapter key, trusted/hosted proof, verdict and required check aliases. Verify their actual migration PR runs; eight earlier fixed-contract comparisons are supporting evidence only.
- Set literal `request-isolated-role-runner: false` only on Tracker/Gate trusted calls still using ea before replacing the metadata role. Wait for already queued/running old-role calls to complete.
- Preserve all configured required names and app bindings above. If any real consumer change needs a different alias, review that separately and coordinate the workflow/required-context update; do not silently delete a requirement.
- Gate scanner publication, producer registration and canonical caller migration remain separate prerequisites for moving the security scan; group admission does not establish those contracts.

Local filtered source records: `/private/tmp/runner-cutover-review-inventory/`. Only these summarized policy/check/caller facts are published here; raw private workflow files, token data, personal grants and response bodies are not included.

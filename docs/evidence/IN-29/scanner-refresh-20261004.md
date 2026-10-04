# IN-29 canonical scanner and producer refresh — 4 October 2026

## Fixed identities

- Protected Gate publication: [run 37227722419](https://github.com/Tuinstra-DEV/gate/actions/runs/37227722419).
- Publication source: `c2faed94053b449948242fedf0950c7a9763fbf1`.
- Scanner: `ghcr.io/tuinstra-dev/gate/ci-scanner@sha256:3fcdefee1936f3e973209fd8f7ed1cb476935f6b0ee6cd7f7d591c0084481355`.
- Policy: `sha256:eb75f555e8d08280cbb658c9317020c24196e67148267bc6a957a16f45374a0b`.
- Bundle and frozen dataset: `sha256:baede296ee579f2a6b7a171816641b2584cf4915ca2e314cd092cb298f327bf9`.
- Producer exclusion table: `sha256:5a505cf05b24a383e619d8c6a12d5c59912042d5db752775b831e1e89c6cfad6`.

All four publication jobs passed: image verification, preserved immutable private publication, authenticated digest pull, and anonymous access refusal. The downloaded OCI artifact, its source receipt, manifest/config/layer bytes, runner source manifest and actual offline non-root runtime were checked locally.

## Scope and explicit approval

The sole additional exclusion is the exact reviewed `frontend/patches/braces@3.0.3.patch`, SHA-256 `ef55a5662ef89adeb3665748fcbe04127f72694fdf796c5072b68d023b538e19`. All 19 prior entries remain unchanged. Patch hunks have no Semgrep coverage; they retain the separately reviewed regression evidence. Changed patch bytes become incomplete. Repository, App rights, OIDC hosts, event mode and all other required controls remain unchanged.

Marcel explicitly approved the exact guarded braces mitigation, unchanged node-forge patch with the new lock binding, and these coherent scanner/producer pins on 4 October: question `call_ZgsrNxrAxSa5DmmjJFDROsPk`, human answer `01a10843-0bc3-7cd1-890f-0de581b4a1a2` (“JA! Fix het!!!”). The existing audit exception expiry remains 9 October 2026, 00:00 Europe/Amsterdam. This delivery does not add an audit ignore or extend that expiry.

## Local verification

- Focused producer tests: **74 PASS**.
- `PATH=/private/tmp/dev48-ansible-venv/bin:$PATH make lint`: **PASS**.
- `PATH=/private/tmp/dev48-ansible-venv/bin:$PATH PYTHONDONTWRITEBYTECODE=1 make test`: **PASS**.
- `git diff --check`: **PASS**.
- Actual published OCI scanner → producer assurance archive → Gate PHP artifact verifier: **12/12 PASS** using synthetic fixtures and the real implementations, with networking disabled and no credentials in the scanner.

| Synthetic case | Observed verdict |
| --- | --- |
| Clean change | pass |
| New high finding | blocked |
| Incomplete scan | incomplete |
| Verified absence | pass |
| Existing debt | pass, debt retained |
| Unsupported Yarn | incomplete |
| Unknown input | incomplete |
| Exact approved exclusions | pass, 8 exclusion records |
| Changed prior exclusion | incomplete |
| Changed braces patch | incomplete |
| Removed exclusions | pass |
| New high with approved exclusions | blocked |

The first attempted integration output location failed safely because Colima does not share `/private/tmp`. Repeating with new synthetic fixtures under the existing shared Gate workspace passed all 12 cases. No scanner or policy checks were relaxed.

## Delivery boundaries

The helper is committed before its immutable workflow pin; the workflow is committed before the caller template pin. No mutable tags or self-referential SHA placeholders are used.

Gate application production is currently healthy at `85a127453cda9743371c06f5eefd28a07b8b14a7`, deployed by run 37228860188. Its existing publisher registration is still the previous profile. This producer refresh does **not** establish live enrollment, App-owned positive/negative verdicts, required merge checks, four-persona acceptance or completion of IN-29. Those remain separate attributable Gate-pilot steps. There is no UI change; screenshots are N/A.

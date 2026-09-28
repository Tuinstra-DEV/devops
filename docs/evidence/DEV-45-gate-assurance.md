# DEV-45 verification and handoff

Date: 27 September 2026. Story: DEV-45 (Chore, 3). Branch:
`feat/DEV-45-gate-assurance-standard`, based on `origin/main` at
`a6fa73bc5c2503972077e8cb10efffabad3f8410` in the existing DevOps checkout.
No additional checkout/worktree was created. This delivery changes documentation,
versioned JSON contracts and synthetic examples only.

## Acceptance mapping

| Criterion | Attributable evidence |
|---|---|
| 13 security repositories / eight runtime applications | `contracts/gate-assurance/v1/inventory.json`; each application lists its owned components, while DevOps/sites have explicit observability exclusions |
| Policy, coverage and outcome definitions | `docs/workflows/contracts/gate-assurance-v1.md`: capability matrix, identical snapshots, finding identity, new/worsened/existing debt, exception semantics, incomplete execution |
| Versioned source, runtime and evidence contracts | `schema.json`; three incomplete fixture examples; native target/version/build/architecture/artifact/symbol mapping; consumer manifest template |
| Engineering/onboarding and recovery | Updated baseline and consumer migration checklist; trust, privacy, controlled probes, production configuration, release validation and rollback in the contract |
| Local verification and review | Results below; independent product/technical/quality planning and focused schema review completed |

## Checks

- `make lint`: **PASS**.
- `PATH="/private/tmp/gate-devops-verification-20260927/bin:$PATH" ANSIBLE_HOME=/private/tmp/gate-devops-ansible-20260927 make test`: **PASS**, exit 0.
- `git diff --check`: **PASS**.
- JSON Schema Draft 2020-12 self-validation and five documents (inventory, three
  incomplete examples, onboarding manifest): **PASS** using `jsonschema==4.25.1`
  and its format checker.
- Inventory invariants: 13 unique repository names, eight observability consumers,
  security required for all, component presence consistent with applicability,
  unique component IDs per application: **PASS**.
- Eleven invalid-fixture cases rejected: missing run attempt; branch name replacing
  exact head SHA; numeric rather than decimal-string repository ID; extra secret
  field; traversing report path; unknown contract major; mutable scanner image;
  native build missing architecture, build number or symbols; malformed installation
  UUID: **PASS**.
- All 19 relative links in the changed standard/onboarding/contract docs resolve.

The test environment uses Python 3.12 and temporary `ansible-core==2.21.4`.
The initial full-suite attempt could not start Ansible's local RPC helper inside
the sandbox. The specific local Umami template test was inspected and passed
outside that sandbox, followed by the complete suite. Tests render temporary
localhost fixtures and syntax-check playbooks; no production playbooks were run.
The existing suite skips optional Packer formatting when Packer is unavailable
and its cross-repository Dependabot check when `DEPENDABOT_FLEET_ROOT` is unset.
Neither is changed by this Story. Existing apt_repository deprecation warnings
remain; no infrastructure refactor was included.

## Review correction

QA requested explicit native release identity. The schema and native fixture now
record platform, target, app version, build number, architecture, artifact digest
and symbol kind/digest/debug ID. The contract requires matching CI provenance,
unique build tuples and matching emitted frames. Runtime validators must enforce
these semantic requirements; schema validation alone is not trust or completeness.

The documentation separately preserves existing Heal QA controls, keeps automatic
fix generation disabled, and limits QA to generated fix PRs. Ordinary observability
migration and normal production promotion do not depend on activating auto-fixes.

## Boundaries and next deliveries

No new scanner, SDK, publisher, workflow, secret, GitHub App installation or branch
rule was activated. Examples are synthetic `fixture` evidence with `incomplete`
outcomes. Inventory stages describe planned rollout, not existing compliance.

Next: IN-25 implements the strict scan profile; IN-26 the trusted publisher;
DEV-46 the reusable workflow. Consumer enrollment and mandatory checks require
separate green/red pilots and attributable delivery. Sentry/runtime integration
Stories follow the approved migration plan. Human PR review and acceptance remain
outstanding; this document does not authorize merging this Story branch.

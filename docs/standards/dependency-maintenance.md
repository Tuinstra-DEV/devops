# Manual dependency maintenance — DEV-47

Marcel approved disabling automatic dependency PRs organization-wide on 1 October 2026. The scope is all 14 Tuinstra-DEV repositories: agent-lab, console, devops, gate, marcel-site, notify, openairco, openairco-site, status, tracker, tuinstra-site, wodiq-app, wodiq-platform, wodiq-site.

Remove active `.github/dependabot.yml` or `.yaml` files from default branches through normal reviewed PRs; disable automatic security-update PRs separately. Keep existing dependency PRs, CVE alerts, dependency graphs, audit checks and required CI verdicts. New repository onboarding must check that no automatic update configuration is introduced.

Marcel reviews routine updates weekly and triages high/critical CVEs within 24 hours. For each remediation, use a Story branch, update the smallest compatible dependency set, run applicable audit/lint/type/unit/build checks locally and then the required CI gates. Routine outdated packages and known-vulnerable packages are different concerns.

Verified API settings: all 14 repositories have security updates disabled. Existing alerts remain enabled for 11; Status, WODIQ Platform and Agent Lab already had alerts disabled and were left unchanged. These existing visibility gaps require an explicit follow-up decision. No existing PRs were closed.

GitHub's Dependabot generator jobs are free on standard runners; savings come from preventing the hosted CI runs those PRs trigger. Historical DEV-13 records remain audit evidence, not the active policy.

Rollback requires a separately authorized normal commit restoring the old configuration and, where intended, enabling security-update PRs. Do not silently re-enable automation or change CVE/audit settings. No UI changed and no UI screenshot applies.

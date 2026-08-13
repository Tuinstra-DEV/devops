# Dependency Policy Rollout Matrix

| Repository | Ecosystems | Target | Rollout order | Rollback |
| --- | --- | --- | ---: | --- |
| Tuinstra-DEV/devops | GitHub Actions | main | 1 | Revert config and policy commit |
| Tuinstra-DEV/marcel-site | GitHub Actions, npm | develop | 2, hosted-only canary | Restore removed Renovate config and remove Dependabot config |
| Tuinstra-DEV/openairco-site | GitHub Actions, npm | develop | 3 | Restore removed Renovate config and remove Dependabot config |
| Tuinstra-DEV/tuinstra-site | GitHub Actions, npm | develop | 3 | Restore removed Renovate config and remove Dependabot config |
| Tuinstra-DEV/wodiq-site | GitHub Actions, npm | develop | 3 | Restore removed Renovate config and remove Dependabot config |
| marcel-tuinstra/sudoku-spark-web | GitHub Actions, npm | develop | 3 | Remove Dependabot config |
| Tuinstra-DEV/console | GitHub Actions, Composer, npm | develop | 4 | Restore removed Renovate config and remove Dependabot config |
| Tuinstra-DEV/notify | GitHub Actions, Composer, npm | main | 4 | Remove Dependabot config |
| Tuinstra-DEV/WODIQ | GitHub Actions, npm | develop | 4 | Remove Dependabot config |
| Tuinstra-DEV/gate | GitHub Actions, Docker (3 directories), Composer, npm | develop | 5 | Revert Dependabot config |
| Tuinstra-DEV/tracker | GitHub Actions, Docker (3 directories), Composer, npm | default branch | 5 | Revert Dependabot config |

Merge DevOps first, then marcel-site as the hosted-only canary. Confirm GitHub
accepts its config, creates the expected routine group or reports no updates,
and targets develop. Observe each wave through its initial dependency check and
for at least seven complete days. Promote only with zero config errors, no more
than one routine PR plus visible majors per ecosystem, zero trusted-heavy bot
jobs, security-fix creation within 24 hours, and projected all-11 runs/minutes
within the non-regression caps. Otherwise stop and roll back that wave.
For an observation of `d` complete days, project each cumulative count as
`ceil(observed / d * 30)`; begin the observation only after the wave's initial
scheduled dependency check. This deliberately conservative linear projection
is a promotion gate, not the final result. Any collector error, unparsed
dependency PR, unknown runner job with positive duration, or missing initial
check blocks promotion.

Gate had 13 open ordinary Dependabot PRs on 2026-08-12: five Composer, four npm,
three GitHub Actions and one Docker. Merge or intentionally close ordinary PRs
until each ecosystem is at or below two before expecting the new grouped
topology to converge. Never close security-fix PRs as queue cleanup.

Rollback in exact reverse wave order. Revert or remove consumer configs first,
preserve open security-fix PRs, and restore the previous tracked Renovate file
only where one existed. Those restored files are historical compatibility, not
an active updater while the Renovate App is absent. The functional containment
state is security updates enabled with ordinary version updates disabled.
Revert DevOps policy last.

Before staging any wave, run the cross-repository contract from a workspace
containing all 11 worktrees:
`DEPENDABOT_FLEET_ROOT=/path/to/DEV-13 make test` in the DevOps worktree.
A single-repository CI checkout cannot prove sibling repository state; the
staged release gate therefore requires this explicit fleet run in addition to
each repository's own checks.

Docker base-image majors are excluded from the newly onboarded repositories in
DEV-13 because they can require coordinated runtime and deployment changes.
Repository owners review Dockerfile base images monthly and open a normal,
human-owned change when needed. Gate and Tracker retain automated Docker checks
because those existing paths already have compatibility constraints and CI
coverage.

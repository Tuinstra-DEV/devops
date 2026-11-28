# DEV-54 compact CI pilot

`reusable-product-ci.yml` accepts only `profile: gate` in `Tuinstra-DEV/gate`.
The consumer pilot runs alongside existing required CI on same-repository PRs
from `chore/DEV-54-compact-ci-pilot` into `develop`, only when that pilot caller
changes. Existing required checks and branch protection remain in force.

The graph contains Bronselectie, a dynamic matrix containing only the selected
Frontend CI and Backend CI jobs, and CI-verdict. Native MariaDB is a step in
Backend CI. It reuses the caller's Composer installation and the existing
digest-pinned isolated database fixture. There are no generic Tracker/WODIQ
jobs, trusted runner admission, release artifacts or deployment in this pilot.

The verdict requires successful selection and successful selected work. An
explicit empty selection permits a skipped matrix; any failed, cancelled,
missing or unexpectedly skipped selected work fails. GitHub can still display
one skipped matrix placeholder for an empty selection. The executable contract
tests cover the exact selection and verdict scripts, including negative cases.

This graph intentionally starts up to four hosted jobs (selection, two suites,
verdict). It tests graph clarity and database co-location; the Story's general
one-to-two hosted jobs target is not achieved by this Gate pilot alone.

Before replacing existing CI, compare both workflows on the same candidate:
record run/attempt/source, selected jobs and database step result, runner starts,
job runtime rounded per job, and queue delay separately. These rounded minutes
are a diagnostic estimate, not an invoice or monthly savings measurement.
Keep negative verdict checks and required-check migration distinct from a
successful run. Release CI/Deploy and candidate artifact digest continuity
remain separate work; do not infer their acceptance from this CI-only pilot.

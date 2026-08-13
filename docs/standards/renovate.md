# Renovate Presets

The legacy shared Renovate presets remain available for compatibility. They are
not used by the DEV-13 rollout because the Renovate GitHub App is not installed
for the Tuinstra-DEV organization as of 2026-08-12.

DEV-13 uses GitHub-native Dependabot so every repository has an operational,
auditable dependency-update path without adding an external app or credential.
See dependency-update-policy.md and the rollout matrix for the active policy.

Do not enable Renovate for a repository that already has Dependabot version
updates without first disabling one bot; two active bots would duplicate pull
requests and CI runs.

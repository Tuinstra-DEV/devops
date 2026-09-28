"""Reviewed producer profile. Changes require a new immutable workflow pin."""

IMAGE = "ghcr.io/tuinstra-dev/gate-ci-scanner@sha256:6e579433706fbb7cf2f2360fc0b3ed9052f4e1a56dbfb9efbd57b811d81c99d0"
POLICY_DIGEST = "sha256:8bd185c4dd22c74bcee7f6490e1c96d21bd7c87de44f76ec07065c72897c0a97"
BUNDLE_DIGEST = "sha256:c2dbaf868aedb5d67202d91d8a9a93e82c0262d7d85b4bdfcd46ae4063169cb7"
DATASET_DIGEST = BUNDLE_DIGEST
ENDPOINT = "https://gate.tuinstra.dev/integrations/github/pr-security/receipts"
AUDIENCE = "https://gate.tuinstra.dev/pr-security"

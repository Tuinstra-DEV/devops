"""Reviewed producer profile. Changes require a new immutable workflow pin."""

IMAGE = "ghcr.io/tuinstra-dev/gate/ci-scanner@sha256:ee183d3c710d69c24923159813183c8d7ec06a0b6f38d9cd874bec00964a40a0"
POLICY_DIGEST = "sha256:1fc6f66fa2e39f58c4cedd290275e5d0715b2858a541477a5fd5abc882997f2f"
EXCLUDED_INPUTS_DIGEST = "sha256:0ba6df75e6aef9ad8568f0a02bdf69febbd17de77daeae498835c128289123e3"
BUNDLE_DIGEST = "sha256:bef09b59369b57eea636b793be28c73b4167faef84729cf856b38ec651217a03"
DATASET_DIGEST = BUNDLE_DIGEST
ENDPOINT = "https://gate.tuinstra.dev/integrations/github/pr-security/receipts"
AUDIENCE = "https://gate.tuinstra.dev/pr-security"

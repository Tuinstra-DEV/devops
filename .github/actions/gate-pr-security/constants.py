"""Reviewed producer profile. Changes require a new immutable workflow pin."""

IMAGE = "ghcr.io/tuinstra-dev/gate/ci-scanner@sha256:6a63e8d1e105f27a083173187cb6b718181953b1dcaa1b0c71d07ba5d648c4ae"
POLICY_DIGEST = "sha256:a7b3fb9f22ec2342c847ffab8eeee24745d9fbe2da136fcb27cc44388d897f6b"
EXCLUDED_INPUTS_DIGEST = "sha256:a1cd26981d719e9b8d071ccf0555b05aa9e16a75a5e5890d27464d63444a91b8"
BUNDLE_DIGEST = "sha256:27416c40c66c07bef5a29ddf3d8e455dfa666086f77d3d6f01c51b926003b4c7"
DATASET_DIGEST = BUNDLE_DIGEST
ENDPOINT = "https://gate.tuinstra.dev/integrations/github/pr-security/receipts"
AUDIENCE = "https://gate.tuinstra.dev/pr-security"

"""Reviewed producer profile. Changes require a new immutable workflow pin."""

IMAGE = "ghcr.io/tuinstra-dev/gate/ci-scanner@sha256:ff504c164b4d715e40f101d7273fb604798137199ccb8664bfa60005783cb0f2"
POLICY_DIGEST = "sha256:6288f3a9d7b463d2104bb31d44043b6df667bca2f486c450b7d2a2b376a77db6"
BUNDLE_DIGEST = "sha256:7506052c4055bf90c79a083f160ef3f381f2b75d21faa088c1f5000601116f24"
DATASET_DIGEST = BUNDLE_DIGEST
ENDPOINT = "https://gate.tuinstra.dev/integrations/github/pr-security/receipts"
AUDIENCE = "https://gate.tuinstra.dev/pr-security"

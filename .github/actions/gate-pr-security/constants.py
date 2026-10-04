"""Reviewed producer profile. Changes require a new immutable workflow pin."""

IMAGE = "ghcr.io/tuinstra-dev/gate/ci-scanner@sha256:3fcdefee1936f3e973209fd8f7ed1cb476935f6b0ee6cd7f7d591c0084481355"
POLICY_DIGEST = "sha256:eb75f555e8d08280cbb658c9317020c24196e67148267bc6a957a16f45374a0b"
EXCLUDED_INPUTS_DIGEST = "sha256:5a505cf05b24a383e619d8c6a12d5c59912042d5db752775b831e1e89c6cfad6"
BUNDLE_DIGEST = "sha256:baede296ee579f2a6b7a171816641b2584cf4915ca2e314cd092cb298f327bf9"
DATASET_DIGEST = BUNDLE_DIGEST
ENDPOINT = "https://gate.tuinstra.dev/integrations/github/pr-security/receipts"
AUDIENCE = "https://gate.tuinstra.dev/pr-security"

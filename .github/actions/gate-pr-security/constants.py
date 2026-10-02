"""Reviewed producer profile. Changes require a new immutable workflow pin."""

IMAGE = "ghcr.io/tuinstra-dev/gate/ci-scanner@sha256:64dd14c6d5d1b56dec146105312ded9ce11bbad1571322bea04145ba2acb4487"
POLICY_DIGEST = "sha256:bca9dae69e5a93f4f72b3941328abf3e3ab49ab05f9bd99c77c5e96dd3ca19be"
EXCLUDED_INPUTS_DIGEST = "sha256:561cb956466b087675668769dac63046c76d1a4f0d60e4b9f2fdee77b98db1ae"
BUNDLE_DIGEST = "sha256:b0d3535458f977a26942a1fc8382ee17df3c215fd027165f6c0c9dc95e850169"
DATASET_DIGEST = BUNDLE_DIGEST
ENDPOINT = "https://gate.tuinstra.dev/integrations/github/pr-security/receipts"
AUDIENCE = "https://gate.tuinstra.dev/pr-security"

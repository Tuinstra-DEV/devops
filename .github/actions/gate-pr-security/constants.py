"""Reviewed producer profile. Changes require a new immutable workflow pin."""

IMAGE = "ghcr.io/tuinstra-dev/gate/ci-scanner@sha256:85cef5779d1323b12ad561542d1c7fa1ac52007f79ee28879ac40b9d352242bc"
POLICY_DIGEST = "sha256:4cb64334618d558d08da5cd4ef3fe33ab7055b6f430013e20f7508d642d75bff"
EXCLUDED_INPUTS_DIGEST = "sha256:f4432b6c1dcf0e387b1eaa1e56065d6e2dc487586c32db32bc439e5841535091"
BUNDLE_DIGEST = "sha256:6d48a8ebf88802e888e5bd527f8bdece0d91cff340c9064efb3592cecdd8362d"
DATASET_DIGEST = BUNDLE_DIGEST
ENDPOINT = "https://gate.tuinstra.dev/integrations/github/pr-security/receipts"
AUDIENCE = "https://gate.tuinstra.dev/pr-security"

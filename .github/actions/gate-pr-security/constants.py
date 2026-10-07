"""Reviewed producer profile. Changes require a new immutable workflow pin."""

IMAGE = "ghcr.io/tuinstra-dev/gate/ci-scanner@sha256:591b8079477e5e42a366bb1ea50dcb6cebfb968e933293a02380d3763a7756c5"
POLICY_DIGEST = "sha256:e865da74ee00ca910a3af661a9841ec0f6dbfdcfbe2499a82c51ffe75247b6e6"
EXCLUDED_INPUTS_DIGEST = "sha256:f32bd3a571f439c8cc162bb6c89ced7153d45b01bcb80362c9856b8738794059"
BUNDLE_DIGEST = "sha256:56c461418ac00b19c79f503949ef9297a5c810a3528167c3fc1e784732e8ae65"
DATASET_DIGEST = BUNDLE_DIGEST
ENDPOINT = "https://gate.tuinstra.dev/integrations/github/pr-security/receipts"
AUDIENCE = "https://gate.tuinstra.dev/pr-security"

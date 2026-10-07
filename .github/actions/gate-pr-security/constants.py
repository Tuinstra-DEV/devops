"""Reviewed producer profile. Changes require a new immutable workflow pin."""

IMAGE = "ghcr.io/tuinstra-dev/gate/ci-scanner@sha256:6dd129dfe8d1e093f8794610248af1352b021ff52059121c80908324dc132414"
POLICY_DIGEST = "sha256:71aafe520ca7ee3b0dc350a308e2875a9f558f20a53e3c7023390acc88f76bc4"
EXCLUDED_INPUTS_DIGEST = "sha256:86d9767c93c58f8df00d971b2da82ea92e26e9b236d3fe5abf2466f014ff0bc5"
BUNDLE_DIGEST = "sha256:2b00dd4947676189b6236f2a8c9394a9300c9ff5b7e709fa40f23180829835d5"
DATASET_DIGEST = BUNDLE_DIGEST
ENDPOINT = "https://gate.tuinstra.dev/integrations/github/pr-security/receipts"
AUDIENCE = "https://gate.tuinstra.dev/pr-security"

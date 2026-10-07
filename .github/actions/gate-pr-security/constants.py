"""Reviewed producer profile. Changes require a new immutable workflow pin."""

IMAGE = "ghcr.io/tuinstra-dev/gate/ci-scanner@sha256:48b768f9e6e61aac3aff458b17f758b530a58f964252075312d0358fee245e54"
POLICY_DIGEST = "sha256:4a7ccc1ccb4fa97ac1c4cf90aea99b3081a16c9c5bbc18027adb5c9420fe5d7b"
EXCLUDED_INPUTS_DIGEST = "sha256:c158040757f2e11368b14f954674c670e18aa5caa01c837b34a85139924ddec7"
BUNDLE_DIGEST = "sha256:2b00dd4947676189b6236f2a8c9394a9300c9ff5b7e709fa40f23180829835d5"
DATASET_DIGEST = BUNDLE_DIGEST
ENDPOINT = "https://gate.tuinstra.dev/integrations/github/pr-security/receipts"
AUDIENCE = "https://gate.tuinstra.dev/pr-security"

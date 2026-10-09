"""Reviewed native producer profiles. Changes require an immutable workflow pin."""

from __future__ import annotations

from types import MappingProxyType

ENDPOINT = "https://gate.tuinstra.dev/integrations/github/pr-security/receipts"
AUDIENCE = "https://gate.tuinstra.dev/pr-security"

_PROFILE_DATA = {
    "Tuinstra-DEV/gate": {
        "image": "ghcr.io/tuinstra-dev/gate/ci-scanner@sha256:7c8e368736a4fe78026b6f42509be15d0bcabb45cc3c87a2cad9cc0d6fd53289",
        "platform": "linux/amd64",
        "policy_digest": "sha256:4cb64334618d558d08da5cd4ef3fe33ab7055b6f430013e20f7508d642d75bff",
        "bundle_digest": "sha256:4f004763e61e1e52c4e1c20188d87fb725273a2f187be580e662760dfefb6217",
        "dataset_digest": "sha256:4f004763e61e1e52c4e1c20188d87fb725273a2f187be580e662760dfefb6217",
        "excluded_inputs_digest": "sha256:f4432b6c1dcf0e387b1eaa1e56065d6e2dc487586c32db32bc439e5841535091",
        "exclusions_file": "excluded-inputs.json",
        "tool_versions": {
            "semgrep": "1.179.0", "gitleaks": "8.30.1", "osv": "2.3.8",
            "gate-text": "1", "policy-exclusion": "1",
        },
        "scope_scanners": {
            "secrets": "gitleaks", "php": "semgrep", "javascript-typescript": "semgrep",
            "composer": "osv", "npm": "osv", "pnpm": "osv", "embedded-web": "semgrep",
            "configuration": "gate-text", "shell-infrastructure": "gate-text",
            "dockerfile": "gate-text", "web-assets": "gate-text", "template": "gate-text",
            "php-framework": "gate-text", "build-configuration": "gate-text",
            "opaque-input": "policy-exclusion", "unknown-input": "policy-exclusion",
            "embedded-code": "policy-exclusion", "python": "semgrep",
        },
    },
    "Tuinstra-DEV/tracker": {
        "image": "ghcr.io/tuinstra-dev/gate/ci-scanner@sha256:fe54383ae144931c798391568a17d6585c5a231b766c57d94158fb457db0e66b",
        "platform": "linux/amd64",
        "policy_digest": "sha256:70119e1c38a2598b4f2cfbcc69a3922819adff3c95ec492788a002e6087686e9",
        "bundle_digest": "sha256:57c42d26810429cfff7d122c2001930c9f3fa64c977912a73020da162f751037",
        "dataset_digest": "sha256:57c42d26810429cfff7d122c2001930c9f3fa64c977912a73020da162f751037",
        "excluded_inputs_digest": "sha256:2ec8b98a1ac94a3905c6034c798fecc1ea29e28f128856e2d1a2cdfe2e646024",
        "exclusions_file": "excluded-inputs-tracker.json",
        "tool_versions": {
            "semgrep": "1.179.0", "gitleaks": "8.30.1", "osv": "2.3.8",
            "gate-text": "1", "gate-assets": "1", "policy-exclusion": "1",
        },
        "scope_scanners": {
            "secrets": "gitleaks", "php": "semgrep", "javascript-typescript": "semgrep",
            "composer": "osv", "npm": "osv", "pnpm": "osv", "embedded-web": "semgrep",
            "configuration": "gate-text", "shell-infrastructure": "gate-text",
            "dockerfile": "gate-text", "web-assets": "gate-text", "template": "gate-text",
            "php-framework": "gate-text", "build-configuration": "gate-text",
            "opaque-input": "policy-exclusion", "unknown-input": "policy-exclusion",
            "embedded-code": "policy-exclusion", "python": "semgrep",
            "patched-javascript": "semgrep", "static-assets": "gate-assets",
        },
    },
}

PROFILES = MappingProxyType({
    repository: MappingProxyType({
        **profile,
        "tool_versions": MappingProxyType(profile["tool_versions"]),
        "scope_scanners": MappingProxyType(profile["scope_scanners"]),
    })
    for repository, profile in _PROFILE_DATA.items()
})

def profile_for(repository: object):
    """Return the fixed profile for an allowlisted repository name."""
    if not isinstance(repository, str):
        raise ValueError("unknown_repository")
    try:
        return PROFILES[repository]
    except KeyError:
        raise ValueError("unknown_repository") from None

# Legacy Gate aliases retained for existing imports and focused tests.
_GATE = PROFILES["Tuinstra-DEV/gate"]
IMAGE = _GATE["image"]
POLICY_DIGEST = _GATE["policy_digest"]
EXCLUDED_INPUTS_DIGEST = _GATE["excluded_inputs_digest"]
BUNDLE_DIGEST = _GATE["bundle_digest"]
DATASET_DIGEST = _GATE["dataset_digest"]
TOOL_VERSIONS = _GATE["tool_versions"]
SCOPE_SCANNERS = _GATE["scope_scanners"]

# Workflow Contracts

This directory contains the contract specifications for all reusable workflows in the devops platform.

## Purpose

Workflow contracts document the stable API surface of each reusable workflow:
- Inputs, outputs, and their types
- Required secrets and permissions
- Expected behavior and guarantees
- Breaking change policy

## Available Contracts

### Node.js / Nuxt

| Workflow | Version | Status |
|----------|---------|--------|
| [reusable-ci.yml](reusable-ci.md) | v1 | Stable |
| [reusable-ci-docker.yml](reusable-ci-docker.md) | v1 | Stable |
| [reusable-cd-nuxt-ssg.yml](reusable-cd-nuxt-ssg.md) | v1 | Stable |

### Vite / SPA

| Workflow | Version | Status |
|----------|---------|--------|
| [reusable-cd-vite-spa.yml](reusable-cd-vite-spa.md) | v1 | Stable |

### PHP / Symfony

| Workflow | Version | Status |
|----------|---------|--------|
| [reusable-php-lint.yml](reusable-php-lint.md) | v1 | Stable |
| [reusable-php-test.yml](reusable-php-test.md) | v1 | Stable |
| [reusable-php-security.yml](reusable-php-security.md) | v1 | Stable |

### Gate / Baseline Evidence

| Workflow | Version | Status |
|----------|---------|--------|
| [reusable-gate-baseline.yml](reusable-gate-baseline.md) | v1 | Stable |

## Governance

Changes to workflow contracts require:
1. Platform owner approval (enforced via CODEOWNERS)
2. Adherence to [versioning policy](../versioning-policy.md)
3. 60-day deprecation window for breaking changes

## Adding New Contracts

When creating a new reusable workflow:
1. Create a contract document in this directory
2. Follow the template structure from existing contracts
3. Add entry to CODEOWNERS for review enforcement

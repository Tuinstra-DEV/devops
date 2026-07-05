# ADR: Auth0 as Central Identity Provider

- Status: accepted
- Date: 2026-02-06
- Epic: SC-200 (DevOps Platform)
- Related stories: SC-231, SC-232, SC-236, SC-237

## Context

AirportToday currently uses Keycloak and Subtrack currently uses a custom auth implementation.
The DevOps platform requires a central identity approach that supports shared governance,
consistent developer workflows, and reduced long-term maintenance cost.

## Decision

Use Auth0 as the central identity provider for the DevOps platform and as the target identity
platform for AirportToday and Subtrack.

## Decision Drivers

- Consolidate identity operations across products.
- Improve security baseline with managed MFA and policy controls.
- Reduce platform team time spent on auth infrastructure.
- Standardize token and session behavior across environments.

## Alternatives Considered

### Keep Keycloak as central platform

- Pros: full control, no vendor subscription lock-in.
- Cons: high maintenance load, slower feature rollout, extra on-call burden.

### Keep per-product auth stacks

- Pros: no immediate migration effort.
- Cons: fragmented security posture, duplicated effort, inconsistent developer experience.

## Tenant and Environment Strategy

- Single Auth0 tenant per company workspace for centralized policy management.
- Environment separation by applications and connections:
  - `dev` applications for local and shared development.
  - `production` applications for live traffic.
- Environment-specific callback URLs, API audiences, and secrets are isolated.
- Product organization:
  - `devops-platform-*`
  - `airporttoday-*`
  - `subtrack-*`

## Security Baseline and Guardrails

- MFA policy:
  - Require MFA for all admin and maintainer roles.
  - Require MFA enrollment at first privileged login.
- Token policy:
  - Access token TTL: 15 minutes.
  - Refresh token rotation enabled.
  - Reuse interval set to 0 seconds.
- Session policy:
  - Absolute session timeout: 12 hours.
  - Idle timeout: 30 minutes.
- Data residency:
  - Tenant region must remain in EU.
  - Cross-region identity data replication is disabled unless explicitly approved.
- Break-glass admin:
  - Maintain two emergency accounts with hardware-backed MFA.
  - Credentials stored in secured vault with audited access.
  - Quarterly access validation and sign-off required.

## Cost and Licensing Impact

- Auth0 introduces recurring licensing spend.
- Cost offset is expected through lower operational load versus self-managed auth.
- Production monthly active users and machine-to-machine usage are tracked for plan sizing.

## Migration and Rollout Notes

- New services adopt Auth0 by default.
- Existing services migrate incrementally starting with shared DevOps integrations.
- Migration plans must include rollback and coexistence strategy for legacy identity providers.

## Consequences

- Positive:
  - Unified identity governance across projects.
  - Faster onboarding for new repositories and services.
  - Stronger baseline security controls.
- Trade-offs:
  - Vendor dependency and subscription risk.
  - Requires migration coordination for existing applications.

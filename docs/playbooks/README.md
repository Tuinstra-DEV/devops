# Incident Playbooks

Step-by-step runbooks for handling production deploy incidents.

## Playbook Index

| Playbook | Trigger |
|----------|---------|
| [Failed Deploy](failed-deploy.md) | CD health check fails after deploy |
| [Unhealthy Service](unhealthy-service.md) | Running service becomes unresponsive |
| [Rollback](rollback.md) | Manual rollback procedure |
| [Secrets Leak](secrets-leak.md) | Credentials exposed in logs/repo |
| [Auth Outage](auth-outage.md) | Auth0 service disruption |

## DRI Model

**DRI** = Directly Responsible Individual

| Role | Responsibility |
|------|----------------|
| Platform DRI | Owns devops repo, reusable workflows, server infrastructure |
| Repo DRI | Owns application code, consumer workflow configs, app-specific debugging |

For solo projects, both roles are the same person. For team projects,
assign explicitly.

## Escalation Path

1. **Repo DRI** investigates first (application logs, recent commits)
2. **Platform DRI** escalates if infrastructure/workflow issue suspected
3. **External** (hosting provider, Auth0 support) if third-party outage

## Response Time Targets

| Environment | Acknowledge | Resolve |
|-------------|-------------|---------|
| Production | 15 min | 1 hour |

These are targets, not SLAs. Adjust based on project criticality.

## Communication

During incidents:

1. Update the relevant Shortcut story with status
2. For production issues affecting users, post in the project's communication channel
3. After resolution, add a brief post-mortem comment to the story

## Related Docs

- [Health Check Standard](../standards/health-check.md)
- [Deploy Script](../scripts/deploy-service.md)
- [Branching Strategy](../workflows/branching-strategy.md)

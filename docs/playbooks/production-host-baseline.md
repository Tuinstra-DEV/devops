# Production host baseline

This playbook prepares `prod01` (`vps01.tuinstra.dev`) without migrating a
site, changing firewall policy, configuring DNS, or creating backups. Those
controls remain explicit follow-up work. Port 80 serves only a Caddy `404`;
HTTPS is added with the reviewed domain routes during migration.

## Managed baseline

- `mtuinstra` uses the controller public key and has passwordless sudo.
- `deploy` has a locked password, its own forced-command key, no Docker group,
  and no general shell or sudo capability.
- Docker Engine and Compose use exact apt versions from inventory. Docker uses
  live restore and bounded local logs.
- Caddy uses the pinned multi-architecture image digest, has a loopback-only
  admin endpoint, imports future root-owned route fragments from the initially
  empty `sites` directory, and publishes only the empty HTTP route.
- Application roots use `/var/www/<app>`. Root controls the root, `shared`, and
  future `compose.yml`; deploy may write only `releases`. Secrets belong in
  `/etc/tuinstra/<app>` mode `0700`; durable data belongs in
  `/var/lib/tuinstra/<app>`.

The deploy key accepts only `status <app>` and `deploy <app>`. The root-owned
helper rejects unknown apps, missing or writable compose definitions, and any
image that is not pinned with `@sha256:<digest>`. Deploy cannot choose a compose
path or Docker argument. Do not reference deploy-writable release files from a
privileged Compose definition.

## Run

Use the repository wrapper; it uses an existing Ansible installation and never
installs Python packages globally:

```sh
scripts/production-host-baseline --check bootstrap
scripts/production-host-baseline bootstrap
ssh -o BatchMode=yes -o StrictHostKeyChecking=yes mtuinstra@vps01.tuinstra.dev sudo -n true
scripts/production-host-baseline --check configure
scripts/production-host-baseline configure
scripts/production-host-baseline --as-admin verify
```

On a clean host, `--check bootstrap` runs only the fail-closed host/key/app
preflight because check mode does not create the users and apt files later tasks
need. `bootstrap` creates the accounts and Docker apt source. After bootstrap,
`--check configure` previews managed files while skipping service commands whose
packages check mode does not install. `configure` applies the complete baseline.
Rerun `configure` and require zero unexplained changes.
After a controlled reboot, rerun `verify`; also confirm the Hetzner Console or
rescue path is available before changing SSH access.

SSH hardening is deliberately separate. Run it only through the independently
verified administrator session after both login and sudo checks passed:

```sh
PRODUCTION_ADMIN_SSH_VERIFIED=true \
PRODUCTION_ADMIN_SUDO_VERIFIED=true \
  scripts/production-host-baseline --check harden-ssh

PRODUCTION_ADMIN_SSH_VERIFIED=true \
PRODUCTION_ADMIN_SUDO_VERIFIED=true \
  scripts/production-host-baseline harden-ssh
```

That playbook disables password authentication and direct root login, validates
the complete sshd configuration, then reloads SSH. The baseline playbook never
does this implicitly.

## Deferred controls

Firewall and network filtering are unchanged by DEV-26. Until that work is
accepted, publish no application, database, Docker API, or monitoring port;
only the empty Caddy port 80 listener is intentional. Backups are also deferred.
Before any migration, configure encrypted application-consistent backups to the
approved spare disk under Sanctuary `/mnt/hdd`, define retention, and prove a
restore. Console monitoring enrollment is managed separately and must not mount
the unrestricted Docker socket.

CI integration is pending. A future workflow may use the per-host deploy key,
but it must retain the forced command and install root-owned digest-pinned
Compose files through a separately reviewed control path.

## Local template regression gate

Run the real Ansible renderer as well as static syntax checks. This catches Bash
array expansions being mistaken for Jinja comments and leading whitespace before
a forced-command shebang:

```sh
ANSIBLE_HOME=/tmp/tuinstra-ansible ANSIBLE_REMOTE_TEMP=/tmp/tuinstra-ansible-local \
  ansible-playbook -i localhost, infra/ansible/production-host-template-test.yml
```

The command renders only synthetic configuration into a temporary directory, checks
the shell syntax, and removes the fixtures. It does not contact production hosts.

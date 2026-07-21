# Sanctuary KVM runner platform

## Decision

Sanctuary runs at most one untrusted CI job in an ephemeral Ubuntu 24.04 KVM
guest. The host is provisioned with Ansible, the guest root disk is a qcow2
overlay backed by a checksum-pinned immutable Packer image, and the VM is
deleted after its single JIT job.

The manager includes a small GitHub REST polling adapter because an organization
scale-set listener is not available with repository-scoped administration. It
polls queued/in-progress workflow runs in an explicit `Tuinstra-DEV` repository
allowlist, selects only queued jobs whose labels include `trusted-heavy`, and
requests a repository JIT configuration. `runner_group_id` is configurable and
defaults to the verified repository runner group ID `1`.

The deployed allowlist is Gate, WODIQ, Tracker, Notify, Console, wodiq-site,
marcel-site, and tuinstra-site. This DevOps repository remains hosted-only and
is intentionally excluded to prevent the runner control plane from executing
its own changes.

The adapter deduplicates job IDs for 24 hours. It launches at most one VM and
removes the offline GitHub runner record if VM launch fails. Any malformed API
response, permission error, transport failure, or rate limit fails closed;
rate-limit responses honor a bounded retry interval.

For manual break-glass operation, a single-use JIT value can still be supplied
to the manager using a mode `0600` file. Delete that host-side file immediately
after `launch` returns:

```sh
sudo -u ci-runner-manager /usr/local/bin/ci-runner-manager launch \
  --lease JOB_ID --jit-config-file /run/ci-runner-manager/JOB_ID.jit
```

The manager passes the value to the root helper over stdin; neither layer logs
it or includes it in a host process argument. The guest uses the JIT value for
one job, powers off after the runner exits, and the reconciler removes the
domain, seed ISO, and overlay.

The polling adapter uses only documented GitHub REST endpoints and is not a
replacement for GitHub's scale-set client. Migrate to the official client when
organization administration becomes available.

## Runner routing contract

The runner must register with the exact custom label `trusted-heavy` in the
restricted runner group. Reusable workflows target `[self-hosted,
trusted-heavy]`. Do not add a generic route that lets arbitrary workflows or
fork pull requests select this machine.

## Resource and admission policy

- Maximum concurrency is 1, enforced under a filesystem lock.
- Each guest receives 8 vCPU, 12 GiB RAM, and a 120 GiB grow-on-write disk.
- Admission requires at least 8 host logical CPUs, 14 GiB available memory,
  140 GiB free on `/mnt/ssd1000-01/ci-runner`, and one-minute load no higher
  than 8. Thresholds are configurable; VM dimensions are root-helper constants.
- The base image is root-owned and mode `0444`. Per-job files live in a mode
  `0700` lease directory.
- A running lease older than `max_lease_seconds` (default 7,200 seconds) is
  destroyed by reconciliation even if libvirt still reports it running.

## Network boundary

The `sanctuary-ci` libvirt NAT network uses documentation prefix
`192.0.2.0/24`. The nftables guard permits only DHCP and the libvirt DNS proxy
to the host, blocks every other guest-to-host packet, and denies RFC1918, loopback,
link-local, carrier-grade NAT, ULA, and multicast destinations. Public internet
egress remains available for GitHub and package registries. No inbound port or
route is exposed.

If production uses public IP space, add those CIDRs to the deny set before
enabling the runner. Provisioning requires the explicit
`runner_production_networks_reviewed=true` acknowledgement and accepts separate
IPv4 and IPv6 production deny lists. Sanctuary's required list includes its
public host address `88.159.77.149/32`. Validate the effective ruleset from a guest; cloud-provider
and upstream routing policy remain separate controls.

The runner policy is loaded as its own `inet sanctuary_ci` table by a dedicated
oneshot service. Ansible validates the candidate batch with `nft -c`; applying
the batch atomically replaces only that table. It never enables, flushes, or
restarts the global nftables service, preserving Docker and unrelated host rules.

## Image supply chain

Build `infra/packer/sanctuary-runner.pkr.hcl` only with SHA-256 values copied
from the authoritative Ubuntu and GitHub Actions runner releases. Store the
qcow2 in an access-controlled artifact location, record its digest in change
evidence, and deploy it through the Ansible checksum assertion.

The image contains no registration token or SSH key. Packer's temporary account
is locked, SSH is disabled, machine identity and cloud-init state are removed,
and the runtime JIT configuration arrives only through the per-lease seed.

### Guest toolchain contract

Every image contains the x86-64 toolchain required by migrated heavy workflows:

- Docker Engine and CLI with Buildx and Compose v2 plugins;
- Node.js 24, npm, and Corepack;
- a pinned Playwright package plus its checksum-validated Chromium download and
  signed Ubuntu system dependencies, cached under `/opt/ms-playwright`;
- Trivy;
- PHP 8.3 and 8.4 CLI, curl, mbstring, XML, and ZIP extensions, plus Composer;
- Git, curl, jq, GNU build tools, and the GitHub Actions runner.

All third-party APT repositories use dedicated `Signed-By` keyrings. Packer
requires a reviewed SHA-256 for each downloaded repository key and exact APT
versions for Docker, Node, Trivy, and PHP. The runner archive, Ubuntu ISO, and
Composer installer are independently checksum-pinned. No downloaded content is
piped into a shell. `verify-image-contract.sh` runs before the image is sealed,
and the resulting qcow2 SHA-256 is the deployment input.

Rebuild at least monthly and within the security response SLA for critical
runner, kernel, container, browser, Node, PHP, Composer, or Trivy advisories.
For every rebuild, resolve exact versions from the signed repositories, review
repository-key changes separately, update the Packer variables, build from a
clean host, retain the manifest, run the image contract, and pass a
non-production canary before changing the Ansible image digest. Never update a
package in place on Sanctuary; roll forward with a new immutable image.

The guest unit invokes a fixed wrapper which reads the JIT value, removes its
runtime file, and `exec`s `run.sh --jitconfig` exactly once. The service does not
restart and powers the VM off after the runner exits, including failure paths.

## Audit and retention

Lifecycle events are written to `/mnt/hdd1000-01/ci-audit/manager.log` without
job payloads or credentials. Logrotate retains daily compressed logs for 30
days and removes older files. Restrict the mount to administrators and the
manager account, monitor rotation, and include the mount in capacity alerts.

The GitHub token is provisioned separately at `/etc/ci-runner/github.token` as
root mode `0600`. systemd exposes it to the unprivileged service through
`LoadCredential`; it never appears in TOML, argv, logs, or repository content.
Grant only repository-level Actions read and self-hosted-runner write access for
allowlisted repositories and rotate it through the normal secret-management process.

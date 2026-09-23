# CI runner host operations

## DEV-23 production profile (2026-09-23)

Sanctuary runs one ephemeral `trusted-heavy` VM at a time, with four
vCPUs, 6,144 MiB guest RAM, a 4,096 MiB host reserve and a 60 GiB minimum on
the main NVMe. The manager and root helper both enforce a single domain.
The WoW pin timer is retired; the WoW container uses CPU set `0-15`. The VM
shares host CPUs with WoW and is admitted only when the one-VM slot, projected
host memory reserve and disk gate permit it. This is the permanent profile.
Changing concurrency, guest memory or WoW CPU affinity requires a separately
reviewed capacity change and a new WoW/CI soak.

The initial one-runner change backed up the previous host files in
`/var/backups/dev23-single-runner-20260923T164122Z`. Before any further host
change, drain CI and confirm there are no `sanctuary-ci-*` domains or overlays.
The Ansible role now enforces the same permanent policy and disables the retired
pin timer. Keep the backup and rollout record for reversal and audit.

## Provision

1. Confirm Ubuntu 24.04, `/dev/kvm`, and persistent SSD/HDD mounts.
2. Build the Packer image with pinned Ubuntu ISO and runner checksums.
3. Publish the image to an access-controlled artifact source.
4. Provision the repository runner token directly on the host as root-owned
   mode `0600` `/etc/ci-runner/github.token`; do not place it in inventory.
5. Set `runner_base_image_source`, `runner_base_image_sha256`, production IPv4
   and IPv6 deny lists, and `runner_production_networks_reviewed=true` in
   protected Ansible inventory, then run `ansible-playbook infra/ansible/site.yml`.
6. Confirm the allowlisted repositories, `trusted-heavy` label, group ID 1,
   concurrency 1, 4 vCPU / 6,144 MiB guest dimensions, 4,096 MiB host memory
   reserve, and 120-minute lease limit in `/etc/ci-runner/manager.toml`.

Image activation is fail-closed: Ansible stops new admission, refuses to switch
the digest symlink while any `sanctuary-ci-*` domain or overlay entry exists,
and retains prior digest-versioned images. Never bypass this drain assertion.
The role disables and removes the retired WoW CPU pin timer and service.
Verify the live WoW container remains on `0-15` after container recreation.
Drain CI before planned WoW maintenance so guest and game load do not overlap
during startup.

Copy `infra/packer/sanctuary-runner.pkrvars.hcl.example` outside source control,
replace every placeholder with an exact reviewed version or checksum, then run
`packer build -var-file=/protected/path/sanctuary-runner.pkrvars.hcl
sanctuary-runner.pkr.hcl` from `infra/packer`. The build fails unless Docker,
Buildx, Compose, Node 24/Corepack, Playwright Chromium, Trivy, PHP 8.3/8.4,
Composer, and the base CLI/build tools satisfy the image contract.
Install Packer and the QEMU plugin only from the artifacts and SHA-256 values in
`infra/packer/toolchain.lock`; retain the verified archives with the image build
evidence.

Validate the role locally before applying it, then execute check mode against
the `sanctuary` inventory target. Check mode gathers host facts but does not
change the server:

```sh
cd infra/ansible
ansible-playbook --syntax-check site.yml
ansible-playbook --check --diff --limit sanctuary site.yml \
  -e runner_base_image_source=https://ARTIFACT/runner.qcow2 \
  -e runner_base_image_sha256=REPLACE_WITH_64_HEX_DIGEST \
  -e runner_production_networks_reviewed=true
```

## Acceptance checks

```sh
sudo kvm-ok
sudo virsh net-info sanctuary-ci
sudo nft list table inet sanctuary_ci
sudo systemctl status ci-runner-manager ci-runner-host-helper.socket libvirtd sanctuary-ci-firewall
systemctl is-enabled ci-wow-cpu-pin.timer  # disabled or not-found
docker inspect --format '{{.HostConfig.CpusetCpus}}' tuinstra-realm-world
sudo systemctl show ci-runner-manager -p NoNewPrivileges
sudo stat -c '%U:%G %a %n' /run/ci-runner-host-helper.sock
sudo journalctl -u ci-runner-manager -u 'ci-runner-host-helper@*' --since '-5 minutes'
```

The manager must report `NoNewPrivileges=yes`; `/etc/sudoers.d/ci-runner-manager`
must not exist. The helper socket must be owned by `ci-runner-manager`, mode
`0600`, and the broker must reject every other peer UID. Do not weaken this
boundary to a group-writable socket or a wildcard sudo rule.
The WoW CPU set must remain `0-15`; the runner VM must have no `vcpu cpuset`.
If WoW health regresses during CI, stop new runner admission, let the current
job finish or cancel it for containment, and compare WoW update times with
the 2,000-bot baseline. Check WoW ports 8085/3724 before reopening admission.
For a runner rollback, restore the reviewed manager/helper/configuration backup
after draining and repeat a single runner canary.

### GitHub JIT redirect canary

Deploy runner-manager transport changes only after the host has no active lease,
`sanctuary-ci-*` domain, overlay, or seed. Run the Ansible role from the exact
reviewed commit, then queue one `trusted-heavy` canary. A valid GitHub JIT 307 or
308 may be followed once only when it remains on the configured HTTPS origin and
resolves to the same repository endpoint or GitHub's numeric canonical
repository endpoint. The manager rejects every other redirect without replaying
the POST.

Record the commit, deployment result, canary job and timestamps. Evidence must
show exactly one `sanctuary-*` runner and VM, successful job assignment, and
removal of the GitHub runner record, lease, overlay and seed. The only redirect
log allowed is the status plus the fixed `target=same-origin` decision; never
record `Location`, `Authorization`, the request body or `encoded_jit_config`.

For DEV-42, the production gate is both PHP-consuming lanes of Tracker PR #256.
They must retain their required check names, `trusted-heavy` routing and
read-only package permissions. Do not accept a local mock as production
evidence.

Roll back when redirect or API errors repeat, no VM appears within two manager
poll cycles, more than one runner record is created, or cleanup leaves any
runner, lease, overlay or seed behind. Drain admission, create a normal revert
commit for the runner-manager change, apply the same Ansible role from that
reviewed revert, and re-run the host health checks. Do not amend, move a tag,
force-push, restore an unreviewed binary, or delete state to hide an orphan.

Launch one non-production canary and verify: exact `trusted-heavy` routing;
4 vCPU, 6,144 MiB RAM and 120 GiB disk per guest; rejection of a second launch;
public GitHub reachability while host, private and production ranges are
blocked; one-job poweroff; complete lease reconciliation within 30
seconds; and audit events without the JIT payload. Repeat with projected free
memory below the configured reserve and confirm a launch is rejected.

Repeat a failure canary with the manager stopped. Confirm the guest and host
timer power the VM off at the configured upper bound, then restart the manager
and confirm its normal reconciliation removes the local lease, overlay, seed
and GitHub runner record.
Simulate one failed GitHub deletion and verify a private cleanup tombstone is
retried without blocking the next VM admission.

The Sanctuary production deny list must contain `88.159.77.149/32` plus every
other public production CIDR. The dedicated firewall service must leave Docker
and all non-`sanctuary_ci` nftables tables unchanged.

## Incidents and orphan cleanup

Disable the runner group first. Preserve the audit log and job identifiers, but
never copy a seed ISO or JIT value into a ticket. Stop the listener, allow the
current job to finish unless containment requires termination, restart the
manager to run reconciliation through its systemd credential and socket
boundaries, and confirm that no `sanctuary-ci-*` domain or lease directory
remains.

## Hosted rollback

1. Disable repository access to the trusted-heavy runner group.
2. Route jobs back to GitHub-hosted labels through the normal reviewed workflow
   change; do not broaden which untrusted jobs can execute.
3. Allow current jobs to finish, or cancel them for containment.
4. Stop the listener and `ci-runner-manager.service`.
5. Run one reconciliation and verify no domain, overlay, seed, or state remains.
6. Retain audit logs for 30 days and record the rollback evidence.

Keep the checksum-pinned base image during rollback so restoration is
reversible. Re-enable only after a canary passes the acceptance checks.
To roll back an image, drain to zero domains and overlays, atomically repoint
`ubuntu-24.04-runner.qcow2` to the digest recorded by
`ubuntu-24.04-runner.previous.qcow2`, restart the manager, and pass the same
non-production canary. Verify backing chains before deleting any retained image.

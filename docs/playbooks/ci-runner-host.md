# CI runner host operations

## Provision

1. Confirm Ubuntu 24.04, `/dev/kvm`, and persistent SSD/HDD mounts.
2. Build the Packer image with pinned Ubuntu ISO and runner checksums.
3. Publish the image to an access-controlled artifact source.
4. Provision the repository runner token directly on the host as root-owned
   mode `0600` `/etc/ci-runner/github.token`; do not place it in inventory.
5. Set `runner_base_image_source`, `runner_base_image_sha256`, production IPv4
   and IPv6 deny lists, and `runner_production_networks_reviewed=true` in
   protected Ansible inventory, then run `ansible-playbook infra/ansible/site.yml`.
6. Confirm the allowlisted repositories, `trusted-heavy` label, group ID 1, and
   120-minute lease limit in `/etc/ci-runner/manager.toml`.

Image activation is fail-closed: Ansible stops new admission, refuses to switch
the digest symlink while any `sanctuary-ci-*` domain or overlay entry exists,
and retains prior digest-versioned images. Never bypass this drain assertion.

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
sudo systemctl show ci-runner-manager -p NoNewPrivileges
sudo stat -c '%U:%G %a %n' /run/ci-runner-host-helper.sock
sudo journalctl -u ci-runner-manager -u 'ci-runner-host-helper@*' --since '-5 minutes'
```

The manager must report `NoNewPrivileges=yes`; `/etc/sudoers.d/ci-runner-manager`
must not exist. The helper socket must be owned by `ci-runner-manager`, mode
`0600`, and the broker must reject every other peer UID. Do not weaken this
boundary to a group-writable socket or a wildcard sudo rule.

Launch a non-production canary and verify: exact `trusted-heavy` routing; 8
vCPU, 12 GiB RAM and 120 GiB disk; rejection of a second launch; public GitHub
reachability while host, private and production ranges are blocked; one-job
poweroff; complete reconciliation within 30 seconds; and audit events without
the JIT payload.

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
3. Allow the current single job to finish, or cancel it for containment.
4. Stop the listener and `ci-runner-manager.service`.
5. Run one reconciliation and verify no domain, overlay, seed, or state remains.
6. Retain audit logs for 30 days and record the rollback evidence.

Keep the checksum-pinned base image during rollback so restoration is
reversible. Re-enable only after a canary passes the acceptance checks.
To roll back an image, drain to zero domains and overlays, atomically repoint
`ubuntu-24.04-runner.qcow2` to the digest recorded by
`ubuntu-24.04-runner.previous.qcow2`, restart the manager, and pass the same
non-production canary. Verify backing chains before deleting any retained image.

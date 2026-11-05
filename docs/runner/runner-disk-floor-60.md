# Sanctuary: 60 GiB free before launch

Marcel selected a **prelaunch** threshold of 60 GiB on 5 November 2026.
For the four-pool bounded-storage mode, `min_free_disk_gib = 60` now means
60 GiB available immediately before allocating a new guest's backing file.
Both manager and root helper enforce this threshold. The previous calculation
required 86 GiB for heavy (`60 + 24 + 2`) and 66 GiB for medium (`60 + 4 + 2`).
Legacy single-runner admission is unchanged.

| Pool | Free before launch | Fully allocated backing | Nominal remainder | Post-allocation guard |
| --- | ---: | ---: | ---: | ---: |
| Heavy | 60 GiB | 24 GiB | approximately 36 GiB | at least 34 GiB |
| Medium | 60 GiB | 4 GiB | approximately 56 GiB | at least 54 GiB |

The 2 GiB allowance covers filesystem metadata and concurrent host writes
**within** the 60 GiB budget. It is no longer added to the launch threshold.
If more headroom disappears during `fallocate`, the helper refuses to create
the VM. Existing allocations are already reflected in measured free space;
they are not subtracted a second time. At 36 or 56 GiB remaining, another
runner cannot start. All CPU, memory and pool-count limits remain in force.

## Storage safety and limits

This policy requires the existing bounded-loop storage mode. The helper fully
allocates each 24/4 GiB backing file, verifies its allocated blocks, creates
ext4 with `nodiscard`, disables loop discard, verifies the loop's kernel
file identity, and checks the mounted filesystem. Guest qcow2 and seed files
live inside that fixed filesystem. A guest's 120 GiB virtual disk cannot grow
its host backing beyond the already allocated 24/4 GiB. A full guest backing
can fail its CI job; it cannot claim another 120 GiB on `/`.

The previous safeguards around discard, fully allocated blocks, loop identity,
mount identity and cleanup remain unchanged. Cleanup still refuses uncertain
mounts/devices and never uses a reused numeric loop ID or path text alone as
proof. These controls matter after the earlier root-full/loop-recovery incident;
a lower threshold without these hard storage bounds would be inappropriate.
The earlier 12 GiB heavy backing is not restored, because heavy jobs needed
more capacity.

This is an explicit reduction of host headroom from the previous 62 GiB
post-allocation floor. The hard guard now retains 34 GiB for heavy. Logs,
operator image builds and unrelated services can still consume that space;
this change does not impose quotas on them or claim they cannot fill `/`.
Image rebuilds must retain their own checked reserve. Operator-selected
prelaunch availability is distinguished from a guarantee for all host writers.

## Deploy after the runner version incident is verified

Do not apply until runner 2.338.2 is active, a real job has been picked up and
its setup log confirms the version. Record that job and the active image digest.
The playbook requires the operator to explicitly attest this prerequisite.

The [code-only repair playbook](../../infra/ansible/runner-disk-floor-repair.yml)
follows `runner-manager-repair.yml`: checksum-pinned controller files,
checksum-pinned installed versions, verified private backups, normal copy to
existing destinations, and service readback after one poll. Both manager and
helper must change together; a manager-only update would still hit the helper's
old 86 GiB guard. No credentials, guest image, config contents, routing, group,
service unit or access permissions are changed.

The playbook refuses active CI domains before pausing admission and rechecks
domains, overlays, leases, cleanup obligations and helper instances afterward.
Wait for natural completion; do not cancel jobs to force this change through.
It installs nothing if that initial drain precondition fails.

From this PR's reviewed checkout, Marcel runs:

```sh
/private/tmp/runner-reuse-venv/bin/ansible-playbook \
  -i 'sanctuary.tuinstra.dev,' -u mtuinstra --ask-become-pass \
  infra/ansible/runner-disk-floor-repair.yml \
  -e runner_2338_smoke_verified=true
```

The before/after hashes are embedded in the playbook. A different installed
version is a blocker requiring review, not an excuse to overwrite it.
Afterward observe a naturally queued heavy and medium job. Confirm their
runner IDs, assignment, normal completion and cleanup. Unit boundary tests
prove admission at exactly 60 GiB without intentionally filling production `/`.
An active service alone is not job-delivery evidence.

Rollback uses both original files in
`/var/backups/ci-runner-disk-floor-60/`, with the same drain and checksum checks:

```sh
/private/tmp/runner-reuse-venv/bin/ansible-playbook \
  -i 'sanctuary.tuinstra.dev,' -u mtuinstra --ask-become-pass \
  infra/ansible/runner-disk-floor-repair.yml -e runner_disk_action=rollback
```

If installation or final drain validation fails after services stop, admission
stays paused. Inspect the failure and run the rollback playbook to restore both
pinned originals. If no code was installed and an existing job merely raced the
initial check, restore the existing services without destroying the job:

```sh
ssh -t mtuinstra@sanctuary.tuinstra.dev \
  'sudo systemctl start ci-runner-host-helper.socket ci-runner-manager.service'
```

## Verification

Regression tests reproduce the old 60 GiB rejection, then cover exactly 60 GiB,
one byte below/above, both pools, no duplicate subtraction for active guests,
pre-JIT waiting/recovery, and a post-allocation host-writer race. Existing
partial-allocation, no-discard, loop identity, cleanup and unrelated permission
checks are retained. Local `make lint`, `make test` and the new playbook syntax
check must pass before push. The three mutable activation tools receive the
new source pins; the historical DEV-54 activation entrypoint remains pinned to
its reviewed version and cannot overwrite this installation.

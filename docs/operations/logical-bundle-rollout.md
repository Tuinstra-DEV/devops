# DEV-50 logical bundle admission transition

The shared reusable workflow remains at `.github/workflows/reusable-trusted-verification.yml`. Before any consumer uses its new immutable commit SHA, the Sanctuary organization runner group and manager configuration must admit that exact ref. `scripts/transition-org-runner-workflows.py` performs this narrow version transition; it does not provision a runner group, token, image, label, repository, or service.

## Prepared inputs

- Use the existing host and named operator login: `mtuinstra@sanctuary.tuinstra.dev`. Keep host-key verification enabled.
- Stage **these three files** in one root-owned, non-group-writable directory: `transition-org-runner-workflows.py`, `activate-org-runner-routing.py`, and `ci_runner_manager.py`. The second file supplies pinned, reviewed file/lock/backup primitives; the transition helper checks its SHA-256 before executing any of its bytes. Do not replace these with symlinks.
- The reviewed installed manager SHA-256 is `1d141705f975efafcdc85c8941f3879c395b4988bc1373dd4750b995935968e7`; the reviewed candidate is `2ace71bf8872ce2090ff5e15668f08f906e84519f920240eafbe23d9590017b4`. The pinned activation module is `37cc8c9df5badbbb19da22a70c7c46b799131987e81fa228369e5ce1be94ff6a`. Re-review and update the helper/tests if the final candidate bytes change.
- Take the new 40-character lowercase commit SHA from the reviewed shared workflow commit. Check the existing `org_runner_group_id` in `/etc/ci-runner/manager.toml`; pass that exact ID. The helper accepts only the two reviewed old workflow SHA values, preserves their order, and adds the new ref at the unchanged workflow path. It refuses a duplicate, unknown, branch/tag, changed repository ID, changed runner-group setting, or a widened admission policy.

## Read-only preflight and apply

Run the staged helper as root through the named operator account. With neither action flag, it performs the default read-only preflight. Replace the angle-bracket values with the reviewed SHA and existing group ID:

```sh
sudo python3 /root/ci-runner-org-routing-stage/transition-org-runner-workflows.py \
  --stage /root/ci-runner-org-routing-stage \
  --workflow-sha <reviewed-40-character-sha> --group-id <existing-group-id>
```

The result is a small JSON status such as `preflight_passed` or `already_applied`; it does not print credentials or raw API responses. Preflight checks the root-owned staged and installed files, hashes, metadata, effective systemd unit and both existing credential bindings, exact selected private repositories, and the organization's complete runner-group policy. Fix any mismatch before applying. A failed preflight leaves admission unchanged.

Only after the final shared workflow SHA and staged bytes are reviewed, run the same command with `--apply`. The helper takes an exclusive transition lock, switches the manager temporarily to reconcile-only mode using its own runtime systemd drop-in, and lets current guests finish. It never cancels a guest. Before each reconcile pass it refuses unreadable or malformed lease state and a lease within 240 seconds of the existing 7200-second expiry, avoiding a transition-driven TTL cleanup of a live guest. It waits at most `--max-drain-seconds` (default 3600; accepted range 60–7200). It requires zero runner VMs, leases, overlay files, cleanup obligations, and active host helpers, then rechecks while holding the host-helper lock. A timeout or near-expiry refusal removes its drop-in and restores the original daemon admission.

At the idle boundary the helper stores a protected backup and manifest under `/var/backups/ci-runner-workflow-transition`, checks the runner group again, PATCHes **only** `selected_workflows`, atomically replaces the manager/config, verifies readback and policy, removes its drain drop-in, and restarts the manager and host-helper socket. It reports `applied` with a backup ID. Repeating the same apply request reports `already_applied` without another drain or write. If the PATCH, file write, validation, or restart fails, it attempts to restore the original group and files before reopening admission. If restoration cannot be verified, it reports a failure with admission stopped for operator investigation; do not claim the rollout succeeded.

## Explicit rollback

After jobs admitted by the new version finish, use the exact backup ID returned by apply:

```sh
sudo python3 /root/ci-runner-org-routing-stage/transition-org-runner-workflows.py \
  --rollback <backup-id> --max-drain-seconds 3600
```

Rollback verifies the backup, installed candidate hashes and metadata, current exact refs, effective unit, and GitHub runner group before draining. It refuses an unexpected installation or group change. Drain timeout restores the currently installed admission. A partial rollback failure leaves admission stopped and reports `rollback incomplete`; investigate both the protected manifest and the group state before any manual recovery. Never edit the token into a command or log.

Keep consumer workflow pins on the old immutable commit until the shared workflow is merged and this admission transition is verified. Then update each consumer to the reviewed new commit SHA and run its relevant local checks before pushing. This transition alone does not deploy a consumer or change branch protections.

# DEV-50 manager assignment recovery

The shared reusable workflow is already admitted at three immutable refs under `.github/workflows/reusable-trusted-verification.yml`. GitHub may assign an organization runner prepared for one admitted ref to a job from another admitted ref. The installed manager rejects that assignment because it expects the trigger and actual refs to match. This recovery updates only the manager: it verifies both refs independently against the same exact three-ref allowlist and records the actual assignment ref with the lease. The runner-group selection, repository IDs, manager config, credentials, labels, capacity, and consumer pins remain unchanged.

## Reviewed inputs

- Connect as `mtuinstra@sanctuary.tuinstra.dev` with normal SSH host-key verification. Use the named account's sudo only for the prepared helper.
- Stage `transition-org-runner-workflows.py`, `activate-org-runner-routing.py`, and `ci_runner_manager.py` together in `/root/ci-runner-org-routing-stage`. The directory and files must be root-owned, regular files with safe modes and matching reviewed hashes; the helper validates the activation module before executing its bytes.
- The expected installed manager SHA-256 is `2ace71bf8872ce2090ff5e15668f08f906e84519f920240eafbe23d9590017b4`. The candidate manager is `79ed7031ff97b3ae371bb87d0c30eba62b32eebf8c06224fea68fde857e2d32c`. The staged activation module is `2c0cd1da090ab70c5a1a41a18e303a0c4fd30153a93f35a9d4342824faa8be74`.
- The existing runner group is ID `4`, named `sanctuary-trusted-verification`. The three admitted commit SHAs are `36f32aebe315274df8825674b7bd0ced637ab056`, `e1a9f09504b903120e9b5297eff64f58a891c907`, and `23c08cf212dd6411ed5076b2ec53a05c1e2e5f3e`. Their workflow path, exact membership, private repository selection, and group policy must match the installed config and GitHub API. List order in the API does not affect admission.

## Preflight and apply

Run the staged helper without an action flag first. This is read-only and returns a small JSON status; it never prints credentials or raw API responses.

```sh
sudo python3 /root/ci-runner-org-routing-stage/transition-org-runner-workflows.py \
  --stage /root/ci-runner-org-routing-stage \
  --workflow-sha 23c08cf212dd6411ed5076b2ec53a05c1e2e5f3e --group-id 4
```

Preflight requires the exact installed manager version, three admitted refs, bounded manager policy, root-owned source and config metadata, effective systemd unit with both existing credentials, selected private repository IDs, and full runner-group policy. A mismatch changes nothing. After preflight passes, repeat the same command with `--apply`.

Apply takes the exclusive transition lock and temporarily runs the manager in reconcile-only mode through its own runtime systemd drop-in. A nonzero reconcile exit while a guest is active does not force guest termination: the helper waits for natural completion, still checks the lease before every pass against the existing 7200-second TTL, and requires no VMs, leases, overlay files, cleanup obligations, or active host helpers before proceeding. It rechecks idle state under the host-helper lock. The drain window defaults to 3600 seconds and is bounded to 60–7200 with `--max-drain-seconds`. Timeout or a near-expiry, malformed, or unreadable lease removes the drop-in and restores the original admission.

At the idle boundary, the helper saves a protected backup and manifest under `/var/backups/ci-runner-workflow-transition`, verifies the runner group again, atomically replaces and reads back **only** the manager binary, removes its drain drop-in, and restarts the manager and host-helper socket. It does not PATCH GitHub or rewrite the config. Success returns `applied` and a backup ID; a repeat returns `already_applied` without draining. A failed write, validation, or restart attempts to restore the original manager. If recovery cannot be verified, the helper reports failure with admission stopped rather than claiming success.

## Explicit rollback

After active jobs finish, use the exact backup ID returned by apply:

```sh
sudo python3 /root/ci-runner-org-routing-stage/transition-org-runner-workflows.py \
  --rollback <backup-id> --max-drain-seconds 3600
```

Rollback verifies the protected backup, installed candidate hash and metadata, exact unchanged config and group refs, and effective service before the same natural drain. It refuses a changed installation. A drain timeout restores current admission; an incomplete rollback leaves admission stopped for operator investigation. A successful rollback restores manager `2ace71bf8872ce2090ff5e15668f08f906e84519f920240eafbe23d9590017b4`, including its known cross-pin assignment limitation, while leaving the three-ref policy intact.

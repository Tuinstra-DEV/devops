# Sanctuary manager repair — 4 November 2026

The installed manager SHA-256 is
`232d37364c0754bd59aefbf44fee9ba5704def038bbda94514975f857dd61e9f`,
exactly the source at `de878021d71315387160fa1586cb46c955ccafe5`.
The host helper matches main (`fef9343a46c4a67ddf1e12db43aa65c2bbb5256e35bbdac14c9cb8a9ad3f1613`).
Ansible's `runner_host` role copies the manager from the controller checkout;
it does not install a separately versioned package. The service start date
alone does not identify its code version.

## Evidence and limits

GitHub reported the following on 4 November (UTC):

| Job | Runner ID | Runner name | Started | Completed |
| --- | --- | --- | --- | --- |
| [126699816446](https://github.com/Tuinstra-DEV/tuinstra-site/actions/runs/41865854890/job/126699816446) | 942 | sanctuary-126699816446 | 16:20:58 | 16:21:40 |
| [126726254409](https://github.com/Tuinstra-DEV/tuinstra-site/actions/runs/41873366966/job/126726254409) | 943 | sanctuary-126699816446 | 17:01:30 | 17:02:15 |

These are different registrations with a reused trigger-derived name. This
does **not** establish that one runner or guest executed two jobs. Current
assignment lookup already uses runner ID, rather than name alone.

[Docker job 126726160262](https://github.com/Tuinstra-DEV/tuinstra-site/actions/runs/41873366935/job/126726160262)
was queued with `runner_id: 0`, no runner name and legacy
`self-hosted,trusted-heavy` labels. Its workflow pin was
`reusable-ci-docker.yml@a8815205609fdc709a521914bd927ba72d8d7ad5`.
The legacy candidate route accepts these labels. Its historical delay cannot
be attributed to a specific manager exception without the protected audit or
journal. Organization runner inspection returned HTTP 403 with the current
operator credential; no extra scope was requested.

The existing `generate-jitconfig` registration route is already single-use;
the guest consumes the credential once, runs `run.sh --jitconfig`, has
`Restart=no`, and powers off when the runner exits. See
[GitHub's runner source](https://github.com/actions/runner/blob/main/src/Runner.Listener/Runner.cs)
and [JIT API](https://docs.github.com/en/rest/actions/self-hosted-runners#create-configuration-for-a-just-in-time-runner-for-an-organization).
`--ephemeral` is a registration option for the separate config-token route,
not an additional JIT request field.

The repair closes three reproduced lifecycle gaps:

- Trusted discovery can become stale before JIT registration. Recheck the
  current job's identity, queued/unassigned state and labels, then freshly apply
  existing source/workflow admission before POST. A definitive preflight
  rejection releases only that dispatch claim and lets later candidates run.
- A running guest was retained even after its actual assigned job completed.
  Discover fast completed assignments too; confirm job ID, run ID, runner ID
  and runner name before teardown. API errors or conflicting identity preserve
  the VM until normal local shutdown/TTL. Remote deletion failures remain durable.
- A verified cross-job handoff only accepted `runner_id: null`. Accept zero too,
  restoring the existing 30-second retry after verified local teardown instead
  of escalating to the conservative backoff (up to 60 minutes).

These are verified defects, not a claim that all historical waits have been
explained. No existing workflow was canceled/rerun and no host cleanup was run.

## Prepare the exact deployed-version repair locally

Run in the authorized worktree. This applies only the lifecycle diff to the
installed revision, keeping its existing admission implementation intact.
It deliberately excludes the other manager changes merged after `de87802`.

```sh
cd /Users/marceltuinstra/Projects/tuinstra-dev/devops/work/devops-runner-reuse
git show de878021d71315387160fa1586cb46c955ccafe5:runner/manager/ci_runner_manager.py > /private/tmp/ci-runner-manager-sanctuary-fixed.py
git diff 4ef239e82428ce556752de48d78ddd13cf820fa0 eb2e559af6e656a94a04706150d7c547a3c0b0f2 -- runner/manager/ci_runner_manager.py > /private/tmp/runner-single-use.patch
patch --fuzz=0 /private/tmp/ci-runner-manager-sanctuary-fixed.py /private/tmp/runner-single-use.patch
shasum -a 256 /private/tmp/ci-runner-manager-sanctuary-fixed.py
```

Expected result:
`54520f176dc5c07adf9493d27f89e549672c27aa6d7fa05f737b6549f0dcb1f3`.
The new regression suite was run against both main's repaired manager and this
deployed-version artifact. Three existing operational script source hashes are
updated for main; the immutable DEV-54 activation entrypoint keeps its original
source pin and must not be used to overwrite this repair. Its original installed
hash precondition will correctly reject the patched host.

## Deploy — Marcel, with sudo

Use Ansible from the normal operator environment, or the temporary verified
environment at `/private/tmp/runner-reuse-venv/bin/ansible-playbook` on this Mac.
The playbook validates both source hashes, saves and verifies the original
manager, copies only the manager through Ansible's existing copy/install route,
restarts `ci-runner-manager`, and checks the hash and service after one poll.
It does not change config, credentials, firewall, service units, guest image or
workflow pins. It issues no VM stop or destroy commands.

```sh
/private/tmp/runner-reuse-venv/bin/ansible-playbook \
  -i 'sanctuary.tuinstra.dev,' -u mtuinstra --ask-become-pass \
  infra/ansible/runner-manager-repair.yml

ssh -t mtuinstra@sanctuary.tuinstra.dev \
  'sudo journalctl --utc -u ci-runner-manager --since "5 minutes ago" --no-pager | grep -E "runner registered|assignment verified|handoff|cleaning completed|poll or reconciliation failed|Traceback|Error"'

gh api repos/Tuinstra-DEV/tuinstra-site/actions/jobs/126726160262 \
  --jq '{id,status,conclusion,runner_id,runner_name,started_at,completed_at}'
```

An active service is only a service check: confirm actual assignment/pickup
using the journal and job API. If the old run is no longer queued, observe the
next naturally queued eligible job; do not rerun/cancel it to manufacture proof.
The updated manager's normal reconciliation will tear down a guest only after
verified actual completion, local shutdown, existing stale-registration rules
or hard TTL. An already-running job remains protected.

Rollback, if required (on Sanctuary after `ssh -t mtuinstra@sanctuary.tuinstra.dev`):

```sh
printf '%s\n' '232d37364c0754bd59aefbf44fee9ba5704def038bbda94514975f857dd61e9f  /var/backups/ci-runner-manager-232d37364c0754bd.py' | sudo sha256sum --check
# Continue only if the preceding check reports OK.
sudo install -o root -g root -m 0755 /var/backups/ci-runner-manager-232d37364c0754bd.py /usr/local/bin/ci-runner-manager
sudo systemctl restart ci-runner-manager
sudo systemctl is-active ci-runner-manager
```

Optional historical diagnostic (server clock is UTC; no token/config output):

```sh
sudo journalctl --utc -u ci-runner-manager --since '2026-11-04 16:10 UTC' --until '2026-11-04 17:40 UTC' --no-pager | grep -E '126699816446|126726254409|126726160262|poll or reconciliation failed|Traceback|Error|rate limited|preflight|capacity'
```

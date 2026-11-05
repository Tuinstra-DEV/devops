# Sanctuary runner 2.338.2 repair — 5 November 2026

GitHub check [127122546774](https://github.com/Tuinstra-DEV/wodiq-platform/actions/runs/41990390391/job/127122546774)
reports the 5 November cutoff for runner 2.336.0. The active Sanctuary image
is `4edecc084221f8b753d782e731f84e60cbee8753b64277ec0901c30480aa1317`.
The [official 2.338.2 release](https://github.com/actions/runner/releases/tag/v2.338.2)
pins the Linux x64 archive to
`baebf204307bac6193480852732f0aba2ae97b6c5597c244f4d5558390147c40`.
An independent download matched that checksum on 5 November.

The Packer defaults, example variables and `toolchain.lock` now record this
runner version and checksum. External variable files override Packer defaults:
remove the obsolete runner entries or explicitly pass the reviewed version and
checksum when using an older protected variable file.

## Operator procedure

The reviewed [repair script](../../scripts/repair-runner-image-2338.py) builds a
standalone derivative of the exact current image. It replaces only the runner
archive and its manifest version, without networking inside the guest or APT
changes. Package snapshots and the guest unit/wrapper checksums must remain
identical. It checks `Runner.Listener --version`, root ownership, absent JIT
state, `RUNNER_DISABLEUPDATE=1` and the complete existing image contract.

The helper resolves the fixed image symlink and requires a digest-named target;
it does not pin the old digest in manager code. Ansible's
`runner_base_image_sha256` is a deployment input, not a runtime policy field.
No manager, helper, configuration, runner group, credential or access change is
part of this repair. Existing credential bindings and file modes are checked
before activation. Credential contents are never printed or written to artifacts.

The script requires root on Sanctuary: unprivileged libguestfs/supermin failed.
Build starts only with 70 GiB free and preserves at least 60 GiB after building.
Retain the old image and the private build directory; do not remove worktrees,
images or failed artifacts as part of this incident.

From the reviewed local checkout:

```sh
scp scripts/repair-runner-image-2338.py \
  mtuinstra@sanctuary.tuinstra.dev:/home/mtuinstra/runner-2338-repair.py
ssh -t mtuinstra@sanctuary.tuinstra.dev
```

Marcel runs on Sanctuary, after reviewing the script:

```sh
sudo install -o root -g root -m 0600 /home/mtuinstra/runner-2338-repair.py /root/runner-2338-repair.py
printf '%s\n' '8ab894681f9afcc3eb739b12411bd6d1b4f97a67f6f92cafefcd613aa3d50333  /root/runner-2338-repair.py' | sudo sha256sum --check --strict
# Continue only after the checksum reports OK.
sudo python3 -I /root/runner-2338-repair.py build
# Continue only after build=verified and review its image_sha256.
sudo python3 -I /root/runner-2338-repair.py activate
```

Build diagnostics and the image receipt remain root-private at
`/var/lib/ci-runner/runner-2338-repair/`. Activation uses the existing
reconcile-only drain pattern, refuses remaining domains, overlays, leases or
cleanup obligations, takes the helper lock and atomically changes the fixed
symlink. `ubuntu-24.04-runner.previous.qcow2` retains the exact old target.

The smoke check waits up to 600 seconds for natural manager dispatch. It links
the live VM backing image to an assigned runner ID, using either online/busy
organization runner status or an exact job assignment. It accepts the manager's
verified `actual_job_id` handoff when the assigned run differs from the trigger.
The binary version is verified offline in that exact image; live API
assignment is evidence of online/pickup, not a separate live version reading.
Confirm the actual version from the completed job's setup log afterward.
No workflow is canceled or rerun. Smoke failure invokes rollback automatically.
A healthy active job is never canceled to force rollback.

Manual rollback, using the retained receipt and old image:

```sh
sudo python3 -I /root/runner-2338-repair.py rollback
```

An error during drain or rotation leaves admission paused and prints the current
image target and recovery command. Inspect that target and the failure before
restoring admission. A drain timeout does not authorize terminating any VM:

```sh
sudo rm -f /run/systemd/system/ci-runner-manager.service.d/runner-2338-drain.conf
sudo systemctl daemon-reload
sudo systemctl start ci-runner-manager.service
```

## Verification and follow-up

Local verification passed: `make lint`, `make test`, nine focused repair tests,
Python compilation, guest shell syntax and ShellCheck. For the existing image
contract, ShellCheck's SC2016 is excluded because its single-quoted PHP snippets
must preserve PHP variables. Local tests used the existing Python 3.14/Ansible
virtual environment and a writable temporary Ansible directory. DevOps PR
checks run on GitHub-hosted runners, independently of this incident.

Deployment, the new image digest and actual job pickup are pending operator
execution. Record actual outputs before claiming the incident resolved.

Two separate follow-ups remain outside this repair:

- Reconcile protected Packer inputs with the active image. The retained host
  inputs still describe 2.336.0 and older PHP and omit later toolchain additions;
  rebuilding from them would undo changes beyond this incident's scope.
- Evaluate Marcel's requested effective 60 GiB admission threshold separately.
  The live manager source projects `min_free_disk_gib + 2 GiB + pool reservation`
  before launch. With a 60 GiB reserve and 24 GiB heavy reservation, this implies
  86 GiB free before a heavy launch. Confirm protected live TOML values first;
  a config-only change may violate the current 60 GiB minimum validation.

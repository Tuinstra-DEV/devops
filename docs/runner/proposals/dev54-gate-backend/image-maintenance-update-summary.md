# DEV-54 image maintenance update

## Candidate

The active image `66eded13…` was not changed. Its SHA-256 remains
`66eded13ed619c122421566de9b0a55ed49de5eb895caa40a2bf3b77d1b82bc4`, with
uid/gid `64055:994`, mode `0444`, size `8,098,807,808` bytes, and mtime
`1793868991`. The earlier PHP 8.3.36 standalone candidate also remains
unchanged at SHA-256
`5583aa0da997b0fd9001211b63b3abd24d51778645cbbccdc007444236bc7ec2`.

The final candidate is
`/home/mtuinstra/dev54-image-candidate-20261118-guard/candidate-php83-8.3.36-guard-standalone-operator-owned.qcow2`:

- SHA-256: `2faaf1bed35a253fd134bb52bcd2f9ce3e93f204a0bcdc4d96a5a2df925971ce`
- Size: `8,126,070,784` bytes; no backing file
- Owner/mode: `mtuinstra:mtuinstra` (`1000:1000`), `0444`
- `qemu-img check`: passed with no errors

This operator-owned sibling is a byte-identical, no-reflink/no-sparse copy of
the retained libvirt-owned candidate at
`/home/mtuinstra/dev54-image-candidate-20261118-guard/candidate-php83-8.3.36-guard-standalone.qcow2`.
Both files have the same SHA-256. The extra copy was made after verifying the
filesystem had enough room to retain the 62 GiB reserve plus the copy's
allocated size; 68,090,695,680 bytes remained free afterward.

## Exact change

Relative to the approved 5583 image, `virt-diff` showed exactly two guest
changes:

1. Add `/etc/needrestart/conf.d/90-ci-runner-job.conf`, root-owned mode `0644`,
   with the exact rule
   `$nrconf{override_rc}{qr(^ci-runner-job\.service$)} = 0;`.
2. Update `/tmp/verify-image-contract.sh` to verify that rule, the unchanged
   unattended APT hook, and the runner unit's existing `RuntimeMaxSec=7200` and
   `ExecStopPost=+/usr/bin/systemctl poweroff`.

The package inventory and `/etc/ci-runner-image-manifest` are unchanged from
5583. The PHP 8.3 family remains pinned at
`8.3.36-1+ubuntu24.04.1+deb.sury.org+1`; no other PHP, Node, Docker, or runtime
version changed for this guard update. The existing APT automatic restart hook
and timers remain enabled. Packer install and image-contract source changes
are in commit `6027bf6` (`fix(runner): defer JIT unit restarts during
updates`). The focused image-contract suite passed 7/7; both changed shell
files passed `bash -n`, and `git diff --check` passed.

## Restart reproduction and guard check

In disposable VMs based on the original image, needrestart
`3.6-7ubuntu4.5` selected `ci-runner-job.service` during an automatic APT
restart after upgrading libc from `2.39-0ubuntu8.7` to
`2.39-0ubuntu8.12`. The unit stopped and its unchanged `ExecStopPost` powered
off the guest. `NEEDRESTART_MODE=l` listed the unit without restarting it.

With the exact service-only override, the same automatic mode deferred that
unit restart and the runner process kept the same PID. List mode continued to
report the unit, and other service restart handling stayed at Ubuntu defaults.
This proves a possible interruption mechanism; it does not prove that this
mechanism caused any historical CI failure.

## Isolated Gate validation

The final standalone image booted in direct QEMU/KVM with user-mode NAT, 2
vCPUs and 3 GiB RAM. The VM was not registered as a runner and had no host
mount, Docker socket, or forwarded port. The whole VM process had a 60-minute
timeout. Validation used Gate commit
`188356ba01eb7796b5dc9a746eeabf34f95cf33f`, PHP 8.3.36, and the same Unit,
Integration, Functional, quality, and API-schema selection.

- PDO MySQL and SQLite query probes as `ci-runner`: passed
- Docker 29.6.2, `overlay2`, containerd snapshotter disabled in the isolated
  VM config: passed
- Updated image contract as `ci-runner`: passed
- All seven configured quality tasks: passed
- PHPUnit: 1,216 tests, 7,567 assertions, 14 skipped, 1 incomplete,
  1 warning; passed
- API schema: passed
- Pinned MariaDB fixture: 12 tests, 99 assertions, 0 skipped; passed
- Guest validation and QEMU exit codes: 0
- Elapsed time from QEMU start to exit: 232 seconds

The prior isolated attempts exposed harness setup problems, not image failures:
one libvirt run used the QEMU-usernet DNS address on the libvirt subnet; a
cloud-localds-only seed omitted the validation payload; an early marker edit
split the `runuser` environment; and a seed without rationalized ISO modes
made the fixture unreadable to `ci-runner`. Each attempt is preserved in local
diagnostic artifacts. The successful run used a full seed ISO with all eight
payload files readable as `0444`, DHCP matching QEMU user-mode NAT, PHP path
verification, phase markers, and the whole-VM timeout.

The filtered machine-readable evidence is in
`image-maintenance-update-result.json`. Raw serial logs and VM overlays remain
on Sanctuary under `/tmp/dev54-needrestart-diagnostic-20261118`; they are not
included in Git.

## Live state and rollback

No image selector, runner group, manager setting, Gate caller, or PR was
changed in this preparation. The original image remains selected. No rollback
is needed now. Any later temporary activation must use the already-approved
drained-runner procedure and restore the original selector and runner
configuration after the comparison, including on failure.

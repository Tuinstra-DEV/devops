# DEV-54 PDO MySQL candidate — 16 November 2026

## Result

The smallest image source change is prepared locally. The candidate build was
attempted and blocked by the unavailability of the **exact** CLI-matched
`php8.3-mysql` package. No successful new image, hash or isolated VM result is
claimed. No selector, manager setting, runner-group setting or PR was changed.
Machine-readable evidence is in [image-candidate-result.json](image-candidate-result.json).

## Direct diagnosis

Read-only libguestfs inspection of the complete hash-verified active base
`66eded13ed619c122421566de9b0a55ed49de5eb895caa40a2bf3b77d1b82bc4`
confirmed:

- `php8.3-cli`, `php8.3-common` and `php8.3-sqlite3` are installed at
  `8.3.35-1+ubuntu24.04.1+deb.sury.org+1`.
- `php8.3-mysql` is **absent**, rather than merely disabled. Neither the
  `mods-available/pdo_mysql.ini` nor CLI enablement link exists.
- PHP 8.3.35 executed **as ci-runner inside that image** reports only the
  SQLite PDO driver and `extension_loaded("pdo_mysql")=false`.
- The new complete build contract, executed as ci-runner against the read-only
  source image, exits 1 with `PHP 8.3 missing pdo_mysql`. This is an offline
  negative test, not Docker/VM runtime evidence.

Unprivileged inspection used the already-existing user-owned libguestfs cached
appliance (`kernel`, `initrd`, `root`) through a separate `README.fixed` directory
under the private candidate directory. No kernel permissions, sudoers, host
packages or credentials were changed. Earlier unprivileged supermin failure
does not prevent using that existing appliance.

## Exact source change

`infra/packer/scripts/install-runner.sh` adds only
`"php8.3-mysql=${PHP83_VERSION}"` to the existing pinned package transaction and
`php83_mysql=${PHP83_VERSION}` to the manifest. It introduces no independent
version knob and changes no other runtime pin.

`verify-image-contract.sh` now requires both the loaded PDO MySQL extension and
the mysql PDO driver, and checks that the manifest's MySQL package version
equals both the CLI package version and existing `php83` manifest pin. Existing
SQLite query and all other toolchain/security checks remain.

The historical 5 November runner-only repair script remains byte-for-byte at
its reviewed SHA-256
`8ab894681f9afcc3eb739b12411bd6d1b4f97a67f6f92cafefcd613aa3d50333`.
Its regression test now asserts this immutable artifact hash instead of
requiring its embedded historical contract to acquire every new Packer
requirement. It does not install MySQL and must not be repurposed to do so.

## Build route and concrete blocker

Packer 1.15.4, QEMU plugin 1.1.6 and KVM are available under the existing account.
However the stored Packer variables still specify runner 2.336.0, PHP 8.3.32
and PHP 8.4.23 and omit four later toolchain pins. The active manifest instead
has runner 2.338.2, PHP 8.3.35 and PHP 8.4.26. Rebuilding blindly from those
inputs would change unrelated runtime versions.

The existing immutable repair route was therefore used: `qemu-img convert`
creates a standalone private copy; `virt-customize --copy-in` followed by
`--run-command 'bash /tmp/image-candidate-guest.sh'` runs the bounded guest
package change. [image-candidate-guest.sh](image-candidate-guest.sh) requires
the exact current CLI/manifest pin, signed APT repositories, no package upgrades,
and a complete before/after package inventory differing only by the new mysql
package. It also verifies unchanged runtime binary and guest security-unit
hashes, and checks MySQL/SQLite as ci-runner before accepting output.

The first attempt used `--run`, which ignores the Bash shebang; it stopped on
`pipefail`. That failed copy is retained. A fresh copy with explicit Bash got
through signed repository refresh but stopped at:

```text
E: Version '8.3.35-1+ubuntu24.04.1+deb.sury.org+1' for 'php8.3-mysql' was not found
```

The [official publication](https://api.launchpad.net/1.0/~ondrej/+archive/ubuntu/php/+binarypub/250320205)
records the exact amd64 binary as superseded and removed on 22 October 2026.
Both its PPA pool URL and original Launchpad build download return HTTP 404;
the source publication returns no amd64 MySQL binary URL, and no exact `.deb`
was found in the existing local caches. The original build's `.changes` remains
available and records package SHA-256
`5cd83710704508e57f63053f7216567198553faf8e255f83f997a57f4327da9c` (127300 bytes).
That checksum alone does not supply a package or authorize an untrusted source.
No fallback version, unsigned APT option, or runtime update was attempted.

Private failed artifacts and logs are retained under
`/home/mtuinstra/dev54-image-candidate-20261116/`; `build-bash.log` records the
blocking exact-pin error. Failed artifacts are **not release candidates** and
must never be selected. The original base's complete SHA-256 and selector
were rechecked unchanged after both attempts.

## Validation remaining

Ten focused image/toolchain tests and shell syntax checks passed locally.
`make lint`, the complete `make test` suite and diff checks also passed. The
first full suite exposed the historical repair-contract coupling described
above; its immutable artifact guard now passes without changing that repair.
The negative check above proves the new contract catches the observed defect.
No candidate VM was started: the build failed before producing a valid image;
this is not a demonstrated VM privilege or startup failure.

After an authenticated, checksum-verified copy of that exact package is
available, repeat the bounded build on a **fresh** standalone copy and record
its full SHA-256. Boot a separate bounded VM overlay with no JIT credential or
runner registration, no host Docker socket, no host-directory mounts and no
incoming port forwarding. Keep the existing base and selector untouched.
In that VM run, as ci-runner:

1. `guest-readiness.sh`: PHP 8.3 MySQL/SQLite modules and drivers, a real SQLite
   query, Composer and usable Docker daemon. Record Docker driver/version.
2. The complete updated image contract; verify all other runtime pins and
   guest security-unit/wrapper hashes against the source image.
3. Gate backend from candidate `188356ba01eb7796b5dc9a746eeabf34f95cf33f`, using
   PHP 8.3 shims and the locked Composer dependencies.
4. The **unchanged** shell step of
   `.github/actions/gate-native-mariadb-fixture/action.yml`, including its pinned
   MariaDB digest, network-none container, temporary Unix socket, existing
   PHPUnit test and fail-on-skipped/fail-on-empty flags. Previous evidence is
   12 tests / 99 assertions; this candidate has **not** run it.

Those positive MySQL, SQLite, Docker, full-contract and MariaDB VM checks all
remain open. Availability of a retained exact package is required to proceed;
changing the existing PHP runtime is outside this candidate's scope.

## Hashes and rollback

- Old/active: `66eded13ed619c122421566de9b0a55ed49de5eb895caa40a2bf3b77d1b82bc4`.
- New validated image: **none**; build blocked, no new digest approved.
- Current rollback: no activation occurred, so no live rollback is necessary.
  Retain failed artifacts for inspection; do not overwrite or select them.
- If a later live switch is separately authorized, retain the old digest file
  and restore the fixed selector to that exact old path only through the
  existing idle/drained rotation procedure. Do not edit or remove a backing
  image referenced by any running VM. Runner admission/manager/caller rollback
  remains the separately prepared one-trial plan; no live switch is authorized
  by this candidate-build approval.

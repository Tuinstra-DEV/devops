# DEV-54 PHP 8.3 family candidate — 17 November 2026

## Approved build scope

The user approved updating the PHP 8.3 family by one patch release for
candidate build and isolated validation. The exact CLI-matched MySQL package
from the source image was unavailable, so the candidate updates the installed
PHP 8.3 core package family from
`8.3.35-1+ubuntu24.04.1+deb.sury.org+1` to
`8.3.36-1+ubuntu24.04.1+deb.sury.org+1` and adds `php8.3-mysql` at that same
version. No PHP 8.4, Xdebug, Node, Docker, runner, Composer, Playwright, Trivy,
or other image pin moves.

The PHP 8.3.36 family was built as an immutable package overlay and validated
in a separate unregistered VM. The VM used a separate writable COW overlay
backed by that package candidate; its Docker configuration, Gate checkout, and
test dependencies were written only to the VM overlay. The Docker test
configuration is not part of the candidate image. The active image remains
unchanged. Filtered results, hashes, and the standalone image verification
are recorded in [image-family-validation-summary.md](image-family-validation-summary.md)
and [image-family-update-result.json](image-family-update-result.json).

## Signed source evidence

- Source: `https://ppa.launchpadcontent.net/ondrej/php/ubuntu`, noble/amd64.
- Signed `InRelease` publication date: Thu, 12 Nov 2026 19:01:31 UTC.
- Existing key file: `infra/packer/keys/ondrej-php.asc`, SHA-256
  `7258b1cb18300b87cd5668a6d64ce78184d9c0e129382879a0d79291c4ef463d`.
- Existing primary fingerprint: `B8DC7E53946656EFBCE4C1DD71DAEAAB4AD4CAB6`.
- Current PPA `InRelease` SHA-256:
  `f359156729911805364067b2264c66e4bdbeead54035d8fb2b3a2bdd8981d88c`.
  GPG reports a good signature from the existing pinned key. The file also
  carries a second signature from an unpinned key; `gpgv` reports that signer
  as unknown. An isolated `apt-get update` using only the existing pinned
  Ondřej key for this PPA succeeded, so apt accepted the authenticated pinned
  signature. No key was added or changed.
- `main/binary-amd64/Packages.gz` SHA-256 is
  `7002c46299f1c81b7a51703dddf13a0c094c8f8f6ebb901bc175443e96e2ac01`, matching
  the SHA-256 recorded in that signed `InRelease`.

The installed PHP 8.3 family was read from the hash-verified source image's
dpkg status. The signed PPA index contains every changed package at the exact
new version. The package SHA-256 values and full plan are in
[image-family-update-manifest.json](image-family-update-manifest.json).

## Packer PHP 8.3 pin

The Packer install script already accepts one exact `PHP83_VERSION` and uses
it for the nine PHP 8.3 core packages and the MySQL package. The local Packer
example now sets the approved PHP 8.3 pin to:

```diff
php83_version = "8.3.36-1+ubuntu24.04.1+deb.sury.org+1"
```

The operator variable file on Sanctuary remains untouched. It is stale and
omits later runtime pins, so it must not be used as a build input. The image
contract already ties the MySQL and SQLite package versions to the PHP 8.3 CLI
version and checks `pdo_mysql`/PDO's MySQL driver.

## Package plan

An isolated `apt-get -s --no-install-recommends install` was run against the
read-only extracted dpkg status of active image
`66eded13ed619c122421566de9b0a55ed49de5eb895caa40a2bf3b77d1b82bc4`. It used a
temporary apt state directory and the existing Ondřej key. Result: **9
upgraded, 1 newly installed, 0 removed**, with 222 unrelated packages left
not upgraded. The only planned changes are the nine installed PHP 8.3 core
packages and the missing MySQL extension. The full simulation is retained in
[image-family-update-apt-simulation.txt](image-family-update-apt-simulation.txt).

Exact package transaction:

```text
php8.3-cli=8.3.36-1+ubuntu24.04.1+deb.sury.org+1
php8.3-common=8.3.36-1+ubuntu24.04.1+deb.sury.org+1
php8.3-curl=8.3.36-1+ubuntu24.04.1+deb.sury.org+1
php8.3-mbstring=8.3.36-1+ubuntu24.04.1+deb.sury.org+1
php8.3-opcache=8.3.36-1+ubuntu24.04.1+deb.sury.org+1
php8.3-readline=8.3.36-1+ubuntu24.04.1+deb.sury.org+1
php8.3-sqlite3=8.3.36-1+ubuntu24.04.1+deb.sury.org+1
php8.3-xml=8.3.36-1+ubuntu24.04.1+deb.sury.org+1
php8.3-zip=8.3.36-1+ubuntu24.04.1+deb.sury.org+1
php8.3-mysql=8.3.36-1+ubuntu24.04.1+deb.sury.org+1
```

The plan leaves `php8.3-xdebug` at `3.5.3-1+ubuntu24.04.1+deb.sury.org+1`,
`php-common` at `2:101~+ubuntu24.04.1+deb.sury.org+1`, and all PHP 8.4 packages
at `8.4.26-1+ubuntu24.04.1+deb.sury.org+1`. All other manifest entries and
checked runtime-file hashes match the active image; only `php83`,
`php83_sqlite`, and the new `php83_mysql` manifest pin reflect the approved
transaction.

## Rollback and activation boundary

The source image remains
`66eded13ed619c122421566de9b0a55ed49de5eb895caa40a2bf3b77d1b82bc4`; its
SHA-256 matched before and after the build. The candidate package layer passed
the exact package delta and runtime checks, the image contract, GrumPHP,
PHPUnit, API schema validation, and the pinned MariaDB fixture. PHPUnit reported
1,216 tests, 7,567 assertions, 14 skips, 1 incomplete test, and 1 warning; the
MariaDB fixture passed 12 tests and 99 assertions with zero skips.

The validated package overlay was flattened to a standalone qcow2 with no
backing file. `qemu-img check` passed, and `qemu-img compare` reported the
standalone image identical to the validated package overlay. The standalone
image SHA-256 is
`5583aa0da997b0fd9001211b63b3abd24d51778645cbbccdc007444236bc7ec2`. The VM
validation overlay has a separate hash and is not a candidate artifact; the
Docker test configuration was written only there. A full Packer rebuild was
not run because its standard install path would reinstall unrelated runtime
pins and the active operator var file is stale. The active image selector,
runner group, manager configuration, Gate caller, and PRs remain untouched.
No rollback is needed now. If a later separately authorized selector change
fails validation, restore the selector to the original
`ubuntu-24.04-runner-66eded13ed619c122421566de9b0a55ed49de5eb895caa40a2bf3b77d1b82bc4.qcow2`
through the existing drained-runner procedure.

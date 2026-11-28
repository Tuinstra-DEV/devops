# DEV-54 PHP 8.3.36 candidate validation

## Candidate and source

- Source image SHA-256 before and after: `66eded13ed619c122421566de9b0a55ed49de5eb895caa40a2bf3b77d1b82bc4`.
- The first private build copy has SHA-256 `bc233338d16d80ac2c4f2f163778fe2ed690d1ab1cd2fa9fb2b691d56f43919d`; it differs because the signed-index download/install attempt left apt indexes, package-cache files, and temporary GPG state. Before the approved transaction, its installed package inventory, image manifest, and checked runtime-file hashes matched the active source. The active source itself did not change.
- Exact package delta: nine PHP 8.3 family upgrades plus `php8.3-mysql`, all at `8.3.36-1+ubuntu24.04.1+deb.sury.org+1`; no package removals.
- Package overlay SHA-256: `e61b8ab5c8ba10ce2f0fa5309f9bc19c4041fdca46ec99527f1c7bfbaf037aae`.
- Standalone image: `/home/mtuinstra/dev54-image-candidate-20261117-family/candidate-php83-8.3.36-standalone.qcow2`, SHA-256 `5583aa0da997b0fd9001211b63b3abd24d51778645cbbccdc007444236bc7ec2`, with no backing file. `qemu-img check` passed and `qemu-img compare` reported it identical to the validated package overlay.
- Other runtime pins and checked runtime file hashes remained unchanged.
- A full Packer rebuild was not run: its install route would reinstall unrelated runtime pins and the active operator var file is stale. The Packer install script consumes the exact PHP 8.3 family pin; the image contract ties MySQL and SQLite to it and verifies PDO MySQL. The example pin is updated.
- Validation used Gate commit `188356ba01eb7796b5dc9a746eeabf34f95cf33f`, PHP 8.3.36, 2 vCPU, 3 GiB RAM, QEMU user networking, no runner registration, host mounts, host Docker socket, or host port forwarding.

## Results

- `ci-runner` PDO MySQL and SQLite query: passed.
- Docker daemon and `ci-runner` Docker access: passed; overlay2. The pinned MariaDB fixture pulled and ran through Docker as `ci-runner`.
- Full immutable image contract: passed.
- GrumPHP quality: all seven configured tasks passed, including PHPStan and Composer audit.
- Gate backend PHPUnit: 1,216 tests, 7,567 assertions, 14 skipped, 1 incomplete, 1 warning; passed on PHP 8.3.36.
- API schema check: passed.
- Pinned MariaDB fixture: 12 tests, 99 assertions, zero skipped; passed.
- Validation VM exit code: 0.

The unfiltered serial output is retained locally, not committed. Its SHA-256 is
`d672ab14338a285fcdf619ae887ed7085514604954fa4ca63654a5c42fee07a6`. The
package-build log is also retained locally, not committed; SHA-256
`931489d90810f735798dd071984ddf9941e3f653afcb65c0bd42e30058b27df1`.

## Activation boundary

The active image, selector, runner group, manager configuration, Gate caller,
and PRs were not changed. The standalone candidate is prepared for review but
has not been activated. The writable Docker configuration used for validation
exists only on the separate VM overlay. Rollback, if a later authorized
activation is attempted, is to restore the selector to the original source
image SHA/path above.

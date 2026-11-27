# DEV-54: Bookworm UAPI header pin update

On 27 November the exact `linux-libc-dev` package version `6.1.192-2` was no longer available from the configured signed Debian Bookworm repositories. Both [legacy release build 46041173031](https://github.com/Tuinstra-DEV/gate/actions/runs/46041173031) and [Release CI build 46041173682](https://github.com/Tuinstra-DEV/gate/actions/runs/46041173682) failed at package installation. The Debian security repository offered `6.1.192-3`.

The one-line Dockerfile update retains exact installation and `dpkg-query` verification for both PHP runtime targets. The original PHP FPM base image, Perl pins and PHP version remain unchanged. A local build of the original FPM base using the actual Dockerfile successfully installed and verified `6.1.192-3`; a full native release remains required.

Only the legacy and Release CI deployment-symbol fingerprints change. The SDK initialization, routing and privacy fingerprints and each component's actual historical source, run, artifact and event remain unchanged. This review does not claim a new event, successful full release or production promotion.

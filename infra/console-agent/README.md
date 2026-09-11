# Console enrollment for production hosts (DEV-26)

`install-container.sh` is adapted from a reviewed copy in the running Console image
`ghcr.io/tuinstra-dev/console@sha256:b29e7355aa96cf203d74c4b041a20bafde69ae916dfddef8bd9ff2a6ddda6b72`.
Its upstream source remains the Console repository's `agent/install-container.sh`.
Local adaptations enforce stdin-only tokens, protected atomic env writes, immutable
images, fixed production inputs and disabled backups with no backup-directory mount.
The orchestration script uses `/var/www/_platform/console-agent`, never copies
registry credentials, and pins the loaded image by verified SHA256 image ID.

After host bootstrap, run from this checkout:

```sh
python3 scripts/console-agent-enroll.py \
  --source mtuinstra@192.168.1.6 --hub mtuinstra@192.168.1.6 \
  --target mtuinstra@vps01.tuinstra.dev --host-slug tuinstra-prod-01
```

For another host change target and host slug. Source must have the reviewed
image, and hub must run `console_prod_php` with the scoped token CLI. SSH hostkeys
must already be trusted. The target admin requires sudo. A large image is streamed
once from the source because the registry image is private. Application traffic
never depends on the source host after installation.

Tokens stay in process memory during transport and in root-only `.env` on the
target. Existing installation or credential issuance conflicts fail closed; the
script never rotates credentials. After a failed enrollment it disables the newly
issued credential. After an interrupted/uncertain issuance, inspect the hub and
target before retrying; explicitly disable any orphan credential. No raw CLI output
or env/config dumps are printed. Do not run with shell tracing.

The official container agent publishes no ports; only its proxy can reach the
Docker socket. It uses read-only host mounts and a GET-only Docker API proxy.
Container mode cannot accurately report host listeners. Backups collection is
explicitly disabled until the separately deferred backup configuration exists.
Firewall configuration is outside this bootstrap's user-approved scope.

Verify Docker and host-capacity collection runs are accepted in Console and the
host credential's last-used timestamp advances; do not query/display token hashes.
Verify the two containers restart after a reboot. For rollback stop this exact
Compose stack and disable its exact credential using
`app:agent:set-enabled agent:<host> disabled`. Keep source-image.txt for audit.

# Restricted production deploy

`reusable-cd-restricted-deploy.yml` invokes exactly `deploy <application-name>`
over SSH as the dedicated `deploy` account. The caller supplies an existing
DevOps application name and a GitHub Environment whose `PROD_HOST` is the
production hostname. The environment provides a dedicated deploy private key
and a pinned `known_hosts` line with that same hostname. Strict host-key
checking is mandatory. The workflow never uploads Compose files, sends stdin
to the host, runs a remote shell or uses sudo. Root-owned Compose definitions
and fully immutable image digests must be staged separately by an interactive
administrator before this workflow is dispatched.

For immutable image preparation, `reusable-cd-vite-spa.yml` supports
`publish_only: true`. It builds, scans and pushes the Git-SHA tag, returns
`image-ref` and `image-digest`, leaves the mutable `latest` tag untouched and
skips its legacy SSH deployment job. Existing callers retain their previous
defaults.

## Immutable website activation on VPS01

The Nuxt/static reusable workflow can use `activate_website_digest: true`
together with `restricted_deploy: true` for Marcel, WODIQ and Tuinstra sites.
The workflow requires the repository's current `main` commit and hands over
the scanned GHCR manifest digest and source SHA. Keep the caller's concurrency
group and disable cancellation of in-progress deploys.

Use the `website-production` environment with a custom `main` branch rule,
`PROD_HOST=vps01.tuinstra.dev`, the pinned `DEPLOY_KNOWN_HOSTS` entry, and a
different `DEPLOY_SSH_PRIVATE_KEY` for each site. Install only the corresponding
public key with `infra/ansible/production-website-release.yml` as the existing
administrator. Provide `production_website_release_keys` as a mapping from
website name to an Ed25519 public key without a comment. The playbook installs a
forced command limited to that app. The legacy deploy key retains its original
fixed commands.

VPS01 takes the shared operations lock, checks the release image's revision
label and waits for two successful Docker health checks on a candidate. Caddy
routes to the candidate while the canonical Compose container is updated.
Once the canonical container passes the same checks, Caddy switches back and
the HTTPS route is verified. The active Compose pin and Caddy route remain
compatible with the existing backup and restore contract. A failed switch
attempt restores the original pin and route. Docker removes only the previous
local image reference without force, after checking other container references.
GHCR keeps the commit tag and digest for recovery.

A successful deployment writes
`/var/lib/tuinstra/<site>/website-deploy-receipt.json`. If a failure prevents
automatic recovery, the root-only `website-deploy-in-progress` directory holds
the pre-deploy Compose and Caddy files. Keep the candidate serving if its route
is active. Under the existing host-operation lock, restore the saved Compose
file, verify the old container is healthy, then restore and reload the saved
route. Verify the public HTTPS endpoint and retire the candidate before
archiving the in-progress directory. The lock and in-progress marker prevent a
second automated deployment from overwriting the recovery files.

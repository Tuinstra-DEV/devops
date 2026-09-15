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

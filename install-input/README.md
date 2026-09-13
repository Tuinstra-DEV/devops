# External installer inputs

This directory is part of the reviewed backup bundle. The host-key files are
external, public inputs and are intentionally not committed to Git. Stage them
only after checking the production host keys over the existing strict SSH
connections and comparing them with the local `known_hosts` entries.

The required files are:

- `hostkeys/vps01.pub`
- `hostkeys/vps02.pub`

`manifest.json` is the reviewed checksum, ownership, mode and Ed25519
fingerprint contract for those files. The installer verifies the manifest and
every staged file before it performs any system mutation. A source archive is
therefore expected to contain this directory and manifest, but not the two
external key files until the controlled staging step.

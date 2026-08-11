# AGENTS

This repository is maintained through story-driven branches.

## Delivery Rules

- Branch naming: `feat/<story-key>-<slug>`.
- Every story must run `make lint` and `make test`.
- PR titles use `<type>[<story-key>] <title>` where `<type>` is `feat`, `chore`, or `bug`.
- Do not merge story branches automatically.

## Release Tags

- Reusable workflows are consumed via major version tags (`v1`, `v2`, ...).
- After merging to `main`, create a new immutable semantic release tag using the documented procedure.
- Never force-push branches or tags and never move an existing release tag.
- See `docs/workflows/release-procedure.md` for full procedure.

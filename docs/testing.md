# Testing Guide

All story branches must pass the local hard gates before commit:

- `make lint`
- `make test`

The local test gate validates required repository structure and baseline docs.

`make test` also executes the Heavy CI v2 preflight contract locally. It validates machine-readable decisions and quantified queue evidence, immutable caller/workflow identity, hosted fallback for fork/Dependabot/`pull_request_target`, checked-out adapter and lockfile inputs, path/symlink conflicts, public inputs and outputs, trust/cache negative cases, immutable artifact checks, action pinning, and the WODIQ- and Tracker-shaped fixtures. `.github/workflows/heavy-ci-v2-integration.yml` exercises both shapes on hosted GitHub runners when the contract or fixtures change.

The integration workflow records per-stage NDJSON evidence. Consumer speed improvements require a separate hosted A/B canary with cold and warm cache runs; the contract test proves correctness and isolation, not performance.

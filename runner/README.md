# Sanctuary ephemeral CI runner

This directory contains the host-side boundary for one ephemeral GitHub Actions
runner. It is intentionally smaller than a runner scale-set client: the
official GitHub client owns job acquisition and JIT configuration, while
`ci-runner-manager` owns local admission, VM launch, and cleanup.

See [the platform design](../docs/runner/sanctuary-kvm-runner.md) and
[the operations runbook](../docs/playbooks/ci-runner-host.md) before deployment.

## Local checks

```sh
./scripts/test-runner-platform.sh
```

No credential, token, private key, or runner registration response belongs in
this repository.

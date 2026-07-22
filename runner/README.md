# Sanctuary ephemeral CI runner

This directory contains the host-side boundary for up to two ephemeral GitHub
Actions runners. It is intentionally smaller than a runner scale-set client: a
repository-scoped polling adapter owns job acquisition and JIT configuration,
while `ci-runner-manager` owns local admission, VM launch, and cleanup.
It remains unprivileged and communicates with the root-only lifecycle helper
through a bounded systemd `SOCK_SEQPACKET` socket guarded by exact peer UID.

See [the platform design](../docs/runner/sanctuary-kvm-runner.md) and
[the operations runbook](../docs/playbooks/ci-runner-host.md) before deployment.

## Local checks

```sh
./scripts/test-runner-platform.sh
```

No credential, token, private key, or runner registration response belongs in
this repository.

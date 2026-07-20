#!/usr/bin/env bash
set -euo pipefail

python3 -c 'import ast,pathlib; [ast.parse(p.read_text()) for root in ("runner/manager", "runner/host-helper", "runner/tests") for p in pathlib.Path(root).glob("*.py")]'
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="runner/manager:runner/host-helper" python3 -B -m unittest discover -s runner/tests -v
bash -n infra/packer/scripts/install-runner.sh infra/packer/scripts/seal-image.sh \
  infra/packer/scripts/verify-image-contract.sh runner/guest/run-jit-runner.sh

if command -v ruby >/dev/null 2>&1; then
  ruby -e 'require "yaml"; ARGV.each { |p| YAML.safe_load(File.read(p), aliases: true) }' \
    infra/ansible/site.yml infra/ansible/inventory/hosts.example.yml \
    infra/ansible/roles/runner_host/defaults/main.yml \
    infra/ansible/roles/runner_host/handlers/main.yml \
    infra/ansible/roles/runner_host/tasks/main.yml infra/packer/http/user-data
fi

for file in infra/packer/sanctuary-runner.pkr.hcl infra/ansible/site.yml; do
  test -s "$file"
done

grep -q 'rotate 30' runner/logrotate/ci-runner-audit
grep -q 'MEMORY_MIB = "12288"' runner/host-helper/ci_runner_host_helper.py
grep -q 'VCPUS = "8"' runner/host-helper/ci_runner_host_helper.py
grep -q 'DISK_GIB = "120G"' runner/host-helper/ci_runner_host_helper.py
grep -q 'concurrency is 1' runner/manager/ci_runner_manager.py
grep -q 'max_lease_seconds = 7200' runner/config/manager.toml
grep -q 'Tuinstra-DEV/tuinstra-site' runner/config/manager.toml
! grep -q 'Tuinstra-DEV/devops' runner/config/manager.toml
grep -q '88.159.77.149/32' infra/ansible/inventory/hosts.example.yml
! grep -q 'nftables.service' infra/ansible/roles/runner_host/tasks/main.yml
grep -q 'docker buildx version' infra/packer/scripts/verify-image-contract.sh
grep -q "node --version.*v24" infra/packer/scripts/verify-image-contract.sh
grep -q 'php8.3' infra/packer/scripts/verify-image-contract.sh
grep -q 'php8.4' infra/packer/scripts/verify-image-contract.sh
grep -q 'playwright install --with-deps chromium' infra/packer/scripts/install-runner.sh
grep -q 'exec ./run.sh --jitconfig' runner/guest/run-jit-runner.sh
grep -q 'ExecStopPost=+/usr/bin/systemctl poweroff' runner/systemd/ci-runner-job.service
grep -q '^Restart=no$' runner/systemd/ci-runner-job.service
! grep -q 'run.sh --jitconfig' runner/systemd/ci-runner-job.service
if grep -Eq 'curl[^|]*\|[[:space:]]*(ba)?sh' infra/packer/scripts/install-runner.sh; then
  echo "image install must not pipe downloads to a shell" >&2
  exit 1
fi

if command -v packer >/dev/null 2>&1; then
  packer fmt -check infra/packer/sanctuary-runner.pkr.hcl
else
  echo "packer not installed; skipped packer fmt check"
fi

if command -v ansible-playbook >/dev/null 2>&1; then
  (cd infra/ansible && ansible-playbook -i 'sanctuary,' --syntax-check site.yml)
else
  echo "ansible-playbook not installed; skipped Ansible syntax check"
fi

echo "runner platform tests passed"

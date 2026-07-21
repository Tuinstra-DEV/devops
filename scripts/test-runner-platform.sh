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
grep -q '^RuntimeMaxSec=7200$' runner/systemd/ci-runner-job.service
grep -q 'systemd-run' runner/host-helper/ci_runner_host_helper.py
grep -q 'ubuntu-24.04-runner-{{ runner_base_image_sha256 }}.qcow2' infra/ansible/roles/runner_host/tasks/main.yml
grep -q "path: /usr/local/libexec.*owner: root.*group: root.*mode: '0755'" infra/ansible/roles/runner_host/tasks/main.yml
grep -q "src: runner/config/manager.toml.*group: ci-runner-manager.*mode: '0640'" infra/ansible/roles/runner_host/tasks/main.yml
grep -q 'dest: /etc/ci-runner/sanctuary-ci.xml' infra/ansible/roles/runner_host/tasks/main.yml
grep -q 'runner_libvirt_uri=qemu:///system' infra/ansible/roles/runner_host/tasks/main.yml
grep -q 'net-uuid sanctuary-ci' infra/ansible/roles/runner_host/tasks/main.yml
grep -q 'net-list --name.*grep -Fxq sanctuary-ci' infra/ansible/roles/runner_host/tasks/main.yml
grep -q 'net-list --all --persistent --name' infra/ansible/roles/runner_host/tasks/main.yml
grep -q 'net-list --all --autostart --name' infra/ansible/roles/runner_host/tasks/main.yml
! grep -q 'when: runner_network_definition.changed' infra/ansible/roles/runner_host/tasks/main.yml
grep -q '<uuid>.*runner_network_uuid.*</uuid>' infra/ansible/roles/runner_host/tasks/main.yml
! grep -q 'virsh net-undefine sanctuary-ci' infra/ansible/roles/runner_host/tasks/main.yml
! grep -q 'dest: /etc/libvirt/qemu/networks/sanctuary-ci.xml' infra/ansible/roles/runner_host/tasks/main.yml
grep -q 'required_version = "= 1.15.4"' infra/packer/sanctuary-runner.pkr.hcl
grep -q 'version = "= 1.1.6"' infra/packer/sanctuary-runner.pkr.hcl
test "$(grep -c "execute_command.*sudo -S env" infra/packer/sanctuary-runner.pkr.hcl)" -eq 3
grep -q "shutdown_command.*passwd --lock packer.*systemctl poweroff" infra/packer/sanctuary-runner.pkr.hcl
! grep -q 'passwd --lock packer' infra/packer/scripts/seal-image.sh
grep -q 'packer_linux_amd64_sha256=15f97a6a99645c7d5308c609973b5280837b38e112beac413ccbce80da927cf1' infra/packer/toolchain.lock
grep -q 'qemu_plugin_linux_amd64_sha256=3f735539fbdd0368785babda272b85738866f736415dce59d04b4cb550c4db87' infra/packer/toolchain.lock
grep -q 'Tuinstra-DEV/tuinstra-site' runner/config/manager.toml
! grep -q 'Tuinstra-DEV/devops' runner/config/manager.toml
grep -q '88.159.77.149/32' infra/ansible/inventory/hosts.example.yml
! grep -q 'nftables.service' infra/ansible/roles/runner_host/tasks/main.yml
grep -q 'docker buildx version' infra/packer/scripts/verify-image-contract.sh
grep -q "node --version.*v24" infra/packer/scripts/verify-image-contract.sh
grep -q 'php8.3' infra/packer/scripts/verify-image-contract.sh
grep -q 'php8.4' infra/packer/scripts/verify-image-contract.sh
grep -q 'B8DC7E53946656EFBCE4C1DD71DAEAAB4AD4CAB6' infra/packer/scripts/install-runner.sh
grep -q 'keys/ondrej-php.asc' infra/packer/sanctuary-runner.pkr.hcl
test "$(shasum -a 256 infra/packer/keys/ondrej-php.asc | awk '{print $1}')" = '7258b1cb18300b87cd5668a6d64ce78184d9c0e129382879a0d79291c4ef463d'
! grep -q 'keyserver.ubuntu.com' infra/packer/scripts/install-runner.sh
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

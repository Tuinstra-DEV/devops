#!/usr/bin/env bash
set -euo pipefail

assert_absent() {
  local pattern="$1"
  local file="$2"
  if grep -q -- "$pattern" "$file"; then
    echo "unexpected pattern in $file: $pattern" >&2
    return 1
  fi
}

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
grep -q '^RuntimeMaxSec=300s$' runner/systemd/ci-runner-host-helper@.service
grep -q '^MaxConnections=4$' runner/systemd/ci-runner-host-helper.socket
grep -q '^HELPER_MUTATION_TIMEOUT_SECONDS = 330$' runner/manager/ci_runner_manager.py
grep -q 'QEMU_USER = "libvirt-qemu"' runner/host-helper/ci_runner_host_helper.py
grep -q '\[systemctl, start, --no-block, ci-runner-job.service\]' runner/host-helper/ci_runner_host_helper.py
grep -q "path: /mnt/ssd1000-01/ci-runner, owner: root, group: kvm, mode: '0710'" infra/ansible/roles/runner_host/tasks/main.yml
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
grep -q 'readlink.*runner_network_autostart_link' infra/ansible/roles/runner_host/tasks/main.yml
grep -q 'refusing unexpected runner autostart entry' infra/ansible/roles/runner_host/tasks/main.yml
assert_absent 'when: runner_network_definition.changed' infra/ansible/roles/runner_host/tasks/main.yml
grep -q '<uuid>.*runner_network_uuid.*</uuid>' infra/ansible/roles/runner_host/tasks/main.yml
assert_absent 'virsh net-undefine sanctuary-ci' infra/ansible/roles/runner_host/tasks/main.yml
assert_absent 'dest: /etc/libvirt/qemu/networks/sanctuary-ci.xml' infra/ansible/roles/runner_host/tasks/main.yml
grep -q 'required_version = "= 1.15.4"' infra/packer/sanctuary-runner.pkr.hcl
grep -q 'version = "= 1.1.6"' infra/packer/sanctuary-runner.pkr.hcl
test "$(grep -c "execute_command.*sudo -S env" infra/packer/sanctuary-runner.pkr.hcl)" -eq 3
grep -q "shutdown_command.*passwd --lock packer.*systemctl poweroff" infra/packer/sanctuary-runner.pkr.hcl
assert_absent 'passwd --lock packer' infra/packer/scripts/seal-image.sh
grep -q 'packer_linux_amd64_sha256=15f97a6a99645c7d5308c609973b5280837b38e112beac413ccbce80da927cf1' infra/packer/toolchain.lock
grep -q 'qemu_plugin_linux_amd64_sha256=3f735539fbdd0368785babda272b85738866f736415dce59d04b4cb550c4db87' infra/packer/toolchain.lock
grep -q 'Tuinstra-DEV/tuinstra-site' runner/config/manager.toml
assert_absent 'Tuinstra-DEV/devops' runner/config/manager.toml
grep -q '88.159.77.149/32' infra/ansible/inventory/hosts.example.yml
assert_absent 'nftables.service' infra/ansible/roles/runner_host/tasks/main.yml
grep -q 'docker buildx version' infra/packer/scripts/verify-image-contract.sh
grep -q "node --version.*v24" infra/packer/scripts/verify-image-contract.sh
grep -q 'php8.3' infra/packer/scripts/verify-image-contract.sh
grep -q 'php8.4' infra/packer/scripts/verify-image-contract.sh
grep -q 'B8DC7E53946656EFBCE4C1DD71DAEAAB4AD4CAB6' infra/packer/scripts/install-runner.sh
grep -q 'keys/ondrej-php.asc' infra/packer/sanctuary-runner.pkr.hcl
test "$(shasum -a 256 infra/packer/keys/ondrej-php.asc | awk '{print $1}')" = '7258b1cb18300b87cd5668a6d64ce78184d9c0e129382879a0d79291c4ef463d'
assert_absent 'keyserver.ubuntu.com' infra/packer/scripts/install-runner.sh
grep -q 'playwright install --with-deps chromium' infra/packer/scripts/install-runner.sh
grep -q 'exec ./run.sh --jitconfig' runner/guest/run-jit-runner.sh
grep -q 'ExecStopPost=+/usr/bin/systemctl poweroff' runner/systemd/ci-runner-job.service
grep -q '^Restart=no$' runner/systemd/ci-runner-job.service
grep -q '^NoNewPrivileges=yes$' runner/systemd/ci-runner-job.service
grep -q '^NoNewPrivileges=yes$' runner/systemd/ci-runner-manager.service
grep -q '^NoNewPrivileges=yes$' runner/systemd/ci-runner-host-helper@.service
grep -q '^ListenSequentialPacket=/run/ci-runner-host-helper.sock$' runner/systemd/ci-runner-host-helper.socket
grep -q '^SocketUser=ci-runner-manager$' runner/systemd/ci-runner-host-helper.socket
grep -q '^SocketMode=0600$' runner/systemd/ci-runner-host-helper.socket
grep -q 'SO_PEERCRED' runner/host-helper/ci_runner_host_helper.py
grep -q 'helper_socket = "/run/ci-runner-host-helper.sock"' runner/config/manager.toml
assert_absent 'sudo' runner/manager/ci_runner_manager.py
if [ -e infra/ansible/roles/runner_host/templates/ci-runner-manager.sudoers.j2 ] ||
   [ -e runner/sudoers/ci-runner-manager ]; then
  echo "obsolete runner sudoers policy remains" >&2
  exit 1
fi
assert_absent 'run.sh --jitconfig' runner/systemd/ci-runner-job.service
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

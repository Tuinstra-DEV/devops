#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
inventory="$repo_root/infra/ansible/rehearsal/inventory.yml"
profile_contract="$repo_root/infra/ansible/rehearsal/profile-contract.yml"
lima_profile="$repo_root/infra/lima/tuinstra-rehearsal-01.yaml"
wrapper="$repo_root/scripts/production-host-baseline"
run_dir="$(mktemp -d "${TMPDIR:-/tmp}/tuinstra-rehearsal-contract.XXXXXX")"
trap 'rm -rf -- "$run_dir"' EXIT

for path in "$inventory" "$profile_contract" "$lima_profile" "$wrapper"; do
  [[ -f "$path" ]] || { echo "missing rehearsal fixture: $path" >&2; exit 1; }
done

if grep -R -E '(PRIVATE KEY|BEGIN OPENSSH|password[[:space:]]*:)' \
  "$repo_root/infra/ansible/rehearsal" "$repo_root/infra/lima/tuinstra-rehearsal-01.yaml" \
  "$repo_root/docs/playbooks/production-host-rehearsal.md" >/dev/null; then
  echo "possible secret material in rehearsal fixture" >&2
  exit 1
fi

ruby - "$inventory" "$profile_contract" "$lima_profile" <<'RUBY'
require "yaml"

inventory = YAML.safe_load(File.read(ARGV.fetch(0)), aliases: false)
hosts = inventory.dig("all", "children", "production_hosts", "hosts")
raise "rehearsal inventory must contain one fixed host" unless hosts&.keys == ["rehearsal01"]

host = hosts.fetch("rehearsal01")
expected = {
  "ansible_host" => "127.0.0.1",
  "ansible_port" => 60_022,
  "ansible_user" => "mtuinstra",
  "production_app_names" => ["umami"],
  "production_caddy_enable_https" => false,
  "production_caddy_http_bind_address" => "127.0.0.1",
  "production_caddy_https_bind_address" => "127.0.0.1",
  "production_umami_expected_hostname" => "tuinstra-rehearsal-01",
  "production_umami_domain" => "umami.rehearsal.invalid",
}
expected.each do |key, value|
  raise "unexpected #{key}" unless host.fetch(key) == value
end

versions = host.fetch("production_docker_package_versions")
raise "all Docker packages must be pinned for Ubuntu 26.04" unless versions.keys.sort == %w[
  containerd.io docker-buildx-plugin docker-ce docker-ce-cli docker-compose-plugin
].sort && versions.values.all? { |value| value.match?(/\A[^\s]+ubuntu\.26\.04~resolute\z/) }

serialized_inventory = File.read(ARGV.fetch(0))
%w[vps01.tuinstra.dev tuinstra-prod-01 umami.tuinstra.dev].each do |production_identity|
  raise "production identity leaked into rehearsal inventory" if serialized_inventory.include?(production_identity)
end
raise "rehearsal key paths must fail closed until protected vars override them" unless
  host.fetch("production_admin_public_key_file").start_with?("/nonexistent/") &&
    host.fetch("production_deploy_public_key_file").start_with?("/nonexistent/")

contract = YAML.safe_load(File.read(ARGV.fetch(1)), aliases: false)
expected_contract = {
  "schema_version" => 1,
  "profile_version" => "2026.09.12.rehearsal.1",
  "host_slug" => "tuinstra-rehearsal-01",
  "inventory_id" => "rehearsal01",
  "inventory_path" => "infra/ansible/rehearsal/inventory.yml",
  "inventory_limit" => "rehearsal01",
  "credential_directory" => "tuinstra-rehearsal-01",
  "source_repository" => "git@github.com:Tuinstra-DEV/devops.git",
  "source_path" => "infra/ansible/rehearsal/inventory.yml",
  "source_sha_binding" => "exact-bundle-head",
}
expected_contract.each do |key, value|
  raise "unexpected profile contract #{key}" unless contract.fetch(key) == value
end
raise "profile contract must contain only rehearsal domains" unless
  contract.fetch("applications").flat_map { |app| app.fetch("domains") } == ["umami.rehearsal.invalid"]
raise "profile contract must never use a production host identity" if
  contract.fetch("host_slug").start_with?("tuinstra-prod-")

lima = YAML.safe_load(File.read(ARGV.fetch(2)), aliases: false)
raise "Lima host architecture must remain native ARM64" unless lima.fetch("vmType") == "vz" && lima.fetch("arch") == "aarch64"
raise "unexpected rehearsal resource budget" unless lima.values_at("cpus", "memory", "disk") == [4, "8GiB", "64GiB"]
raise "host mounts are forbidden" unless lima.fetch("mounts") == []
raise "Lima containerd is forbidden" unless lima.dig("containerd", "system") == false && lima.dig("containerd", "user") == false
raise "personal keys or agents must not enter the VM" unless
  lima.dig("ssh", "loadDotSSHPubKeys") == false && lima.dig("ssh", "forwardAgent") == false
raise "all guest service forwarding must be blocked" unless lima.fetch("portForwards") == [{"guestIP" => "0.0.0.0", "proto" => "any", "ignore" => true}]
raise "backup rehearsal disk is not fixed" unless lima.fetch("additionalDisks") == [{"name" => "tuinstra-rehearsal-backup-01", "format" => true, "fsType" => "ext4"}]

image = lima.fetch("images").fetch(0)
raise "Ubuntu rehearsal image must be digest-pinned" unless image.fetch("arch") == "aarch64" && image.fetch("digest").match?(/\Asha256:[a-f0-9]{64}\z/)
RUBY

fake_ansible="$run_dir/ansible-playbook"
cat >"$fake_ansible" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$@" >"${REHEARSAL_ANSIBLE_CAPTURE:?}"
SH
chmod 0700 "$fake_ansible"

vars="$run_dir/vars.yml"
cat >"$vars" <<EOF
production_admin_public_key_file: $run_dir/admin.pub
production_deploy_public_key_file: $run_dir/deploy.pub
EOF
touch "$run_dir/admin.pub" "$run_dir/deploy.pub"

capture="$run_dir/check.args"
ANSIBLE_PLAYBOOK="$fake_ansible" REHEARSAL_ANSIBLE_CAPTURE="$capture" \
  "$wrapper" --inventory "$inventory" --limit rehearsal01 --extra-vars "$vars" \
  --as-admin --check configure
grep -Fx -- "--inventory" "$capture" >/dev/null
grep -Fx -- "$inventory" "$capture" >/dev/null
grep -Fx -- "--limit" "$capture" >/dev/null
grep -Fx -- "rehearsal01" "$capture" >/dev/null
grep -Fx -- "--check" "$capture" >/dev/null
grep -Fx -- "--diff" "$capture" >/dev/null
grep -Fx -- "@$vars" "$capture" >/dev/null
grep -Fx -- "$repo_root/infra/ansible/production-host-baseline.yml" "$capture" >/dev/null

capture="$run_dir/verify.args"
ANSIBLE_PLAYBOOK="$fake_ansible" REHEARSAL_ANSIBLE_CAPTURE="$capture" \
  "$wrapper" --inventory "$inventory" --limit rehearsal01 --extra-vars "$vars" \
  --as-admin verify
grep -Fx -- "$repo_root/infra/ansible/production-host-verify.yml" "$capture" >/dev/null
if grep -Fx -- "--check" "$capture" >/dev/null; then
  echo "verify unexpectedly entered check mode" >&2
  exit 1
fi

ansible_playbook=""
if command -v ansible-playbook >/dev/null 2>&1; then
  ansible_playbook="$(command -v ansible-playbook)"
elif [[ -x "$repo_root/../.tooling/ansible/bin/ansible-playbook" ]]; then
  ansible_playbook="$repo_root/../.tooling/ansible/bin/ansible-playbook"
fi
[[ -n "$ansible_playbook" ]] || { echo "ansible-playbook is required for rehearsal contracts" >&2; exit 69; }

ANSIBLE_HOME="$run_dir/ansible" ANSIBLE_LOCAL_TEMP="$run_dir/local" ANSIBLE_REMOTE_TEMP=/tmp/tuinstra-rehearsal-ansible \
  "$ansible_playbook" --inventory "$inventory" --limit rehearsal01 --syntax-check \
  "$repo_root/infra/ansible/production-host-baseline.yml" >/dev/null
ANSIBLE_HOME="$run_dir/ansible" ANSIBLE_LOCAL_TEMP="$run_dir/local" ANSIBLE_REMOTE_TEMP=/tmp/tuinstra-rehearsal-ansible \
  "$ansible_playbook" --inventory "$inventory" --limit rehearsal01 --syntax-check \
  "$repo_root/infra/ansible/production-host-verify.yml" >/dev/null

echo "production host rehearsal contract passed"

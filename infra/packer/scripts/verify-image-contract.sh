#!/usr/bin/env bash
set -euo pipefail

# libguestfs may omit local tool directories from its command environment.
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

required_commands=(
  composer
  corepack
  curl
  docker
  git
  gh
  skopeo
  qemu-aarch64-static
  jq
  node
  npm
  npx
  php8.3
  php8.4
  trivy
  unzip
)

for command in "${required_commands[@]}"; do
  command -v "$command" >/dev/null || {
    echo "image contract missing command: $command" >&2
    exit 1
  }
done

docker buildx version
docker compose version
node --version | grep -E '^v24\.' >/dev/null
php8.3 --version | grep -E '^PHP 8\.3\.' >/dev/null
php8.4 --version | grep -E '^PHP 8\.4\.' >/dev/null
# Fail the image build before a no-new-privileges job discovers a missing module.
php8.3 -r '
  foreach (["ctype", "iconv", "openssl", "pdo", "pdo_sqlite", "sqlite3", "Zend OPcache", "zip", "xdebug"] as $ext) {
    if (!extension_loaded($ext)) { fwrite(STDERR, "PHP 8.3 missing $ext\n"); exit(1); }
  }
  try {
    $pdo = new PDO("sqlite::memory:");
    $pdo->exec("CREATE TABLE ci_probe (value INTEGER NOT NULL)");
    $pdo->exec("INSERT INTO ci_probe (value) VALUES (1)");
    if ((int) $pdo->query("SELECT value FROM ci_probe")->fetchColumn() !== 1) {
      throw new RuntimeException("query mismatch");
    }
  } catch (Throwable $error) { fwrite(STDERR, "PHP 8.3 SQLite PDO unusable\n"); exit(1); }
'
XDEBUG_MODE=coverage php8.3 -r 'if (!in_array("coverage", xdebug_info("mode"), true)) { fwrite(STDERR, "PHP 8.3 coverage mode unavailable\n"); exit(1); }'
skopeo --version
gh --version
qemu-aarch64-static --version
test -r /proc/sys/fs/binfmt_misc/qemu-aarch64
grep -E '^flags:.*F' /proc/sys/fs/binfmt_misc/qemu-aarch64 >/dev/null

php8.4 -r 'foreach (["ctype", "fileinfo", "iconv", "intl", "mbstring", "openssl", "pdo_pgsql", "zip", "dom", "SimpleXML", "xml", "xmlwriter", "tokenizer"] as $ext) { if (!extension_loaded($ext)) { fwrite(STDERR, "PHP 8.4 missing $ext\n"); exit(1); } }'
composer --version | grep -E '^Composer version 2\.' >/dev/null
playwright --version
chromium --version
trivy --version
test -x /opt/actions-runner/run.sh
test -x /usr/local/bin/run-jit-runner
test "$(stat -c '%U:%G:%a' /opt/actions-runner)" = "root:root:755"
for runtime_file in .runner .credentials .credentials_rsaparams .runner_migrated .credentials_migrated; do
  runtime_path="/opt/actions-runner/$runtime_file"
  if [[ -e "$runtime_path" || -L "$runtime_path" ]]; then
    echo "image contract found pre-existing runner state: $runtime_path" >&2
    exit 1
  fi
done
unsafe_runner_entry="$(find /opt/actions-runner -xdev \
  \( -path /opt/actions-runner/_diag -o -path /opt/actions-runner/_work \) -prune -o \
  \( \( ! -user root -o ! -group root \) -o \( \( -type f -o -type d \) -perm /022 \) \) \
  -print -quit)"
test -z "$unsafe_runner_entry" || {
  echo "image contract found mutable non-runtime runner entry: $unsafe_runner_entry" >&2
  exit 1
}
test -d /opt/ms-playwright
test -x /usr/local/bin/chromium
readlink -f /usr/local/bin/chromium | grep -E '^/opt/ms-playwright/' >/dev/null
test -s /etc/ci-runner-image-manifest
php83_manifest_version="$(sed -n 's/^php83=//p' /etc/ci-runner-image-manifest)"
php83_sqlite_manifest_version="$(sed -n 's/^php83_sqlite=//p' /etc/ci-runner-image-manifest)"
test -n "$php83_manifest_version"
test "$php83_manifest_version" = "$php83_sqlite_manifest_version"
test "$php83_sqlite_manifest_version" = "$(dpkg-query -W -f='${Version}' php8.3-sqlite3)"

for package in "skopeo" "gh" "php8.3-xdebug" "qemu-user-static"; do
  case "$package" in
    php8.3-xdebug) manifest_key=php83_xdebug ;;
    qemu-user-static) manifest_key=qemu_user_static ;;
    *) manifest_key=$package ;;
  esac
  declared="$(sed -n "s/^${manifest_key}=//p" /etc/ci-runner-image-manifest)"
  test -n "$declared"
  test "$declared" = "$(dpkg-query -W -f='${Version}' "$package")"
done

echo "immutable runner image contract passed"

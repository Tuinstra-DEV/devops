#!/usr/bin/env bash
# Runs only inside a candidate via virt-customize --run-command 'bash ...'.
set -euo pipefail
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export DEBIAN_FRONTEND=noninteractive
expected='8.3.35-1+ubuntu24.04.1+deb.sury.org+1'
test "$(dpkg-query -W -f='${Version}' php8.3-cli)" = "$expected"
test "$(sed -n 's/^php83=//p' /etc/ci-runner-image-manifest)" = "$expected"
if dpkg-query -W -f='${db:Status-Status}' php8.3-mysql 2>/dev/null | grep -x installed; then
  echo 'Source unexpectedly already contains php8.3-mysql' >&2
  exit 1
fi
test ! -e /etc/php/8.3/mods-available/pdo_mysql.ini
test ! -e /etc/php/8.3/cli/conf.d/20-pdo_mysql.ini

before=$(mktemp -d /tmp/dev54-image-before.XXXXXX)
dpkg-query -W -f='${binary:Package}\t${Version}\t${db:Status-Status}\n' | sort > "$before/packages"
cp /etc/ci-runner-image-manifest "$before/manifest"
sha256sum /usr/bin/php8.3 /usr/bin/php8.4 /usr/bin/node /usr/bin/docker \
  /usr/local/bin/composer /opt/actions-runner/bin/Runner.Listener \
  /etc/systemd/system/ci-runner-job.service /usr/local/bin/run-jit-runner > "$before/runtime.sha256"

# Signed repositories and exact CLI-matched version. No upgrade/downgrade or
# fallback version; a vanished pin or additional package change blocks output.
apt-get update
apt-get install --yes --no-upgrade --no-install-recommends "php8.3-mysql=$expected"
phpenmod -v 8.3 -s cli mysqlnd mysqli pdo_mysql
sha256sum --check --strict "$before/runtime.sha256"
dpkg-query -W -f='${binary:Package}\t${Version}\t${db:Status-Status}\n' | sort > "$before/packages.after"
python3 - "$before/packages" "$before/packages.after" "$expected" <<'PY'
import sys
from pathlib import Path
def packages(path):
    return {p: (v, s) for p, v, s in (row.split('\t') for row in Path(path).read_text().splitlines())}
before, after = packages(sys.argv[1]), packages(sys.argv[2])
assert after.pop('php8.3-mysql') == (sys.argv[3], 'installed')
assert after == before, 'Unrelated package inventory changed'
print('package_delta=only_php8.3-mysql')
PY
cmp /etc/ci-runner-image-manifest "$before/manifest"
printf 'php83_mysql=%s\n' "$expected" >> /etc/ci-runner-image-manifest
runuser -u ci-runner -- php8.3 -r '
if (!extension_loaded("pdo_mysql") || !in_array("mysql", PDO::getAvailableDrivers(), true)) { exit(1); }
$pdo = new PDO("sqlite::memory:");
if ((int) $pdo->query("SELECT 1")->fetchColumn() !== 1) { exit(1); }
echo "offline_php83_mysql_sqlite=passed\n";
'
apt-get clean
find /var/lib/apt/lists -mindepth 1 -delete
rm -f "$before/packages" "$before/packages.after" "$before/manifest" "$before/runtime.sha256"
rmdir "$before"

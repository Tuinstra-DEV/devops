#!/usr/bin/env bash
# Run as the existing unprivileged runner user, never as an installation step.
set -euo pipefail
[[ "$(/usr/bin/php8.3 -r 'echo PHP_MAJOR_VERSION,".",PHP_MINOR_VERSION;')" == 8.3 ]]
/usr/bin/php8.3 -r '
foreach (["ctype", "iconv", "openssl", "pdo_sqlite", "sqlite3", "pdo_mysql", "Zend OPcache", "zip"] as $extension) {
    if (!extension_loaded($extension)) { fwrite(STDERR, "Missing baked extension: $extension\n"); exit(1); }
}
foreach (["sqlite", "mysql"] as $driver) {
    if (!in_array($driver, PDO::getAvailableDrivers(), true)) { fwrite(STDERR, "Missing PDO driver: $driver\n"); exit(1); }
}
$db = new PDO("sqlite::memory:");
if ((int) $db->query("SELECT 1")->fetchColumn() !== 1) { exit(1); }
'
/usr/bin/php8.3 /usr/local/bin/composer --version --no-ansi
docker info --format '{{.ServerVersion}}'
echo 'Gate backend runtime prerequisites passed; no packages installed.'

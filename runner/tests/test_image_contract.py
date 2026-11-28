import io
import re
import shutil
import tarfile
from pathlib import Path
import subprocess
import tempfile
import unittest

CONTRACT = Path(__file__).resolve().parents[2] / "infra/packer/scripts/verify-image-contract.sh"
INSTALLER = Path(__file__).resolve().parents[2] / "infra/packer/scripts/install-runner.sh"


class ImageContractTests(unittest.TestCase):
    def test_needrestart_excludes_only_the_jit_runner_unit(self):
        installer = INSTALLER.read_text()
        contract = CONTRACT.read_text()
        unit = (INSTALLER.parents[3] / 'runner/systemd/ci-runner-job.service').read_text()
        expected = r'$nrconf{override_rc}{qr(^ci-runner-job\.service$)} = 0;'
        self.assertIn('sudo tee /etc/needrestart/conf.d/90-ci-runner-job.conf', installer)
        self.assertIn(expected, installer)
        self.assertIn('needrestart/conf.d/90-ci-runner-job.conf', contract)
        self.assertIn(expected, contract)
        self.assertIn("/etc/apt/apt.conf.d/99needrestart", contract)
        self.assertIn("needrestart/apt-pinvoke -m u", contract)
        self.assertIn('RuntimeMaxSec=7200', unit)
        self.assertIn('ExecStopPost=+/usr/bin/systemctl poweroff', unit)

        perl = shutil.which('perl')
        if not perl:
            self.skipTest('Perl is required to evaluate needrestart configuration syntax')
        match = re.search(
            r"sudo tee /etc/needrestart/conf\.d/90-ci-runner-job\.conf >/dev/null <<'NEEDRESTART'\n"
            r"(.*?)\nNEEDRESTART",
            installer,
            re.DOTALL,
        )
        self.assertIsNotNone(match, 'exact needrestart drop-in heredoc not found')
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / '90-ci-runner-job.conf'
            config.write_text(match.group(1) + '\n')
            script = r'''
use strict; use warnings;
our %nrconf;
my $path = shift;
do $path;
die $@ if $@;
my @rules = keys %{ $nrconf{override_rc} // {} };
die "unexpected override count\n" unless @rules == 1;
my $rule = $rules[0];
die "runner did not match\n" unless "ci-runner-job.service" =~ $rule;
die "runner override is not false\n" unless $nrconf{override_rc}{$rule} == 0;
for my $other ("ci-runner-jobXservice", "systemd-journald.service", "ci-runner-job.service.d") {
  die "unexpected service matched: $other\n" if $other =~ $rule;
}
'''
            result = subprocess.run([perl, '-e', script, str(config)], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_gate_php83_mysql_matches_cli_pin_and_requires_pdo_driver(self):
        installer = INSTALLER.read_text()
        self.assertIn('"php8.3-mysql=${PHP83_VERSION}"', installer)
        self.assertIn('php83_mysql=${PHP83_VERSION}', installer)
        source = CONTRACT.read_text()
        self.assertIn('"pdo_mysql"', source)
        self.assertIn('in_array("mysql", PDO::getAvailableDrivers(), true)', source)
        self.assertIn('php83_mysql=', source)
        self.assertIn("dpkg-query -W -f='${Version}' php8.3-mysql", source)
        self.assertIn("dpkg-query -W -f='${Version}' php8.3-cli", source)

    def test_gate_php83_sqlite_is_installed_and_probed(self):
        installer = INSTALLER.read_text()
        self.assertIn('"php8.3-sqlite3=${PHP83_VERSION}"', installer)
        self.assertIn('php83_sqlite=${PHP83_VERSION}', installer)
        source = CONTRACT.read_text()
        self.assertIn('"pdo_sqlite"', source)
        self.assertIn('"sqlite3"', source)
        self.assertIn('sqlite::memory:', source)
        self.assertIn('php83_sqlite=', source)
        self.assertIn('dpkg-query -W -f=', source)

    def command_preflight(self, missing_composer=False):
        if not shutil.which("docker"):
            self.skipTest("Docker is required for the Linux PATH regression")
        image = subprocess.run(["docker", "image", "inspect", "ubuntu:24.04", "--format", "{{.Id}}"], capture_output=True, text=True, timeout=10)
        if image.returncode:
            self.skipTest("Cached ubuntu:24.04 image unavailable; no automatic pull")
        source = CONTRACT.read_text().split("\ndocker buildx version", 1)[0]
        archive = io.BytesIO()
        files = {"preflight": source}
        for name in ("composer", "corepack", "curl", "docker", "git", "gh", "skopeo", "qemu-aarch64-static", "jq", "node", "npm", "npx", "php8.3", "php8.4", "trivy", "unzip"):
            if name != "composer" or not missing_composer:
                files[name] = "#!/bin/sh\nexit 0\n"
        with tarfile.open(fileobj=archive, mode="w") as tar:
            for name, contents in files.items():
                payload = contents.encode()
                member = tarfile.TarInfo(name)
                member.mode = 0o755
                member.size = len(payload)
                tar.addfile(member, io.BytesIO(payload))
        result = subprocess.run(["docker", "run", "--rm", "-i", "--network", "none", "--entrypoint", "/bin/bash", "--env", "PATH=/usr/sbin:/usr/bin:/sbin:/bin", image.stdout.strip(), "-c", "tar -x -C /usr/local/bin && /bin/bash /usr/local/bin/preflight"], input=archive.getvalue(), capture_output=True, timeout=20)
        result.stdout = result.stdout.decode()
        result.stderr = result.stderr.decode()
        return result

    def test_restricted_guest_path_finds_installed_local_commands(self):
        result = self.command_preflight()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_missing_composer_still_fails(self):
        result = self.command_preflight(missing_composer=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("image contract missing command: composer", result.stderr)

    def version_check(self, version):
        line = next(line for line in CONTRACT.read_text().splitlines() if line.startswith("node --version |"))
        producer = "node() { printf '%s\\n' '" + version + "'; seq 1 100000; }; "
        return subprocess.run(["bash", "-o", "pipefail", "-c", producer + line], capture_output=True, text=True, timeout=10)

    def test_version_check_consumes_output_under_pipefail(self):
        result = self.version_check("v24.1.0")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_wrong_version_still_fails(self):
        self.assertNotEqual(self.version_check("v22.1.0").returncode, 0)


if __name__ == "__main__":
    unittest.main()

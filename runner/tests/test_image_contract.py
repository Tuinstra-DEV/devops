import io
import tarfile
from pathlib import Path
import shutil
import subprocess
import unittest

CONTRACT = Path(__file__).resolve().parents[2] / "infra/packer/scripts/verify-image-contract.sh"
INSTALLER = Path(__file__).resolve().parents[2] / "infra/packer/scripts/install-runner.sh"


class ImageContractTests(unittest.TestCase):
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
        for name in ("composer", "corepack", "curl", "docker", "git", "jq", "node", "npm", "npx", "php8.3", "php8.4", "trivy", "unzip"):
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

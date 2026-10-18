"""Pinned image tools must be usable before the hardened job service starts."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
INSTALLER = ROOT / 'infra/packer/scripts/install-runner.sh'
CONTRACT = ROOT / 'infra/packer/scripts/verify-image-contract.sh'
ADDED = ['SKOPEO_VERSION', 'GH_VERSION', 'PHP83_XDEBUG_VERSION', 'QEMU_USER_STATIC_VERSION']
EXISTING = ['RUNNER_VERSION', 'RUNNER_SHA256', 'DOCKER_KEY_SHA256', 'DOCKER_CE_VERSION',
            'DOCKER_CLI_VERSION', 'CONTAINERD_VERSION', 'BUILDX_VERSION', 'COMPOSE_VERSION',
            'NODE_KEY_SHA256', 'NODE_VERSION', 'TRIVY_KEY_SHA256', 'TRIVY_VERSION',
            'PHP_KEY_SHA256', 'PHP83_VERSION', 'PHP84_VERSION', 'COMPOSER_VERSION',
            'COMPOSER_SHA384', 'PLAYWRIGHT_VERSION']


class GuestToolchainExtensionTests(unittest.TestCase):
    def preflight(self, versions):
        # Execute the real installer before its first privileged statement.
        # No package installation, network or host binfmt registration occurs.
        source = INSTALLER.read_text().split('\nsudo install -d', 1)[0]
        with tempfile.TemporaryDirectory() as directory:
            dpkg = Path(directory) / 'dpkg'
            dpkg.write_text('#!/bin/sh\nprintf "%s\\n" amd64\n')
            dpkg.chmod(0o700)
            environment = {**os.environ, **dict.fromkeys(EXISTING, '1.2.3'),
                           'PATH': directory + ':' + os.environ['PATH']}
            for name in ADDED:
                environment.pop(name, None)
            environment.update(versions)
            return subprocess.run(['bash', '-c', source], env=environment,
                                  capture_output=True, text=True, timeout=10)

    def test_missing_tool_pin_fails_before_image_mutation(self):
        for missing in ADDED:
            with self.subTest(missing=missing):
                pins = dict.fromkeys(ADDED, '1:2.3.4-0ubuntu1')
                del pins[missing]
                result = self.preflight(pins)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(missing, result.stderr)

    def test_floating_or_injected_package_version_is_rejected(self):
        for bad in ['latest', '1.2.*', '1.2 --allow-unauthenticated', '1.2\nOTHER=3']:
            with self.subTest(version=bad):
                pins = dict.fromkeys(ADDED, '1:2.3.4-0ubuntu1')
                pins['SKOPEO_VERSION'] = bad
                self.assertNotEqual(self.preflight(pins).returncode, 0)
        self.assertEqual(self.preflight(dict.fromkeys(ADDED, '1:2.3.4-0ubuntu1')).returncode, 0)

    def test_image_contract_checks_real_tool_packages_and_coverage_mode(self):
        installer = INSTALLER.read_text()
        contract = CONTRACT.read_text()
        for package, variable in [('skopeo', 'SKOPEO_VERSION'), ('gh', 'GH_VERSION'),
                                  ('php8.3-xdebug', 'PHP83_XDEBUG_VERSION'),
                                  ('qemu-user-static', 'QEMU_USER_STATIC_VERSION')]:
            with self.subTest(package=package):
                self.assertIn(f'"{package}=${{{variable}}}"', installer)
                self.assertIn(f'"{package}"', contract)
                self.assertIn("dpkg-query -W -f='${Version}'", contract)
        self.assertIn('XDEBUG_MODE=coverage php8.3', contract)
        self.assertIn('xdebug_info("mode")', contract)
        self.assertIn('/proc/sys/fs/binfmt_misc/qemu-aarch64', contract)
        self.assertIn('flags:.*F', contract)
        unit = (ROOT / 'runner/systemd/ci-runner-job.service').read_text()
        for setting in ['NoNewPrivileges=yes', 'ProtectSystem=strict', 'ProtectHome=yes', 'User=ci-runner']:
            self.assertIn(setting, unit)


    def test_boot_guard_refuses_missing_disabled_or_nonfixed_handlers_before_jit(self):
        script = (ROOT / 'runner/guest/run-jit-runner.sh').read_text()
        self.assertLess(script.index('verify_aarch64_binfmt /proc/'), script.index('jit_file='))
        prefix = script.split('\nverify_aarch64_binfmt /proc/', 1)[0]
        with tempfile.TemporaryDirectory() as directory:
            handler = Path(directory) / 'handler'
            for text, accepted in [('enabled\nflags: POCF\n', True),
                                   ('disabled\nflags: POCF\n', False),
                                   ('enabled\nflags: POC\n', False)]:
                handler.write_text(text)
                result = subprocess.run(['bash', '-c', prefix + '\nverify_aarch64_binfmt "$1"',
                                         '_', str(handler)], capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode == 0, accepted, result.stderr)
            handler.unlink()
            result = subprocess.run(['bash', '-c', prefix + '\nverify_aarch64_binfmt "$1"',
                                     '_', str(handler)], capture_output=True, text=True, timeout=10)
            self.assertNotEqual(result.returncode, 0)


if __name__ == '__main__':
    unittest.main()

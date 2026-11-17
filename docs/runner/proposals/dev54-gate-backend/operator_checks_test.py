import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))
import operator_check as checks


class OperatorChecksTests(unittest.TestCase):
    def credential_context(self, root):
        directory = root / 'credentials'
        directory.mkdir(mode=0o700)
        path = directory / 'org_github_token'
        path.write_text('synthetic-private-sentinel\n')
        path.chmod(0o440)
        bindings = {'type': 'a(ss)', 'data': [
            ['github_token', '/etc/ci-runner/github.token'],
            ['org_github_token', '/etc/ci-runner/org-github.token']]}

        def systemd(args, **kwargs):
            if args[0] == 'busctl': return json.dumps(bindings)
            self.assertEqual(args, ['systemctl', 'show', 'ci-runner-manager.service', '-p', 'MainPID', '--value'])
            return '123\n'

        def environment(pid):
            self.assertEqual(pid, '123')
            return b'UNRELATED_PRIVATE=synthetic-environment-sentinel\0CREDENTIALS_DIRECTORY=' + bytes(directory) + b'\0'

        return path, systemd, environment

    def test_loaded_credential_uses_real_reader_and_only_restores_verified_service_context(self):
        # Exercise the actual deployed reader, whose fingerprint policy is checked
        # by read_effective before the live operator ever gets here.
        # This reader is identical to the fingerprint-verified deployed reader.
        manager_path = Path(__file__).resolve().parents[4] / 'runner/manager/ci_runner_manager.py'
        spec = importlib.util.spec_from_file_location('credential_reader_test', manager_path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            path, systemd, environment = self.credential_context(Path(directory))
            with mock.patch.dict(checks.os.environ, {}, clear=True):
                with self.assertRaises(module.RunnerError):
                    module.read_token(path)
                with mock.patch.object(checks, 'SYSTEMD_CREDENTIAL_DIRECTORY', path.parent), \
                        mock.patch.object(checks, 'command', side_effect=systemd), \
                        mock.patch.object(checks, 'manager_environment', side_effect=environment):
                    before = path.stat()
                    self.assertEqual(checks.loaded_manager_credential({'org_github_token_file': str(path)}, module),
                                     'synthetic-private-sentinel')
                    self.assertNotIn('CREDENTIALS_DIRECTORY', checks.os.environ)
                    self.assertNotIn('UNRELATED_PRIVATE', checks.os.environ)
                    self.assertEqual(path.stat().st_mode, before.st_mode)
                    self.assertEqual(path.stat().st_mtime_ns, before.st_mtime_ns)

    def test_credential_file_context_and_reader_failures_are_distinct(self):
        with tempfile.TemporaryDirectory() as directory:
            path, systemd, environment = self.credential_context(Path(directory))
            module = mock.Mock()
            with mock.patch.object(checks, 'SYSTEMD_CREDENTIAL_DIRECTORY', path.parent), \
                    mock.patch.object(checks, 'command', side_effect=systemd), \
                    mock.patch.object(checks, 'manager_environment', side_effect=environment) as env, \
                    mock.patch.dict(checks.os.environ, {'CREDENTIALS_DIRECTORY': '/unrelated'}, clear=True):
                with mock.patch.object(Path, 'open', side_effect=PermissionError('private sentinel')):
                    with self.assertRaises(checks.Blocked) as caught:
                        checks.loaded_manager_credential({'org_github_token_file': str(path)}, module)
                self.assertEqual(caught.exception.code, 'credential_file_unreachable')
                module.read_token.assert_not_called()
                env.return_value = b'PRIVATE=synthetic-private-sentinel\0'
                env.side_effect = None
                with self.assertRaises(checks.Blocked) as caught:
                    checks.loaded_manager_credential({'org_github_token_file': str(path)}, module)
                self.assertEqual(caught.exception.code, 'credential_service_context_missing')
                module.read_token.assert_not_called()
                env.side_effect = environment
                module.read_token.side_effect = RuntimeError('synthetic-private-sentinel')
                with self.assertRaises(checks.Blocked) as caught:
                    checks.loaded_manager_credential({'org_github_token_file': str(path)}, module)
                self.assertEqual(caught.exception.code, 'credential_reader_rejected')
                self.assertEqual(checks.os.environ['CREDENTIALS_DIRECTORY'], '/unrelated')

    def test_unverified_binding_context_or_service_restart_never_grants_a_read(self):
        with tempfile.TemporaryDirectory() as directory:
            path, systemd, environment = self.credential_context(Path(directory))
            cfg = {'org_github_token_file': str(path)}
            module = mock.Mock()
            with mock.patch.object(checks, 'SYSTEMD_CREDENTIAL_DIRECTORY', path.parent):
                with mock.patch.object(checks, 'command', return_value='{"type":"a(ss)","data":[]}'), \
                        mock.patch.object(checks, 'manager_environment') as env:
                    with self.assertRaises(checks.Blocked) as caught:
                        checks.loaded_manager_credential(cfg, module)
                    self.assertEqual(caught.exception.code, 'credential_service_binding_differs')
                    env.assert_not_called()
                    module.read_token.assert_not_called()
                with mock.patch.object(checks, 'command', side_effect=systemd), \
                        mock.patch.object(checks, 'manager_environment', return_value=b'CREDENTIALS_DIRECTORY=/unrelated\0'):
                    with self.assertRaises(checks.Blocked) as caught:
                        checks.loaded_manager_credential(cfg, module)
                    self.assertEqual(caught.exception.code, 'credential_service_context_differs')
                    module.read_token.assert_not_called()
                with mock.patch.object(checks, 'command', side_effect=systemd), \
                        mock.patch.object(checks, 'manager_environment', side_effect=PermissionError('private sentinel')):
                    with self.assertRaises(checks.Blocked) as caught:
                        checks.loaded_manager_credential(cfg, module)
                    self.assertEqual(caught.exception.code, 'credential_service_context_unavailable')
                    module.read_token.assert_not_called()
                binding = systemd(['busctl'])
                with mock.patch.object(checks, 'command', side_effect=[binding, '123', '124']), \
                        mock.patch.object(checks, 'manager_environment', side_effect=environment), \
                        mock.patch.dict(checks.os.environ, {}, clear=True):
                    with self.assertRaises(checks.Blocked) as caught:
                        checks.loaded_manager_credential(cfg, module)
                    self.assertEqual(caught.exception.code, 'credential_service_context_changed')
                    self.assertNotIn('CREDENTIALS_DIRECTORY', checks.os.environ)

    def credential_fixture(self):
        manifest = {'gate_remote_head': 'a' * 40, 'gate_base': 'b' * 40}
        client = mock.Mock()
        def get(method, path):
            self.assertEqual(method, 'GET')
            if path.endswith('/pulls/444'):
                return {'state': 'open', 'head': {'sha': 'a' * 40}, 'base': {'sha': 'b' * 40}, 'merge_commit_sha': 'c' * 40}
            if path.endswith('/branches/develop'): return {'protected': True, 'commit': {'sha': 'b' * 40}}
            if '/git/commits/' in path: return {'sha': 'c' * 40, 'parents': [{'sha': 'a' * 40}, {'sha': 'b' * 40}]}
            return {}
        client.request.side_effect = get
        module = mock.Mock()
        module.read_token.return_value = 'synthetic-private-sentinel'
        module.GitHubClient.return_value = client
        return manifest, client, module

    def test_existing_credentials_are_used_for_get_only_and_never_returned(self):
        manifest, client, module = self.credential_fixture()
        with mock.patch.object(checks, 'loaded_manager_credential', return_value='synthetic-private-sentinel'):
            result = checks.credential_checks({'org_github_token_file': '/synthetic/credential'}, manifest, module)
        self.assertTrue(all(result.values()))
        self.assertNotIn('synthetic-private-sentinel', json.dumps(result))
        self.assertEqual(client.request.call_count, 5)
        client.verify_org_group.assert_called_once()

    def test_credential_403_and_candidate_drift_stop_without_disclosing_response(self):
        manifest, client, module = self.credential_fixture()
        client.request.side_effect = RuntimeError('HTTP 403 synthetic-private-sentinel')
        with mock.patch.object(checks, 'loaded_manager_credential', return_value='synthetic-private-sentinel'), self.assertRaises(checks.Blocked) as caught:
            checks.credential_checks({'org_github_token_file': '/synthetic/credential'}, manifest, module)
        self.assertEqual(caught.exception.http_status, 403)
        self.assertEqual(caught.exception.code, 'credential_get_pull_request_failed')
        manifest, client, module = self.credential_fixture()
        manifest['gate_remote_head'] = 'd' * 40
        with mock.patch.object(checks, 'loaded_manager_credential', return_value='synthetic-private-sentinel'), self.assertRaises(checks.Blocked) as caught:
            checks.credential_checks({'org_github_token_file': '/synthetic/credential'}, manifest, module)
        self.assertEqual(caught.exception.code, 'gate_candidate_differs')
        self.assertEqual(client.request.call_count, 1)

    def test_credentials_never_go_to_another_api_origin(self):
        _, _, module = self.credential_fixture()
        with self.assertRaises(checks.Blocked):
            checks.credential_checks({'github_api_url': 'https://example.invalid'}, {}, module)
        module.read_token.assert_not_called()

    def test_image_selector_and_full_content_digest_are_bound(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            payload = b'synthetic sealed image'
            image = root / ('ubuntu-24.04-runner-' + hashlib.sha256(payload).hexdigest() + '.qcow2')
            image.write_bytes(payload)
            helper = root / 'helper'; helper.write_bytes(b'synthetic helper')
            selector = root / 'current'; selector.symlink_to(image)
            (root / 'operator-manifest.json').write_text(json.dumps({
                'active_image': str(image), 'helper_sha256': hashlib.sha256(helper.read_bytes()).hexdigest()}))
            self.assertEqual(checks.active_image(root, helper, selector), image)
            image.write_bytes(payload + b'changed')
            with self.assertRaises(checks.Blocked) as caught:
                checks.active_image(root, helper, selector)
            self.assertEqual(caught.exception.code, 'selected_runner_image_hash_differs')

    def test_php_is_probed_in_readonly_image_and_missing_mysql_blocks(self):
        result = json.dumps({'php_version': '8.3', 'php_version_full': '8.3.36', 'pdo_mysql': False, 'pdo_sqlite': True})
        with mock.patch.object(checks, 'command', return_value=result) as command:
            with self.assertRaises(checks.Blocked) as caught:
                checks.image_php_check(Path('/synthetic/image.qcow2'))
        self.assertEqual(caught.exception.code, 'selected_image_pdo_mysql_missing')
        args = command.call_args.args[0]
        self.assertEqual(args[:3], ['guestfish', '--ro', '--format=qcow2'])
        self.assertIn('/synthetic/image.qcow2', args)
        self.assertIn('runuser -u ci-runner', args[-1])
        self.assertIn('/usr/bin/php8.3', args[-1])

    def candidate_result(self):
        # Exercise the recorded validation artifact's actual schema rather than
        # duplicating it in a synthetic fixture that can silently drift.
        return json.loads((Path(__file__).parent / 'image-family-update-result.json').read_text())

    def test_candidate_preflight_binds_source_candidate_hash_and_vm_proof(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            package = root / 'package'; package.mkdir()
            source, candidate, helper = root / 'source.qcow2', root / 'candidate.qcow2', root / 'helper'
            source.touch(); candidate.touch(); helper.touch()
            selector = root / 'current'; selector.symlink_to(source)
            result = self.candidate_result(); result['candidate']['standalone_image_path'] = str(candidate)
            (package / 'image-family-update-result.json').write_text(json.dumps(result))
            hashes = {helper: 'fef9343a46c4a67ddf1e12db43aa65c2bbb5256e35bbdac14c9cb8a9ad3f1613',
                      source: '66eded13ed619c122421566de9b0a55ed49de5eb895caa40a2bf3b77d1b82bc4',
                      candidate: '5583aa0da997b0fd9001211b63b3abd24d51778645cbbccdc007444236bc7ec2'}
            with mock.patch.object(checks, 'sha256_file', side_effect=lambda path: hashes[Path(path)]):
                image, evidence = checks.gate_candidate_image(package, selector, helper, source, candidate, candidate, source)
            self.assertEqual(image, candidate)
            self.assertTrue(evidence['sha256_verified'])
            self.assertEqual(evidence['mariadb'], '12 tests / 99 assertions; 0 skipped')

    def test_candidate_preflight_rejects_stale_validation_and_wrong_candidate_content(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve(); package = root / 'package'; package.mkdir()
            source, candidate, helper = root / 'source', root / 'candidate', root / 'helper'
            source.touch(); candidate.touch(); helper.touch(); selector = root / 'current'; selector.symlink_to(source)
            hashes = {helper: 'fef9343a46c4a67ddf1e12db43aa65c2bbb5256e35bbdac14c9cb8a9ad3f1613',
                      source: '66eded13ed619c122421566de9b0a55ed49de5eb895caa40a2bf3b77d1b82bc4',
                      candidate: '5583aa0da997b0fd9001211b63b3abd24d51778645cbbccdc007444236bc7ec2'}
            with mock.patch.object(checks, 'sha256_file', side_effect=lambda path: hashes[Path(path)]):
                for field in ('vm_exit_code', 'registered_as_runner',
                              'host_mounts_or_docker_socket', 'host_port_forwarding', 'missing_vm'):
                    with self.subTest(field=field):
                        result = self.candidate_result()
                        vm = result['isolated_validation']
                        if field == 'vm_exit_code':
                            vm[field] = 1
                        elif field == 'missing_vm':
                            vm.pop('vm')
                        else:
                            vm['vm'][field] = True
                        result['candidate']['standalone_image_path'] = str(candidate)
                        (package / 'image-family-update-result.json').write_text(json.dumps(result))
                        with self.assertRaises(checks.Blocked) as caught:
                            checks.gate_candidate_image(package, selector, helper, source, candidate, candidate, source)
                        self.assertEqual(caught.exception.code, 'candidate_isolated_validation_evidence_differs')
                result = self.candidate_result()
                result['candidate']['standalone_image_path'] = str(candidate)
                (package / 'image-family-update-result.json').write_text(json.dumps(result))
                hashes[candidate] = '0' * 64
                with self.assertRaises(checks.Blocked) as caught:
                    checks.gate_candidate_image(package, selector, helper, source, candidate, candidate, source)
                self.assertEqual(caught.exception.code, 'candidate_image_hash_or_manifest_differs')

    def test_candidate_php_check_requires_exact_pdo_patch_and_uses_ro_guestfish_only(self):
        payload = json.dumps({'php_version': '8.3', 'php_version_full': '8.3.36',
                              'pdo_mysql': True, 'pdo_sqlite': True})
        with mock.patch.object(checks, 'command', return_value=payload) as command:
            result = checks.candidate_image_php_check(Path('/synthetic/candidate.qcow2'))
        self.assertEqual(result['php_version_full'], '8.3.36')
        self.assertEqual(command.call_count, 1)
        self.assertEqual(command.call_args.args[0][:3], ['guestfish', '--ro', '--format=qcow2'])

    def test_docker_never_falls_back_to_host_or_unrelated_vm(self):
        xml = '<domain><devices><disk><backingStore><source file="/synthetic/image.qcow2"/></backingStore></disk><channel><target name="org.qemu.guest_agent.0" state="disconnected"/></channel></devices></domain>'
        with mock.patch.object(checks, 'command', side_effect=['sanctuary-ci-test\n', xml]) as command:
            with self.assertRaises(checks.Blocked) as caught:
                checks.docker_guest_check(Path('/synthetic/image.qcow2'))
        self.assertEqual(caught.exception.code, 'selected_image_runner_guest_agent_disconnected')
        self.assertTrue(all(call.args[0][0] == 'virsh' for call in command.call_args_list))
        with mock.patch.object(checks, 'command', return_value=''):
            with self.assertRaises(checks.Blocked) as caught:
                checks.docker_guest_check(Path('/synthetic/image.qcow2'))
        self.assertEqual(caught.exception.code, 'no_running_runner_from_selected_image')

    def test_connected_guest_probe_uses_runner_user_and_bounded_info_only(self):
        import base64
        xml = '<domain><devices><disk><backingStore><source file="/synthetic/image.qcow2"/></backingStore></disk><channel><target name="org.qemu.guest_agent.0" state="connected"/></channel></devices></domain>'
        out = base64.b64encode(json.dumps({'runner_user': 'ci-runner', 'docker_daemon': True, 'storage_driver': 'overlay2'}).encode()).decode()
        with mock.patch.object(checks, 'command', side_effect=['sanctuary-ci-test\n', xml,
                json.dumps({'return': {'pid': 1}}), json.dumps({'return': {'exited': True, 'exitcode': 0, 'out-data': out}})]) as command:
            result = checks.docker_guest_check(Path('/synthetic/image.qcow2'))
        self.assertTrue(result['verified_in_runner_guest'])
        payload = json.loads(command.call_args_list[2].args[0][5])
        self.assertEqual(payload['arguments']['path'], '/usr/sbin/runuser')
        self.assertEqual(payload['arguments']['arg'][:2], ['-u', 'ci-runner'])

    def test_top_level_failure_never_discloses_config_or_arbitrary_exception_text(self):
        def service(args, **kwargs):
            if 'ActiveState' in args: return 'active'
            if 'User' in args: return 'ci-runner-manager'
            return 'argv[]=/usr/local/bin/ci-runner-manager --config /etc/ci-runner/manager.toml daemon --interval 30'
        output = io.StringIO()
        with mock.patch.object(sys, 'argv', ['operator_check.py', '--group-state', '/synthetic/group', '--repositories-state', '/synthetic/repos']), \
                mock.patch.object(checks.os, 'geteuid', return_value=0), \
                mock.patch.object(checks, 'command', side_effect=service), \
                mock.patch.object(Path, 'read_bytes', return_value=b'synthetic-private-config-sentinel'), \
                mock.patch.object(checks, 'inspect', side_effect=ValueError('synthetic-private-config-sentinel')), \
                contextlib.redirect_stdout(output):
            self.assertEqual(checks.main(), 1)
        result = json.loads(output.getvalue())
        self.assertFalse(result['all_checks_green'])
        self.assertNotIn('synthetic-private-config-sentinel', output.getvalue())


if __name__ == '__main__':
    unittest.main()

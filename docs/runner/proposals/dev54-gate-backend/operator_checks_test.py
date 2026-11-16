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
        result = checks.credential_checks({'org_github_token_file': '/synthetic/credential'}, manifest, module)
        self.assertTrue(all(result.values()))
        self.assertNotIn('synthetic-private-sentinel', json.dumps(result))
        self.assertEqual(client.request.call_count, 5)
        client.verify_org_group.assert_called_once()

    def test_credential_403_and_candidate_drift_stop_without_disclosing_response(self):
        manifest, client, module = self.credential_fixture()
        client.request.side_effect = RuntimeError('HTTP 403 synthetic-private-sentinel')
        with self.assertRaises(checks.Blocked) as caught:
            checks.credential_checks({'org_github_token_file': '/synthetic/credential'}, manifest, module)
        self.assertEqual(caught.exception.http_status, 403)
        self.assertEqual(caught.exception.code, 'credential_get_pull_request_failed')
        manifest, client, module = self.credential_fixture()
        manifest['gate_remote_head'] = 'd' * 40
        with self.assertRaises(checks.Blocked) as caught:
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
        result = json.dumps({'php_version': '8.3', 'pdo_mysql': False, 'pdo_sqlite': True})
        with mock.patch.object(checks, 'command', return_value=result) as command:
            with self.assertRaises(checks.Blocked) as caught:
                checks.image_php_check(Path('/synthetic/image.qcow2'))
        self.assertEqual(caught.exception.code, 'selected_image_pdo_mysql_missing')
        args = command.call_args.args[0]
        self.assertEqual(args[:3], ['guestfish', '--ro', '--format=qcow2'])
        self.assertIn('/synthetic/image.qcow2', args)
        self.assertIn('runuser -u ci-runner', args[-1])
        self.assertIn('/usr/bin/php8.3', args[-1])

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

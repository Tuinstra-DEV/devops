#!/usr/bin/env python3
"""WODIQ-specific contracts; existing adapter regression tests stay untouched."""
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import tarfile
import unittest
from unittest import mock

from scripts import test_production_backup as base

backup = base.backup
ROOT = Path(__file__).resolve().parents[1]


def marker():
    return {"algorithm": "wodiq-prisma-snapshot-v1", "ledger": [{
        "id": "synthetic-1", "migration_name": "20261114_init", "checksum": "a" * 64,
        "started_at": "2026-11-14T00:00:00+00:00", "finished_at": "2026-11-14T00:00:01+00:00",
        "rolled_back_at": None, "applied_steps_count": 1}],
        "row_counts": {"_prisma_migrations": 1, "User": 2}}


class WodiqBackupTests(unittest.TestCase):
    setUp = base.BackupTests.setUp
    tearDown = base.BackupTests.tearDown
    add_host_lock = base.BackupTests.add_host_lock

    def app(self):
        app = json.loads((ROOT / 'backup/profiles/prod02.json').read_text())['applications'][1]
        env = self.root / 'release.env'
        self.release_refs = {
            'wodiq-api': 'synthetic/api@sha256:' + 'a' * 64,
            'wodiq-management': 'synthetic/manage@sha256:' + 'b' * 64,
            'wodiq-sync-worker': 'synthetic/sync@sha256:' + 'c' * 64,
            'wodiq-analytics-retention': 'synthetic/retention@sha256:' + 'd' * 64,
            'wodiq-migrate': 'synthetic/migrate@sha256:' + 'e' * 64,
        }
        self.release_keys = dict(zip(self.release_refs, ('WODIQ_API_IMAGE', 'WODIQ_MANAGEMENT_IMAGE',
            'WODIQ_SYNC_WORKER_IMAGE', 'WODIQ_ANALYTICS_RETENTION_IMAGE', 'WODIQ_MIGRATION_IMAGE')))
        env.write_text(''.join(f'{self.release_keys[service]}={image}\n' for service, image in self.release_refs.items()))
        env.chmod(0o600)
        app['compose_env_file'] = str(env)
        return app

    def test_marker_rejects_corrupt_ledger_checksum_and_counts(self):
        self.assertEqual(backup.validate_wodiq_marker(marker()), marker())
        for mutate in (lambda m: m['ledger'][0].update(checksum='bad'),
                       lambda m: m['row_counts'].update(User=-1),
                       lambda m: m['row_counts'].update(_prisma_migrations=2),
                       lambda m: m['row_counts'].update(User=True)):
            value = marker()
            mutate(value)
            with self.assertRaises(backup.BackupError):
                backup.validate_wodiq_marker(value)

    def test_dump_and_marker_share_exported_snapshot_and_holder_closes(self):
        import io
        app = self.app()
        holder = mock.Mock(stdin=io.BytesIO(), stdout=io.BytesIO(b'00000003-00000012-1\n'))
        holder.poll.return_value = None
        observed = []

        def execute(argv, **kwargs):
            observed.append(argv)
            if 'pg_dump' in argv[-3]:
                kwargs['stdout'].write(b'PGDMPsynthetic')
                return mock.Mock(stdout=b'')
            sql = kwargs['stdin'].read().decode()
            self.assertIn("SET TRANSACTION SNAPSHOT '00000003-00000012-1'", sql)
            self.assertIn(backup.WODIQ_MARKER_SQL, sql)
            self.assertNotIn('two_factor', sql)
            return mock.Mock(stdout=json.dumps({k: v for k, v in marker().items() if k != 'algorithm'}).encode())

        with mock.patch.object(backup.subprocess, 'Popen', return_value=holder), \
             mock.patch.object(backup.select, 'select', return_value=([holder.stdout], [], [])), \
             mock.patch.object(backup, 'run', side_effect=execute):
            result = backup.wodiq_database_export(app, self.root / 'database.dump')
        self.assertEqual(result, marker())
        dump = observed[0]
        self.assertIn('--format=custom', dump[-3])
        self.assertIn('--dbname=wodiq120', dump[-3])
        self.assertIn('--snapshot="$1"', dump[-3])
        self.assertEqual(dump[-1], '00000003-00000012-1')
        holder.wait.assert_called_once()
        self.assertTrue(holder.stdin.closed)
        self.assertTrue(holder.stdout.closed)

    def test_holder_is_released_after_dump_failure(self):
        import io
        app = self.app()
        holder = mock.Mock(stdin=io.BytesIO(), stdout=io.BytesIO(b'00000003-00000012-1\n'))
        holder.poll.return_value = None
        with mock.patch.object(backup.subprocess, 'Popen', return_value=holder), \
             mock.patch.object(backup.select, 'select', return_value=([holder.stdout], [], [])), \
             mock.patch.object(backup, 'run', side_effect=backup.BackupError('synthetic dump failure')):
            with self.assertRaises(backup.BackupError):
                backup.wodiq_database_export(app, self.root / 'database.dump')
        self.assertTrue(holder.stdin.closed)
        holder.wait.assert_called_once()

    def image_runner(self, mapping, *, mismatched_service=None, missing_service=None, config_overrides=None):
        containers = {service: f'{index:064x}' for index, service in enumerate(mapping, 1)}
        images = {containers[service]: image for service, image in mapping.items()}
        if mismatched_service:
            images[containers[mismatched_service]] = 'synthetic/wrong@sha256:' + 'f' * 64

        def execute(argv, **kwargs):
            if 'config' in argv:
                services = {s: {'image': i, 'restart': 'unless-stopped'} for s, i in mapping.items()}
                services['wodiq-migrate']['profiles'] = ['release']
                services['wodiq-migrate']['restart'] = 'no'
                for service, overrides in (config_overrides or {}).items():
                    services[service].update(overrides)
                return mock.Mock(stdout=json.dumps({'services': services}).encode())
            if 'ps' in argv:
                return mock.Mock(stdout=(containers[argv[-1]] if argv[-1] != missing_service else '').encode())
            if argv[:2] == ['docker', 'inspect']:
                return mock.Mock(stdout=('sha256:' + images[argv[-1]].split('@sha256:')[1]).encode())
            if argv[:3] == ['docker', 'image', 'inspect']:
                image = next(image for image in images.values() if image.endswith('@' + argv[-1]))
                return mock.Mock(stdout=json.dumps([image]).encode())
            raise AssertionError('unexpected image command')
        return execute

    def release_mapping(self, app):
        return {**self.release_refs, 'postgres': app['approved_images']['postgres'],
                'redis': 'synthetic/redis@sha256:' + '1' * 64}

    def test_release_change_and_matching_running_containers_are_accepted(self):
        app = self.app()
        mapping = self.release_mapping(app)
        with mock.patch.object(backup, 'run', side_effect=self.image_runner(mapping)):
            first = backup.running_compose_images(app)
        self.assertEqual(first['wodiq-management'], self.release_refs['wodiq-management'])
        env = Path(app['compose_env_file'])
        changed = 'synthetic/manage@sha256:' + '9' * 64
        env.write_text(env.read_text().replace(self.release_refs['wodiq-management'], changed))
        mapping['wodiq-management'] = changed
        with mock.patch.object(backup, 'run', side_effect=self.image_runner(mapping)):
            self.assertEqual(backup.running_compose_images(app)['wodiq-management'], changed)
        self.assertEqual(set(app['approved_images']), {'postgres'})

    def test_container_different_from_release_is_rejected(self):
        app = self.app()
        with mock.patch.object(backup, 'run', side_effect=self.image_runner(
                self.release_mapping(app), mismatched_service='wodiq-management')):
            with self.assertRaisesRegex(backup.BackupError, 'approved image digest'):
                backup.running_compose_images(app)

    def test_release_tag_without_digest_is_rejected_before_docker(self):
        app = self.app()
        env = Path(app['compose_env_file'])
        env.write_text(env.read_text().replace(self.release_refs['wodiq-management'], 'synthetic/manage:latest'))
        with mock.patch.object(backup, 'run') as execute:
            with self.assertRaisesRegex(backup.BackupError, 'release image'):
                backup.running_compose_images(app)
        execute.assert_not_called()

    def test_missing_core_compose_or_runtime_service_is_rejected(self):
        app = self.app()
        mapping = self.release_mapping(app)
        del mapping['postgres']
        with mock.patch.object(backup, 'run', side_effect=self.image_runner(mapping)):
            with self.assertRaisesRegex(backup.BackupError, 'service mapping'):
                backup.running_compose_images(app)
        with mock.patch.object(backup, 'run', side_effect=self.image_runner(
                self.release_mapping(app), missing_service='wodiq-sync-worker')):
            with self.assertRaisesRegex(backup.BackupError, 'not running'):
                backup.running_compose_images(app)
        env = Path(app['compose_env_file'])
        env.write_text('\n'.join(line for line in env.read_text().splitlines() if not line.startswith('WODIQ_MIGRATION_IMAGE=')))
        with mock.patch.object(backup, 'run', side_effect=self.image_runner(self.release_mapping(app))):
            with self.assertRaisesRegex(backup.BackupError, 'release images'):
                backup.running_compose_images(app)

    def test_new_service_and_new_release_key_are_accepted_without_profile_edit(self):
        app = self.app()
        mapping = self.release_mapping(app)
        new_image = 'synthetic/account-erasure@sha256:' + '3' * 64
        mapping['wodiq-account-erasure-worker'] = new_image
        env = Path(app['compose_env_file'])
        env.write_text(env.read_text() + 'WODIQ_ACCOUNT_ERASURE_WORKER_IMAGE=' + new_image + '\n')
        with mock.patch.object(backup, 'run', side_effect=self.image_runner(mapping)):
            self.assertEqual(backup.running_compose_images(app)['wodiq-account-erasure-worker'], new_image)

    def test_new_compose_service_without_release_key_is_rejected(self):
        app = self.app()
        mapping = self.release_mapping(app)
        mapping['wodiq-account-erasure-worker'] = 'synthetic/account-erasure@sha256:' + '3' * 64
        with mock.patch.object(backup, 'run', side_effect=self.image_runner(mapping)):
            with self.assertRaisesRegex(backup.BackupError, 'release images'):
                backup.running_compose_images(app)

    def test_compose_metadata_excludes_one_shot_profile_and_disabled_services(self):
        app = self.app()
        for metadata in ({'restart': 'no'}, {'profiles': ['maintenance']}, {'scale': 0},
                         {'deploy': {'replicas': 0}}, {'deploy': {'mode': 'replicated-job'}}):
            with self.subTest(metadata=metadata):
                with mock.patch.object(backup, 'run', side_effect=self.image_runner(
                        self.release_mapping(app), missing_service='wodiq-sync-worker',
                        config_overrides={'wodiq-sync-worker': metadata})) as execute:
                    self.assertEqual(backup.running_compose_images(app)['wodiq-sync-worker'], self.release_refs['wodiq-sync-worker'])
                self.assertFalse(any('ps' in call.args[0] and call.args[0][-1] == 'wodiq-sync-worker'
                                     for call in execute.call_args_list))

    def test_every_release_image_key_requires_a_digest_even_when_unused(self):
        app = self.app()
        env = Path(app['compose_env_file'])
        valid = env.read_text()
        for extra in ('WODIQ_FUTURE_DISABLED_IMAGE=synthetic/future:latest',
                      'WODIQ_FUTURE_DISABLED_IMAGE = synthetic/future:latest',
                      'export WODIQ_FUTURE_DISABLED_IMAGE=synthetic/future:latest',
                      'WODIQ_FUTURE_DISABLED_IMAGE=', 'WODIQ_FUTURE_DISABLED_IMAGE'):
            with self.subTest(extra=extra):
                env.write_text(valid + extra + '\n')
                with mock.patch.object(backup, 'run') as execute:
                    with self.assertRaisesRegex(backup.BackupError, 'release image'):
                        backup.running_compose_images(app)
                execute.assert_not_called()

    def test_redis_configured_image_requires_a_digest(self):
        app = self.app()
        mapping = self.release_mapping(app)
        mapping['redis'] = 'synthetic/redis:latest'
        with mock.patch.object(backup, 'run', side_effect=self.image_runner(mapping)):
            with self.assertRaisesRegex(backup.BackupError, 'digest reference'):
                backup.running_compose_images(app)

    def test_postgres_dump_service_pin_stays_independent_of_release(self):
        app = self.app()
        mapping = self.release_mapping(app)
        mapping['postgres'] = 'synthetic/postgres@sha256:' + '2' * 64
        with mock.patch.object(backup, 'run', side_effect=self.image_runner(mapping)):
            with self.assertRaisesRegex(backup.BackupError, 'PostgreSQL image'):
                backup.running_compose_images(app)

    def test_compose_app_image_must_appear_in_release(self):
        app = self.app()
        mapping = self.release_mapping(app)
        mapping['wodiq-api'] = 'synthetic/unreleased@sha256:' + '8' * 64
        with mock.patch.object(backup, 'run', side_effect=self.image_runner(mapping)):
            with self.assertRaisesRegex(backup.BackupError, 'release images'):
                backup.running_compose_images(app)

    def test_compose_requires_private_release_env_and_explicit_database(self):
        app = self.app()
        command = backup.compose_command(app)
        self.assertIn('--env-file', command)
        self.assertIn(app['compose_env_file'], command)
        Path(app['compose_env_file']).chmod(0o644)
        with self.assertRaisesRegex(backup.BackupError, 'root-owned'):
            backup.compose_command(app)
        app['database_name'] = 'wrong'
        with self.assertRaisesRegex(backup.BackupError, 'database contract'):
            backup.wodiq_database_export(app, self.root / 'no.dump')

    def test_token_archive_rejects_changes_active_writers_symlinks_and_quota(self):
        source = self.root / 'tokens'
        source.mkdir()
        (source / 'tokens.json').write_bytes(b'encrypted-synthetic')
        target = self.root / 'tokens.tar'
        backup.wodiq_token_snapshot(source, target, 1024)
        with tarfile.open(target) as archive:
            self.assertEqual(archive.getnames(), ['garmin-tokens/tokens.json'])
        (source / '.tokens.json.active.tmp').write_bytes(b'partial')
        with self.assertRaisesRegex(backup.BackupError, 'active writer'):
            backup.wodiq_token_snapshot(source, target, 1024)
        (source / '.tokens.json.active.tmp').unlink()
        with self.assertRaisesRegex(backup.BackupError, 'exceeds policy'):
            backup.wodiq_token_snapshot(source, target, 2)
        inventory = backup.wodiq_token_inventory(source, 1024)
        changed = {**inventory, 'new-file': ('file',)}
        with mock.patch.object(backup, 'wodiq_token_inventory', side_effect=[inventory, changed]):
            with self.assertRaisesRegex(backup.BackupError, 'changed'):
                backup.wodiq_token_snapshot(source, target, 1024)
        self.assertFalse(target.exists())
        (source / 'link').symlink_to(source / 'tokens.json')
        with self.assertRaises(backup.BackupError):
            backup.wodiq_token_snapshot(source, target, 1024)

    def test_only_owned_crash_leftovers_in_private_token_staging_are_removed(self):
        source = self.root / 'live-tokens'
        source.mkdir()
        live_tmp = source / ('.tokens.json.' + 'a' * 32 + '.tmp')
        live_tmp.write_bytes(b'live app writer')
        staging = self.root / 'private-staging'
        staging.mkdir(mode=0o700)
        stale = staging / ('.tokens.json.' + 'b' * 32 + '.tmp')
        stale.write_bytes(b'own interrupted staging')
        stale.chmod(0o600)
        foreign = staging / 'foreign.tmp'
        foreign.write_bytes(b'leave alone')
        linked = staging / ('.tokens.json.' + 'c' * 32 + '.tmp')
        linked.symlink_to(live_tmp)
        broad = staging / ('.tokens.json.' + 'd' * 32 + '.tmp')
        broad.write_bytes(b'not our private file')
        broad.chmod(0o644)
        with self.assertRaisesRegex(backup.BackupError, 'active writer'):
            backup.wodiq_token_snapshot(source, self.root / 'tokens.tar', 1024, staging_directory=staging)
        self.assertFalse(stale.exists())
        self.assertTrue(live_tmp.exists())
        self.assertTrue(foreign.exists())
        self.assertTrue(linked.is_symlink())
        self.assertTrue(broad.exists())
        live_tmp.unlink()
        (source / 'tokens.json').write_bytes(b'encrypted synthetic')
        backup.wodiq_token_snapshot(source, self.root / 'tokens.tar', 1024, staging_directory=staging)
        self.assertEqual(set(staging.iterdir()), {foreign, linked, broad})

    def test_wodiq_corrupt_transfer_never_acknowledges(self):
        host = {'host_slug': 'tuinstra-prod-02', 'applications': ['wodiq'], 'ssh_user': 'pull',
                'ssh_host': 'vps02.tuinstra.dev', 'identity_file': '/key', 'known_hosts_file': '/known'}
        public = {'schema_version': 1, 'artifact_id': '12345678-1234-4123-8123-123456789abc',
                  'host_slug': host['host_slug'], 'app_id': 'wodiq', 'adapter': backup.WODIQ_ADAPTER,
                  'created_at': backup.now(), 'payload_sha256': '0' * 64, 'payload_bytes': 6}
        config = {'hosts': [host], 'pull_lock_root': str(self.root / 'locks'), 'work_dir': str(self.root),
                  'incoming_root': str(self.root / 'incoming'), 'max_artifact_bytes': 1024}
        def execute(argv, **kwargs):
            kwargs['stdout'].write(b'broken')
            return mock.Mock(stdout=b'')
        with mock.patch.object(backup, 'ssh_json', return_value=[public]) as remote, \
             mock.patch.object(backup, 'run', side_effect=execute), \
             mock.patch.object(backup, 'ingest_artifact') as ingest:
            with self.assertRaisesRegex(backup.BackupError, 'checksum'):
                backup.pull_host(config, host['host_slug'])
        self.assertEqual(remote.call_args_list, [mock.call(host, 'list')])
        ingest.assert_not_called()

    def test_policy_14_4_0_is_idempotent_with_05_00_cycle_06_15_fallback(self):
        host = 'tuinstra-prod-02'
        config = {key: str(self.root / key) for key in ('policy_root', 'systemd_unit_root', 'repository_root',
                  'work_dir', 'incoming_root', 'pull_lock_root', 'operation_lock_root', 'ingest_root', 'catalog_root')}
        config['hosts'] = [{'host_slug': host, 'applications': ['tracker', 'wodiq'],
                            'credential_name': 'prod02.key', 'identity_file': '/same/key'}]
        self.add_host_lock(config, host)
        document = backup.policy_document(host, 'wodiq', 'wodiq-v1', 5, 0, 14, 4, 0)
        digest = backup.document_hash(document)
        self.assertEqual(digest, '8878816abf4abca9c89daeadadf4d106aed65613984986f5e156a25251fba3ca')
        with mock.patch.object(backup, 'run', return_value=mock.Mock(stdout=b'')):
            self.assertEqual(backup.reconcile_policy(config, document, digest)['status'], 'applied')
            self.assertEqual(backup.reconcile_policy(config, document, digest)['status'], 'unchanged')
        units = Path(config['systemd_unit_root'])
        self.assertIn('05:00:00 Europe/Amsterdam', (units / f'tuinstra-backup-cycle-{host}--wodiq.timer').read_text())
        self.assertIn('06:15:00 Europe/Amsterdam', (units / f'tuinstra-backup-daily-fallback-{host}--wodiq.timer').read_text())
        passwords = self.root / 'passwords' / host
        passwords.mkdir(parents=True)
        (passwords / 'wodiq.password').write_text('synthetic')
        (passwords / 'wodiq.password').chmod(0o600)
        config.update(password_root=str(passwords.parent), retention_lock_file=str(self.root / 'retain.lock'),
                      retention_audit_root=str(self.root / 'audit'))
        with mock.patch.object(backup, 'run', side_effect=[mock.Mock(stdout=b'[{"keep":[{"id":"abc"}],"remove":[]}]'),
                mock.Mock(stdout=b''), mock.Mock(stdout=b''), mock.Mock(stdout=b'[]')]) as execute:
            backup.retain(config, host, 'wodiq', 'wodiq-v1')
        command = execute.call_args_list[0].args[0]
        for option, value in (('--keep-daily','14'), ('--keep-weekly','4'), ('--keep-monthly','0')):
            self.assertEqual(command[command.index(option)+1], value)
        self.assertEqual(execute.call_args_list[2].args[0][:2], ['restic', 'check'])

    def test_installer_adds_wodiq_without_replacing_current_tracker(self):
        loader = importlib.machinery.SourceFileLoader('wodiq_installer', str(ROOT / 'scripts/install-wodiq-producer'))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        installer = importlib.util.module_from_spec(spec)
        loader.exec_module(installer)
        config_path, engine_path = self.root / 'producer.json', self.root / 'engine.py'
        current = json.loads((ROOT / 'backup/profiles/prod02.json').read_text())
        current['applications'] = [current['applications'][0]]
        current['applications'][0]['approved_images']['php'] = 'synthetic-new-live-pin'
        config_path.write_text(json.dumps(current))
        engine_path.write_text('# synthetic old engine\n')
        with mock.patch.object(installer.importlib.util, 'module_from_spec', return_value=backup), \
             mock.patch.object(installer.importlib.util, 'spec_from_file_location', return_value=mock.Mock(loader=mock.Mock())), \
             mock.patch.object(backup, 'running_compose_images'):
            rollback = installer.install(ROOT, config_path, engine_path, self.root / 'rollback')
            installed = json.loads(config_path.read_text())
            installer.install(ROOT, config_path, engine_path, self.root / 'rollback')
        self.assertEqual(installed['applications'][0], current['applications'][0])
        self.assertEqual(json.loads(config_path.read_text()), installed)
        self.assertEqual(json.loads((rollback / 'producer.json').read_text()), current)
        self.assertEqual((rollback / 'tuinstra_backup.py').read_text(), '# synthetic old engine\n')
        self.assertEqual(config_path.stat().st_mode & 0o777, 0o640)
        self.assertEqual(len(list((self.root / 'rollback').iterdir())), 2)


if __name__ == '__main__':
    unittest.main()

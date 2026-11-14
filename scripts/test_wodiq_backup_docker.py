#!/usr/bin/env python3
"""Opt-in real PostgreSQL drill, synthetic data only; no host ports or providers.

Run with Python >=3.12 and access to the local Docker daemon. Pre-pull the reviewed
WODIQ_POSTGRES_IMAGE. This does not contact a production database or use secrets.
"""
import importlib.util
import importlib.machinery
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backup'))
import tuinstra_backup as backup


def main():
    project = 'wodiq-drill-' + uuid.uuid4().hex[:12]
    with tempfile.TemporaryDirectory(prefix='wodiq-real-drill-') as temporary:
        root = Path(temporary).resolve()
        compose = root / 'compose.json'
        compose.write_text(json.dumps({'services': {'postgres': {
            'image': backup.WODIQ_POSTGRES_IMAGE, 'network_mode': 'none',
            'environment': {'POSTGRES_USER': 'postgres', 'POSTGRES_DB': 'wodiq120',
                            'POSTGRES_HOST_AUTH_METHOD': 'trust'},
            'tmpfs': ['/var/lib/postgresql/data:rw,size=2147483648'],
        }}}))
        release = root / 'release.env'
        release.write_text('SYNTHETIC=true\n')
        release.chmod(0o600)
        backup.ROOT_UID = os.getuid()
        app = {'adapter': backup.WODIQ_ADAPTER, 'database_name': 'wodiq120', 'postgres_service': 'postgres',
               'compose_project': project, 'compose_file': str(compose), 'compose_env_file': str(release)}
        command = backup.compose_command(app)
        def sql(query):
            return backup.run([*command, 'exec', '-T', 'postgres', 'psql', '-XqAt', '-v', 'ON_ERROR_STOP=1',
                               '-U', 'postgres', '-d', 'wodiq120', '-c', query]).stdout
        try:
            backup.run([*command, 'up', '-d', '--pull', 'never'])
            for _ in range(60):
                try:
                    sql('SELECT 1')
                    break
                except backup.BackupError:
                    time.sleep(1)
            else:
                raise RuntimeError('synthetic postgres did not become ready')
            sql('''CREATE TABLE _prisma_migrations(id text PRIMARY KEY, checksum text, migration_name text,
                started_at timestamptz, finished_at timestamptz, rolled_back_at timestamptz, applied_steps_count int);
                INSERT INTO _prisma_migrations VALUES ('synthetic-1', repeat('a',64), '20261114_init',
                  '2026-11-14 00:00:00Z', '2026-11-14 00:00:01Z', NULL, 1);
                CREATE TABLE "User"(id int PRIMARY KEY); INSERT INTO "User" VALUES (1);
                CREATE TABLE empty_table(id int);''')
            payload = root / 'payload'
            (payload / 'files').mkdir(parents=True)
            real_run = backup.run
            mutated = False
            def execute(argv, **kwargs):
                nonlocal mutated
                result = real_run(argv, **kwargs)
                if any('exec pg_dump --format=custom' in arg for arg in argv):
                    # Mutate the live DB after the dump, before marker collection.
                    mutated = True
                    sql('INSERT INTO "User" VALUES (2)')
                return result
            with mock.patch.object(backup, 'run', side_effect=execute):
                marker = backup.wodiq_database_export(app, payload / 'database.dump')
            assert mutated
            assert marker['row_counts'] == {'_prisma_migrations': 1, 'User': 1, 'empty_table': 0}
            assert sql('SELECT count(*) FROM "User"').strip() == b'2'
            assert (payload / 'database.dump').read_bytes().startswith(b'PGDMP')
            for name in ('compose', 'release', 'api-env', 'sync-env', 'postgres-env'):
                (payload / 'files' / name).write_text('synthetic fixture only\n')
            tokens = root / 'tokens'
            (tokens / 'synthetic-account').mkdir(parents=True)
            (tokens / 'synthetic-account/tokens.json').write_bytes(b'synthetic encrypted token fixture')
            backup.wodiq_token_snapshot(tokens, payload / 'files/garmin-tokens.tar', 1024)
            manifest = {'schema_version': 1, 'artifact_id': str(uuid.uuid4()),
                        'host_slug': 'tuinstra-prod-02', 'app_id': 'wodiq', 'adapter': backup.WODIQ_ADAPTER,
                        'created_at': backup.now(), 'database_service': 'postgres',
                        'images': [backup.WODIQ_POSTGRES_IMAGE, 'synthetic-api@sha256:' + 'b' * 64],
                        'image_services': {'postgres': backup.WODIQ_POSTGRES_IMAGE,
                                           'wodiq-api': 'synthetic-api@sha256:' + 'b' * 64},
                        'database': {**backup.postgres_versions(app), 'content_marker': marker},
                        'inputs': [{'name': p.relative_to(payload).as_posix(), 'sha256': backup.sha256(p),
                                    'bytes': p.stat().st_size} for p in payload.rglob('*') if p.is_file()]}
            manifest_path = payload / 'backup-manifest.json'
            backup.atomic_json(manifest_path, manifest, 0o600)
            adapter = Path(__file__).resolve().parents[1] / 'backup/restore-wodiq'
            loader = importlib.machinery.SourceFileLoader('restore_wodiq', str(adapter))
            spec = importlib.util.spec_from_loader(loader.name, loader)
            restorer = importlib.util.module_from_spec(spec)
            loader.exec_module(restorer)
            try:
                evidence = restorer.restore(payload, manifest_path)
            except backup.BackupError as error:
                if isinstance(error.__cause__, subprocess.CalledProcessError):
                    # This drill contains synthetic data only.
                    print(error.__cause__.stderr.decode(), file=sys.stderr)
                raise
            backup.validate_wodiq_restore_adapter_result(evidence)
            assert evidence['public_table_count'] == 3
            manifest['database']['content_marker']['ledger'][0]['checksum'] = 'f' * 64
            backup.atomic_json(manifest_path, manifest, 0o600)
            result = subprocess.run([sys.executable, str(adapter), str(payload)], capture_output=True)
            assert result.returncode != 0 and b'"status":"passed"' not in result.stdout
            # The deliberate mismatch must still remove its validator container/workspace.
            assert not real_run(['docker', 'ps', '-aq', '--filter', 'name=^/wodiq-restore-']).stdout.strip()
            assert not list(root.glob('wodiq-validator-*'))
            print('PASS: real custom dump, concurrent-write snapshot ledger/counts, empty table, isolated restore, mismatch rejection and cleanup')
        finally:
            backup.run([*command, 'down', '--volumes', '--remove-orphans'])
    assert not root.exists()
    print('PASS: source container and synthetic workspace removed')


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest


SOURCE = Path(__file__).resolve().parent / "stage_backup_escrow.py"
SPEC = importlib.util.spec_from_file_location("stage_backup_escrow", SOURCE)
stage = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(stage)


class StageBackupEscrowTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        stage.ROOT_UID = os.getuid()
        self.sources = {}
        for name in stage.SOURCES:
            source = self.root / name
            source.write_text(f"{name}-secret\n", encoding="ascii")
            source.chmod(0o600)
            self.sources[name] = source

    def tearDown(self):
        self.temporary.cleanup()

    def test_exact_bundle_is_staged_once_with_private_permissions(self):
        destination = self.root / "home/.local/share/tuinstra-backup-escrow.json"
        (self.root / "home").mkdir()
        marker = self.root / "state/escrow-staged-v1"
        result = stage.stage(destination, marker, os.getuid(), os.getgid(), self.sources)
        self.assertEqual(result, "staged")
        self.assertEqual(destination.stat().st_mode & 0o777, 0o600)
        document = json.loads(destination.read_text(encoding="ascii"))
        self.assertEqual(set(document), {"schema_version", "source_host", "secrets"})
        self.assertEqual(document["secrets"], {
            name: f"{name}-secret\n" for name in self.sources
        })
        destination.unlink()
        self.assertEqual(stage.stage(destination, marker, os.getuid(), os.getgid(), self.sources),
                         "already-staged")
        self.assertFalse(destination.exists())

    def test_refuses_unsafe_source_or_unmarked_existing_handoff(self):
        destination = self.root / "home/.local/share/tuinstra-backup-escrow.json"
        (self.root / "home").mkdir()
        marker = self.root / "state/escrow-staged-v1"
        self.sources["ssh_prod02"].chmod(0o644)
        with self.assertRaisesRegex(stage.StageError, "unsafe"):
            stage.stage(destination, marker, os.getuid(), os.getgid(), self.sources)
        self.sources["ssh_prod02"].chmod(0o600)
        destination.parent.mkdir(parents=True)
        destination.write_text("untrusted")
        with self.assertRaisesRegex(stage.StageError, "already exists"):
            stage.stage(destination, marker, os.getuid(), os.getgid(), self.sources)


if __name__ == "__main__":
    unittest.main()

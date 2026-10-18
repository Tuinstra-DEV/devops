"""No privileged execution: validate the operator's immutable staging boundary."""

import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("fair_repair", ROOT / "scripts/stage-fair-runner-repair.py")
repair = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(repair)


class StagingTests(unittest.TestCase):
    def test_payload_hashes_match_reviewed_sources(self):
        for name, digest in repair.PAYLOADS.items():
            source = ROOT / ("runner/manager" if name == "ci_runner_manager.py" else "scripts") / name
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), digest)

    def test_stage_is_private_and_contains_verified_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            source.mkdir()
            source.joinpath("fixture.py").write_bytes(b"reviewed")
            with mock.patch.object(repair, "PAYLOADS", {"fixture.py": hashlib.sha256(b"reviewed").hexdigest()}):
                stage = repair.stage_verified(source, root)
            self.assertEqual(stage.stat().st_mode & 0o777, 0o700)
            self.assertEqual((stage / "fixture.py").read_bytes(), b"reviewed")
            self.assertEqual((stage / "fixture.py").stat().st_mode & 0o777, 0o644)

    def test_wrong_payload_fails_before_creating_stage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            root.joinpath("fixture.py").write_bytes(b"tampered")
            with mock.patch.object(repair, "PAYLOADS", {"fixture.py": hashlib.sha256(b"reviewed").hexdigest()}):
                with self.assertRaises(ValueError):
                    repair.stage_verified(root, root)
            self.assertEqual(list(root.glob("dev50-fair-scheduler-*")), [])

    def test_unprotected_or_symlink_parent_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            parent = root / "parent"
            parent.mkdir(mode=0o777)
            parent.chmod(0o777)
            link = root / "link"
            link.symlink_to(parent, target_is_directory=True)
            with mock.patch.object(repair, "PAYLOADS", {}):
                for path in (parent, link):
                    with self.assertRaises(ValueError):
                        repair.stage_verified(root, path)

    def test_cli_refuses_non_root_without_staging(self):
        with mock.patch.object(repair.os, "geteuid", return_value=1000), \
                mock.patch.object(repair, "stage_verified") as stage:
            with self.assertRaises(SystemExit):
                repair.main(["--apply"])
            stage.assert_not_called()


if __name__ == "__main__":
    unittest.main()

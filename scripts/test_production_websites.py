"""Filesystem effects with a fake Docker boundary; native Caddy is tested separately."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("production_websites", Path(__file__).with_name("production-websites.py"))
web = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(web)
IMAGE = "caddy:2.11.4-alpine@sha256:" + "a" * 64


class WebsiteOperationsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patches = [patch.object(web, "ROOT", self.root),
                        patch.object(web, "SITES", self.root / "sites"),
                        patch.object(web, "BACKUPS", self.root / "backups"),
                        patch.object(web, "secure", side_effect=lambda path, **kwargs: path)]
        for p in self.patches:
            p.start()
        # Ownership/host/lock checks belong to the operator boundary; this unit
        # exercises real atomic file writes and recovery without root or Docker.
        web.SITES.mkdir()
        self.candidate = self.root / "candidate"
        self.candidate.mkdir()
        (self.root / "Caddyfile").write_text("import /etc/caddy/sites/*.caddy\n")
        for name in web.NAMES:
            (web.SITES / name).write_text("old " + name)
            (self.candidate / name).write_text("new " + name)
        (web.SITES / "unrelated.caddy").write_text("do not replace\n")
        self.original = {p.name: p.read_bytes() for p in web.SITES.iterdir()}

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.temp.cleanup()

    def docker(self, args):
        if args[:2] == ["docker", "ps"]:
            return b"fixture-container\n"
        if args[:2] == ["docker", "inspect"]:
            return json.dumps([{"Config": {"Image": IMAGE}}]).encode()
        if args[:2] == ["docker", "run"]:
            mount = args[args.index("--mount") + 1]
            stage = Path(mount.split("source=", 1)[1].split(",", 1)[0])
            self.assertEqual((stage / "sites/unrelated.caddy").read_bytes(), self.original["unrelated.caddy"])
            for name in web.NAMES:
                self.assertEqual((stage / "sites" / name).read_bytes(), (self.candidate / name).read_bytes())
        return b""

    def assert_original(self):
        self.assertEqual({p.name: p.read_bytes() for p in web.SITES.iterdir()}, self.original)

    def test_check_validates_complete_candidate_without_touching_serving_routes(self):
        with patch.object(web, "run", side_effect=self.docker):
            result = web.apply(self.candidate, check=True)
        self.assertTrue(result["checked"])
        self.assertFalse(result["applied"])
        self.assert_original()
        self.assertFalse(web.BACKUPS.exists())

    def test_failed_validation_keeps_original_routes_and_creates_no_recovery_snapshot(self):
        def docker(args):
            if args[:2] == ["docker", "run"]:
                raise RuntimeError("invalid candidate")
            return self.docker(args)
        with patch.object(web, "run", side_effect=docker), self.assertRaisesRegex(RuntimeError, "invalid candidate"):
            web.apply(self.candidate)
        self.assert_original()
        self.assertFalse(web.BACKUPS.exists())

    def test_failed_reload_restores_originals_and_retains_checksummed_snapshot(self):
        reloads = []
        def docker(args):
            if args[:2] == ["docker", "exec"]:
                reloads.append(args)
                if len(reloads) == 1:
                    raise RuntimeError("rejected reload")
            return self.docker(args)
        with patch.object(web, "run", side_effect=docker), self.assertRaisesRegex(RuntimeError, "original routes restored"):
            web.apply(self.candidate)
        self.assert_original()
        self.assertEqual(len(reloads), 2)
        snapshot = next(web.BACKUPS.iterdir())
        manifest = json.loads((snapshot / "manifest.json").read_text())
        for name in web.NAMES:
            self.assertEqual((snapshot / name).read_bytes(), self.original[name])
            self.assertEqual(manifest["before"][name], hashlib.sha256(self.original[name]).hexdigest())

    def test_stale_rollback_is_rejected_before_docker_or_file_changes(self):
        with patch.object(web, "run") as docker, self.assertRaisesRegex(RuntimeError, "stale rollback"):
            web.apply(self.candidate, expected={n: "0" * 64 for n in web.NAMES})
        docker.assert_not_called()
        self.assert_original()



if __name__ == "__main__":
    unittest.main()

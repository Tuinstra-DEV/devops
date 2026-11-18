"""Validate the deployed-base patch, admission and unchanged shared dispatch."""
import ast
import gzip
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

import test_gate_backend_pilot as pilot
import test_gate_permanent as permanent

ROOT = Path(__file__).resolve().parents[2]
DIRECTORY = ROOT / 'docs/runner/proposals/dev54-gate-permanent'


class EffectiveGatePatchTests(unittest.TestCase):
    def test_deployed_base_patch_and_permanent_admission_without_other_manager_changes(self):
        meta = json.loads((DIRECTORY / 'manager-manifest.json').read_text())
        source = gzip.decompress((ROOT / 'runner/tests/fixtures/gate-permanent/manager-5404.py.gz').read_bytes())
        self.assertEqual(hashlib.sha256(source).hexdigest(), meta['source_sha256'])
        self.assertEqual(hashlib.sha256((DIRECTORY / 'manager-effective.patch').read_bytes()).hexdigest(), meta['patch_sha256'])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / 'runner/manager/ci_runner_manager.py'
            target.parent.mkdir(parents=True)
            target.write_bytes(source)
            subprocess.run(['patch', '--batch', '--fuzz=0', '-p1', '-i', str(DIRECTORY / 'manager-effective.patch')], cwd=root, check=True, capture_output=True)
            candidate = target.read_bytes()
            self.assertEqual(hashlib.sha256(candidate).hexdigest(), meta['target_sha256'])
            allowed = {'configured_org_execution_roles', 'role_labels_match',
                       'GitHubClient._role_run_evidence', 'GitHubClient._role_evidence',
                       'GitHubClient.candidate_role_jobs', 'GitHubClient.verify_role_job'}

            def normalize(data):
                tree = ast.parse(data)
                body = []
                for node in tree.body:
                    if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in {'GATE_ROLES', 'GATE_CORE_WORKFLOW', 'GATE_BROWSER_WORKFLOW'} for t in node.targets):
                        continue
                    if isinstance(node, ast.For) and isinstance(node.target, ast.Tuple) and any(isinstance(t, ast.Name) and t.id == '_role' for t in node.target.elts):
                        continue
                    if isinstance(node, ast.FunctionDef) and (node.name in allowed or node.name == 'gate_role_references'):
                        continue
                    if isinstance(node, ast.ClassDef):
                        node.body = [method for method in node.body if not isinstance(method, ast.FunctionDef) or node.name + '.' + method.name not in allowed]
                    body.append(node)
                tree.body = body
                return ast.dump(tree, include_attributes=False)

            # All pool/disk/lease/credential/dispatch code remains AST-identical.
            self.assertEqual(normalize(source), normalize(candidate))
            spec = importlib.util.spec_from_file_location('effective_gate_manager', target)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            with mock.patch.object(pilot, 'manager', module), mock.patch.object(permanent, 'manager', module):
                suite = unittest.defaultTestLoader.loadTestsFromTestCase(permanent.PermanentGateTests)
                stream = io.StringIO()
                result = unittest.TextTestRunner(stream=stream).run(suite)
                self.assertTrue(result.wasSuccessful(), stream.getvalue())
                self.assertGreaterEqual(result.testsRun, 7)

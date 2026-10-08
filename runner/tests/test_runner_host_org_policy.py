"""Keep the Ansible organization route closed until its private inputs exist."""

from pathlib import Path
import json
import re
import sys
import tomllib
import unittest


ROOT = Path(__file__).resolve().parents[2]
ROLE = ROOT / "infra/ansible/roles/runner_host"
sys.path.insert(0, str(ROOT / "runner/manager"))
import ci_runner_manager as manager  # noqa: E402


class RunnerHostOrgPolicyTests(unittest.TestCase):
    def test_opt_in_defaults_to_off_and_needs_exact_manifest(self):
        defaults = (ROLE / "defaults/main.yml").read_text()
        tasks = (ROLE / "tasks/main.yml").read_text()
        manifest = json.loads((ROOT / "runner/policy/org-routing-repositories.json").read_text())
        self.assertEqual(len(manifest), 12)
        self.assertIn("runner_org_routing_enabled: false", defaults)
        self.assertIn("runner_org_runner_group_id: 0", defaults)
        self.assertIn("runner_org_repository_ids: {}", defaults)
        self.assertIn("runner_org_runner_group_id | int > 1", tasks)
        self.assertIn("runner_org_repository_ids ==", tasks)
        self.assertIn("runner/policy/org-routing-repositories.json", tasks)
        self.assertIn("@[a-f0-9]{40}", tasks)
        self.assertIn("runner_pool_mode == 'four'", tasks)

    def test_separate_credential_is_conditional_and_never_copied(self):
        tasks = (ROLE / "tasks/main.yml").read_text()
        config = (ROLE / "templates/manager.toml.j2").read_text()
        dropin = (ROLE / "templates/ci-runner-manager-org-credential.conf.j2").read_text()
        base_unit = (ROOT / "runner/systemd/ci-runner-manager.service").read_text()
        self.assertRegex(config, r"\{% if runner_org_routing_enabled \| bool %\}[\s\S]*org_routing_enabled = true[\s\S]*\{% endif %\}")
        self.assertIn('org_github_token_file = "/run/credentials/ci-runner-manager.service/org_github_token"', config)
        self.assertEqual(dropin, "[Service]\nLoadCredential=org_github_token:/etc/ci-runner/org-github.token\n")
        self.assertEqual(base_unit.count("LoadCredential="), 1)
        self.assertIn("LoadCredential=github_token:/etc/ci-runner/github.token", base_unit)
        self.assertIn("when: runner_org_routing_enabled | bool", tasks)
        self.assertNotIn("content: {{ runner_org", tasks)

    def test_enabled_table_parses_and_matches_manager_policy(self):
        source = (ROLE / "templates/manager.toml.j2").read_text()
        block = source.split("{% if runner_org_routing_enabled | bool %}\n", 1)[1]
        block = block.split("{% endif %}", 1)[0]
        repositories = json.loads(
            (ROOT / "runner/policy/org-routing-repositories.json").read_text())

        def expand_repositories(match):
            row = match.group(1)
            return "\n".join(
                row.replace("{{ repository | to_json }}", json.dumps(repository))
                   .replace("{{ repository_id | int }}", str(identifier))
                for repository, identifier in sorted(repositories.items()))

        block, count = re.subn(
            r"{% for repository, repository_id in runner_org_repository_ids \| dictsort %}\n"
            r"(.*?)\n{% endfor %}", expand_repositories, block, flags=re.DOTALL)
        self.assertEqual(count, 1)
        block = block.replace("{{ runner_org_runner_group_id | int }}", "42")
        block = block.replace("{{ runner_org_workflow_ref | to_json }}",
                              json.dumps(manager.ORG_WORKFLOW_PREFIX + "a" * 40))
        self.assertNotIn("{{", block)
        self.assertNotIn("{%", block)
        config = tomllib.loads(block)
        self.assertEqual(config["org_repository_ids"], repositories)
        manager.validate_org_config({
            **config,
            "pool_mode": "four",
            "github_token_file":
                "/run/credentials/ci-runner-manager.service/github_token",
        })

    def test_missing_or_unsafe_credential_blocks_before_admission_stops(self):
        tasks = (ROLE / "tasks/main.yml").read_text()
        self.assertLess(tasks.index("Inspect independently provisioned organization credential"),
                        tasks.index("Install virtualization and policy packages"))
        check = tasks.split("- name: Inspect independently provisioned organization credential", 1)[1]
        check = check.split("- name: Inspect the organization credential unit drop-in", 1)[0]
        for requirement in ("follow: false", "get_checksum: false", "get_mime: false",
                            ".stat.isreg", "not runner_org_github_credential.stat.islnk",
                            "stat.pw_name", "stat.gr_name", "'0600'"):
            self.assertIn(requirement, check)
        self.assertIn("Refuse to silently turn off existing organization routing", tasks)
        self.assertIn("Refuse an orphaned organization credential drop-in", tasks)


if __name__ == "__main__":
    unittest.main()

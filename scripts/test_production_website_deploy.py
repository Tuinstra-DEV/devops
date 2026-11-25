import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch


spec = importlib.util.spec_from_file_location(
    "production_website_release", Path(__file__).with_name("production_website_release.py")
)
websites = importlib.util.module_from_spec(spec)
spec.loader.exec_module(websites)


class WebsiteDeploymentTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.state = Path(temporary.name)
        self.site = Mock()
        self.site.app = "site-marcel"
        self.site.previous = "ghcr.io/tuinstra-dev/site-marcel@sha256:" + "1" * 64
        self.site.target = "ghcr.io/tuinstra-dev/site-marcel@sha256:" + "2" * 64
        self.site.state = self.state
        self.site.live = "live"
        self.site.candidate = "candidate"

    def outcome(self):
        return [item[0] for item in self.site.method_calls]

    def test_switch_waits_and_removes_only_old_image_after_live_health(self):
        websites.deploy_website(self.site)
        self.assertEqual(self.outcome(), [
            "prepare", "pull", "start_candidate", "wait_healthy", "route_candidate",
            "probe_public", "replace_live", "wait_healthy", "route_live",
            "probe_public", "remove_candidate", "record_success", "remove_old_image",
        ])
        self.site.wait_healthy.assert_any_call(self.site.candidate, self.site.target)
        self.site.wait_healthy.assert_any_call(self.site.live, self.site.target)
        self.site.remove_old_image.assert_called_once_with()

    def test_unhealthy_candidate_leaves_original_route_and_container_in_service(self):
        self.site.wait_healthy.side_effect = RuntimeError("candidate unhealthy")
        with self.assertRaisesRegex(RuntimeError, "candidate unhealthy"):
            websites.deploy_website(self.site)
        self.site.route_candidate.assert_not_called()
        self.site.replace_live.assert_not_called()
        self.site.remove_candidate.assert_called_once()
        self.site.remove_old_image.assert_not_called()

    def test_unhealthy_live_restores_original_image_before_removing_candidate(self):
        self.site.wait_healthy.side_effect = [None, RuntimeError("new live unhealthy")]
        with self.assertRaisesRegex(RuntimeError, "new live unhealthy"):
            websites.deploy_website(self.site)
        self.assertEqual(self.outcome()[-4:], ["route_candidate", "restore_live", "route_live", "remove_candidate"])
        self.site.remove_old_image.assert_not_called()

    def test_failed_rollback_leaves_candidate_and_recovery_bundle(self):
        self.site.wait_healthy.side_effect = [None, RuntimeError("new live unhealthy")]
        self.site.restore_live.side_effect = RuntimeError("rollback unhealthy")
        with self.assertRaisesRegex(RuntimeError, "recovery"):
            websites.deploy_website(self.site)
        self.site.remove_candidate.assert_not_called()
        self.site.remove_old_image.assert_not_called()

    def test_retry_of_active_digest_checks_health_without_replacing_live(self):
        self.site.previous = self.site.target
        websites.deploy_website(self.site)
        self.assertEqual(self.outcome(), ["verify_revision", "wait_healthy", "probe_public", "record_success"])

    def test_failed_candidate_https_check_restores_route_without_replacing_live(self):
        self.site.probe_public.side_effect = RuntimeError("HTTPS check failed")
        with self.assertRaisesRegex(RuntimeError, "HTTPS check failed"):
            websites.deploy_website(self.site)
        self.assertEqual(self.outcome()[-2:], ["route_live", "remove_candidate"])
        self.site.replace_live.assert_not_called()
        self.site.remove_old_image.assert_not_called()

    def test_cleanup_targets_only_previous_image_and_respects_container_references(self):
        site = websites.WebsiteRelease.__new__(websites.WebsiteRelease)
        site.docker = ["docker"]
        site.previous = self.site.previous
        with patch.object(websites, "command", return_value=b"other-container-id") as command:
            site.remove_old_image()
            command.assert_called_once_with(
                ["docker", "ps", "-aq", "--filter", f"ancestor={site.previous}"],
                "check references to previous image",
            )
        with patch.object(websites, "command", return_value=b"") as command:
            site.remove_old_image()
            command.assert_called_with(
                ["docker", "image", "rm", site.previous], "remove previous local image"
            )

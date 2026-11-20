"""Activate an immutable, previously scanned static website image on VPS01."""

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time


HOSTS = {
    "wodiq-site": "wodiq.nl",
    "site-marcel": "marcel.tuinstra.dev",
    "site-tuinstra": "tuinstra.dev",
}


def command(argv, operation, timeout=90):
    try:
        result = subprocess.run(argv, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"{operation} timed out") from None
    if result.returncode:
        raise RuntimeError(f"{operation} failed (exit {result.returncode})")
    return result.stdout


class WebsiteRelease:
    def __init__(self, app, source, image_digest, caddy_sites, secure, atomic):
        if app not in HOSTS or not re.fullmatch(r"[a-f0-9]{40}", source):
            raise RuntimeError("invalid website release")
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", image_digest):
            raise RuntimeError("invalid website image digest")
        self.app = app
        self.source = source
        registry = f"ghcr.io/tuinstra-dev/{app}"
        self.target = f"{registry}@{image_digest}"
        self.app_root = Path("/var/www") / app
        self.config = secure(self.app_root / "compose.yml")
        self.original_config = self.config.read_bytes()
        image_pattern = rb"(?m)^([ \t]+image:[ \t]*)(" + re.escape(registry.encode()) + rb"@sha256:[a-f0-9]{64})([ \t]*)$"
        images = list(re.finditer(image_pattern, self.original_config))
        if len(images) != 1 or len(re.findall(rb"(?m)^[ \t]+image:", self.original_config)) != 1:
            raise RuntimeError("expected exactly one pinned website image")
        self.previous = images[0][2].decode("ascii")
        self.new_config = re.sub(image_pattern, lambda match: match[1] + self.target.encode() + match[3],
                                 self.original_config)

        self.route = secure(caddy_sites / f"{app}.caddy")
        self.original_route = self.route.read_bytes()
        self.live_upstream = f"reverse_proxy {app}:80\n".encode()
        if self.original_route.count(self.live_upstream) != 1:
            raise RuntimeError("unexpected website route")
        self.state_root = secure(Path("/var/lib/tuinstra") / app, directory=True)
        self.state = self.state_root / "website-deploy-in-progress"
        if os.path.lexists(self.state):
            raise RuntimeError("unfinished website deploy requires administrator recovery")

        self.atomic = atomic
        self.live = f"{app}-{app}-1"
        self.candidate = f"{app}-release-candidate"
        self.candidate_created = False
        self.docker = ["/usr/bin/docker"]
        registry_config = Path("/etc/tuinstra") / app / "docker-config/config.json"
        if os.path.lexists(registry_config):
            secure(registry_config)
            if registry_config.stat().st_mode & 0o077:
                raise RuntimeError("registry configuration must be private")
            self.docker += ["--config", str(registry_config.parent)]
        self.compose = self.docker + ["compose", "--project-name", app,
                                     "--project-directory", str(self.app_root)]
        env = Path("/etc/tuinstra") / app / ".env"
        if os.path.lexists(env):
            self.compose += ["--env-file", str(secure(env))]
        self.config_info = self.config.stat()
        self.route_info = self.route.stat()

    def prepare(self):
        self.wait_healthy(self.live, self.previous, samples=1)
        if self.find_candidate():
            raise RuntimeError("existing website candidate requires administrator recovery")
        self.state.mkdir(mode=0o700)
        self.atomic(self.state / "compose.before", self.original_config, 0o600)
        self.atomic(self.state / "route.before", self.original_route, 0o600)
        self.atomic(self.state / "compose.yml", self.new_config, 0o600)

    def find_candidate(self):
        return command(self.docker + ["ps", "-aq", "--filter", f"name=^/{self.candidate}$"],
                       "identify candidate container").strip()

    def pull(self):
        command(self.docker + ["pull", self.target], "pull immutable website image", timeout=180)
        self.verify_revision()

    def verify_revision(self):
        revision = command(self.docker + ["image", "inspect", "--format",
                           '{{index .Config.Labels "org.opencontainers.image.revision"}}',
                           self.target], "read immutable image revision").decode().strip()
        if revision != self.source:
            raise RuntimeError("published image revision does not match the release commit")

    def start_candidate(self):
        self.candidate_created = True
        command(self.compose + ["--file", str(self.state / "compose.yml"), "run", "--detach",
                                "--no-deps", "--pull", "never", "--name", self.candidate, self.app],
                "start candidate container")

    def wait_healthy(self, container, expected, samples=2):
        deadline = time.monotonic() + 120
        healthy = 0
        last_probe = None
        while time.monotonic() < deadline:
            result = command(self.docker + ["inspect", "--format",
                             "{{.Config.Image}} {{.State.Status}} {{json .State.Health}}",
                             container], "read website container health", timeout=10).decode().split(maxsplit=2)
            if len(result) != 3 or result[0] != expected or result[1] != "running":
                raise RuntimeError("running website image did not match the expected digest")
            health = json.loads(result[2])
            checks = health.get("Log", []) if health else []
            latest = checks[-1] if checks else {}
            if health and health["Status"] == "healthy" and latest.get("ExitCode") == 0:
                if latest["End"] == last_probe:
                    time.sleep(5)
                    continue
                last_probe = latest["End"]
                healthy += 1
                if healthy == samples:
                    return
            else:
                healthy = 0
            time.sleep(5)
        raise RuntimeError("website container did not become stably healthy")

    def reload_route(self, route):
        self.atomic(self.route, route, self.route_info.st_mode & 0o777, self.route_info.st_gid)
        base = self.docker + ["exec", "caddy-caddy-1", "caddy"]
        config = ["--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"]
        command(base + ["validate", *config], "validate edge configuration")
        command(base + ["reload", *config], "reload edge configuration")

    def route_candidate(self):
        candidate = f"reverse_proxy {self.candidate}:80\n".encode()
        self.reload_route(self.original_route.replace(self.live_upstream, candidate))

    def route_live(self):
        self.reload_route(self.original_route)

    def probe_public(self):
        host = HOSTS[self.app]
        result = command(["/usr/bin/curl", "--fail", "--silent", "--show-error",
                          "--max-time", "8", "--resolve", f"{host}:443:127.0.0.1",
                          f"https://{host}/health"], "check public HTTPS route")
        if result.strip() != b"ok":
            raise RuntimeError("website health response was unexpected")

    def replace_live(self):
        self.run_live(self.new_config)

    def restore_live(self):
        self.run_live(self.original_config)
        self.wait_healthy(self.live, self.previous, samples=1)

    def run_live(self, config):
        self.atomic(self.config, config, self.config_info.st_mode & 0o777, self.config_info.st_gid)
        command(self.compose + ["--file", str(self.config), "up", "--detach", "--no-deps",
                                "--pull", "never", self.app], "activate canonical website container")

    def remove_candidate(self):
        if self.candidate_created and self.find_candidate():
            command(self.docker + ["stop", "--time", "10", self.candidate], "stop candidate container")
            command(self.docker + ["rm", self.candidate], "remove candidate container")

    def record_success(self):
        receipt = {"commit": self.source, "image": self.target, "previous": self.previous}
        self.atomic(self.state_root / "website-deploy-receipt.json",
                    (json.dumps(receipt) + "\n").encode(), 0o600)
        print(json.dumps(receipt), flush=True)

    def remove_old_image(self):
        references = command(self.docker + ["ps", "-aq", "--filter", f"ancestor={self.previous}"],
                             "check references to previous image").strip()
        if references:
            print("Previous local image retained because a container still references it", flush=True)
            return
        try:
            command(self.docker + ["image", "rm", self.previous], "remove previous local image")
        except RuntimeError as error:
            print(str(error), flush=True)


def deploy_website(site):
    if site.previous == site.target:
        site.verify_revision()
        site.wait_healthy(site.live, site.target)
        site.probe_public()
        site.record_success()
        return

    site.prepare()
    routed = False
    live_changed = False
    try:
        site.pull()
        site.start_candidate()
        site.wait_healthy(site.candidate, site.target)
        routed = True
        site.route_candidate()
        site.probe_public()
        live_changed = True
        site.replace_live()
        site.wait_healthy(site.live, site.target)
        site.route_live()
        site.probe_public()
    except Exception:
        try:
            if live_changed:
                # A failed final probe may already be targeting the new canonical container.
                site.route_candidate()
                site.restore_live()
            if routed:
                site.route_live()
            site.remove_candidate()
            shutil.rmtree(site.state)
        except Exception:
            raise RuntimeError("website rollback requires administrator recovery") from None
        raise
    site.remove_candidate()
    site.record_success()
    shutil.rmtree(site.state)
    site.remove_old_image()

"""Adapter for restoring a journal-bound release workflow ref.

Used only by the release-ref transaction after its protected journal and
snapshots have been validated. Credential values remain in process memory.
"""
import os
import stat


class LiveRecovery:
    def __init__(self, base, manager, checker, backup, metadata):
        self.base = base
        self.manager = manager
        self.checker = checker
        self.backup = backup
        self.metadata = metadata

    def ensure_empty(self):
        self.base.ensure_empty()

    def verify_fixed_resources(self, before):
        actual = self.base.source_fingerprints()
        for key in ('manager_sha256', 'helper_sha256', 'selector_target', 'active_image_sha256'):
            if actual[key] != before.fixed[key]:
                raise ValueError('recovery_fixed_resource_drift')
        info = self.base.SELECTOR.lstat()
        if (not stat.S_ISLNK(info.st_mode) or
                (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) !=
                (before.fixed['selector_uid'], before.fixed['selector_gid'], before.fixed['selector_mode']) or
                os.readlink(self.base.SELECTOR) != before.fixed['selector_link']):
            raise ValueError('recovery_selector_metadata_drift')
        self.base.verify_sealed_candidate(self.base.NEW_IMAGE)

    def read_config(self):
        return self.base.secure_regular(self.base.CONFIG)

    def service_running(self):
        state = self.base.service_state()['ActiveState']
        if state not in ('active', 'inactive'):
            raise ValueError('recovery_service_state_unknown')
        return state == 'active'

    def client(self):
        config = self.base.parse_manager_config(self.read_config())
        _, client = self.base.existing_manager_client(config, self.checker, self.manager)
        return client

    def read_group_and_repositories(self):
        group, repos = self.base.read_group(self.client(), {})
        if not self.base.group_policy_matches(group):
            raise ValueError('recovery_group_policy_drift')
        return group, self.base.repository_map(repos)

    def stop_service(self):
        self.base.safe_command(['systemctl', 'stop', self.base.SERVICE])
        self.base.wait_service(False)

    def write_config(self, config):
        expected = self.base.secure_regular(self.backup / 'manager.toml.before',
                                             self.metadata['config_sha256'])
        if config != expected:
            raise ValueError('recovery_backup_differs')
        self.base.atomic_write(self.base.CONFIG, config, self.metadata['config_uid'],
                               self.metadata['config_gid'], self.metadata['config_mode'])

    def start_service(self):
        if not self.service_running():
            self.base.safe_command(['systemctl', 'start', self.base.SERVICE])
            self.base.wait_service(True)

    def restore_group(self, group):
        self.base.patch_group(self.client(), group['selected_workflows'])

    def verify_restored(self, before):
        self.verify_fixed_resources(before)
        self.base.verify_restored_files(self.metadata)
        group, repos = self.read_group_and_repositories()
        if group != before.group or repos != before.repositories or not self.service_running():
            raise ValueError('recovery_final_readback_differs')
        config = self.base.parse_manager_config(self.read_config())
        self.manager.validate_org_config(config)
        if set(self.manager.configured_org_workflow_refs(config)) != set(group['selected_workflows']):
            raise ValueError('recovery_manager_group_differs')

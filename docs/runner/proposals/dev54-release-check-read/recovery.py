"""Recovery order for the journal-bound release admission change.

An adapter supplies protected reads, atomic writes and the existing systemd
credential route. This module does not access credentials or perform IO.
"""


def restore(adapter, before, after):
    """Restore the exact original state from any journal-bound partial state."""
    adapter.ensure_empty()
    adapter.verify_fixed_resources(before)
    config = adapter.read_config()
    if config not in (before.config, after.config):
        raise ValueError('recovery_config_drift')
    if adapter.service_running():
        group, repositories = adapter.read_group_and_repositories()
        if group not in (before.group, after.group):
            raise ValueError('recovery_group_drift')
        if repositories != before.repositories:
            raise ValueError('recovery_repository_drift')
        adapter.stop_service()
        adapter.ensure_empty()
        adapter.verify_fixed_resources(before)
        if adapter.read_config() != config:
            raise ValueError('recovery_config_drift')
    if config != before.config:
        adapter.write_config(before.config)
    # systemd credential files disappear while stopped. Reacquire the existing
    # credential through the restarted service, without persisting its value.
    adapter.start_service()
    adapter.ensure_empty()
    adapter.verify_fixed_resources(before)
    if adapter.read_config() != before.config:
        raise ValueError('recovery_config_drift')
    group, repositories = adapter.read_group_and_repositories()
    if repositories != before.repositories:
        raise ValueError('recovery_repository_drift')
    if group == after.group:
        adapter.restore_group(before.group)
    elif group != before.group:
        raise ValueError('recovery_group_drift')
    adapter.verify_restored(before)

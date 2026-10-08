import base64
import contextlib
import errno
import io
import json
import os
import stat
from pathlib import Path
import shutil
import subprocess
import struct
import tempfile
import unittest
from unittest import mock

import ci_runner_host_helper as helper


class HostHelperTests(unittest.TestCase):
    request_id = "a" * 32

    def setUp(self):
        self.policy = mock.patch.object(helper, "active_pool_mode", return_value="legacy")
        self.policy.start()
        self.addCleanup(self.policy.stop)

    def test_root_policy_rejects_mode_and_profile_bypass(self):
        legacy = {"max_concurrency": 1, "runner_vcpus": 4,
                  "runner_memory_mib": 6144, "runner_label": "trusted-heavy",
                  "host_memory_reserve_mib": 4096, "min_free_disk_gib": 60}
        four = {**legacy, "pool_mode": "four", "max_concurrency": 4,
                "medium_runner_label": "trusted-medium", "medium_runner_vcpus": 2,
                "medium_runner_memory_mib": 3072, "max_heavy": 2, "max_medium": 2,
                "heavy_disk_reservation_gib": 24,
                "medium_disk_reservation_gib": 4, "storage_mode": "bounded-loop"}
        self.assertEqual(helper.policy_mode_from_mapping(legacy), "legacy")
        self.assertEqual(helper.policy_mode_from_mapping(four), "four")
        with self.assertRaisesRegex(helper.ProtocolError, "policy is not approved"):
            helper.policy_mode_from_mapping({**four, "heavy_disk_reservation_gib": 12})
        with self.assertRaises(helper.ProtocolError):
            helper.policy_mode_from_mapping({**four, "storage_mode": "sparse"})
        with mock.patch.object(helper, "active_pool_mode", return_value="four"):
            with self.assertRaises(helper.ProtocolError):
                helper.enforce_launch_mode(None)
        with self.assertRaises(helper.ProtocolError):
            helper.enforce_launch_mode("medium")

    def test_four_pool_socket_rejects_legacy_launch_before_jit(self):
        request = {"v": 1, "id": self.request_id, "op": "launch", "lease": "job-1",
                   "vcpus": 4, "memory_mib": 6144}
        connection = self.connection_for_uid(1002)
        connection.recv.return_value = json.dumps(request).encode()
        with mock.patch.object(helper, "active_pool_mode", return_value="four"), \
                mock.patch.object(helper, "launch") as launch:
            helper.serve_connection(connection, expected_uid=1002)
        launch.assert_not_called()
        connection.recv.assert_called_once()
        self.assertEqual(self.decoded_response(connection)["error"], "invalid_request")

    @mock.patch.object(helper.os, "geteuid", return_value=0)
    @mock.patch.object(helper, "run")
    def test_four_pool_direct_launch_rejects_legacy_overlay(self, run, _euid):
        with mock.patch.object(helper, "active_pool_mode", return_value="four"):
            with self.assertRaisesRegex(helper.ProtocolError, "requires a bounded profile"):
                helper.launch("legacy-attempt", b"aml0")
        run.assert_not_called()

    def test_bounded_profiles_reject_resource_and_slot_drift(self):
        self.assertEqual(helper.validate_resources(4, 6144, "heavy"), (4, 6144))
        self.assertEqual(helper.validate_resources(2, 3072, "medium"), (2, 3072))
        for profile, cpu, memory in (("medium", 4, 6144), ("heavy", 2, 3072),
                                     ("unknown", 2, 3072)):
            with self.subTest(profile=profile), self.assertRaises(helper.ProtocolError):
                helper.validate_resources(cpu, memory, profile)
        self.assertTrue(helper.profile_has_slot({"heavy": 1, "medium": 2}, "heavy"))
        self.assertFalse(helper.profile_has_slot({"heavy": 2, "medium": 1}, "heavy"))
        self.assertFalse(helper.profile_has_slot({"heavy": 2, "medium": 2}, "medium"))

    def test_bounded_launch_requires_positive_reserved_disk_headroom(self):
        self.assertEqual(helper.PROFILE_DISK_GIB["heavy"], 24)
        self.assertTrue(helper.reservation_fits(98 * 1024**3, "heavy"))
        self.assertFalse(helper.reservation_fits(85 * 1024**3, "heavy"))
        self.assertFalse(helper.reservation_fits(63 * 1024**3, "medium"))

    def test_recorded_heavy_accepts_only_full_legacy_12_or_new_24_gib_backing(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "profile").write_text("heavy\n", encoding="ascii")
            backing = directory / "bounded.img"
            backing.write_bytes(b"")
            real_stat = Path.stat
            size = 12 * 1024**3

            def backing_stat(path, *args, **kwargs):
                if path == backing:
                    return mock.Mock(st_size=size, st_blocks=size // 512,
                                     st_mode=stat.S_IFREG | 0o600)
                return real_stat(path, *args, **kwargs)

            with mock.patch.object(helper, "lease_dir", return_value=directory), \
                    mock.patch.object(helper, "bounded_paths", return_value=(
                        directory, backing, directory / "work")), \
                    mock.patch.object(Path, "stat", autospec=True,
                                      side_effect=backing_stat), \
                    mock.patch.object(helper, "verify_bounded_mount", return_value=True):
                for gib in (12, 24):
                    size = gib * 1024**3
                    with self.subTest(gib=gib):
                        self.assertEqual(helper.recorded_profile("lease"), "heavy")
                for gib in (8, 16, 32):
                    size = gib * 1024**3
                    with self.subTest(gib=gib), self.assertRaisesRegex(
                            RuntimeError, "bounded backing file"):
                        helper.recorded_profile("lease")

    @mock.patch.object(helper, "run")
    @mock.patch.object(helper.shutil, "disk_usage", return_value=mock.Mock(free=98 * 1024**3))
    def test_new_heavy_allocates_24_gib_before_mount(self, _disk, run):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            backing = directory / "bounded.img"
            paths = (directory, backing, directory / "work")
            with mock.patch.object(helper, "bounded_paths", return_value=paths):
                with self.assertRaisesRegex(RuntimeError, "not fully allocated"):
                    helper.create_bounded_storage("new-heavy", "heavy", 994)
            run.assert_called_once_with([
                "fallocate", "--length", str(24 * 1024**3), str(backing)])

    @mock.patch.object(helper, "local_mount_source", return_value="/dev/mapper/other")
    @mock.patch.object(helper, "host_mount_source", return_value="/dev/mapper/other")
    def test_bounded_mount_rejects_unexpected_device(self, _source, _local):
        with self.assertRaisesRegex(RuntimeError, "not loop-backed"):
            helper.verify_bounded_mount(Path("/lease/bounded.img"), Path("/lease/work"))

    @mock.patch.object(helper, "run")
    def test_local_mount_queries_visible_exact_target(self, run):
        run.return_value = mock.Mock(returncode=0, stderr="", stdout=(
            '{"filesystems":[{"source":"/dev/loop8","target":"/lease/work"}]}'))
        self.assertEqual(helper.local_mount_source(Path("/lease/work")), "/dev/loop8")
        self.assertEqual(run.call_args.args[0], [
            "findmnt", "--kernel", "--uniq", "--json", "--output", "SOURCE,TARGET",
            "--mountpoint", "/lease/work"])

    @mock.patch.object(helper, "run")
    def test_host_mount_queries_visible_exact_target(self, run):
        run.return_value = mock.Mock(returncode=0, stderr="", stdout=(
            '{"filesystems":[{"source":"/dev/loop8","target":"/lease/work"}]}'))
        self.assertEqual(helper.host_mount_source(Path("/lease/work")), "/dev/loop8")
        self.assertEqual(run.call_args.args[0], [
            "nsenter", "--mount=/proc/1/ns/mnt", "--", "findmnt", "--kernel",
            "--uniq", "--json", "--output", "SOURCE,TARGET", "--mountpoint",
            "/lease/work"])

    @mock.patch.object(helper, "run")
    def test_mount_query_rejects_parent_wrong_source_and_ambiguous_records(self, run):
        path = Path("/lease/work")
        for record in (
            '{"filesystems":[{"source":"/dev/root","target":"/lease"}]}',
            '{"filesystems":[{"source":"/dev/mapper/other","target":"/lease/work"}]}',
            '{"filesystems":[{"source":"/dev/loop7","target":"/lease/work"},'
            '{"source":"/dev/loop8","target":"/lease/work"}]}',
            '{"filesystems":[{"source":"/dev/loop8"}]}',
            'not json',
        ):
            with self.subTest(record=record):
                run.return_value = mock.Mock(returncode=0, stderr="", stdout=record)
                with self.assertRaisesRegex(RuntimeError, "mount state cannot be verified"):
                    helper.local_mount_source(path)

    @mock.patch.object(helper, "run")
    def test_mount_query_reports_absent_only_for_empty_not_found(self, run):
        run.return_value = mock.Mock(returncode=1, stdout="", stderr="")
        self.assertIsNone(helper.local_mount_source(Path("/lease/work")))
        run.return_value = mock.Mock(returncode=1, stdout="unexpected output", stderr="")
        with self.assertRaisesRegex(RuntimeError, "mount state cannot be verified"):
            helper.local_mount_source(Path("/lease/work"))
        run.return_value = mock.Mock(returncode=1, stdout="", stderr="findmnt query failed")
        with self.assertRaisesRegex(RuntimeError, "mount state cannot be verified"):
            helper.local_mount_source(Path("/lease/work"))

    def test_findmnt_uniq_fixture_keeps_later_stacked_mount(self):
        if not shutil.which("findmnt"):
            self.skipTest("findmnt is only installed on Linux")
        with tempfile.TemporaryDirectory() as temporary:
            mountinfo = Path(temporary) / "mountinfo"
            mountinfo.write_text(
                "100 1 7:7 / /lease/work rw - ext4 /dev/loop7 rw\n"
                "101 1 7:8 / /lease/work rw - ext4 /dev/loop8 rw\n",
                encoding="ascii")
            result = subprocess.run([
                "findmnt", "--kernel", "--tab-file", str(mountinfo), "--uniq",
                "--json", "--output", "SOURCE,TARGET", "--mountpoint", "/lease/work",
            ], text=True, capture_output=True, check=True)
        self.assertEqual(json.loads(result.stdout), {"filesystems": [
            {"source": "/dev/loop8", "target": "/lease/work"}]})

    @mock.patch.object(helper, "run")
    @mock.patch.object(helper, "local_mount_source", return_value=None)
    @mock.patch.object(helper, "host_mount_source", side_effect=["/dev/loop7", "/dev/loop7"])
    def test_bounded_cleanup_preserves_storage_when_umount_does_not_complete(
            self, _source, _local, run):
        with mock.patch.object(helper, "associated_loop", return_value="/dev/loop7"), \
                mock.patch.object(helper, "validated_bounded_profile", return_value=(
                    "heavy", Path("/lease/bounded.img"), Path("/lease/work"),
                    mock.Mock(st_size=12 * 1024**3))), \
                mock.patch.object(helper, "verify_loop_binding"), \
                mock.patch.object(helper.shutil, "rmtree") as rmtree:
            with self.assertRaisesRegex(RuntimeError, "mount remains active"):
                helper.remove_bounded_storage("a")
            rmtree.assert_not_called()

    @mock.patch.object(helper, "run")
    @mock.patch.object(helper, "local_mount_source", return_value=None)
    @mock.patch.object(helper, "host_mount_source", side_effect=["/dev/loop7", None])
    def test_bounded_cleanup_allows_discard_drift_after_domain_destroy(
            self, _host, _local, _run):
        def verify_identity(_backing, _loop, *, require_discard=True):
            if require_discard:
                raise RuntimeError("runner loop discard is enabled")

        with mock.patch.object(helper, "associated_loop",
                               side_effect=["/dev/loop7", None]), \
                mock.patch.object(helper, "validated_bounded_profile", return_value=(
                    "heavy", Path("/lease/bounded.img"), Path("/lease/work"),
                    mock.Mock(st_size=12 * 1024**3))), \
                mock.patch.object(helper, "verify_loop_binding",
                                  side_effect=verify_identity) as verify, \
                mock.patch.object(helper.shutil, "rmtree") as rmtree:
            helper.remove_bounded_storage("a")
        self.assertEqual(verify.call_count, 2)
        self.assertTrue(all(call.kwargs == {"require_discard": False}
                            for call in verify.call_args_list))
        rmtree.assert_called_once()

    @mock.patch.object(helper, "run")
    @mock.patch.object(helper.shutil, "disk_usage", return_value=mock.Mock(free=97 * 1024**3))
    def test_partial_backing_allocation_stops_before_mount_or_vm(self, _disk, run):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            paths = (directory, directory / "bounded.img", directory / "work")
            with mock.patch.object(helper, "bounded_paths", return_value=paths):
                with self.assertRaisesRegex(RuntimeError, "not fully allocated"):
                    helper.create_bounded_storage("lease", "medium", 994)
        self.assertEqual([call.args[0][0] for call in run.call_args_list], ["fallocate"])

    def test_shortened_namespace_path_uses_kernel_file_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            backing = Path(temporary) / "bounded.img"
            backing.write_bytes(b"test")
            stat_result = backing.stat()
            identity = (helper.kernel_encode_dev(stat_result.st_dev), stat_result.st_ino,
                        0, 0, 7)
            with mock.patch.object(helper, "loop_status", return_value=identity), \
                    mock.patch.object(helper, "associated_loop", return_value="/dev/loop7"), \
                    mock.patch.object(helper, "run", return_value=mock.Mock(stdout="4\n")), \
                    mock.patch.object(helper, "loop_discard_limit", return_value=0):
                # The kernel/sysfs pathname can become /lease/bounded.img after
                # the helper mount namespace that attached the loop exits.
                helper.verify_loop_binding(backing, "/dev/loop7")

    def test_kernel_loop_status_reads_device_inode_and_rejects_alias_device(self):
        payload = struct.pack("=QQQQQIIII", 123, 456, 0, 0, 0, 7, 0, 0, 0)

        def fill_status(_fd, command, buffer, mutate):
            self.assertEqual(command, 0x4C05)
            self.assertTrue(mutate)
            buffer[:len(payload)] = payload

        with mock.patch.object(helper.os, "open", return_value=31), \
                mock.patch.object(helper.os, "fstat", return_value=mock.Mock(
                    st_mode=stat.S_IFBLK | 0o660, st_rdev=os.makedev(7, 7))), \
                mock.patch.object(helper.fcntl, "ioctl", side_effect=fill_status), \
                mock.patch.object(helper.os, "close") as close:
            self.assertEqual(helper.loop_status("/dev/loop7"), (123, 456, 0, 0, 7))
            close.assert_called_once_with(31)
        with mock.patch.object(helper.os, "open", return_value=31), \
                mock.patch.object(helper.os, "fstat", return_value=mock.Mock(
                    st_mode=stat.S_IFBLK | 0o660, st_rdev=os.makedev(7, 8))), \
                mock.patch.object(helper.fcntl, "ioctl") as ioctl, \
                mock.patch.object(helper.os, "close"):
            with self.assertRaisesRegex(RuntimeError, "device identity"):
                helper.loop_status("/dev/loop7")
            ioctl.assert_not_called()

    def test_kernel_device_encoding_matches_linux_huge_encode_dev(self):
        self.assertEqual(helper.kernel_encode_dev(os.makedev(8, 258)),
                         (8 << 8) | 2 | (256 << 12))

    def test_associated_loop_finds_shortened_path_by_inode(self):
        with tempfile.TemporaryDirectory() as temporary:
            backing = Path(temporary) / "bounded.img"
            backing.write_bytes(b"test")
            stat_result = backing.stat()
            identity = (helper.kernel_encode_dev(stat_result.st_dev), stat_result.st_ino,
                        0, 0, 7)
            with mock.patch.object(helper.os, "listdir", return_value=[
                "loop7", "loop-control", "loop8"]), \
                    mock.patch.object(helper, "loop_status", side_effect=[
                        identity, OSError(errno.ENXIO, "unbound")]):
                self.assertEqual(helper.associated_loop(backing), "/dev/loop7")

    def test_wrong_loop_backing_stops_before_mount_or_vm(self):
        with tempfile.TemporaryDirectory() as temporary:
            backing = Path(temporary) / "bounded.img"
            backing.write_bytes(b"test")
            with mock.patch.object(helper, "loop_status",
                                   return_value=(123, 456, 0, 0, 7)):
                with self.assertRaisesRegex(RuntimeError, "does not match lease"):
                    helper.verify_loop_binding(backing, "/dev/loop7")

    def test_loop_binding_rejects_offset_sizelimit_and_wrong_number(self):
        with tempfile.TemporaryDirectory() as temporary:
            backing = Path(temporary) / "bounded.img"
            backing.write_bytes(b"test")
            stat_result = backing.stat()
            dev = helper.kernel_encode_dev(stat_result.st_dev)
            for offset, limit, number in ((1, 0, 7), (0, 1, 7), (0, 0, 8)):
                with self.subTest(offset=offset, limit=limit, number=number), \
                        mock.patch.object(helper, "loop_status",
                                          return_value=(dev, stat_result.st_ino,
                                                        offset, limit, number)):
                    with self.assertRaisesRegex(RuntimeError, "does not match lease"):
                        helper.verify_loop_binding(backing, "/dev/loop7")

    def test_loop_binding_rejects_symlink(self):
        with tempfile.TemporaryDirectory() as temporary:
            backing = Path(temporary) / "bounded.img"
            backing.write_bytes(b"test")
            alias = Path(temporary) / "alias.img"
            alias.symlink_to(backing)
            with mock.patch.object(helper, "loop_status") as status:
                with self.assertRaisesRegex(RuntimeError, "backing file"):
                    helper.verify_loop_binding(alias, "/dev/loop7")
            status.assert_not_called()

    def test_associated_loop_rejects_duplicate_kernel_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            backing = Path(temporary) / "bounded.img"
            backing.write_bytes(b"test")
            stat_result = backing.stat()
            identity = (helper.kernel_encode_dev(stat_result.st_dev), stat_result.st_ino,
                        0, 0, 7)
            with mock.patch.object(helper.os, "listdir", return_value=["loop7", "loop8"]), \
                    mock.patch.object(helper, "loop_status", side_effect=[
                        identity, (*identity[:4], 8)]):
                with self.assertRaisesRegex(RuntimeError, "multiple loop"):
                    helper.associated_loop(backing)

    def test_associated_loop_propagates_unexpected_ioctl_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            backing = Path(temporary) / "bounded.img"
            backing.write_bytes(b"test")
            for error in (errno.EIO, errno.ENOENT):
                with self.subTest(error=error), \
                        mock.patch.object(helper.os, "listdir", return_value=["loop7"]), \
                        mock.patch.object(helper, "loop_status",
                                          side_effect=OSError(error, "query failed")):
                    with self.assertRaisesRegex(RuntimeError, "loop association"):
                        helper.associated_loop(backing)

    def test_cleanup_detaches_namespace_shortened_loop_after_unmount(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "lease"
            directory.mkdir()
            backing = directory / "bounded.img"
            backing.write_bytes(b"")
            with backing.open("r+b") as handle:
                handle.truncate(12 * 1024**3)
            (directory / "profile").write_text("heavy\n", encoding="ascii")
            mountpoint = directory / "work"
            mountpoint.mkdir()
            metadata = backing.stat()
            identity = (helper.kernel_encode_dev(metadata.st_dev), metadata.st_ino,
                        0, 0, 7)
            detached = False

            def status(_loop):
                if detached:
                    raise OSError(errno.ENXIO, "unbound")
                return identity

            def command(args, **_kwargs):
                nonlocal detached
                if args[:2] == ["losetup", "--detach"]:
                    detached = True
                return mock.Mock(returncode=0, stdout=f"{backing.stat().st_size}\n"
                                 if args[0] == "blockdev"
                                 else "", stderr="")

            with mock.patch.object(helper, "bounded_paths", return_value=(
                    directory, backing, mountpoint)), \
                    mock.patch.object(helper, "lease_dir", return_value=directory), \
                    mock.patch.object(helper, "host_mount_source",
                                      side_effect=["/dev/loop7", None]), \
                    mock.patch.object(helper, "local_mount_source", return_value=None), \
                    mock.patch.object(helper.os, "listdir", return_value=["loop7"]), \
                    mock.patch.object(helper, "loop_status", side_effect=status), \
                    mock.patch.object(helper, "run", side_effect=command):
                helper.remove_bounded_storage("lease")
            self.assertTrue(detached)
            self.assertFalse(directory.exists())

    def test_cleanup_rejects_unknown_backing_size_before_unmount(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "profile").write_text("heavy\n", encoding="ascii")
            backing = directory / "bounded.img"
            with backing.open("wb") as handle:
                handle.truncate(16 * 1024**3)
            paths = (directory, backing, directory / "work")
            with mock.patch.object(helper, "bounded_paths", return_value=paths), \
                    mock.patch.object(helper, "lease_dir", return_value=directory), \
                    mock.patch.object(helper, "host_mount_source") as mount:
                with self.assertRaisesRegex(RuntimeError, "bounded backing file"):
                    helper.remove_bounded_storage("lease")
                mount.assert_not_called()

    def test_cleanup_empty_failed_reservation_only_when_no_mount_or_loop(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "lease"
            directory.mkdir()
            (directory / "profile").write_text("heavy\n", encoding="ascii")
            backing = directory / "bounded.img"
            backing.write_bytes(b"")
            paths = (directory, backing, directory / "work")
            with mock.patch.object(helper, "bounded_paths", return_value=paths), \
                    mock.patch.object(helper, "lease_dir", return_value=directory), \
                    mock.patch.object(helper, "host_mount_source", return_value=None), \
                    mock.patch.object(helper, "local_mount_source", return_value=None), \
                    mock.patch.object(helper, "associated_loop", return_value="/dev/loop7"):
                with self.assertRaisesRegex(RuntimeError, "still attached"):
                    helper.remove_bounded_storage("lease")
            self.assertTrue(directory.exists())
            with mock.patch.object(helper, "bounded_paths", return_value=paths), \
                    mock.patch.object(helper, "lease_dir", return_value=directory), \
                    mock.patch.object(helper, "host_mount_source", return_value=None), \
                    mock.patch.object(helper, "local_mount_source", return_value=None), \
                    mock.patch.object(helper, "associated_loop", return_value=None):
                helper.remove_bounded_storage("lease")
            self.assertFalse(directory.exists())

    @mock.patch.object(helper.shutil, "disk_usage", return_value=mock.Mock(free=97 * 1024**3))
    def test_storage_creation_rejects_wrong_loop_backing_before_mount(self, _disk):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            backing = directory / "bounded.img"
            paths = (directory, backing, directory / "work")
            real_stat = Path.stat
            reserved = 4 * 1024**3

            def backing_stat(path, *args, **kwargs):
                if path == backing:
                    return mock.Mock(st_size=reserved, st_blocks=reserved // 512,
                                     st_mode=stat.S_IFREG | 0o600,
                                     st_dev=1, st_ino=123)
                return real_stat(path, *args, **kwargs)

            def runner_command(command, **_kwargs):
                if command[:3] == ["losetup", "--find", "--show"]:
                    return mock.Mock(stdout="/dev/loop7\n")
                return mock.Mock(stdout="")

            with mock.patch.object(helper, "bounded_paths", return_value=paths), \
                    mock.patch.object(helper, "associated_loop", return_value=None), \
                    mock.patch.object(helper, "loop_status",
                                      return_value=(123, 456, 0, 0, 7)), \
                    mock.patch.object(Path, "stat", autospec=True, side_effect=backing_stat), \
                    mock.patch.object(helper, "run", side_effect=runner_command) as run:
                with self.assertRaisesRegex(RuntimeError, "does not match lease"):
                    helper.create_bounded_storage("lease", "medium", 994)
            commands = [call.args[0][0] for call in run.call_args_list]
            self.assertNotIn("systemd-mount", commands)
            self.assertNotIn("qemu-img", commands)

    @mock.patch.object(helper, "local_mount_source", return_value=None)
    @mock.patch.object(helper, "host_mount_source", return_value="/dev/loop7")
    def test_mount_must_be_visible_in_helper_namespace(self, _host, _local):
        with self.assertRaisesRegex(RuntimeError, "namespaces disagree"):
            helper.verify_bounded_mount(Path("/lease/bounded.img"), Path("/lease/work"))

    @mock.patch.object(helper, "run")
    def test_discard_must_read_back_zero_before_guest_storage(self, run):
        run.side_effect = [mock.Mock(stdout="0\n"), mock.Mock(stdout="1\n")]
        with self.assertRaisesRegex(RuntimeError, "could not be disabled"):
            helper.disable_loop_discard("/dev/loop7")
        self.assertEqual(run.call_args_list[0].kwargs["input_text"], "0\n")
        self.assertEqual(run.call_args_list[0].args[0][-1],
                         "/sys/class/block/loop7/queue/discard_max_bytes")

    @mock.patch.object(helper, "run")
    def test_destroy_preserves_storage_when_domain_remains(self, run):
        run.side_effect = [mock.Mock(stdout="", returncode=0),
                           mock.Mock(stdout="", returncode=0),
                           mock.Mock(stdout="", returncode=0),
                           mock.Mock(stdout="sanctuary-ci-a\n", returncode=0)]
        with mock.patch.object(helper, "remove_bounded_storage") as remove:
            with self.assertRaisesRegex(RuntimeError, "domain remains defined"):
                helper.destroy("a")
            remove.assert_not_called()

    @staticmethod
    def decoded_response(connection):
        return json.loads(connection.sendall.call_args.args[0])

    @staticmethod
    def connection_for_uid(uid):
        connection = mock.Mock()
        connection.getsockopt.return_value = struct.pack("3i", 123, uid, 456)
        return connection

    def test_domain_name_is_namespaced(self):
        self.assertEqual(helper.name("job-9"), "sanctuary-ci-job-9")

    def test_cloud_init_queues_runner_after_cloud_final_without_deadlock(self):
        user_data = helper.cloud_init_user_data(base64.b64encode(b"opaque-jit"))
        for filename in (".runner", ".credentials", ".credentials_rsaparams",
                         ".runner_migrated", ".credentials_migrated"):
            self.assertNotIn(f"/opt/actions-runner/{filename}", user_data)
        self.assertIn("- [chown, root:ci-runner, /opt/actions-runner]", user_data)
        self.assertIn("- [chmod, '1770', /opt/actions-runner]", user_data)
        self.assertIn("ReadWritePaths=/opt/actions-runner", user_data)
        self.assertIn("UMask=0022", user_data)
        self.assertNotIn("UMask=0077", user_data)
        self.assertIn("- [install, -d, -o, ci-runner, -g, ci-runner, -m, '0700', /run/ci-runner]", user_data)
        self.assertIn("permissions: '0600'", user_data)
        self.assertIn("systemctl daemon-reload", user_data)
        self.assertIn("systemctl start --no-block ci-runner-job.service", user_data)
        self.assertNotIn("- [systemctl, start, --no-block, ci-runner-job.service]", user_data)
        self.assertNotIn("- [systemctl, start, ci-runner-job.service]", user_data)

    def test_cloud_init_selects_verified_overlay2_before_runner_start(self):
        user_data = helper.cloud_init_user_data(base64.b64encode(b"opaque-jit"))

        self.assertIn('"containerd-snapshotter": false', user_data)
        self.assertIn('"storage-driver": "overlay2"', user_data)
        self.assertIn("docker info --format '{{.Driver}}'", user_data)
        verify = user_data.index("test \"$(docker info --format '{{.Driver}}')\" = overlay2")
        runner = user_data.index("systemctl start --no-block ci-runner-job.service")
        self.assertLess(verify, runner)
        runcmd = user_data.split("runcmd:\n", 1)[1]
        self.assertEqual(runcmd, "  - [/usr/local/sbin/ci-runner-prepare-docker]\n")

    def test_cloud_init_network_uses_name_independent_dhcp(self):
        network_config = helper.cloud_init_network_config()

        self.assertIn('name: "en*"', network_config)
        self.assertIn("dhcp4: true", network_config)
        self.assertIn("dhcp6: false", network_config)
        self.assertNotIn("ens3", network_config)

    def test_lease_directory_cannot_escape_overlay_root(self):
        for value in ("../etc", "a/b", "white space", ""):
            with self.subTest(value=value), self.assertRaises(ValueError):
                helper.lease_dir(value)

    @mock.patch.object(helper, "run")
    @mock.patch.object(helper, "resolved_base_image")
    @mock.patch.object(helper.os, "geteuid", return_value=0)
    def test_launch_rejects_second_domain_before_mutation(self, _euid, image, run):
        image.return_value = mock.Mock()
        run.return_value = mock.Mock(
            stdout="sanctuary-ci-existing-1\n"
        )
        with self.assertRaisesRegex(RuntimeError, "slots are occupied"):
            helper.launch("new", b"aml0")

    @mock.patch.object(helper.Path, "is_file", return_value=True)
    @mock.patch.object(helper.Path, "resolve")
    def test_base_image_must_resolve_to_digest_versioned_file(self, resolve, _is_file):
        resolve.return_value = helper.IMAGE_ROOT / ("ubuntu-24.04-runner-" + "a" * 64 + ".qcow2")
        self.assertEqual(helper.resolved_base_image(), resolve.return_value)

        resolve.return_value = helper.IMAGE_ROOT / "ubuntu-24.04-runner.qcow2"
        with self.assertRaisesRegex(ValueError, "digest-versioned"):
            helper.resolved_base_image()

    @mock.patch.object(helper.os, "geteuid", return_value=0)
    @mock.patch.object(helper, "resolved_base_image")
    @mock.patch.object(helper, "run")
    def test_launch_schedules_independent_host_expiry_timer(self, run, image, _euid):
        with tempfile.TemporaryDirectory() as directory:
            stdin = mock.Mock()
            stdin.buffer.read.return_value = b"aml0"
            image.return_value = helper.IMAGE_ROOT / ("ubuntu-24.04-runner-" + "a" * 64 + ".qcow2")
            run.return_value = mock.Mock(stdout="")
            with mock.patch.object(helper, "lease_dir", return_value=Path(directory) / "lease"), \
                    mock.patch.object(helper.sys, "stdin", stdin), \
                    mock.patch.object(helper, "qemu_identity", return_value=(64055, 994)), \
                    mock.patch.object(helper, "set_qemu_access") as set_access:
                helper.launch("lease")

            lease = Path(directory) / "lease"
            self.assertEqual(set_access.call_args_list, [
                mock.call(lease, 0, 994, 0o710),
                mock.call(lease / "root.qcow2", 64055, 994, 0o600),
                mock.call(lease / "seed.iso", 64055, 994, 0o600),
            ])

        virt_install = next(
            call.args[0] for call in run.call_args_list
            if call.args[0][0] == "virt-install"
        )
        self.assertEqual(
            virt_install[virt_install.index("--vcpus") + 1],
            "4"
        )
        self.assertEqual(
            virt_install[virt_install.index("--memory") + 1], str(helper.MEMORY_MIB)
        )
        cloud_localds = next(
            call.args[0] for call in run.call_args_list
            if call.args[0][0] == "cloud-localds"
        )
        self.assertTrue(
            any(argument.startswith("--network-config=") for argument in cloud_localds)
        )

        self.assertIn(mock.call([
            "systemd-run", "--unit", "sanctuary-ci-expire-lease",
            "--on-active", "7200s", "--timer-property", "AccuracySec=30s",
            "--property=NoNewPrivileges=yes", "--property=ProtectSystem=strict",
            "--property=ProtectHome=yes", "--property=PrivateTmp=yes",
            "--property=ReadWritePaths=/var/lib/ci-runner/overlay /run/lock",
            "--property=RestrictAddressFamilies=AF_UNIX",
            "/usr/local/libexec/ci-runner-host-helper", "destroy", "lease",
        ]), run.call_args_list)

    @mock.patch.object(helper.os, "chmod")
    @mock.patch.object(helper.os, "chown")
    def test_qemu_access_sets_exact_owner_group_and_mode(self, chown, chmod):
        path = Path("/runner/seed.iso")
        helper.set_qemu_access(path, 64055, 994, 0o600)
        chown.assert_called_once_with(path, 64055, 994)
        chmod.assert_called_once_with(path, 0o600)

    def test_serve_rejects_untrusted_peer_before_reading_request(self):
        connection = self.connection_for_uid(1001)

        helper.serve_connection(connection, expected_uid=1002)

        connection.recv.assert_not_called()
        self.assertEqual(self.decoded_response(connection)["error"], "unauthorized")

    def test_parse_request_enforces_exact_operation_schemas(self):
        valid = [
            {"v": 1, "id": self.request_id, "op": "list"},
            {"v": 1, "id": self.request_id, "op": "launch", "lease": "job-1",
             "vcpus": 4, "memory_mib": 6144},
            {"v": 1, "id": self.request_id, "op": "destroy", "lease": "job-1"},
        ]
        for request in valid:
            with self.subTest(request=request):
                self.assertEqual(helper.parse_request(json.dumps(request).encode()), request)

        invalid = [
            {"v": True, "id": self.request_id, "op": "list"},
            {"v": 1, "id": self.request_id.upper(), "op": "list"},
            {"v": 1, "id": self.request_id, "op": "unknown"},
            {"v": 1, "id": self.request_id, "op": "list", "lease": "extra"},
            {"v": 1, "id": self.request_id, "op": "destroy"},
            {"v": 1, "id": self.request_id, "op": "launch", "lease": "../escape"},
            {"v": 1, "id": self.request_id, "op": "launch", "lease": "job-1",
             "vcpus": True, "memory_mib": 6144},
            {"v": 1, "id": self.request_id, "op": "launch", "lease": "job-1",
             "vcpus": 8, "memory_mib": 6144},
            {"v": 1, "id": self.request_id, "op": "launch", "lease": "job-1",
             "vcpus": 4, "memory_mib": 4096},
            {"v": 1, "id": self.request_id, "op": "launch", "lease": "job-1",
             "vcpus": 4, "memory_mib": 12288},
            {"v": 1, "id": self.request_id, "op": "list", "extra": False},
        ]
        for request in invalid:
            with self.subTest(request=request), self.assertRaises(helper.ProtocolError):
                helper.parse_request(json.dumps(request).encode())
        duplicate = ('{"v":1,"id":"' + self.request_id +
                     '","op":"list","op":"list"}').encode()
        with self.assertRaises(helper.ProtocolError):
            helper.parse_request(duplicate)

    def test_request_and_jit_packets_are_size_limited(self):
        with self.assertRaises(helper.ProtocolError):
            helper.parse_request(b"x" * (helper.MAX_REQUEST_BYTES + 1))
        with self.assertRaises(helper.ProtocolError):
            helper.validate_jit_payload(b"Y" * (helper.MAX_JIT_BYTES + 1))

    @mock.patch.object(helper, "launch")
    def test_serve_passes_validated_raw_jit_packet_to_launch(self, launch):
        connection = self.connection_for_uid(1002)
        request = {"v": 1, "id": self.request_id, "op": "launch", "lease": "job-1",
                   "vcpus": 4, "memory_mib": 6144}
        jit = base64.b64encode(b"ephemeral registration material")
        connection.recv.side_effect = [json.dumps(request).encode(), jit]
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(helper, "HELPER_LOCK", Path(directory) / "helper.lock"):
            helper.serve_connection(connection, expected_uid=1002)

        launch.assert_called_once_with("job-1", jit, vcpus=4, memory_mib=6144)
        self.assertEqual(connection.settimeout.call_args_list, [mock.call(5.0), mock.call(None)])
        self.assertEqual(self.decoded_response(connection), {
            "v": 1, "id": self.request_id, "ok": True, "result": None,
        })

    @mock.patch.object(helper, "launch")
    def test_serve_validates_jit_before_launch_mutation(self, launch):
        connection = self.connection_for_uid(1002)
        request = {"v": 1, "id": self.request_id, "op": "launch", "lease": "job-1",
                   "vcpus": 4, "memory_mib": 6144}
        connection.recv.side_effect = [json.dumps(request).encode(), b"not base64!!"]

        helper.serve_connection(connection, expected_uid=1002)

        launch.assert_not_called()
        self.assertEqual(self.decoded_response(connection)["error"], "invalid_request")
        self.assertEqual(self.decoded_response(connection)["id"], self.request_id)

    @mock.patch.object(helper, "launch")
    def test_operation_errors_are_sanitized_in_response(self, launch):
        connection = self.connection_for_uid(1002)
        request = {"v": 1, "id": self.request_id, "op": "launch", "lease": "job-1",
                   "vcpus": 4, "memory_mib": 6144}
        secret = "sensitive-jit-material"
        connection.recv.side_effect = [json.dumps(request).encode(), base64.b64encode(b"jit")]
        launch.side_effect = RuntimeError(secret)
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(helper, "HELPER_LOCK", Path(directory) / "helper.lock"), \
                contextlib.redirect_stderr(io.StringIO()) as stderr:
            helper.serve_connection(connection, expected_uid=1002)

        encoded = connection.sendall.call_args.args[0]
        self.assertNotIn(secret.encode(), encoded)
        self.assertNotIn(secret, stderr.getvalue())
        self.assertIn("RuntimeError", stderr.getvalue())
        self.assertEqual(json.loads(encoded)["error"], "operation_failed")

    @mock.patch.object(helper.subprocess, "run")
    def test_command_failure_keeps_output_secret_and_exposes_safe_stage(self, run):
        secret = "sensitive-command-output"
        run.side_effect = helper.subprocess.CalledProcessError(
            7, ["virt-install"], output="ignored", stderr=secret
        )

        with self.assertRaises(helper.HostCommandError) as raised:
            helper.run(["virt-install", "--connect", "qemu:///system"])

        self.assertEqual(raised.exception.command_name, "virt-install")
        self.assertEqual(raised.exception.returncode, 7)
        self.assertNotIn(secret, str(raised.exception))

    def test_command_diagnostic_rejects_unknown_name_and_normalizes_timeout(self):
        error = helper.HostCommandError("untrusted-command-name", "anything")
        self.assertEqual(error.command_name, "unknown")
        self.assertEqual(error.returncode, "timeout")
        self.assertEqual(str(error), "unknown rc=timeout")

    @mock.patch.object(helper, "run")
    def test_libvirt_commands_use_explicit_system_uri(self, run):
        run.return_value = mock.Mock(stdout="")
        helper.list_leases()
        helper.destroy("lease")

        for call in run.call_args_list:
            command = call.args[0]
            if command[0] == "virsh":
                self.assertEqual(command[1:3], ["--connect", "qemu:///system"])

    def test_response_falls_back_when_result_exceeds_packet_limit(self):
        encoded = helper.encode_response(self.request_id, result="x" * helper.MAX_RESPONSE_BYTES)
        self.assertLessEqual(len(encoded), helper.MAX_RESPONSE_BYTES)
        self.assertEqual(json.loads(encoded)["error"], "operation_failed")


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""eMMC 備份離線回歸；不建立 SSH 連線、不開啟真實區塊裝置。"""

from contextlib import contextmanager, redirect_stderr, redirect_stdout
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import shlex
import stat
import struct
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import bpi_h618_emmc_backup as backup


CID = "0123456789abcdef" * 2
CONTROLLER = "/sys/devices/platform/soc/TEST_ONLY.mmc"
RAW = bytes(range(256)) * 8


def sha(data):
    return hashlib.sha256(data).hexdigest()


def identity(expected=None):
    expected = expected or {"cid": CID, "bytes": len(RAW), "controller": CONTROLLER}
    return {**expected, "device": "/dev/mmcblk7", "devnum": "179:56", "type": "MMC",
            "partitions": [], "mounted": False, "swap": False, "holders": False}


class Clock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value


class FakeTransport:
    def __init__(self, raw=RAW):
        self.raw = raw
        self.archive = gzip.compress(raw, compresslevel=1, mtime=0)
        self.changes = {}
        self.code = 0
        self.calls = []
        self.closed = False
        self.drop_complete = False
        self.duplicate = False
        self.failure = None
        self.tick = 0

    def __call__(self, argv, deadline, monotonic):
        self.calls.append(argv)
        request = json.loads(shlex.split(argv[-1])[-1])
        common = {"schema": 1, "nonce": request["nonce"], "time_utc": "2026-01-01T00:00:00+00:00",
                  "identity": identity(request["expected"])}
        begin = {**common, "event": "start", "read_only": True}
        complete = {**common, "event": "complete", "elapsed_seconds": 1.0,
                    "raw": {"bytes": len(self.raw), "sha256": sha(self.raw)}}
        complete.update(self.changes)

        def encoded(record):
            return backup.PREFIX + json.dumps(record).encode() + b"\n"

        try:
            yield "stderr", encoded(begin)
            for start in range(0, len(self.archive), 31):
                monotonic.value += self.tick
                yield "stdout", self.archive[start:start + 31]
            if self.failure:
                raise self.failure
            if not self.drop_complete:
                yield "stderr", encoded(complete)
                if self.duplicate:
                    yield "stderr", encoded(complete)
            yield "exit", self.code
        finally:
            self.closed = True


class HostTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.config = self.root / "ssh-config"
        self.config.write_text("Host fake\n HostName 127.0.0.1\n StrictHostKeyChecking yes\n")
        self.output = self.root / "attempt"
        self.fake = FakeTransport()
        self.clock = Clock()

    def run_backup(self, **changes):
        args = {"ssh_config": self.config, "alias": "fake", "expected_cid": CID,
                "expected_size": len(RAW), "expected_controller": CONTROLLER,
                "output_dir": self.output, "transport": self.fake, "monotonic": self.clock}
        args.update(changes)
        with mock.patch.object(backup.subprocess, "Popen", side_effect=AssertionError("不可建立真 SSH")):
            return backup.backup(**args)

    def assert_partial(self):
        self.assertFalse((self.output / "emmc-userarea.img.gz").exists())
        self.assertFalse((self.output / "manifest.json").exists())
        self.assertTrue((self.output / "emmc-userarea.img.gz.partial").exists())
        record = json.loads((self.output / "manifest.json.partial").read_text())
        self.assertEqual(record["status"], "failed")
        self.assertFalse(record["ok"])
        self.assertFalse(record["media_written"])
        self.assertFalse(record["write_authorized"])
        return record

    def test_success_reopens_gzip_and_compares_both_raw_hashes(self):
        report = self.run_backup(expected_controller=CONTROLLER + "/")
        self.assertTrue(report["ok"])
        self.assertEqual(report["host"]["raw"], {"bytes": len(RAW), "sha256": sha(RAW)})
        self.assertEqual(report["host"]["compressed"], {"bytes": len(self.fake.archive), "sha256": sha(self.fake.archive)})
        self.assertEqual(report["host"]["raw"], report["remote"][1]["raw"])
        self.assertEqual(report["expected"]["controller"], CONTROLLER)
        self.assertFalse(report["restore_verified"])
        self.assertEqual(gzip.decompress((self.output / "emmc-userarea.img.gz").read_bytes()), RAW)
        self.assertEqual(json.loads((self.output / "manifest.json").read_text()), report)
        self.assertEqual({item.name for item in self.output.iterdir()},
                         {"emmc-userarea.img.gz", "manifest.json", "ssh-stderr.log"})
        self.assertEqual(self.output.stat().st_mode & 0o777, 0o700)
        self.assertTrue(all(item.stat().st_mode & 0o777 == 0o600 for item in self.output.iterdir()))
        self.assertTrue(self.fake.closed)
        self.assertEqual(report["ssh_config"]["sha256"], sha(self.config.read_bytes()))
        self.assertEqual(report["command_argv"], self.fake.calls[0])

    def test_ssh_arguments_force_hostkey_publickey_no_mux_and_no_tty(self):
        self.run_backup()
        argv = self.fake.calls[0]
        for value in ("StrictHostKeyChecking=yes", "BatchMode=yes", "UpdateHostKeys=no",
                      "PasswordAuthentication=no", "KbdInteractiveAuthentication=no", "ControlPath=none",
                      "ClearAllForwardings=yes", "PermitLocalCommand=no", "RequestTTY=no"):
            self.assertIn(value, argv)
        self.assertEqual(argv[0:3], ["ssh", "-F", str(self.config)])
        remote = shlex.split(argv[-1])
        self.assertEqual(remote[:3], ["python3", "-B", "-c"])
        self.assertEqual(remote[3], backup.REMOTE_SCRIPT)

    def test_truncated_corrupt_extra_member_and_extra_bytes_fail(self):
        variants = [self.fake.archive[:-1], self.fake.archive[:20] + b"invalid" + self.fake.archive[20:],
                    self.fake.archive + gzip.compress(b""), self.fake.archive + b"\0"]
        for index, data in enumerate(variants):
            with self.subTest(index=index):
                self.output = self.root / f"attempt-{index}"
                self.fake.archive = data
                with self.assertRaises(backup.BackupError):
                    self.run_backup()
                self.assert_partial()

    def test_wrong_remote_hash_nonce_identity_or_status_never_publishes(self):
        variants = [{"raw": {"bytes": len(RAW), "sha256": "0" * 64}}, {"nonce": "old"},
                    {"identity": {**identity(), "cid": "f" * 32}}, {"event": "error"},
                    {"raw": {"bytes": len(RAW) - 1, "sha256": sha(RAW)}},
                    {"identity": {**identity(), "type": "SD"}}]
        for index, changes in enumerate(variants):
            with self.subTest(index=index):
                self.output = self.root / f"attempt-{index}"
                self.fake.changes = changes
                with self.assertRaises(backup.BackupError):
                    self.run_backup()
                self.assert_partial()

    def test_missing_duplicate_completion_and_nonzero_exit_fail(self):
        for index, field in enumerate(("drop_complete", "duplicate", "code")):
            with self.subTest(field=field):
                self.fake = FakeTransport()
                setattr(self.fake, field, 1)
                self.output = self.root / f"attempt-{index}"
                with self.assertRaises(backup.BackupError):
                    self.run_backup()
                self.assert_partial()

    def test_decompressed_size_short_or_long_never_publishes(self):
        for index, data in enumerate((RAW[:-1], RAW + b"x")):
            self.fake = FakeTransport(data)
            self.fake.changes = {"raw": {"bytes": len(RAW), "sha256": sha(RAW)}}
            self.output = self.root / f"attempt-{index}"
            with self.assertRaises(backup.BackupError):
                self.run_backup()
            self.assert_partial()

    def test_absolute_timeout_continuous_stream_and_remote_failure_keep_partial(self):
        self.fake.tick = 0.3
        with self.assertRaisesRegex(backup.BackupError, "期限"):
            self.run_backup(timeout=1)
        self.assert_partial()
        self.assertTrue(self.fake.closed)
        self.output = self.root / "other"
        self.fake = FakeTransport()
        self.fake.failure = OSError("模擬傳輸中斷")
        with self.assertRaises(OSError):
            self.run_backup()
        self.assert_partial()

    def test_verification_shares_total_deadline(self):
        original = backup.verify_gzip

        def delayed(*args):
            self.clock.value += 2
            return original(*args)

        with mock.patch.object(backup, "verify_gzip", side_effect=delayed):
            with self.assertRaisesRegex(backup.BackupError, "期限"):
                self.run_backup(timeout=1)
        self.assert_partial()

    def test_high_ratio_zero_stream_uses_bounded_one_mib_decode_output(self):
        zero = bytes(backup.CHUNK)
        expected_size = 65 * backup.CHUNK
        digest = hashlib.sha256()
        for _ in range(65):
            digest.update(zero)
        for level in (1, 9):
            with self.subTest(level=level):
                encoder = zlib.compressobj(level, zlib.DEFLATED, 31)
                compressed = b"".join(encoder.compress(zero) for _ in range(65)) + encoder.flush()
                self.assertLess(len(compressed), backup.CHUNK)
                path = self.root / f"zeros-{level}.gz"
                path.write_bytes(compressed)
                real_decoder = zlib.decompressobj(31)
                sizes = []
                testcase = self

                class BoundedDecoder:
                    def __getattr__(self, name):
                        return getattr(real_decoder, name)

                    def decompress(self, data, max_length):
                        testcase.assertEqual(max_length, backup.CHUNK)
                        decoded = real_decoder.decompress(data, max_length)
                        testcase.assertLessEqual(len(decoded), backup.CHUNK)
                        sizes.append(len(decoded))
                        return decoded

                with backup.safe.open_root(self.root) as directory, \
                        mock.patch.object(backup.zlib, "decompressobj", return_value=BoundedDecoder()):
                    result = backup.verify_gzip(directory, path.name, expected_size, 10, self.clock)
                self.assertEqual(result["raw"], {"bytes": expected_size, "sha256": digest.hexdigest()})
                self.assertEqual(result["compressed"], {"bytes": len(compressed), "sha256": sha(compressed)})
                self.assertGreaterEqual(len(sizes), 65)
                self.assertEqual(sum(sizes), expected_size)

    def test_existing_output_and_symlink_ancestors_refuse_before_transport(self):
        self.output.mkdir()
        original = self.output / "original"
        original.write_bytes(b"unchanged")
        with self.assertRaises(OSError):
            self.run_backup()
        link = self.root / "linked"
        link.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(OSError):
            self.run_backup(output_dir=link / "new")
        self.assertEqual(original.read_bytes(), b"unchanged")
        self.assertFalse(self.fake.calls)

    def test_symlink_config_and_invalid_arguments_do_not_create_output(self):
        link = self.root / "config-link"
        link.symlink_to(self.config)
        with self.assertRaises(backup.BackupError):
            self.run_backup(ssh_config=link)
        for change in ({"alias": "-oProxyCommand=x"}, {"expected_cid": "bad"},
                       {"expected_size": len(RAW) + 1}, {"expected_controller": "/sys/devices/x/../y"},
                       {"timeout": float("nan")}, {"timeout": 0}, {"timeout": True}):
            with self.subTest(change=change), self.assertRaises(backup.BackupError):
                self.run_backup(**change)
        self.assertFalse(self.output.exists())
        self.assertFalse(self.fake.calls)

    def test_publication_failure_rolls_back_only_new_links(self):
        real_link = os.link

        def fail_manifest(source, destination, **kwargs):
            if destination == "manifest.json":
                raise OSError("模擬發布失敗")
            return real_link(source, destination, **kwargs)

        with mock.patch.object(backup.os, "link", side_effect=fail_manifest):
            with self.assertRaises(OSError):
                self.run_backup()
        self.assert_partial()
        self.assertFalse((self.output / "ssh-stderr.log").exists())

    def test_post_cleanup_fsync_failure_makes_cli_fail_without_success_manifest(self):
        real_fsync, real_backup = os.fsync, backup.backup
        injected = False

        def late_failure(fd):
            nonlocal injected
            if (not injected and (self.output / "manifest.json").exists()
                    and not (self.output / "manifest.json.partial").exists()):
                injected = True
                raise OSError("模擬清理後最後 fsync 失敗")
            return real_fsync(fd)

        def fake_backup(**kwargs):
            return real_backup(**kwargs, transport=self.fake, monotonic=self.clock)

        args = ["--ssh-config", str(self.config), "--alias", "fake", "--expected-cid", CID,
                "--expected-size", str(len(RAW)), "--expected-controller", CONTROLLER,
                "--output-dir", str(self.output)]
        with mock.patch.object(backup.os, "fsync", side_effect=late_failure), \
                mock.patch.object(backup, "backup", side_effect=fake_backup), \
                mock.patch.object(backup.subprocess, "Popen", side_effect=AssertionError("不可建立真 SSH")), \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as error:
            self.assertEqual(backup.main(args), 1)
        self.assertTrue(injected)
        self.assertFalse(json.loads(error.getvalue())["ok"])
        self.assert_partial()
        self.assertEqual((self.output / "emmc-userarea.img.gz.partial").read_bytes(), self.fake.archive)

    def test_manifest_context_exit_fsync_failure_revokes_publication(self):
        real_fsync = os.fsync
        injected = False

        def late_failure(fd):
            nonlocal injected
            if (not injected and (self.output / "manifest.json").exists()
                    and not (self.output / "manifest.json.partial").exists()
                    and stat.S_ISREG(os.fstat(fd).st_mode)):
                injected = True
                raise OSError("模擬清單 context 關閉前同步失敗")
            return real_fsync(fd)

        with mock.patch.object(backup.os, "fsync", side_effect=late_failure):
            with self.assertRaises(OSError):
                self.run_backup()
        self.assertTrue(injected)
        self.assert_partial()

    def test_manifest_close_failure_revokes_publication_and_preserves_partial(self):
        real_open = backup.safe.open_file
        injected = False

        @contextmanager
        def failing_close(directory, name, *, create=False):
            nonlocal injected
            with real_open(directory, name, create=create) as stream:
                yield stream
            if create and name == "manifest.json.partial" and not injected:
                injected = True
                raise OSError("模擬清單已關閉但回報錯誤")

        with mock.patch.object(backup.safe, "open_file", side_effect=failing_close):
            with self.assertRaises(OSError):
                self.run_backup()
        self.assertTrue(injected)
        self.assert_partial()

    def test_directory_close_failure_revokes_publication(self):
        real_close = os.close
        injected = False

        def failing_close(fd):
            nonlocal injected
            info = os.fstat(fd)
            fail = (not injected and stat.S_ISDIR(info.st_mode) and self.output.exists()
                    and info.st_ino == self.output.stat().st_ino
                    and (self.output / "manifest.json").exists()
                    and not (self.output / "manifest.json.partial").exists())
            real_close(fd)
            if fail:
                injected = True
                raise OSError("模擬目錄已關閉但回報錯誤")

        with mock.patch.object(backup.os, "close", side_effect=failing_close):
            with self.assertRaises(OSError):
                self.run_backup()
        self.assertTrue(injected)
        self.assert_partial()

    def test_publication_does_not_replace_existing_symlink(self):
        real_verify = backup.verify_gzip
        target = self.root / "original"
        target.write_bytes(b"original")

        def conflict(*args):
            verified = real_verify(*args)
            (self.output / "emmc-userarea.img.gz").symlink_to(target)
            return verified

        with mock.patch.object(backup, "verify_gzip", side_effect=conflict):
            with self.assertRaises(OSError):
                self.run_backup()
        self.assertEqual(target.read_bytes(), b"original")
        self.assertTrue((self.output / "emmc-userarea.img.gz").is_symlink())
        self.assertFalse((self.output / "manifest.json").exists())

    def test_partial_symlink_substitution_after_verification_is_rejected(self):
        real_verify = backup.verify_gzip
        original = self.root / "original"
        original.write_bytes(b"original")

        def replace_source(*args):
            verified = real_verify(*args)
            path = self.output / "emmc-userarea.img.gz.partial"
            path.unlink()
            path.symlink_to(original)
            return verified

        with mock.patch.object(backup, "verify_gzip", side_effect=replace_source):
            with self.assertRaisesRegex(backup.BackupError, "發布前"):
                self.run_backup()
        self.assertFalse((self.output / "emmc-userarea.img.gz").exists())
        self.assertFalse((self.output / "manifest.json").exists())
        self.assertEqual(original.read_bytes(), b"original")

    def test_failure_manifest_retains_received_hashes_and_unvalidated_remote_evidence(self):
        self.fake.code = 255
        with self.assertRaises(backup.BackupError):
            self.run_backup()
        record = self.assert_partial()
        self.assertEqual(record["host"]["received_compressed"],
                         {"bytes": len(self.fake.archive), "sha256": sha(self.fake.archive)})
        self.assertEqual(record["remote_observed_unvalidated"][1]["raw"]["sha256"], sha(RAW))
        self.assertIsNone(record["remote"])
        self.assertEqual(record["ssh_exitcode"], 255)

    def test_disk_readback_tampering_is_detected(self):
        real_verify = backup.verify_gzip

        def tamper(directory, name, *args):
            (self.output / name).write_bytes(gzip.compress(b"x" * len(RAW), compresslevel=1, mtime=0))
            return real_verify(directory, name, *args)

        with mock.patch.object(backup, "verify_gzip", side_effect=tamper):
            with self.assertRaises(backup.BackupError):
                self.run_backup()
        self.assert_partial()

    def test_diagnostics_and_compressed_stream_have_size_limits(self):
        def oversized(argv, deadline, clock):
            yield "stderr", b"x" * (backup.MAX_STDERR + 1)

        with self.assertRaises(backup.BackupError):
            self.run_backup(transport=oversized)
        self.assert_partial()
        self.output = self.root / "other"

        def oversized_archive(argv, deadline, clock):
            yield "stdout", bytes(len(RAW) + len(RAW) // 100 + backup.CHUNK + 1)

        with self.assertRaises(backup.BackupError):
            self.run_backup(transport=oversized_archive)
        self.assert_partial()

    def test_cli_help_does_not_access_ssh_or_block_devices(self):
        with mock.patch.object(backup.subprocess, "Popen") as opened, redirect_stdout(io.StringIO()) as output:
            with self.assertRaises(SystemExit) as stopped:
                backup.main(["--help"])
        self.assertEqual(stopped.exception.code, 0)
        self.assertIn("唯讀備份", output.getvalue())
        opened.assert_not_called()


class RemoteTests(unittest.TestCase):
    def setUp(self):
        self.remote = {"__name__": "backup_remote_test"}
        exec(compile(backup.REMOTE_SCRIPT, "<遠端唯讀程式>", "exec"), self.remote)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.sysroot, self.procroot, self.devroot = [self.root / name for name in ("sys", "proc", "dev")]
        self.controller = self.root / "controller"
        self.device = self.controller / "mmc_host/mmc7/mmc7:0001"
        self.device.mkdir(parents=True)
        self.base = self.sysroot / "mmcblk7"
        (self.base / "holders").mkdir(parents=True)
        (self.base / "device").symlink_to(self.device, target_is_directory=True)
        (self.base / "dev").write_text("179:56\n")
        (self.base / "size").write_text(str(len(RAW) // 512))
        (self.device / "type").write_text("MMC\n")
        (self.device / "cid").write_text(CID)
        (self.procroot / "self").mkdir(parents=True)
        (self.procroot / "self/mountinfo").write_text("1 0 8:1 / / rw - ext4 /dev/root rw\n")
        (self.procroot / "swaps").write_text("Filename Type Size Used Priority\n")
        self.expected = {"cid": CID, "bytes": len(RAW), "controller": str(self.controller)}
        real_stat = os.stat
        self.nodes = {str(self.devroot / "mmcblk7"): SimpleNamespace(st_mode=stat.S_IFBLK, st_rdev=os.makedev(179, 56))}

        def fake_stat(path, *args, **kwargs):
            return self.nodes[str(path)] if str(path) in self.nodes else real_stat(path, *args, **kwargs)

        patcher = mock.patch.object(self.remote["os"], "stat", side_effect=fake_stat)
        patcher.start()
        self.addCleanup(patcher.stop)

    def inspect(self):
        return self.remote["inspect"](self.expected, self.sysroot, self.procroot, self.devroot)

    def partition(self):
        path = self.base / "mmcblk7p1"
        (path / "holders").mkdir(parents=True)
        (path / "dev").write_text("179:57")
        return path

    def test_dynamic_name_cid_type_controller_and_exact_capacity(self):
        actual = self.inspect()
        self.assertEqual(actual["device"], str(self.devroot / "mmcblk7"))
        self.assertEqual(actual["bytes"], len(RAW))
        for file, value in ((self.device / "type", "SD"), (self.device / "cid", "f" * 32),
                            (self.base / "size", "1")):
            original = file.read_text()
            file.write_text(value)
            with self.assertRaises(ValueError):
                self.inspect()
            file.write_text(original)
        self.expected["controller"] += "0"
        with self.assertRaises(ValueError):
            self.inspect()

    def test_ambiguous_matching_devices_are_rejected(self):
        other = self.sysroot / "mmcblk2"
        other.mkdir()
        (other / "device").symlink_to(self.device, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "唯一"):
            self.inspect()

    def test_whole_device_and_child_mounts_and_holders_are_rejected(self):
        child = self.partition()
        for number in ("179:56", "179:57"):
            (self.procroot / "self/mountinfo").write_text(f"1 0 {number} / /mnt rw - ext4 /dev/alias rw\n")
            with self.assertRaisesRegex(ValueError, "掛載"):
                self.inspect()
        (self.procroot / "self/mountinfo").write_text("")
        for entry in (self.base, child):
            holder = entry / "holders/dm-0"
            holder.touch()
            with self.assertRaisesRegex(ValueError, "holders"):
                self.inspect()
            holder.unlink()

    def test_swap_devices_aliases_and_swap_files_are_rejected(self):
        self.partition()
        for index, node in enumerate((SimpleNamespace(st_mode=stat.S_IFBLK, st_rdev=os.makedev(179, 56)),
                                      SimpleNamespace(st_mode=stat.S_IFBLK, st_rdev=os.makedev(179, 57)),
                                      SimpleNamespace(st_mode=stat.S_IFREG, st_dev=os.makedev(179, 57)))):
            path = str(self.devroot / f"alias-{index}")
            self.nodes[path] = node
            (self.procroot / "swaps").write_text(f"Filename Type Size Used Priority\n{path} file 1 0 -2\n")
            with self.assertRaisesRegex(ValueError, "swap"):
                self.inspect()

    def test_nonblock_node_and_sysfs_number_mismatch_are_rejected(self):
        node = self.nodes[str(self.devroot / "mmcblk7")]
        node.st_mode = stat.S_IFREG
        with self.assertRaisesRegex(ValueError, "區塊裝置"):
            self.inspect()
        node.st_mode = stat.S_IFBLK
        node.st_rdev = os.makedev(179, 0)
        with self.assertRaisesRegex(ValueError, "裝置號"):
            self.inspect()

    def test_fd_size_and_number_are_verified_with_readonly_ioctl(self):
        node = SimpleNamespace(st_mode=stat.S_IFBLK, st_rdev=os.makedev(179, 56))
        with mock.patch.object(self.remote["os"], "fstat", return_value=node), \
                mock.patch.object(self.remote["fcntl"], "ioctl", return_value=struct.pack("=Q", len(RAW))) as ioctl:
            self.remote["check_fd"](99, identity())
            ioctl.assert_called_once_with(99, 0x80081272, bytes(8))
            ioctl.return_value = struct.pack("=Q", len(RAW) - 512)
            with self.assertRaisesRegex(ValueError, "容量"):
                self.remote["check_fd"](99, identity())

    def test_remote_stream_hashes_raw_and_uses_level_one_gzip(self):
        output = io.BytesIO()
        with mock.patch.object(self.remote["os"], "read", side_effect=[RAW[:7], RAW[7:]]) as read:
            result = self.remote["stream_fd"](99, len(RAW), output, lambda: None)
        self.assertEqual(result, {"bytes": len(RAW), "sha256": sha(RAW)})
        self.assertEqual(gzip.decompress(output.getvalue()), RAW)
        self.assertEqual(output.getvalue()[8], 4)
        self.assertEqual(read.call_args_list, [mock.call(99, len(RAW)), mock.call(99, len(RAW) - 7)])
        with mock.patch.object(self.remote["os"], "read", return_value=b""):
            with self.assertRaisesRegex(ValueError, "截斷"):
                self.remote["stream_fd"](99, len(RAW), io.BytesIO(), lambda: None)

    def test_remote_run_only_opens_readonly_exclusive_fd_and_checks_three_times(self):
        emitted = []
        actual = identity()
        with mock.patch.dict(self.remote, {"inspect": mock.Mock(return_value=actual), "check_fd": mock.Mock()}), \
                mock.patch.object(self.remote["os"], "open", return_value=99) as opened, \
                mock.patch.object(self.remote["os"], "close") as closed, \
                mock.patch.object(self.remote["os"], "read", return_value=RAW), \
                mock.patch.object(self.remote["signal"], "signal"), \
                mock.patch.object(self.remote["signal"], "setitimer"):
            self.remote["run"]({"expected": self.expected, "timeout": 1},
                               lambda event, **fields: emitted.append((event, fields)), io.BytesIO())
            flags = opened.call_args.args[1]
            self.assertEqual(flags & os.O_ACCMODE, os.O_RDONLY)
            self.assertTrue(flags & os.O_EXCL)
            self.assertTrue(flags & os.O_NOFOLLOW)
            self.assertEqual(self.remote["inspect"].call_count, 3)
            self.assertEqual(self.remote["check_fd"].call_count, 2)
        closed.assert_called_once_with(99)
        self.assertEqual([event for event, _ in emitted], ["start", "complete"])

    def test_remote_identity_change_after_read_never_emits_complete(self):
        actual = identity()
        emitted = []
        with mock.patch.dict(self.remote, {"inspect": mock.Mock(side_effect=[actual, actual, {**actual, "mounted": True}]),
                                           "check_fd": mock.Mock()}), \
                mock.patch.object(self.remote["os"], "open", return_value=99), \
                mock.patch.object(self.remote["os"], "close") as closed, \
                mock.patch.object(self.remote["os"], "read", return_value=RAW), \
                mock.patch.object(self.remote["signal"], "signal"), \
                mock.patch.object(self.remote["signal"], "setitimer"):
            with self.assertRaisesRegex(ValueError, "狀態已變更"):
                self.remote["run"]({"expected": self.expected, "timeout": 1},
                                   lambda event, **fields: emitted.append(event), io.BytesIO())
        self.assertEqual(emitted, ["start"])
        closed.assert_called_once_with(99)


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.process = mock.Mock(pid=123456)
        self.process.stdout.fileno.return_value = 101
        self.process.stderr.fileno.return_value = 102
        self.process.wait.return_value = 0
        self.process.poll.return_value = 0
        self.selector = mock.Mock()
        self.selector.__enter__ = mock.Mock(return_value=self.selector)
        self.selector.__exit__ = mock.Mock(return_value=False)
        self.keys = {}
        self.selector.register.side_effect = lambda stream, events, name: self.keys.update(
            {stream: SimpleNamespace(fileobj=stream, data=name)})
        self.selector.unregister.side_effect = lambda stream: self.keys.pop(stream)
        self.selector.get_map.side_effect = lambda: self.keys
        self.selector.select.side_effect = lambda timeout: [(key, 1) for key in list(self.keys.values())]
        self.clock = Clock()

    def test_selector_consumes_both_pipes_without_ssh_and_waits_for_exit(self):
        with mock.patch.object(backup.subprocess, "Popen", return_value=self.process) as opened, \
                mock.patch.object(backup.selectors, "DefaultSelector", return_value=self.selector), \
                mock.patch.object(backup.os, "read", side_effect=[b"gzip", b"evidence", b"", b""]), \
                mock.patch.object(backup.os, "killpg") as killed:
            events = list(backup.ssh_stream(["FAKE_ONLY"], 1, self.clock))
        self.assertEqual(events, [("stdout", b"gzip"), ("stderr", b"evidence"), ("exit", 0)])
        opened.assert_called_once()
        killed.assert_not_called()
        self.process.stdout.close.assert_called_once()
        self.process.stderr.close.assert_called_once()

    def test_consumer_abort_kills_and_reaps_fake_process(self):
        self.process.poll.return_value = None
        with mock.patch.object(backup.subprocess, "Popen", return_value=self.process), \
                mock.patch.object(backup.selectors, "DefaultSelector", return_value=self.selector), \
                mock.patch.object(backup.os, "read", return_value=b"partial"), \
                mock.patch.object(backup.os, "killpg") as killed:
            events = backup.ssh_stream(["FAKE_ONLY"], 1, self.clock)
            self.assertEqual(next(events), ("stdout", b"partial"))
            events.close()
        killed.assert_called_once_with(self.process.pid, backup.signal.SIGKILL)
        self.process.wait.assert_called_once_with(timeout=5)

    def test_expired_deadline_never_starts_process(self):
        self.clock.value = 2
        with mock.patch.object(backup.subprocess, "Popen") as opened:
            with self.assertRaisesRegex(backup.BackupError, "期限"):
                list(backup.ssh_stream(["FAKE_ONLY"], 1, self.clock))
        opened.assert_not_called()


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""受限部署離線測試；所有 SSH、區塊裝置、媒體寫入及計時訊號均使用替身。"""

from contextlib import ExitStack, redirect_stdout
import copy
import gzip
import hashlib
import io
import json
import lzma
import os
from pathlib import Path
import shlex
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import bpi_h618_emmc_deploy as deploy


CID = "0123456789abcdef" * 2
SD_CID = "abcdef0123456789" * 2
CONTROLLER = "/sys/devices/platform/soc/TEST_EMMC.mmc"
SD_CONTROLLER = "/sys/devices/platform/soc/TEST_SD.mmc"
CAPACITY = 8192
RAW = bytes(range(256)) * 4
EXPECTED = {"cid": CID, "bytes": CAPACITY, "controller": CONTROLLER}
PROTECTED = {"cid": SD_CID, "controller": SD_CONTROLLER}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def source_summary(raw, compressed):
    return {"raw": {"bytes": len(raw), "sha256": sha(raw)},
            "compressed": {"bytes": len(compressed), "sha256": sha(compressed)}}


def emmc_identity():
    return {**EXPECTED, "type": "MMC", "device": "/dev/mmcblk7", "devnum": "179:56",
            "partitions": [], "mounted": False, "swap": False, "holders": False}


def sd_identity():
    return {**PROTECTED, "type": "SD", "device": "/dev/mmcblk3", "devnum": "179:24", "bytes": 8 * deploy.CHUNK}


def rescue_identity():
    return {"schema": "bpi-h618-rescue-v1", "kernel": "TEST_KERNEL", "root_ram": True,
            "root_fs": "tmpfs", "root_dev": "0:21", "identity_sha256": sha(b"identity")}


def remote_state(request, final=False):
    sd = {"identity": sd_identity(), "prefix": {"bytes": 4 * deploy.CHUNK, "sha256": sha(b"SD")}}
    state = {"status": "writing", "bytes_written": 0, "attempted_end": 0, "write_started": False,
             "range": {"start": 0, "end_exclusive": request["source"]["raw"]["bytes"]},
             "bootable": False, "boot_selected": False, "boot_verified": False,
             "identity": emmc_identity(), "rescue": rescue_identity(), "sd_before": sd, "sd_after": None}
    if final:
        state.update(status="verified", bytes_written=request["source"]["raw"]["bytes"],
                     attempted_end=request["source"]["raw"]["bytes"], write_started=True,
                     source=request["source"], readback=request["source"]["raw"], sd_after=sd)
    return state


class Clock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value


class FakeUpload:
    def __init__(self):
        self.calls = []
        self.sent = bytearray()
        self.closed = False
        self.before_send = None
        self.final_changes = {}
        self.code = 0
        self.fail = False
        self.no_verified = False
        self.tick = 0

    def __call__(self, argv, chunks, deadline, clock):
        self.calls.append(argv)
        request = json.loads(shlex.split(argv[-1])[-1])

        def event(name, state):
            return deploy.PREFIX + json.dumps({"schema": 1, "nonce": request["nonce"],
                "time_utc": "2026-01-01T00:00:00+00:00", "event": name, "state": state}).encode() + b"\n"

        try:
            yield "stdout", event("ready", remote_state(request))
            if self.before_send:
                self.before_send()
            for data in chunks:
                clock.value += self.tick
                self.sent.extend(data)
                yield "sent", len(data)
            final = remote_state(request, True)
            final.update(self.final_changes)
            if self.fail:
                final.update(status="failed", bytes_written=7, attempted_end=len(RAW), error="模擬短寫失敗")
                yield "stdout", event("failed", final)
            elif not self.no_verified:
                yield "stdout", event("verified", final)
            yield "exit", self.code
        finally:
            self.closed = True


class HostTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.output = self.root / "attempt"
        self.config = self.root / "ssh-config"
        self.config.write_text("Host fake\n HostName 127.0.0.1\n StrictHostKeyChecking yes\n")
        self.source = self.root / "source.img.xz"
        self.xz = lzma.compress(RAW)
        self.source.write_bytes(self.xz)
        self.backup_dir = self.root / "backup"
        self.backup_dir.mkdir()
        self.backup_path = self.backup_dir / "manifest.json"
        original = bytes(CAPACITY)
        archive = gzip.compress(original, mtime=0)
        (self.backup_dir / "emmc-userarea.img.gz").write_bytes(archive)
        raw = {"bytes": len(original), "sha256": sha(original)}
        compressed = {"bytes": len(archive), "sha256": sha(archive)}
        common = {"schema": 1, "nonce": "backup-test-nonce", "time_utc": "2026-01-01T00:00:00+00:00",
                  "identity": emmc_identity()}
        self.backup_record = {"schema": 1, "kind": "bpi-h618-emmc-userarea-backup", "status": "complete",
            "ok": True, "expected": EXPECTED, "ssh_exitcode": 0, "nonce": common["nonce"],
            "artifact": "emmc-userarea.img.gz", "host": {"raw": raw, "compressed": compressed,
                "received_compressed": compressed}, "remote": [{**common, "event": "start", "read_only": True},
                {**common, "event": "complete", "raw": raw, "elapsed_seconds": 1.0}]}
        self.backup_path.write_text(json.dumps(self.backup_record))
        self.fake, self.clock = FakeUpload(), Clock()

    def run_deploy(self, **changes):
        args = {"confirm_overwrite": True, "backup_manifest": self.backup_path, "source": self.source,
            "compressed_sha256": sha(self.xz), "raw_sha256": sha(RAW), "raw_size": len(RAW),
            "expected_cid": CID, "expected_size": CAPACITY, "expected_controller": CONTROLLER,
            "protected_sd_cid": SD_CID, "protected_sd_controller": SD_CONTROLLER,
            "ssh_config": self.config, "alias": "fake", "output_dir": self.output,
            "transport": self.fake, "monotonic": self.clock}
        args.update(changes)
        with mock.patch.object(deploy.subprocess, "Popen", side_effect=AssertionError("不可啟動真 SSH")):
            return deploy.deploy(**args)

    def assert_failed(self):
        self.assertFalse((self.output / "receipt.json").exists())
        record = json.loads((self.output / "receipt.json.partial").read_text())
        self.assertEqual(record["status"], "failed")
        self.assertFalse(record["ok"])
        self.assertFalse(record["bootable"])
        self.assertFalse(record["boot_selected"])
        self.assertFalse(record["restore_verified"])
        return record

    def pins(self):
        return {"backup_manifest_sha256": sha(self.backup_path.read_bytes()),
                "ssh_config_sha256": sha(self.config.read_bytes()),
                "sd_prefix": {"bytes": 4 * deploy.CHUNK, "sha256": sha(b"SD")},
                "rescue": {key: rescue_identity()[key] for key in ("kernel", "identity_sha256")}}

    def test_pinned_preflight_uses_fixed_ssh_snapshot(self):
        result = self.run_deploy(pinned_preflight=self.pins())
        snapshot = self.output / "ssh-config.snapshot"
        self.assertEqual(snapshot.read_bytes(), self.config.read_bytes())
        self.assertEqual(snapshot.stat().st_mode & 0o777, 0o400)
        self.assertEqual(self.fake.calls[0][self.fake.calls[0].index("-F") + 1], str(snapshot))
        self.assertEqual(result["request"]["pinned_preflight"], self.pins())

    def test_pinned_changed_backup_or_config_never_starts_transport(self):
        for field in ("backup_manifest_sha256", "ssh_config_sha256"):
            with self.subTest(field=field):
                pins = self.pins()
                pins[field] = "0" * 64
                with self.assertRaises(deploy.DeployError):
                    self.run_deploy(pinned_preflight=pins)
                self.assertEqual(self.fake.calls, [])

    def test_pinned_config_change_during_source_check_prevents_transport(self):
        pins = self.pins()
        original = deploy.remote_namespace
        def namespace():
            result = original()
            process = result["process_xz"]
            def changed(*args, **kwargs):
                output = process(*args, **kwargs)
                self.config.write_text("Host changed\n")
                return output
            result["process_xz"] = changed
            return result
        with mock.patch.object(deploy, "remote_namespace", side_effect=namespace), \
                self.assertRaises(deploy.DeployError):
            self.run_deploy(pinned_preflight=pins)
        self.assertEqual(self.fake.calls, [])
        self.assert_failed()

    def test_pinned_response_rejects_wrong_rescue_or_prefix(self):
        for field in ("rescue", "sd_before"):
            with self.subTest(field=field):
                pins = self.pins()
                request = {"source": source_summary(RAW, self.xz), "expected": EXPECTED,
                           "protected_sd": PROTECTED, "pinned_preflight": pins,
                           "backup_manifest_sha256": pins["backup_manifest_sha256"]}
                state = remote_state(request)
                if field == "rescue":
                    state[field]["kernel"] = "OTHER_KERNEL"
                else:
                    state[field]["prefix"]["sha256"] = "0" * 64
                with self.assertRaises(deploy.DeployError):
                    deploy.validate_state(state, request)

    def test_pinned_backup_change_during_source_check_prevents_transport(self):
        original = deploy.remote_namespace
        for filename in ("manifest.json", "emmc-userarea.img.gz"):
            with self.subTest(filename=filename):
                pins = self.pins()
                target = self.backup_dir / filename
                before = target.read_bytes()
                def namespace():
                    result = original()
                    process = result["process_xz"]
                    def changed(*args, **kwargs):
                        output = process(*args, **kwargs)
                        target.write_bytes(b"corrupted")
                        return output
                    result["process_xz"] = changed
                    return result
                self.output = self.root / ("attempt-" + filename)
                with mock.patch.object(deploy, "remote_namespace", side_effect=namespace), \
                        self.assertRaises(deploy.DeployError):
                    self.run_deploy(pinned_preflight=pins)
                self.assertEqual(self.fake.calls, [])
                self.assert_failed()
                target.write_bytes(before)

    def test_success_produces_only_verified_deploy_not_boot_or_restore_success(self):
        report = self.run_deploy()
        self.assertTrue(report["ok"])
        self.assertEqual(report["status"], "verified")
        self.assertFalse(report["bootable"])
        self.assertFalse(report["boot_selected"])
        self.assertFalse(report["restore_verified"])
        self.assertEqual(bytes(self.fake.sent), self.xz)
        self.assertTrue(self.fake.closed)
        self.assertEqual(json.loads((self.output / "receipt.json").read_text()), report)
        self.assertEqual(self.output.stat().st_mode & 0o777, 0o700)
        self.assertTrue(all(path.stat().st_mode & 0o777 == 0o600 for path in self.output.iterdir()))
        self.assertEqual(report["range"], {"start": 0, "end_exclusive": len(RAW)})

    def test_missing_confirmation_and_invalid_input_never_start_transport(self):
        for changes in ({"confirm_overwrite": False}, {"compressed_sha256": None}, {"raw_sha256": "bad"},
                        {"raw_size": CAPACITY + 512}, {"raw_size": True}, {"expected_cid": "bad"},
                        {"protected_sd_cid": CID}, {"protected_sd_controller": CONTROLLER},
                        {"timeout": 0}, {"timeout": float("nan")}, {"alias": "-oProxyCommand=x"}):
            with self.subTest(changes=changes), self.assertRaises(deploy.DeployError):
                self.run_deploy(**changes)
        self.assertFalse(self.fake.calls)
        self.assertFalse(self.output.exists())

    def test_bad_backup_status_cid_size_or_missing_evidence_prevents_write(self):
        variants = [{"ok": False}, {"status": "streaming"}, {"expected": {**EXPECTED, "cid": "f" * 32}},
                    {"expected": {**EXPECTED, "bytes": CAPACITY + 512}}, {"remote": []}, {"host": {}}]
        for fields in variants:
            with self.subTest(fields=fields):
                self.backup_path.write_text(json.dumps({**self.backup_record, **fields}))
                with self.assertRaises(deploy.DeployError):
                    self.run_deploy()
        self.assertFalse(self.fake.calls)

    def test_backup_gzip_missing_or_tampered_prevents_write(self):
        archive = self.backup_dir / "emmc-userarea.img.gz"
        archive.write_bytes(b"incorrect")
        with self.assertRaises(deploy.DeployError):
            self.run_deploy()
        archive.unlink()
        with self.assertRaises(OSError):
            self.run_deploy()
        self.assertFalse(self.fake.calls)

    def test_source_preflight_rejects_hash_truncation_and_symlinks_without_ssh(self):
        for changes in ({"compressed_sha256": "0" * 64}, {"raw_sha256": "0" * 64}, {"raw_size": 512}):
            with self.assertRaises(deploy.DeployError):
                self.run_deploy(**changes)
        self.source.write_bytes(self.xz[:-1])
        with self.assertRaises(deploy.DeployError):
            self.run_deploy(compressed_sha256=sha(self.xz[:-1]))
        link = self.root / "source-link.xz"
        link.symlink_to(self.source)
        with self.assertRaises(deploy.DeployError):
            self.run_deploy(source=link)
        self.assertFalse(self.fake.calls)

    def test_source_replaced_or_modified_after_preflight_stops_before_payload(self):
        for index, replace in enumerate((False, True)):
            self.fake = FakeUpload()
            self.output = self.root / f"attempt-{index}"
            self.source.write_bytes(self.xz)

            def mutate():
                if replace:
                    self.source.unlink()
                self.source.write_bytes(self.xz)

            self.fake.before_send = mutate
            with self.assertRaisesRegex(deploy.DeployError, "身分已變更"):
                self.run_deploy()
            self.assert_failed()
            self.assertFalse(self.fake.sent)

    def test_wrong_readback_or_sd_change_cannot_publish_verified(self):
        for index, changes in enumerate(({"readback": {"bytes": len(RAW), "sha256": "0" * 64}},
                                         {"sd_after": None}, {"bytes_written": len(RAW) - 1},
                                         {"attempted_end": len(RAW) + 1}, {"bootable": True})):
            self.output = self.root / f"attempt-{index}"
            self.fake.final_changes = changes
            with self.assertRaises(deploy.DeployError):
                self.run_deploy()
            self.assert_failed()

    def test_remote_failure_preserves_partial_bytes_and_possible_range(self):
        self.fake.fail = True
        with self.assertRaises(deploy.DeployError):
            self.run_deploy()
        report = self.assert_failed()
        self.assertEqual(report["remote_state"]["bytes_written"], 7)
        self.assertEqual(report["remote_state"]["attempted_end"], len(RAW))
        self.assertTrue(report["write_possible"])
        self.assertEqual(report["range"]["end_exclusive"], len(RAW))

    def test_no_verified_or_nonzero_exit_or_timeout_is_not_success(self):
        for index, setting in enumerate(("no_verified", "code", "tick")):
            self.fake = FakeUpload()
            setattr(self.fake, setting, 2)
            self.output = self.root / f"attempt-{index}"
            with self.assertRaises(deploy.DeployError):
                self.run_deploy(timeout=1)
            self.assert_failed()
            self.assertTrue(self.fake.closed)

    def test_publish_failure_and_post_link_fsync_failure_leave_no_success_receipt(self):
        with mock.patch.object(deploy.os, "link", side_effect=OSError("模擬發布失敗")):
            with self.assertRaises(OSError):
                self.run_deploy()
        self.assert_failed()
        self.output = self.root / "after-link"
        original = os.fsync
        failed = False

        def fail_once(fd):
            nonlocal failed
            if not failed and (self.output / "receipt.json").exists():
                failed = True
                raise OSError("模擬發布後目錄同步失敗")
            return original(fd)

        with mock.patch.object(deploy.os, "fsync", side_effect=fail_once):
            with self.assertRaises(OSError):
                self.run_deploy()
        self.assertTrue(failed)
        self.assert_failed()

    def test_initial_evidence_fsync_failure_never_starts_transport(self):
        original = os.fsync
        calls = 0

        def fail_once(fd):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("模擬初始證據同步失敗")
            return original(fd)

        with mock.patch.object(deploy.os, "fsync", side_effect=fail_once):
            with self.assertRaises(OSError):
                self.run_deploy()
        self.assertFalse(self.fake.calls)
        self.assert_failed()

    def test_publish_never_overwrites_existing_success_name_or_symlink(self):
        original = self.root / "original"
        original.write_bytes(b"unchanged")
        real_link = os.link

        def conflict(source, target, **kwargs):
            (self.output / "receipt.json").symlink_to(original)
            return real_link(source, target, **kwargs)

        with mock.patch.object(deploy.os, "link", side_effect=conflict):
            with self.assertRaises(OSError):
                self.run_deploy()
        self.assertEqual(original.read_bytes(), b"unchanged")
        self.assertTrue((self.output / "receipt.json").is_symlink())
        self.assertFalse(json.loads((self.output / "receipt.json.partial").read_text())["ok"])

    def test_timeout_during_publication_rolls_back_success_marker(self):
        original = os.fsync

        def delayed(fd):
            result = original(fd)
            if (self.output / "receipt.json").exists():
                self.clock.value = 2
            return result

        with mock.patch.object(deploy.os, "fsync", side_effect=delayed):
            with self.assertRaisesRegex(deploy.DeployError, "期限"):
                self.run_deploy(timeout=1)
        self.assert_failed()

    def test_malformed_remote_nested_state_is_rejected_as_regular_error(self):
        self.fake.final_changes = {"identity": None}
        with self.assertRaisesRegex(deploy.DeployError, "結構化"):
            self.run_deploy()
        self.assert_failed()

    def test_output_symlink_or_existing_directory_refuses_no_ssh(self):
        self.output.mkdir()
        with self.assertRaises(OSError):
            self.run_deploy()
        link = self.root / "link"
        link.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(OSError):
            self.run_deploy(output_dir=link / "new")
        self.assertFalse(self.fake.calls)

    def test_remote_program_executes_known_backup_string_without_running_main(self):
        namespace = deploy.remote_namespace()
        self.assertEqual(namespace["checks"]["__name__"], "_bpi_backup_checks")
        self.assertIn("inspect", namespace["checks"])
        self.assertIn("check_fd", namespace["checks"])
        self.assertTrue(deploy.remote_program().startswith("BACKUP_SOURCE = " + repr(deploy.backup.REMOTE_SCRIPT)))
        self.run_deploy()
        argv = self.fake.calls[0]
        self.assertIn("StrictHostKeyChecking=yes", argv)
        self.assertIn("ControlPath=none", argv)
        self.assertEqual(shlex.split(argv[-1])[3], deploy.remote_program())

    def test_cli_help_never_starts_transport(self):
        with mock.patch.object(deploy.subprocess, "Popen") as opened, redirect_stdout(io.StringIO()) as output:
            with self.assertRaises(SystemExit):
                deploy.main(["--help"])
        self.assertIn("受限", output.getvalue())
        opened.assert_not_called()


class XzTests(unittest.TestCase):
    def setUp(self):
        self.core = deploy.remote_namespace()

    def test_optional_diagnostics_on_success_truncation_and_read_limit(self):
        compressed = lzma.compress(RAW)
        for variant in ("success", "truncated", "read_limit"):
            data = compressed[:-1] if variant == "truncated" else compressed
            expected = source_summary(RAW, data)
            if variant == "read_limit":
                expected["compressed"]["bytes"] -= 1
            diagnostics, output = {}, bytearray()
            with self.subTest(variant=variant):
                if variant == "success":
                    result = self.core["process_xz"](io.BytesIO(data), expected, CAPACITY, lambda: None,
                                                      output.extend, diagnostics=diagnostics)
                    self.assertEqual(result, expected)
                else:
                    with self.assertRaises(ValueError):
                        self.core["process_xz"](io.BytesIO(data), expected, CAPACITY, lambda: None,
                                                output.extend, diagnostics=diagnostics)
                self.assertEqual(diagnostics["compressed"], {"bytes_read": len(data), "sha256": sha(data)})
                self.assertEqual(diagnostics["raw"], {"bytes_emitted": len(output), "sha256": sha(output)})
                self.assertIsNone(diagnostics["lzma_error"])

    def test_concatenation_and_padding_cross_read_boundaries(self):
        compressed = lzma.compress(RAW[:512]) + bytes(8) + lzma.compress(RAW[512:]) + bytes(12)
        expected = source_summary(RAW, compressed)

        class Fragments(io.BytesIO):
            def read(self, size):
                return super().read(min(size, 3))

        output = bytearray()
        result = self.core["process_xz"](Fragments(compressed), expected, CAPACITY, lambda: None, output.extend)
        self.assertEqual(result, expected)
        self.assertEqual(output, RAW)

    def test_high_ratio_requires_draining_decoder_without_new_input_and_bounds_output(self):
        zero = bytes(deploy.CHUNK)
        encoder = lzma.LZMACompressor(preset=0)
        compressed = b"".join(encoder.compress(zero) for _ in range(65)) + encoder.flush()
        digest = hashlib.sha256()
        for _ in range(65):
            digest.update(zero)
        expected = {"raw": {"bytes": 65 * deploy.CHUNK, "sha256": digest.hexdigest()},
                    "compressed": {"bytes": len(compressed), "sha256": sha(compressed)}}
        sizes = []

        def consume(data):
            self.assertLessEqual(len(data), deploy.CHUNK)
            sizes.append(len(data))

        self.assertLess(len(compressed), deploy.CHUNK)
        self.core["process_xz"](io.BytesIO(compressed), expected, expected["raw"]["bytes"], lambda: None, consume)
        self.assertEqual(sum(sizes), expected["raw"]["bytes"])
        self.assertGreaterEqual(len(sizes), 65)

    def test_excess_raw_or_capacity_stops_before_out_of_range_write(self):
        compressed = lzma.compress(RAW)
        expected = source_summary(RAW, compressed)
        sink = mock.Mock()
        with self.assertRaisesRegex(ValueError, "容量"):
            self.core["process_xz"](io.BytesIO(compressed), expected, 512, lambda: None, sink)
        expected["raw"]["bytes"] = 512
        with self.assertRaisesRegex(ValueError, "範圍"):
            self.core["process_xz"](io.BytesIO(compressed), expected, CAPACITY, lambda: None, sink)
        sink.assert_not_called()

    def test_invalid_padding_truncation_and_check_none_fail(self):
        variants = [lzma.compress(RAW)[:-1], lzma.compress(RAW) + bytes(3),
                    lzma.compress(RAW[:512]) + bytes(3) + lzma.compress(RAW[512:]),
                    lzma.compress(RAW, check=lzma.CHECK_NONE)]
        for compressed in variants:
            with self.assertRaises(ValueError):
                self.core["process_xz"](io.BytesIO(compressed), source_summary(RAW, compressed), CAPACITY, lambda: None)


class RemoteTests(unittest.TestCase):
    def setUp(self):
        self.core = deploy.remote_namespace()
        self.request = {"confirm_overwrite": True, "backup_verified": True, "expected": EXPECTED,
                        "protected_sd": PROTECTED, "source": source_summary(RAW, lzma.compress(RAW)), "timeout": 10}
        self.target = bytearray(b"\xaa" * CAPACITY)
        self.sd = bytearray(4 * deploy.CHUNK)
        self.events = []
        self.writes = []
        self.opens = []
        self.write_size = None
        self.zero_write = False
        self.write_error_after = None
        self.bad_readback = False
        self.mutate_sd = False
        self.fsync_error = False

    def rig(self):
        stack = ExitStack()
        sync_completed = cache_flushed = False
        stack.enter_context(mock.patch.dict(self.core, {"rescue_identity": mock.Mock(return_value=rescue_identity()),
                                                       "inspect_sd": mock.Mock(return_value=sd_identity())}))
        stack.enter_context(mock.patch.dict(self.core["checks"], {"inspect": mock.Mock(return_value=emmc_identity()),
                                                                 "check_fd": mock.Mock()}))

        def opened(path, flags):
            self.opens.append((path, flags))
            if path == sd_identity()["device"]:
                self.assertEqual(flags & os.O_ACCMODE, os.O_RDONLY)
                return 10
            self.assertEqual(path, emmc_identity()["device"])
            self.assertEqual(flags & os.O_ACCMODE, os.O_RDWR)
            self.assertTrue(flags & os.O_EXCL)
            self.assertTrue(flags & os.O_NOFOLLOW)
            return 20

        def pwrite(fd, data, offset):
            self.assertEqual(fd, 20)
            self.assertGreaterEqual(offset, 0)
            self.assertLessEqual(offset + len(data), len(RAW))
            if self.zero_write:
                return 0
            if self.write_error_after is not None and offset >= self.write_error_after:
                raise OSError("模擬部分寫入後失敗")
            count = min(len(data), self.write_size or len(data))
            self.target[offset:offset + count] = data[:count]
            self.writes.append((offset, count))
            return count

        def pread(fd, size, offset):
            if fd == 20:
                self.assertTrue(cache_flushed, "完整目標回讀必須在快取刷新成功之後")
            data = self.sd if fd == 10 else self.target
            result = bytes(data[offset:offset + size])
            return b"x" * len(result) if fd == 20 and self.bad_readback else result

        def fsync(fd):
            nonlocal sync_completed
            self.assertEqual(fd, 20)
            if self.mutate_sd:
                self.sd[0] = 1
            if self.fsync_error:
                raise OSError("模擬目標 fsync 失敗")
            sync_completed = True

        def flush(fd, command):
            nonlocal cache_flushed
            self.assertEqual((fd, command), (20, 0x1261))
            self.assertTrue(sync_completed, "丟棄快取前必須先完成 fsync")
            cache_flushed = True

        stack.enter_context(mock.patch.object(self.core["os"], "open", side_effect=opened))
        stack.enter_context(mock.patch.object(self.core["os"], "close"))
        stack.enter_context(mock.patch.object(self.core["os"], "pwrite", side_effect=pwrite))
        stack.enter_context(mock.patch.object(self.core["os"], "pread", side_effect=pread))
        stack.enter_context(mock.patch.object(self.core["os"], "fsync", side_effect=fsync))
        ioctl = stack.enter_context(mock.patch.object(self.core["checks"]["fcntl"], "ioctl"))
        ioctl.side_effect = flush
        stack.enter_context(mock.patch.object(self.core["signal"], "signal"))
        stack.enter_context(mock.patch.object(self.core["signal"], "setitimer"))
        return stack

    def run_remote(self):
        return self.core["run"](self.request, io.BytesIO(lzma.compress(RAW)),
                                lambda event, **fields: self.events.append((event, copy.deepcopy(fields))))

    def assert_no_verified(self):
        self.assertNotIn("verified", [event for event, _ in self.events])
        state = self.events[-1][1]["state"]
        self.assertEqual(state["status"], "failed")
        self.assertFalse(state["bootable"])
        return state

    def test_success_changes_only_raw_range_and_reads_sd_without_writes(self):
        with self.rig():
            result = self.run_remote()
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["readback"], self.request["source"]["raw"])
        self.assertEqual(result["sd_before"], result["sd_after"])
        self.assertEqual(self.target[:len(RAW)], RAW)
        self.assertEqual(self.target[len(RAW):], b"\xaa" * (CAPACITY - len(RAW)))
        self.assertEqual([event for event, _ in self.events], ["ready", "verified"])
        self.assertTrue(all(path in (sd_identity()["device"], emmc_identity()["device"]) for path, _ in self.opens))

    def test_pins_mismatch_never_opens_target_or_writes(self):
        for field in ("kernel", "identity_sha256", "sd_prefix", "backup_manifest_sha256"):
            with self.subTest(field=field):
                self.opens.clear()
                self.events.clear()
                self.request["backup_manifest_sha256"] = "a" * 64
                pins = {"backup_manifest_sha256": "a" * 64, "ssh_config_sha256": "b" * 64,
                        "sd_prefix": {"bytes": len(self.sd), "sha256": sha(self.sd)},
                        "rescue": {key: rescue_identity()[key] for key in ("kernel", "identity_sha256")}}
                if field in ("kernel", "identity_sha256"):
                    pins["rescue"][field] = "OTHER_KERNEL" if field == "kernel" else "0" * 64
                elif field == "sd_prefix":
                    pins[field]["sha256"] = "0" * 64
                else:
                    pins[field] = "0" * 64
                self.request["pinned_preflight"] = pins
                with self.rig(), self.assertRaises(ValueError):
                    self.run_remote()
                self.assertEqual(self.writes, [])
                self.assertTrue(all(path == sd_identity()["device"] for path, flags in self.opens))
                self.assertEqual(self.assert_no_verified()["bytes_written"], 0)

    def test_lzma_failure_keeps_original_error_and_read_emitted_hashes_in_state(self):
        # 模擬先輸出一段、仍有內部輸入時解碼失敗；read 不可誤稱 consumed。
        for message in ("Corrupt input data", "Memory usage limit exceeded"):
            self.events.clear()
            original = lzma.LZMAError(message)
            decoder = mock.Mock(needs_input=False, eof=False)
            decoder.decompress.side_effect = [RAW[:128], original]
            with self.subTest(message=message), self.rig(), \
                    mock.patch.object(self.core["lzma"], "LZMADecompressor", return_value=decoder), \
                    self.assertRaisesRegex(ValueError, "XZ 損壞或解碼記憶體超限") as raised:
                self.run_remote()
            self.assertIs(raised.exception.__cause__, original)
            state = self.assert_no_verified()
            self.assertEqual(state["bytes_written"], 128)
            self.assertEqual(state["sd_before"], state["sd_after"])
            compressed = lzma.compress(RAW)
            self.assertEqual(state["xz_diagnostics"], {
                "compressed": {"bytes_read": len(compressed), "sha256": sha(compressed)},
                "raw": {"bytes_emitted": 128, "sha256": sha(RAW[:128])},
                "lzma_error": {"type": "LZMAError", "message": message}})
            self.assertEqual(decoder.decompress.call_args_list[1].args[0], b"")

    def test_short_writes_advance_offsets_without_duplication(self):
        self.write_size = 7
        with self.rig():
            result = self.run_remote()
        self.assertEqual(self.target[:len(RAW)], RAW)
        self.assertEqual(sum(count for _, count in self.writes), len(RAW))
        self.assertEqual(result["bytes_written"], len(RAW))

    def test_zero_write_and_partial_oserror_retain_attempted_range(self):
        self.zero_write = True
        with self.rig(), self.assertRaises(ValueError):
            self.run_remote()
        state = self.assert_no_verified()
        self.assertEqual(state["bytes_written"], 0)
        self.assertEqual(state["attempted_end"], len(RAW))
        self.events.clear()
        self.zero_write = False
        self.write_size = self.write_error_after = 7
        with self.rig(), self.assertRaises(OSError):
            self.run_remote()
        state = self.assert_no_verified()
        self.assertEqual(state["bytes_written"], 7)
        self.assertEqual(state["attempted_end"], len(RAW))
        self.assertEqual(state["sd_before"], state["sd_after"])

    def test_sd_change_readback_mismatch_and_fsync_error_fail_closed(self):
        for field in ("mutate_sd", "bad_readback", "fsync_error"):
            self.events.clear()
            setattr(self, field, True)
            with self.rig(), self.assertRaises((ValueError, OSError)):
                self.run_remote()
            self.assert_no_verified()
            setattr(self, field, False)

    def test_wrong_cid_mount_swap_holder_or_wrong_rescue_never_open_rw(self):
        for reason in ("CID 不符", "容量不符", "子分割區仍被掛載", "swap 使用中", "holders 使用中"):
            self.opens.clear()
            with self.rig():
                self.core["checks"]["inspect"].side_effect = ValueError(reason)
                with self.assertRaises(ValueError):
                    self.run_remote()
            self.assertFalse(self.opens)
        self.opens.clear()
        with self.rig():
            self.core["rescue_identity"].side_effect = ValueError("不是 RAM 救援")
            with self.assertRaises(ValueError):
                self.run_remote()
        self.assertFalse(self.opens)
        self.assertFalse(self.writes)

    def test_missing_remote_confirmation_or_backup_gate_never_open_any_media(self):
        for key in ("confirm_overwrite", "backup_verified"):
            self.request[key] = False
            with self.rig(), self.assertRaises(ValueError):
                self.run_remote()
            self.request[key] = True
        self.assertFalse(self.opens)
        self.assertFalse(self.writes)

    def test_cache_flush_failure_never_degrades_to_cached_readback(self):
        with self.rig():
            self.core["checks"]["fcntl"].ioctl.side_effect = OSError("模擬 BLKFLSBUF 不支援")
            with self.assertRaises(OSError):
                self.run_remote()
        state = self.assert_no_verified()
        self.assertNotIn("readback", state)
        self.assertEqual(state["bytes_written"], len(RAW))
        self.assertEqual(state["sd_before"], state["sd_after"])

    def test_truncated_remote_stream_may_leave_partial_but_cannot_verify(self):
        source = io.BytesIO(lzma.compress(RAW)[:-1])
        with self.rig(), self.assertRaises(ValueError):
            self.core["run"](self.request, source,
                             lambda event, **fields: self.events.append((event, copy.deepcopy(fields))))
        state = self.assert_no_verified()
        self.assertLessEqual(state["bytes_written"], len(RAW))
        self.assertLessEqual(state["attempted_end"], len(RAW))
        self.assertEqual(self.target[len(RAW):], b"\xaa" * (CAPACITY - len(RAW)))


class RescueTests(unittest.TestCase):
    def setUp(self):
        self.core = deploy.remote_namespace()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = self.root / "identity.json"
        self.mounts = self.root / "mountinfo"
        self.config.write_text(json.dumps({"schema": "bpi-h618-rescue-v1", "kernel": "TEST_KERNEL"}))
        self.mounts.write_text("1 0 0:21 / / rw - tmpfs tmpfs rw\n")

    def inspect(self):
        original = os.stat
        with mock.patch.object(self.core["os"], "uname", return_value=SimpleNamespace(release="TEST_KERNEL")), \
                mock.patch.object(self.core["os"], "stat", side_effect=lambda path, **kwargs:
                    SimpleNamespace(st_dev=os.makedev(0, 21)) if str(path) == "/" else original(path, **kwargs)):
            return self.core["rescue_identity"](str(self.config), str(self.mounts))

    def test_ram_root_requires_schema_kernel_and_matching_mount_device(self):
        self.assertTrue(self.inspect()["root_ram"])
        for fs, dev in (("ext4", "179:56"), ("overlay", "0:21"), ("tmpfs", "0:22")):
            self.mounts.write_text(f"1 0 {dev} / / rw - {fs} source rw\n")
            with self.assertRaises(ValueError):
                self.inspect()
        self.mounts.write_text("1 0 0:21 / / rw - tmpfs tmpfs rw\n")
        for fields in ({"schema": "other", "kernel": "TEST_KERNEL"},
                       {"schema": "bpi-h618-rescue-v1", "kernel": "other"}):
            self.config.write_text(json.dumps(fields))
            with self.assertRaises(ValueError):
                self.inspect()

    def test_symlink_rescue_identity_is_rejected(self):
        target = self.root / "target"
        self.config.rename(target)
        self.config.symlink_to(target)
        with self.assertRaises(OSError):
            self.inspect()


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.process = mock.Mock(pid=123456)
        self.process.stdin.fileno.return_value = 103
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

    def test_short_pipe_writes_send_every_byte_while_draining_both_outputs(self):
        reads = {101: iter([b"ready", b"verified", b""]), 102: iter([b"diagnostic", b""])}
        sent = bytearray()

        def write(fd, data):
            self.assertEqual(fd, 103)
            count = min(3, len(data))
            sent.extend(data[:count])
            return count

        with mock.patch.object(deploy.subprocess, "Popen", return_value=self.process), \
                mock.patch.object(deploy.selectors, "DefaultSelector", return_value=self.selector), \
                mock.patch.object(deploy.os, "read", side_effect=lambda fd, size: next(reads[fd])), \
                mock.patch.object(deploy.os, "write", side_effect=write), \
                mock.patch.object(deploy.os, "set_blocking") as blocking, \
                mock.patch.object(deploy.os, "killpg") as killed:
            events = list(deploy.upload_stream(["FAKE_ONLY"], iter([b"abcde", b"xy"]), 1, self.clock))
        self.assertEqual(sent, b"abcdexy")
        self.assertEqual(sum(value for kind, value in events if kind == "sent"), 7)
        self.assertIn(("stdout", b"verified"), events)
        self.assertIn(("stderr", b"diagnostic"), events)
        self.assertEqual(events[-1], ("exit", 0))
        blocking.assert_called_once_with(103, False)
        killed.assert_not_called()

    def test_zero_pipe_write_kills_and_reaps_without_retry(self):
        self.process.poll.return_value = None
        with mock.patch.object(deploy.subprocess, "Popen", return_value=self.process), \
                mock.patch.object(deploy.selectors, "DefaultSelector", return_value=self.selector), \
                mock.patch.object(deploy.os, "write", return_value=0) as write, \
                mock.patch.object(deploy.os, "set_blocking"), \
                mock.patch.object(deploy.os, "killpg") as killed:
            with self.assertRaises(deploy.DeployError):
                list(deploy.upload_stream(["FAKE_ONLY"], iter([b"abc"]), 1, self.clock))
        self.assertEqual(write.call_count, 1)
        killed.assert_called_once_with(self.process.pid, deploy.signal.SIGKILL)
        self.process.wait.assert_called_once_with(timeout=5)

    def test_timeout_during_silent_transport_is_absolute_and_reaps(self):
        self.process.poll.return_value = None

        def silent(timeout):
            self.clock.value += timeout
            return []

        self.selector.select.side_effect = silent
        with mock.patch.object(deploy.subprocess, "Popen", return_value=self.process), \
                mock.patch.object(deploy.selectors, "DefaultSelector", return_value=self.selector), \
                mock.patch.object(deploy.os, "set_blocking"), \
                mock.patch.object(deploy.os, "killpg") as killed:
            with self.assertRaises(deploy.DeployError):
                list(deploy.upload_stream(["FAKE_ONLY"], iter([b"abc"]), 1, self.clock))
        self.assertAlmostEqual(self.clock.value, 1)
        killed.assert_called_once()

    def test_expired_deadline_does_not_start_any_process(self):
        self.clock.value = 2
        with mock.patch.object(deploy.subprocess, "Popen") as opened:
            with self.assertRaises(deploy.DeployError):
                list(deploy.upload_stream(["FAKE_ONLY"], iter([b"abc"]), 1, self.clock))
        opened.assert_not_called()


if __name__ == "__main__":
    unittest.main()

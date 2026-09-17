"""完整假 SSH／sysfs／區塊 I/O 執行路徑與故障隔離回歸。"""

import copy
import base64
import hashlib
import io
import json
import lzma
import os
from pathlib import Path
import shlex
import shutil
import struct
import sys
import tempfile
import unittest
from unittest import mock
import uuid
import zlib

from tools import bpi_lab_external as external
from tools import bpi_lab_media as media
from test_bpi_lab_media import FileOps, Fixture


def fingerprint(blob):
    return {"bytes": len(blob), "sha256": hashlib.sha256(blob).hexdigest()}


class ExternalFixture(Fixture):
    def __init__(self, root, kind="usb"):
        super().__init__(root, kind)
        self.root_spec = {"uuid": "12345678-1234-5678-1234-567812345678", "partition_index": 1,
                          "start_lba": 8, "sectors": 120}
        raw = bytearray(65536)
        raw[440:444] = b"\x01\x02\x03\x04"
        raw[510:512] = b"\x55\xaa"
        raw[450] = 0x83
        struct.pack_into("<II", raw, 454, 8, 120)
        superblock = 8 * 512 + 1024
        struct.pack_into("<I", raw, superblock + 4, 60)
        raw[superblock + 56:superblock + 58] = b"\x53\xef"
        raw[superblock + 104:superblock + 120] = uuid.UUID(self.root_spec["uuid"]).bytes
        self.raw = bytes(raw)
        self.compressed = lzma.compress(self.raw)
        self.image = self.root / "source.img.xz"
        self.image.write_bytes(self.compressed)
        self.source = {"path": str(self.image), "compressed": fingerprint(self.compressed), "raw": fingerprint(self.raw)}
        refs = {}
        for key in ("identity", "known_hosts"):
            path = self.root / key
            blob = b"fixture-only\n"
            path.write_bytes(blob)
            refs[key] = {"path": str(path), "sha256": fingerprint(blob)["sha256"]}
        self.contract = {"schema": external.SCHEMA, "hardware_id": "fixture-only", "target": self.target,
                         "protected_sd": self.sd, "root": self.root_spec,
                         "rescue": {"schema": "bpi-external-rescue-v1", "kernel": "6.1.1-lab",
                                    "identity_sha256": fingerprint(self.ops.rescue_blob)["sha256"]},
                         "backup": None, "write_plan": None,
                         "authorization": {"record": "fixture-only", "hardware_id": "fixture-only",
                                           "media_identity": media.media_identity(self.target), "backup_read": True,
                                           "write": False, "backup_sha256": None, "source_sha256": None, "write_plan_sha256": None},
                         "ssh": {"host": "192.0.2.1", "port": 22, "user": "root", **refs},
                         "isolation_dir": str(self.root / "isolation")}
        self.calls = []
        self.drop = False
        self.tamper = None
        self.exitcode = None

    def transport(self, argv, chunks, deadline, monotonic):
        self.calls.append(argv)
        require_args = shlex.split(argv[-1])
        assert require_args[:4] == ["python3", "-I", "-B", "-c"]
        request = json.loads(require_args[-1])
        data = b"".join(chunks)
        output, records = io.BytesIO(), []
        if self.drop:
            raise OSError("合成 SSH 中斷；沒有終止證據")
        def emit(event, **fields):
            records.append({"schema": "bpi-lab-external-wire-v1", "nonce": request["nonce"], "event": event,
                            **copy.deepcopy(fields)})
        state = external.execute(request, io.BytesIO(data), output, emit, **self.kwargs)
        if self.tamper:
            self.tamper(records)
        if data:
            yield "sent", len(data)
        if output.getvalue():
            yield "stdout", output.getvalue()
        yield "stderr", b"".join(external.PREFIX + media.encoded(row) for row in records)
        yield "exit", self.exitcode if self.exitcode is not None else (0 if state["status"] == "verified" else 1)

    def prepare(self):
        report = external.backup(self.contract, str(self.root / "backup"), transport=self.transport)
        if report["status"] != "verified":
            raise AssertionError(report["blockers"])
        path = self.root / "backup/manifest.json"
        self.contract["backup"] = {"path": str(path), "sha256": fingerprint(path.read_bytes())["sha256"]}
        plan = {"schema": "bpi-lab-external-write-v1", "source_sha256": external.source_digest(self.source),
                "ranges": [{"offset": 0, **self.source["raw"]}], "tail_policy": "preserve-zero"}
        self.contract["write_plan"] = plan
        self.contract["authorization"].update(write=True, backup_sha256=self.contract["backup"]["sha256"],
                                              source_sha256=plan["source_sha256"], write_plan_sha256=media.digest(plan))
        return report

    def authorize_reuse(self):
        plan = {"schema": "bpi-lab-external-write-v1", "source_sha256": external.source_digest(self.source),
                "tail_policy": "zero", "ranges": [{"offset": 0, **self.source["raw"]},
                    {"offset": len(self.raw), **external.zero_digest(self.target["bytes"] - len(self.raw))}]}
        self.contract["write_plan"] = plan
        self.contract["authorization"].update(scope="reusable-test-area", source_sha256=plan["source_sha256"],
                                               write_plan_sha256=media.digest(plan))

    def smaller_source(self):
        raw = bytearray(self.raw[:32768])
        self.root_spec = {**self.root_spec, "sectors": 56, "uuid": "87654321-4321-8765-4321-876543218765"}
        struct.pack_into("<I", raw, 458, 56)
        sb = self.root_spec["start_lba"] * 512 + 1024
        struct.pack_into("<I", raw, sb + 4, 28)
        raw[sb + 104:sb + 120] = uuid.UUID(self.root_spec["uuid"]).bytes
        self.raw, self.contract["root"] = bytes(raw), self.root_spec
        self.compressed = lzma.compress(self.raw)
        self.image = self.root / "smaller.img.xz"
        self.image.write_bytes(self.compressed)
        self.source = {"path": str(self.image), "compressed": fingerprint(self.compressed), "raw": fingerprint(self.raw)}
        self.authorize_reuse()


class ExternalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.f = ExternalFixture(self.temp.name)

    def deploy(self, **options):
        return external.deploy(self.f.contract, self.f.source, str(self.f.root / "deploy"),
                               confirm_overwrite=True, transport=self.f.transport, **options)

    def test_contract_is_pure_and_no_restore_api(self):
        self.assertEqual(external.validate_contract(self.f.contract), self.f.contract)
        self.assertFalse(hasattr(external, "restore"))
        self.assertEqual(self.f.ops.opens, [])

    def test_full_backup_is_redecoded_and_never_writes(self):
        report = self.f.prepare()
        self.assertEqual(report["artifact"]["raw"], fingerprint(bytes(self.f.target["bytes"])))
        self.assertFalse(report["hardware_validated"])
        self.assertFalse(any(writable for _, writable in self.f.ops.opens))
        self.assertTrue(all(self.f.ops.closed_while_leased))
        self.assertEqual(self.f.ops.fds, {})
        self.assertTrue((self.f.root / "backup/disk.img.gz").is_file())

    def test_preflight_and_complete_usb_deploy(self):
        self.f.prepare()
        result = external.preflight(self.f.contract, self.f.source, str(self.f.root / "preflight"), transport=self.f.transport)
        self.assertEqual(result["status"], "verified", result["blockers"])
        self.assertFalse(any(writable for _, writable in self.f.ops.opens))
        report = self.deploy()
        self.assertEqual(report["status"], "verified", report["blockers"])
        external.validate_result(report, self.f.contract, self.f.source)
        self.assertTrue(report["full_readback_verified"])
        self.assertEqual(report["remote"]["readback"], self.f.source["raw"])
        self.assertEqual(report["remote"]["sd_before"], report["remote"]["sd_after"])
        self.assertEqual(bytes(self.f.ops.data[str(self.f.dev / "sda")][:len(self.f.raw)]), self.f.raw)
        self.assertEqual(bytes(self.f.ops.data[str(self.f.dev / "mmcblk0")]), bytes(self.f.sd["bytes"]))
        self.assertTrue(all(self.f.ops.closed_while_leased))
        self.assertEqual(self.f.ops.fds, {})

    def test_nvme_complete_deploy(self):
        with tempfile.TemporaryDirectory() as path:
            f = ExternalFixture(path, "nvme")
            f.prepare()
            report = external.deploy(f.contract, f.source, str(f.root / "deploy"), confirm_overwrite=True, transport=f.transport)
            self.assertEqual(report["status"], "verified", report["blockers"])
            self.assertNotIn("cid", report["remote"]["identity"])

    def test_real_file_pread_pwrite_fsync_without_device_nodes(self):
        self.f.ops = FileOps(self.f.ops)
        self.f.kwargs["ops"] = self.f.ops
        self.f.prepare()
        report = self.deploy()
        self.assertEqual(report["status"], "verified", report["blockers"])
        self.assertEqual((self.f.dev / "sda").read_bytes(), self.f.raw + bytes(self.f.target["bytes"] - len(self.f.raw)))
        self.assertEqual((self.f.dev / "mmcblk0").read_bytes(), bytes(self.f.sd["bytes"]))
        self.assertTrue(self.f.ops.flushes)

    def test_real_subprocess_stream_with_generated_remote_and_file_io(self):
        self.f.ops = FileOps(self.f.ops)
        self.f.kwargs["ops"] = self.f.ops
        self.f.prepare()
        program, _ = external._program()
        packed = base64.b64encode(zlib.compress(program.encode())).decode()
        repository = str(Path(external.__file__).resolve().parent.parent)
        child = (
            "import sys,json,base64,zlib\n"
            f"sys.path[:0]=[{repository!r},{str(Path(__file__).resolve().parent)!r}]\n"
            "from test_bpi_lab_media import FakeOps,FileOps\n"
            f"original=FakeOps({str(self.f.root)!r})\n"
            f"original.nodes=json.loads({json.dumps(self.f.ops.nodes)!r})\n"
            "ops=FileOps(original)\n"
            f"program=zlib.decompress(base64.b64decode({packed!r})).decode()\n"
            "ns={}\nexec(program.removesuffix(\"scope['_remote_main']()\\n\"),ns)\n"
            "scope=ns['scope']; execute=scope['execute']\n"
            f"scope['execute']=lambda *a,**k: execute(*a,**k,ops=ops,sysroot={str(self.f.sys)!r},"
            f"procroot={str(self.f.proc)!r},devroot={str(self.f.dev)!r})\n"
            "scope['_remote_main']()\n"
        )
        def transport(argv, chunks, deadline, monotonic):
            self.assertEqual(argv[0], "ssh")
            request = shlex.split(argv[-1])[-1]
            yield from external._libraries()[1].upload_stream([sys.executable, "-B", "-c", child, request],
                                                               chunks, deadline, monotonic)
        report = external.deploy(self.f.contract, self.f.source, str(self.f.root / "native-pipes"),
                                 confirm_overwrite=True, transport=transport)
        self.assertEqual(report["status"], "verified", report["blockers"])
        self.assertEqual((self.f.dev / "sda").read_bytes()[:len(self.f.raw)], self.f.raw)
        self.assertTrue(report["writer_stopped"])

    def test_failed_drain_keeps_quarantine_after_descriptor_close(self):
        self.f.prepare()
        self.f.ops.fail_write = True
        with mock.patch.object(self.f.ops, "fsync", side_effect=OSError("合成排空失敗")):
            report = self.deploy()
        self.assertTrue(report["remote"]["descriptors_closed"])
        self.assertFalse(report["remote"]["io_drained"])
        self.assertFalse(report["writer_stopped"])
        self.assertTrue(report["quarantined"])

    def test_publication_failure_keeps_pending_marker(self):
        self.f.prepare()
        original = external._save
        def save(directory, name, value):
            if name == "manifest.json":
                raise OSError("合成收據發布失敗")
            return original(directory, name, value)
        with mock.patch.object(external, "_save", side_effect=save), self.assertRaises(OSError):
            self.deploy()
        self.assertTrue(list((self.f.root / "isolation").glob("*.pending.json")))

    def test_publication_fsync_failures_and_hard_exits_reject_leftover_verified_json(self):
        for stage in ("manifest", "output_directory", "completion", "completion_directory"):
            for hard_exit in (False, True):
                with self.subTest(stage=stage, hard_exit=hard_exit), tempfile.TemporaryDirectory() as directory:
                    f = ExternalFixture(directory)
                    f.prepare()
                    out, isolation = f.root / "deploy", f.root / "isolation"
                    sync = os.fsync
                    def interrupted(fd):
                        path = Path(os.readlink(f"/proc/self/fd/{fd}"))
                        new_completion = list(isolation.glob("*.complete.json"))
                        completed = len(new_completion) == 2
                        matches = {"manifest": path == out / "manifest.json",
                                   "output_directory": path == out and (out / "manifest.json").exists(),
                                   "completion": path.parent == isolation and path.name.endswith(".complete.json"),
                                   "completion_directory": path == isolation and completed}[stage]
                        if matches:
                            if hard_exit:
                                os._exit(73)
                            raise OSError("合成發布同步失敗")
                        return sync(fd)
                    def run():
                        with mock.patch.object(external.os, "fsync", side_effect=interrupted):
                            return external.deploy(f.contract, f.source, str(out), confirm_overwrite=True, transport=f.transport)
                    if hard_exit:
                        pid = os.fork()
                        if pid == 0:
                            try:
                                run()
                            finally:
                                os._exit(74)
                        _, status = os.waitpid(pid, 0)
                        self.assertEqual(os.waitstatus_to_exitcode(status), 73)
                    else:
                        with self.assertRaises(OSError):
                            run()
                    report = json.loads((out / "manifest.json").read_bytes())
                    self.assertEqual(report["status"], "verified")
                    external._validate_result_data(report, f.contract, f.source)
                    self.assertTrue(list(isolation.glob("*.pending.json")))
                    with self.assertRaisesRegex(ValueError, "隔離"):
                        external.validate_result(report, f.contract, f.source)
                    with self.assertRaisesRegex(ValueError, "隔離"):
                        external.preflight(f.contract, f.source, str(f.root / "later"), transport=f.transport)

    def test_pending_release_fsync_failure_recreates_quarantine(self):
        self.f.prepare()
        isolation = self.f.root / "isolation"
        sync = os.fsync
        def interrupted(fd):
            if (Path(os.readlink(f"/proc/self/fd/{fd}")) == isolation
                    and len(list(isolation.glob("*.complete.json"))) == 2
                    and not list(isolation.glob("*.pending.json"))):
                raise OSError("合成隔離解除同步失敗")
            return sync(fd)
        with mock.patch.object(external.os, "fsync", side_effect=interrupted), self.assertRaises(OSError):
            self.deploy()
        report = json.loads((self.f.root / "deploy/manifest.json").read_bytes())
        with self.assertRaisesRegex(ValueError, "隔離"):
            external.validate_result(report, self.f.contract, self.f.source)

    def test_replay_requires_original_completion_and_rejects_rebound_copy(self):
        self.f.prepare()
        report = self.deploy()
        external.validate_result(report, self.f.contract, self.f.source)
        clone = self.f.root / "clone"
        shutil.copytree(self.f.root / "deploy", clone)
        altered = copy.deepcopy(report)
        altered["publication"]["directory"] = str(clone)
        for key in ("request_evidence", "stderr_evidence", "stdout_evidence"):
            altered[key]["path"] = str(clone / Path(altered[key]["path"]).name)
        (clone / "manifest.json").write_bytes(media.encoded(altered))
        with self.assertRaisesRegex(ValueError, "完成憑證"):
            external.validate_result(altered, self.f.contract, self.f.source)
        completion = self.f.root / "isolation" / external._completion_name(report)
        completion.unlink()
        with self.assertRaises(FileNotFoundError):
            external.validate_result(report, self.f.contract, self.f.source)
        missing = copy.deepcopy(report)
        del missing["publication"]
        with self.assertRaises(ValueError):
            external.validate_result(missing, self.f.contract, self.f.source)

    def test_replay_rejects_new_pending_and_changed_wire_evidence(self):
        self.f.prepare()
        report = self.deploy()
        pending = self.f.root / "isolation" / (report["media_identity"].split(":")[1] + ".pending.json")
        pending.write_bytes(b"{}")
        with self.assertRaisesRegex(ValueError, "隔離"):
            external.validate_result(report, self.f.contract, self.f.source)
        pending.unlink()
        Path(report["stderr_evidence"]["path"]).write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "摘要"):
            external.validate_result(report, self.f.contract, self.f.source)

    def test_replay_rejects_identical_manifest_replaced_at_original_path(self):
        self.f.prepare()
        report = self.deploy()
        path = self.f.root / "deploy/manifest.json"
        moved = path.with_name("original.json")
        path.rename(moved)
        path.write_bytes(moved.read_bytes())
        path.chmod(0o600)
        with self.assertRaisesRegex(ValueError, "完成憑證"):
            external.validate_result(report, self.f.contract, self.f.source)

    def test_replay_requires_backup_file_identity_and_isolation_sync(self):
        self.f.prepare()
        report = self.deploy()
        with mock.patch.object(external.os, "fsync", side_effect=OSError("合成重播隔離同步失敗")):
            with self.assertRaises(OSError):
                external.validate_result(report, self.f.contract, self.f.source)
        (self.f.root / "backup/disk.img.gz").write_bytes(b"changed")
        with self.assertRaises(ValueError):
            external.validate_result(report, self.f.contract, self.f.source)

    def test_reusable_large_then_small_after_customer_write_and_root_expansion(self):
        self.f.ops = FileOps(self.f.ops)
        self.f.kwargs["ops"] = self.f.ops
        self.f.prepare()
        original_backup = (self.f.root / "backup/disk.img.gz").read_bytes()
        backup_manifest = (self.f.root / "backup/manifest.json").read_bytes()
        original_xz = self.f.image.read_bytes()
        self.f.authorize_reuse()
        large = self.deploy()
        self.assertEqual(large["status"], "verified", large["blockers"])
        self.assertTrue(large["remote"]["baseline_match"])
        # 客戶擴大根分割並寫入尾端；返回救援後仍沿用原始整碟備份。
        current = bytearray((self.f.dev / "sda").read_bytes())
        struct.pack_into("<I", current, 458, self.f.target["bytes"] // 512 - 8)
        struct.pack_into("<I", current, 8 * 512 + 1024 + 4, (len(current) - 4096) // 1024)
        current[-4096:] = b"C" * 4096
        current[10000:10008] = b"customer"
        (self.f.dev / "sda").write_bytes(current)
        partition = self.f.partition()
        self.f.put(partition / "size", self.f.target["bytes"] // 512 - 8)
        self.f.smaller_source()
        small_xz = self.f.image.read_bytes()
        result = external.preflight(self.f.contract, self.f.source, str(self.f.root / "preflight-small"), transport=self.f.transport)
        self.assertEqual(result["status"], "verified", result["blockers"])
        self.assertFalse(result["remote"]["baseline_match"])
        self.assertEqual(result["remote"]["identity"]["partitions"][0]["sectors"], self.f.target["bytes"] // 512 - 8)
        self.assertEqual((self.f.dev / "sda").read_bytes(), current)
        small = external.deploy(self.f.contract, self.f.source, str(self.f.root / "small"),
                                confirm_overwrite=True, transport=self.f.transport)
        self.assertEqual(small["status"], "verified", small["blockers"])
        external.validate_result(small, self.f.contract, self.f.source)
        state = small["remote"]
        self.assertEqual(state["bytes_written"], self.f.target["bytes"])
        self.assertEqual(state["source_bytes_written"], len(self.f.raw))
        self.assertEqual(state["tail_bytes_written"], self.f.target["bytes"] - len(self.f.raw))
        self.assertEqual(state["tail_readback"], external.zero_digest(state["tail_bytes_written"]))
        self.assertEqual((self.f.dev / "sda").read_bytes(), self.f.raw + bytes(state["tail_bytes_written"]))
        self.assertEqual((self.f.dev / "mmcblk0").read_bytes(), bytes(self.f.sd["bytes"]))
        self.assertEqual((self.f.root / "backup/disk.img.gz").read_bytes(), original_backup)
        self.assertEqual((self.f.root / "backup/manifest.json").read_bytes(), backup_manifest)
        self.assertEqual((self.f.root / "source.img.xz").read_bytes(), original_xz)
        self.assertEqual(self.f.image.read_bytes(), small_xz)

    def test_reusable_requires_scope_and_exact_tail_authorization(self):
        self.f.prepare()
        self.f.authorize_reuse()
        good = copy.deepcopy(self.f.contract)
        for case in ("scope", "missing_tail", "offset", "length", "policy"):
            contract = copy.deepcopy(good)
            if case == "scope":
                del contract["authorization"]["scope"]
            elif case == "missing_tail":
                contract["write_plan"]["ranges"].pop()
            elif case == "policy":
                contract["write_plan"]["tail_policy"] = "preserve-zero"
            else:
                key = "offset" if case == "offset" else "bytes"
                contract["write_plan"]["ranges"][1][key] += 512
            contract["authorization"]["write_plan_sha256"] = media.digest(contract["write_plan"])
            with self.subTest(case=case), self.assertRaises(ValueError):
                external.validate_contract(contract)
        self.assertEqual(self.f.ops.writes, [])

    def test_wrong_zero_hash_is_rejected_before_host_ssh_and_remote_open(self):
        self.f.prepare()
        self.f.authorize_reuse()
        self.f.contract["write_plan"]["ranges"][1]["sha256"] = "a" * 64
        self.f.contract["authorization"]["write_plan_sha256"] = media.digest(self.f.contract["write_plan"])
        calls, opened = len(self.f.calls), len(self.f.ops.opens)
        result = self.deploy()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(len(self.f.calls), calls)
        request = {"operation": "deploy", "contract": self.f.contract, "source": self.f.source, "timeout": 60,
                   "backup_raw": fingerprint(bytes(self.f.target["bytes"])), "confirm_overwrite": True}
        state = external.execute(request, io.BytesIO(self.f.compressed), io.BytesIO(), lambda *a, **k: None, **self.f.kwargs)
        self.assertEqual(state["status"], "failed")
        self.assertEqual(len(self.f.ops.opens), opened)

    def test_tail_short_writes_and_partial_failure_are_bounded(self):
        self.f.prepare()
        self.f.authorize_reuse()
        self.f.ops.short_write = 4096
        def fail_in_tail():
            if self.f.ops.writes[-1][1] >= len(self.f.raw):
                self.f.ops.fail_write = True
        self.f.ops.on_write = fail_in_tail
        report = self.deploy()
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["remote"]["source_bytes_written"], len(self.f.raw))
        self.assertEqual(report["remote"]["tail_bytes_written"], 4096)
        self.assertLessEqual(report["remote"]["attempted_end"], self.f.target["bytes"])
        self.assertTrue(report["writer_stopped"])
        self.assertEqual(self.f.ops.fds, {})

    def test_reusable_short_writes_complete_both_ranges(self):
        self.f.prepare()
        self.f.authorize_reuse()
        self.f.ops.data[str(self.f.dev / "sda")][-4096:] = b"Z" * 4096
        self.f.ops.short_write = 4096
        report = self.deploy()
        self.assertEqual(report["status"], "verified", report["blockers"])
        external.validate_result(report, self.f.contract, self.f.source)
        self.assertFalse(report["remote"]["baseline_match"])
        self.assertEqual(sum(row[2] for row in self.f.ops.writes), self.f.target["bytes"])
        self.assertEqual(max(offset + count for _, offset, count in self.f.ops.writes), self.f.target["bytes"])
        self.assertTrue(all(path == str(self.f.dev / "sda") for path, _, _ in self.f.ops.writes))

    def test_reusable_full_capacity_image_has_explicit_empty_tail(self):
        self.f.prepare()
        self.f.raw += bytes(self.f.target["bytes"] - len(self.f.raw))
        self.f.compressed = lzma.compress(self.f.raw)
        path = self.f.root / "full.img.xz"
        path.write_bytes(self.f.compressed)
        self.f.source = {"path": str(path), "raw": fingerprint(self.f.raw), "compressed": fingerprint(self.f.compressed)}
        self.f.authorize_reuse()
        report = self.deploy()
        self.assertEqual(report["status"], "verified", report["blockers"])
        self.assertEqual(report["remote"]["tail_readback"], fingerprint(b""))
        self.assertEqual(report["remote"]["tail_bytes_written"], 0)
        external.validate_result(report, self.f.contract, self.f.source)

    def test_tail_zero_progress_and_transport_loss_do_not_claim_success(self):
        self.f.prepare()
        self.f.authorize_reuse()
        write = self.f.ops.pwrite
        def stop(fd, blob, offset):
            return 0 if offset >= len(self.f.raw) else write(fd, blob, offset)
        with mock.patch.object(self.f.ops, "pwrite", side_effect=stop):
            report = self.deploy()
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["remote"]["tail_bytes_written"], 0)
        self.assertEqual(report["remote"]["source_bytes_written"], len(self.f.raw))
        self.assertTrue(report["writer_stopped"])
        transport = self.f.transport
        def disconnected(*args):
            for channel, value in transport(*args):
                if channel == "stderr":
                    raise OSError("合成尾端寫入後失聯")
                yield channel, value
        report = external.deploy(self.f.contract, self.f.source, str(self.f.root / "unknown"),
                                 confirm_overwrite=True, transport=disconnected)
        self.assertTrue(report["quarantined"])
        self.assertFalse(report["writer_stopped"])

    def test_reusable_still_rejects_changed_protected_sd(self):
        self.f.prepare()
        self.f.authorize_reuse()
        self.f.ops.data[str(self.f.dev / "mmcblk0")][-1] = 1
        report = self.deploy()
        self.assertEqual(report["status"], "failed")
        self.assertEqual(self.f.ops.writes, [])

    def test_reusable_tail_timeout_retains_writer_quarantine(self):
        self.f.prepare()
        self.f.authorize_reuse()
        self.f.ops.short_write = 4096
        now = [0]
        self.f.ops.on_write = lambda: now.__setitem__(0, 120 if self.f.ops.writes[-1][1] >= len(self.f.raw) else 0)
        execute = external.execute
        def timed(*args, **kwargs):
            args[0]["timeout"] = 60
            return execute(*args, **kwargs, monotonic=lambda: now[0])
        with mock.patch.object(external, "execute", side_effect=timed):
            result = self.deploy()
        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["remote"]["io_drained"])
        self.assertTrue(result["quarantined"])
        self.assertTrue(result["remote"]["descriptors_closed"])
        self.assertEqual(result["remote"]["tail_bytes_written"], 4096)

    def test_tail_only_corrupt_readback_is_rejected(self):
        self.f.prepare()
        self.f.authorize_reuse()
        read = self.f.ops.pread
        def corrupt(fd, count, offset):
            blob = read(fd, count, offset)
            if self.f.ops.writes and self.f.ops.fds[fd][1] and offset == len(self.f.raw):
                return b"!" + blob[1:]
            return blob
        with mock.patch.object(self.f.ops, "pread", side_effect=corrupt):
            result = self.deploy()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["remote"]["readback"], self.f.source["raw"])
        self.assertFalse(result["full_readback_verified"])

    def test_reusable_receipts_reject_forged_counters_baseline_and_tail(self):
        self.f.prepare()
        self.f.authorize_reuse()
        report = self.deploy()
        self.assertEqual(report["status"], "verified", report["blockers"])
        external.validate_result(report, self.f.contract, self.f.source)
        for key, value in (("tail_bytes_written", 0), ("source_bytes_written", 0), ("bytes_written", len(self.f.raw)),
                           ("attempted_end", self.f.target["bytes"] + 1), ("baseline_match", False),
                           ("tail_readback", None), ("ranges", [])):
            altered = copy.deepcopy(report)
            altered["remote"][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                external._validate_result_data(altered, self.f.contract, self.f.source)

    def test_uart_ssh_claim_is_not_part_of_d2(self):
        report = self.f.prepare()
        self.assertNotIn("boot_verified", report)
        self.assertNotIn("root_uuid_verified", report)

    def test_default_deploy_does_not_connect(self):
        with self.assertRaises(ValueError):
            external.deploy(self.f.contract, self.f.source, str(self.f.root / "deny"), transport=self.f.transport)
        self.assertEqual(self.f.calls, [])

    def test_bad_plan_and_source_fail_before_ssh(self):
        self.f.prepare()
        calls = len(self.f.calls)
        self.f.source["raw"]["sha256"] = "f" * 64
        result = self.deploy()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(len(self.f.calls), calls)
        self.assertEqual(self.f.ops.writes, [])

    def test_corrupt_backup_fails_before_ssh(self):
        self.f.prepare()
        (self.f.root / "backup/disk.img.gz").write_bytes(b"broken")
        calls = len(self.f.calls)
        result = self.deploy()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(len(self.f.calls), calls)

    def test_backup_changed_during_source_validation_never_connects(self):
        self.f.prepare()
        verify = external._verify_source
        def change(*args):
            result = verify(*args)
            (self.f.root / "backup/disk.img.gz").write_bytes(b"changed")
            return result
        calls = len(self.f.calls)
        with mock.patch.object(external, "_verify_source", side_effect=change):
            report = self.deploy()
        self.assertEqual(report["status"], "failed")
        self.assertEqual(len(self.f.calls), calls)
        self.assertFalse(report["quarantined"])

    def test_old_kernel_without_diskseq_still_uses_event_watch(self):
        self.f.prepare()
        for name in ("sda", "mmcblk0"):
            (self.f.sys / "class/block" / name / "diskseq").unlink()
        report = self.deploy()
        self.assertEqual(report["status"], "verified", report["blockers"])
        self.assertIsNone(report["remote"]["identity"]["diskseq"])

    def test_source_symlink_is_not_opened(self):
        self.f.prepare()
        symlink = self.f.root / "linked.img.xz"
        symlink.symlink_to(self.f.image)
        self.f.source["path"] = str(symlink)
        result = self.deploy()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(self.f.ops.writes, [])

    def test_nonzero_tail_is_not_erased(self):
        self.f.ops.data[str(self.f.dev / "sda")][-1] = 42
        self.f.prepare()
        result = self.deploy()
        self.assertEqual(result["status"], "failed")
        self.assertIn("尾端", " ".join(result["blockers"]))
        self.assertEqual(self.f.ops.writes, [])
        self.assertEqual(self.f.ops.data[str(self.f.dev / "sda")][-1], 42)

    def test_stale_backup_and_wrong_sd_prevent_write(self):
        self.f.prepare()
        self.f.ops.data[str(self.f.dev / "sda")][0] = 1
        result = self.deploy()
        self.assertEqual(result["status"], "failed")
        self.assertIn("備份", " ".join(result["blockers"]))
        self.assertEqual(self.f.ops.writes, [])

    def test_sd_full_hash_includes_beyond_prefix(self):
        self.f.prepare()
        self.f.ops.data[str(self.f.dev / "mmcblk0")][-1] = 2
        result = self.deploy()
        self.assertEqual(result["status"], "failed")
        self.assertIn("SD", " ".join(result["blockers"]))
        self.assertEqual(self.f.ops.writes, [])

    def test_short_writes_are_bounded_and_completed(self):
        self.f.prepare()
        self.f.ops.short_write = 4096
        result = self.deploy()
        self.assertEqual(result["status"], "verified", result["blockers"])
        self.assertEqual(sum(row[2] for row in self.f.ops.writes), len(self.f.raw))
        self.assertLessEqual(max(offset + count for _, offset, count in self.f.ops.writes), len(self.f.raw))

    def test_write_error_stops_and_releases_only_after_close(self):
        self.f.prepare()
        self.f.ops.fail_write = True
        report = self.deploy()
        self.assertEqual(report["status"], "failed")
        self.assertTrue(report["writer_stopped"])
        self.assertFalse(report["quarantined"])
        self.assertTrue(report["remote"]["write_started"])
        self.assertEqual(report["remote"]["bytes_written"], 0)
        self.assertTrue(all(self.f.ops.closed_while_leased))

    def test_readback_corruption_is_rejected(self):
        self.f.prepare()
        self.f.ops.corrupt_readback = True
        report = self.deploy()
        self.assertEqual(report["status"], "failed")
        self.assertFalse(report["full_readback_verified"])

    def test_hotplug_event_after_partial_write(self):
        self.f.prepare()
        self.f.ops.short_write = 4096
        self.f.ops.on_write = lambda: setattr(self.f.ops, "event", True)
        report = self.deploy()
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["remote"]["bytes_written"], 4096)
        self.assertTrue(report["writer_stopped"])

    def test_disconnect_persists_quarantine_and_blocks_next_call(self):
        self.f.prepare()
        self.f.drop = True
        report = self.deploy()
        self.assertTrue(report["quarantined"])
        self.assertFalse(report["writer_stopped"])
        self.f.drop = False
        calls = len(self.f.calls)
        with self.assertRaisesRegex(ValueError, "隔離"):
            external.backup(self.f.contract, str(self.f.root / "later"), transport=self.f.transport)
        self.assertEqual(len(self.f.calls), calls)

    def test_forged_success_summary_and_missing_close_are_rejected(self):
        self.f.prepare()
        self.f.tamper = lambda rows: rows[-1]["state"].update(readback={"bytes": 1, "sha256": "0" * 64})
        report = self.deploy()
        self.assertEqual(report["status"], "failed")
        self.assertTrue(report["quarantined"])

    def test_fixed_ssh_has_no_proxy_or_arbitrary_program(self):
        self.f.prepare()
        argv = self.f.calls[0]
        self.assertIn("StrictHostKeyChecking=yes", argv)
        self.assertIn("PasswordAuthentication=no", argv)
        config = (self.f.root / "backup/ssh-config").read_text()
        self.assertIn("ProxyCommand none", config)
        self.assertIn("KnownHostsCommand none", config)

    def test_generated_remote_runs_without_repository_imports(self):
        program, _ = external._program()
        namespace = {}
        before = sys.modules.get("bpi_lab_media")
        try:
            exec(compile(program.removesuffix("scope['_remote_main']()\n"), "fixture-remote", "exec"), namespace)
            scope = namespace["scope"]
            request = {"operation": "backup", "contract": self.f.contract, "timeout": 60}
            output, events = io.BytesIO(), []
            result = scope["execute"](request, io.BytesIO(), output,
                                      lambda event, **data: events.append(event), **self.f.kwargs)
            self.assertEqual(result["status"], "verified", result["error"])
            self.assertEqual(events, ["ready", "complete"])
            self.assertTrue(output.getvalue().startswith(b"\x1f\x8b"))
        finally:
            if before is None:
                sys.modules.pop("bpi_lab_media", None)
            else:
                sys.modules["bpi_lab_media"] = before

    def test_mbr_and_root_negative_cases(self):
        for offset, data in ((450, b"\xee"), (510, b"\0\0"), (8 * 512 + 1024 + 56, b"\0\0"),
                             (8 * 512 + 1024 + 104, bytes(16))):
            raw = bytearray(self.f.raw)
            raw[offset:offset + len(data)] = data
            probe = external.ImageProbe(self.f.root_spec, len(raw))
            probe.feed(bytes(raw))
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                probe.finish()

    def test_sunplus_original_ea_partition_type(self):
        raw = bytearray(self.f.raw)
        raw[466] = 0xea
        struct.pack_into("<II", raw, 470, 1, 4)
        probe = external.ImageProbe(self.f.root_spec, len(raw))
        probe.feed(bytes(raw))
        self.assertEqual(probe.finish()["partitions"][1]["type"], 0xea)

    def test_oversized_remote_xz_never_exceeds_authorized_range(self):
        self.f.prepare()
        extra = self.f.compressed + lzma.compress(b"X" * 4096)
        source = copy.deepcopy(self.f.source)
        source["compressed"] = fingerprint(extra)
        contract = copy.deepcopy(self.f.contract)
        contract["write_plan"]["source_sha256"] = external.source_digest(source)
        contract["authorization"].update(source_sha256=external.source_digest(source),
                                         write_plan_sha256=media.digest(contract["write_plan"]))
        request = {"operation": "deploy", "contract": contract, "source": source,
                   "backup_raw": fingerprint(bytes(self.f.target["bytes"])), "confirm_overwrite": True, "timeout": 60}
        state = external.execute(request, io.BytesIO(extra), io.BytesIO(), lambda *a, **k: None, **self.f.kwargs)
        self.assertEqual(state["status"], "failed")
        self.assertLessEqual(state["attempted_end"], len(self.f.raw))
        self.assertEqual(bytes(self.f.ops.data[str(self.f.dev / "sda")][len(self.f.raw):]), bytes(self.f.target["bytes"] - len(self.f.raw)))

    def test_deadline_before_remote_open(self):
        request = {"operation": "backup", "contract": self.f.contract, "timeout": 1}
        with mock.patch.object(self.f.ops, "watch") as watch:
            # 遠端期限在第一個描述符開啟前核對；替身不操作實體設備。
            result = external.execute(request, io.BytesIO(), io.BytesIO(), lambda *a, **k: None,
                                      **self.f.kwargs, monotonic=iter([0, 2]).__next__)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(self.f.ops.opens, [])
        self.assertTrue(watch.called)


if __name__ == "__main__":
    unittest.main()

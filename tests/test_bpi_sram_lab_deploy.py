#!/usr/bin/env python3
"""第三版部署集中回歸；只讀建置 fixture、只寫暫存一般檔案，不代表全 rootfs 驗證。"""

from contextlib import contextmanager
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import struct
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
SPEC = importlib.util.spec_from_file_location("bpi_sram_lab_deploy", TOOLS / "bpi_sram_lab_deploy.py")
assert SPEC is not None and SPEC.loader is not None
deploy = importlib.util.module_from_spec(SPEC)
with mock.patch.object(sys, "path", [str(TOOLS), *sys.path]):
    SPEC.loader.exec_module(deploy)
sd = deploy.sd
RANGES = ((8192, 40960), (1048576, 1048576), (3276800, 131072), (3538944, 131072))
REAL_PWRITE = os.pwrite


def sha(blob):
    return hashlib.sha256(blob).hexdigest()


def flipped(blob, offset=0):
    result = bytearray(blob)
    result[offset] ^= 1
    return bytes(result)


class LabDeployTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        evidence = ROOT / "output/evidence/bpi-h618-recovery-network"
        try:
            try:
                cls.before = deploy.base.read_regular_file(
                    evidence / "P4B-0845-20260916/deployment/expected.bin", sd.PREFIX)
            except (FileNotFoundError, PermissionError):
                # 受保護的 ABI2 備份不可讀時，只在記憶體重建，再核對固定整體雜湊。
                prefix = bytearray(deploy.base.read_regular_file(
                    evidence / "N2-m4zero-0845-20260915/deployment/expected.bin", sd.PREFIX))
                old_loader = deploy.base.read_regular_file(
                    evidence / "P4A-0845-20260915/loader-v2-build-003/spl1-egon.bin", 40960)
                prefix[8192:49152] = old_loader
                cls.before = bytes(prefix)
            cls.loader = deploy.base.read_regular_file(
                evidence / "U0-0845-20260916/loader-v3-build-001/spl1-egon.bin", 40960)
        except (FileNotFoundError, PermissionError) as exc:
            raise unittest.SkipTest("缺少可讀的固定建置 fixture；不放寬產物雜湊、不存取裝置") from exc
        if sha(cls.before) != "59a8e09244f38ac4134944e23212e4ca69d754171b0ac830694b910a457012f2":
            raise AssertionError("ABI2 fixture 整包雜湊不符，禁止以替身摘要繞過")
        if sha(cls.loader) != "4e363738b409afdd9b035fa83aeab7e21a565da2a302662455d9003b68fabaae":
            raise AssertionError("ABI3 入口 fixture 雜湊不符")
        # 僅滿足本輪 FIT 標頭檢查契約；此合成資料不是可開機產物。
        cls.fit = struct.pack(">II", 0xd00dfeed, 512) + bytes(32) + b"\x55" * 472
        cls.updater = deploy.lab.build_package(b"\x33" * 513, 0x18000, 3)
        cls.boot = deploy.lab.build_package(b"\x44" * 1025, 0x18000, 4)
        cls.hashes = {"fit": sha(cls.fit), "updater": sha(cls.updater), "boot": sha(cls.boot)}
        after = bytearray(cls.before)
        for (offset, size), blob in zip(RANGES, (cls.loader, cls.fit, cls.updater, cls.boot)):
            after[offset:offset + size] = blob + bytes(size - len(blob))
        cls.after = bytes(after)
        start, sectors = struct.unpack_from("<II", cls.before, 454)
        cls.partition = {"start": start, "sectors": sectors}
        cls.identity = {"cid": "1" * 32, "devnum": "179:0",
                        "bytes": max(64 * 1024**2, (start + sectors + 8192) * 512)}

    def setUp(self):
        patcher = mock.patch.object(sd, "inspect_device", side_effect=AssertionError("禁止探測實體媒體"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def plan(self, **changes):
        values = dict(before=self.before, loader=self.loader, fit=self.fit, updater=self.updater,
                      boot=self.boot, hashes=self.hashes)
        return deploy.planned_prefix(**{**values, **changes})

    @contextmanager
    def media(self, initial=None):
        with tempfile.TemporaryFile() as stream:
            stream.truncate(self.identity["bytes"])
            stream.write(self.before if initial is None else initial)
            stream.seek(self.partition["start"] * 512)
            stream.write(b"\xa5" * sd.GUARD)
            stream.seek((self.partition["start"] + self.partition["sectors"]) * 512 - sd.GUARD)
            stream.write(b"\x5a" * sd.GUARD)
            stream.flush()
            fd = stream.fileno()
            self.assertTrue(stat.S_ISREG(os.fstat(fd).st_mode))

            def ioctl(descriptor, request):
                self.assertEqual((descriptor, request), (fd, 0x1261))
                return 0

            with mock.patch.object(deploy.fcntl, "ioctl", side_effect=ioctl) as flush:
                yield fd, flush

    @contextmanager
    def environment(self, prepared=True):
        with tempfile.TemporaryDirectory(prefix="bpi-sram-lab-deploy-test-") as directory, self.media() as (fd, flush):
            root = Path(directory)
            args = SimpleNamespace(action="prepare", device="/mock/not-a-device",
                evidence_dir=str(root / "evidence"), loader=str(root / "loader.bin"), fit=str(root / "fit.bin"),
                updater=str(root / "updater.bin"), boot=str(root / "boot.bin"),
                fit_sha256=self.hashes["fit"], updater_sha256=self.hashes["updater"], boot_sha256=self.hashes["boot"],
                prepared_sha256=None)
            for name in ("loader", "fit", "updater", "boot"):
                Path(getattr(args, name)).write_bytes(getattr(self, name))
            with mock.patch.object(sd, "inspect_device", return_value=self.partition) as inspect:
                result = None
                if prepared:
                    result = deploy.prepare(args, fd, self.identity, self.partition)
                    args.action = "apply"
                    args.prepared_sha256 = result["prepared_sha256"]
                    inspect.reset_mock()
                yield SimpleNamespace(args=args, fd=fd, root=Path(args.evidence_dir),
                                      inspect=inspect, flush=flush, result=result)

    def record(self, env):
        return json.loads((env.root / "prepared.json").read_bytes())

    def replace_record(self, env, record):
        blob = sd.json_bytes(record)
        (env.root / "prepared.json").write_bytes(blob)
        env.args.prepared_sha256 = sha(blob)

    def assert_partition_samples(self, fd):
        self.assertEqual(sd.guards(fd, self.partition), [sha(b"\xa5" * sd.GUARD), sha(b"\x5a" * sd.GUARD)])
        self.assertEqual(os.fstat(fd).st_size, self.identity["bytes"])

    def test_plan_uses_fixed_hashes_ranges_real_parsers_and_zero_padding(self):
        self.assertEqual(deploy.RANGES, RANGES)
        self.assertEqual(deploy.OLD_PREFIX_SHA, sha(self.before))
        self.assertEqual(deploy.LOADER_SHA, sha(self.loader))
        self.assertEqual(deploy.PURPOSE, "0845-lab-v3-a1-fit-preserve-original-system")
        with mock.patch.object(deploy.lab, "parse_package", wraps=deploy.lab.parse_package) as parse:
            self.assertEqual(self.plan(), self.after)
        self.assertEqual(parse.call_args_list, [mock.call(self.updater), mock.call(self.boot)])
        for (offset, size), blob in zip(RANGES, (self.loader, self.fit, self.updater, self.boot)):
            self.assertEqual(self.after[offset:offset + len(blob)], blob)
            self.assertEqual(self.after[offset + len(blob):offset + size], bytes(size - len(blob)))
        old_magic, old_size = struct.unpack_from(">II", self.before, 49152)
        self.assertEqual(old_magic, 0xd00dfeed)
        self.assertLessEqual(49152 + old_size, 1048576)
        self.assertEqual(self.after[49152:49152 + old_size], self.before[49152:49152 + old_size])

    def test_unchanged_checks_all_boundaries_mbr_other_slots_gaps_and_lengths(self):
        protected = (0, 446, 510, 8191, 49152, 1048575, 2097152, 3072000, 3145728, 3276799,
                     3407872, 3407873, 3538943, 3670016, sd.PREFIX - 1)
        for offset in protected:
            with self.subTest(offset=offset):
                self.assertFalse(deploy.unchanged(self.before, flipped(self.before, offset)))
        for offset, size in RANGES:
            for edge in (offset, offset + size - 1):
                self.assertTrue(deploy.unchanged(self.before, flipped(self.before, edge)))
        self.assertTrue(deploy.unchanged(self.before, self.after))
        for before, after in ((self.before[:-1], self.before[:-1]), (self.before, self.after[:-1]),
                              (self.before + b"\0", self.after + b"\0")):
            self.assertFalse(deploy.unchanged(before, after))

    def test_plan_rejects_wrong_old_prefix_and_loader_without_hash_substitution(self):
        for key, values in (("before", (self.before[:-1], self.before + b"\0", flipped(self.before),
                                         flipped(self.before, 8192))),
                            ("loader", (self.loader[:-1], self.loader + b"\0", flipped(self.loader)))):
            for blob in values:
                with self.subTest(key=key, size=len(blob)), self.assertRaises(ValueError):
                    self.plan(**{key: blob})

    def test_plan_rejects_wrong_kind_runtime_external_hash_and_corrupt_package(self):
        for name, kind in (("updater", 3), ("boot", 4)):
            blob = getattr(self, name)
            variants = (deploy.lab.build_package(b"\x11" * 513, 0x18000, 7 - kind),
                        deploy.lab.build_package(b"\x11" * 513, 1024, kind),
                        flipped(blob, 508), flipped(blob, 512), blob[:-1], blob + b"\0")
            for candidate in variants:
                with self.subTest(name=name, size=len(candidate)), self.assertRaises(ValueError):
                    self.plan(**{name: candidate, "hashes": {**self.hashes, name: sha(candidate)}})
            for value in ("0" * 64, sha(blob).upper(), "none", deploy.lab.parse_package(blob)["hash"]):
                with self.subTest(name=name, hash=value), self.assertRaises(ValueError):
                    self.plan(hashes={**self.hashes, name: value})

    def test_plan_rejects_wrong_fit_sha_magic_length_and_total_before_prepare(self):
        for value in ("0" * 64, sha(self.fit).upper(), "none", sha(self.fit)[:-1]):
            with self.subTest(hash=value), self.assertRaises(ValueError):
                self.plan(hashes={**self.hashes, "fit": value})
        with self.assertRaises(ValueError):
            self.plan(fit=flipped(self.fit, 40))
        variants = [self.fit[:39], self.fit + bytes(1048577 - len(self.fit)), flipped(self.fit)]
        for total in (0, 39, len(self.fit) + 1, 0xffffffff):
            variants.append(struct.pack(">II", 0xd00dfeed, total) + self.fit[8:])
        for blob in variants:
            with self.subTest(length=len(blob), header=blob[:8]), self.assertRaises(ValueError):
                self.plan(fit=blob, hashes={**self.hashes, "fit": sha(blob)})
        for total in (40, len(self.fit)):
            blob = struct.pack(">II", 0xd00dfeed, total) + self.fit[8:]
            planned = self.plan(fit=blob, hashes={**self.hashes, "fit": sha(blob)})
            self.assertEqual(planned[1048576:1048576 + len(blob)], blob)
        with self.environment(prepared=False) as env, mock.patch.object(os, "pwrite") as write:
            env.args.fit_sha256 = "0" * 64
            with self.assertRaises(ValueError):
                deploy.prepare(env.args, env.fd, self.identity, self.partition)
            write.assert_not_called()
            self.assertFalse(env.root.exists())

    def test_write_ranges_retries_short_writes_syncs_payloads_then_entry(self):
        with self.media() as (fd, flush):
            events, writes = [], []

            def write(descriptor, blob, offset):
                self.assertEqual(descriptor, fd)
                self.assertEqual(events[-1], "recheck")
                index = next(i for i, (start, size) in enumerate(RANGES)
                             if start <= offset < start + size)
                start, size = RANGES[index]
                self.assertEqual(blob, self.after[offset:start + size])
                count = REAL_PWRITE(fd, blob[:4093], offset)
                writes.append((offset, count, index))
                events.append(("write", index))
                return count

            with mock.patch.object(os, "pwrite", side_effect=write), mock.patch.object(
                    os, "fsync", side_effect=lambda descriptor: events.append(("sync", descriptor))):
                deploy.write_ranges(fd, self.before, self.after, lambda: events.append("recheck"))
            for index in (1, 2, 3, 0):
                chunks = [(offset, size) for offset, size, selected in writes if selected == index]
                self.assertEqual(chunks[0][0], RANGES[index][0])
                self.assertEqual(sum(size for _, size in chunks), RANGES[index][1])
                for (offset, size), (next_offset, _) in zip(chunks, chunks[1:]):
                    self.assertEqual(offset + size, next_offset)
            self.assertEqual([index for position, (_, _, index) in enumerate(writes)
                              if position == 0 or index != writes[position - 1][2]], [1, 2, 3, 0])
            for previous, following in ((1, 2), (2, 3), (3, 0)):
                end = max(i for i, item in enumerate(events) if item == ("write", previous))
                start = events.index(("write", following))
                self.assertIn(("sync", fd), events[end + 1:start])
            last_write = max(i for i, item in enumerate(events) if item == ("write", 0))
            self.assertIn(("sync", fd), events[last_write + 1:])
            self.assertEqual(sd.read_exact(fd, 0, sd.PREFIX), self.after)
            self.assert_partition_samples(fd)
            self.assertGreaterEqual(flush.call_count, 1)

    def test_write_ranges_rejects_zero_negative_bool_and_oversized_counts(self):
        for value in (0, -1, True, 1.0, None, RANGES[1][1] + 1):
            with self.subTest(count=value), self.media() as (fd, flush), mock.patch.object(
                    os, "pwrite", return_value=value) as write, mock.patch.object(os, "fsync") as sync:
                with self.assertRaises(ValueError):
                    deploy.write_ranges(fd, self.before, self.after, mock.Mock())
                write.assert_called_once()
                sync.assert_not_called()
                flush.assert_not_called()
                self.assertEqual(sd.read_exact(fd, 0, sd.PREFIX), self.before)

    def test_write_ranges_rejects_outside_changes_lengths_and_stale_current_before_write(self):
        for desired in (flipped(self.after), flipped(self.after, 3407872),
                        self.after[:-1], self.after + b"\0"):
            with self.subTest(size=len(desired)), self.media() as (fd, _), mock.patch.object(os, "pwrite") as write:
                recheck = mock.Mock()
                with self.assertRaises(ValueError):
                    deploy.write_ranges(fd, self.before, desired, recheck)
                write.assert_not_called()
                recheck.assert_not_called()
        with self.media(flipped(self.before, 8192)) as (fd, _), mock.patch.object(os, "pwrite") as write:
            with self.assertRaisesRegex(ValueError, "寫入前"):
                deploy.write_ranges(fd, self.before, self.after, mock.Mock())
            write.assert_not_called()

    def test_recheck_failure_before_first_or_mid_write_stops_immediately(self):
        for failure_at, expected_writes in ((1, 0), (2, 0), (4, 2)):
            with self.subTest(failure_at=failure_at), self.media() as (fd, flush):
                recheck = mock.Mock(side_effect=[None] * (failure_at - 1) + [ValueError("替身身分重查失敗")])
                with mock.patch.object(os, "pwrite", side_effect=lambda descriptor, blob, offset:
                                       REAL_PWRITE(fd, blob[:4096], offset)) as write:
                    with self.assertRaisesRegex(ValueError, "重查失敗"):
                        deploy.write_ranges(fd, self.before, self.after, recheck)
                self.assertEqual(write.call_count, expected_writes)
                self.assertEqual(recheck.call_count, failure_at)
                flush.assert_not_called()
                self.assertEqual(os.pread(fd, 40960, 8192), self.before[8192:49152])
                self.assert_partition_samples(fd)

    def test_full_readback_mismatch_never_returns_success(self):
        with self.media() as (fd, _), mock.patch.object(os, "pwrite", side_effect=lambda descriptor, blob, offset:
                REAL_PWRITE(fd, flipped(blob), offset)):
            with self.assertRaisesRegex(ValueError, "回讀不符"):
                deploy.write_ranges(fd, self.before, self.after, mock.Mock())

    def test_payload_readback_failure_must_prevent_entry_switch(self):
        for failed_index in (1, 2, 3):
            with self.subTest(range_index=failed_index), self.media() as (fd, _):
                offsets = []

                def corrupt_payload(descriptor, blob, offset):
                    offsets.append(offset)
                    return REAL_PWRITE(fd, flipped(blob) if offset == RANGES[failed_index][0] else blob, offset)

                with mock.patch.object(os, "pwrite", side_effect=corrupt_payload):
                    with self.assertRaises(ValueError):
                        deploy.write_ranges(fd, self.before, self.after, mock.Mock())
                self.assertEqual(offsets, [RANGES[index][0] for index in range(1, failed_index + 1)])
                self.assertNotIn(8192, offsets, "負載回讀未通過之前不得切換固定入口")
                self.assertEqual(os.pread(fd, 40960, 8192), self.before[8192:49152])

    def test_prepare_saves_exact_private_backups_manifest_and_no_media_write(self):
        with self.environment(prepared=False) as env, mock.patch.object(os, "pwrite") as write:
            result = deploy.prepare(env.args, env.fd, self.identity, self.partition)
            write.assert_not_called()
            env.flush.assert_not_called()
            self.assertEqual(stat.S_IMODE(env.root.stat().st_mode), 0o700)
            self.assertEqual({path.name for path in env.root.iterdir()},
                             {"before.bin", "expected.bin", "loader.bin", "fit.bin", "updater.bin", "boot.bin", "prepared.json"})
            for name, blob in (("before", self.before), ("expected", self.after),
                               ("loader", self.loader), ("fit", self.fit), ("updater", self.updater), ("boot", self.boot)):
                self.assertEqual((env.root / (name + ".bin")).read_bytes(), blob)
                self.assertEqual(stat.S_IMODE((env.root / (name + ".bin")).stat().st_mode), 0o600)
            self.assertEqual(self.record(env), {
                "schema": 1, "purpose": deploy.PURPOSE, "identity": self.identity,
                "partition": self.partition, "board": "0845", "before_sha256": sha(self.before),
                "expected_sha256": sha(self.after), "loader_sha256": sha(self.loader),
                "payload_sha256": self.hashes, "ranges": [list(value) for value in RANGES],
                "partition_guard_sha256": [sha(b"\xa5" * sd.GUARD), sha(b"\x5a" * sd.GUARD)]})
            self.assertEqual(result["prepared_sha256"], sha((env.root / "prepared.json").read_bytes()))
            self.assertIs(result["media_written"], False)
            self.assertIs(result["hardware_validated"], False)
            self.assert_partition_samples(env.fd)
            with self.assertRaises(FileExistsError):
                deploy.prepare(env.args, env.fd, self.identity, self.partition)

    def test_prepare_must_read_back_persisted_manifest_before_reporting_prepared(self):
        original_save = sd.save
        with self.environment(prepared=False) as env:
            def corrupt_manifest(root, name, blob):
                original_save(root, name, flipped(blob) if name == "prepared.json" else blob)

            with mock.patch.object(sd, "save", side_effect=corrupt_manifest), mock.patch.object(os, "pwrite") as write:
                with self.assertRaises(ValueError, msg="持久化清單損毀時不得回報備份成功"):
                    deploy.prepare(env.args, env.fd, self.identity, self.partition)
                write.assert_not_called()

    def test_change_rejects_manifest_scope_hash_identity_and_backup_tampering(self):
        with self.environment() as env:
            original = self.record(env)
            changes = ({"ranges": [[0, sd.PREFIX]]}, {"ranges": [list(x) for x in RANGES] + [[sd.PREFIX, 512]]},
                       {"ranges": [list(x) for x in reversed(RANGES)]}, {"board": "1116"},
                       {"purpose": "rootfs"}, {"purpose": "0845-lab-v3-preserve-original-system"},
                       {"schema": 2}, {"loader_sha256": "0" * 64},
                       {"identity": {**self.identity, "cid": "2" * 32}},
                       {"partition": {**self.partition, "start": 8193}},
                       {"before_sha256": "0" * 64}, {"expected_sha256": "0" * 64},
                       {"partition_guard_sha256": ["0" * 64] * 2},
                       {"payload_sha256": {**self.hashes, "fit": "0" * 64}},
                       {"payload_sha256": {name: self.hashes[name] for name in ("updater", "boot")}},
                       {"payload_sha256": {**self.hashes, "boot": "0" * 64}})
            for change in changes:
                with self.subTest(fields=list(change)), mock.patch.object(deploy, "write_ranges") as write:
                    self.replace_record(env, {**original, **change})
                    with self.assertRaises(ValueError):
                        deploy.change(env.args, env.fd, self.identity, self.partition)
                    write.assert_not_called()
            self.replace_record(env, original)
            env.args.prepared_sha256 = "0" * 64
            with mock.patch.object(deploy, "write_ranges") as write, self.assertRaises(ValueError):
                deploy.change(env.args, env.fd, self.identity, self.partition)
            write.assert_not_called()
            self.replace_record(env, original)
            for name in ("before.bin", "expected.bin", "loader.bin", "fit.bin", "updater.bin", "boot.bin"):
                path = env.root / name
                blob = path.read_bytes()
                path.write_bytes(flipped(blob))
                with self.subTest(name=name), mock.patch.object(deploy, "write_ranges") as write:
                    with self.assertRaises(ValueError):
                        deploy.change(env.args, env.fd, self.identity, self.partition)
                    write.assert_not_called()
                path.write_bytes(blob)
            self.assertFalse(list(env.root.glob("apply-*")))

    def test_manifest_unknown_fields_boolean_schema_and_float_ranges_are_not_accepted(self):
        with self.environment() as env:
            original = self.record(env)
            REAL_PWRITE(env.fd, self.after, 0)
            for changes in ({"scope": "額外部署整個 rootfs"}, {"schema": True}, {"schema": 1.0},
                            {"ranges": [[float(a), float(b)] for a, b in RANGES]},
                            {"payload_sha256": {**self.hashes, "rootfs": "0" * 64}}):
                with self.subTest(fields=list(changes)), mock.patch.object(deploy, "write_ranges") as write:
                    self.replace_record(env, {**copy.deepcopy(original), **changes})
                    with self.assertRaises(ValueError, msg="清單欄位與型別不可因數值相等或忽略未知欄位而放寬"):
                        deploy.change(env.args, env.fd, self.identity, self.partition)
                    write.assert_not_called()

    def test_change_apply_restore_and_mid_recheck_failure_preserve_evidence_and_stop(self):
        with self.environment() as env:
            backups = {path.name: path.read_bytes() for path in env.root.iterdir()}
            for action, desired in (("apply", self.after), ("restore", self.before)):
                env.args.action = action
                result = deploy.change(env.args, env.fd, self.identity, self.partition)
                self.assertEqual(result["status"], "applied" if action == "apply" else "restored")
                self.assertFalse(result["hardware_validated"])
                self.assertTrue(result["unchanged_outside_ranges"])
                self.assertEqual(sd.read_exact(env.fd, 0, sd.PREFIX), desired)
                self.assert_partition_samples(env.fd)
            env.args.action = "apply"
            env.inspect.side_effect = [self.partition, self.partition, ValueError("替身身分重查失敗")]
            with mock.patch.object(os, "pwrite", side_effect=REAL_PWRITE) as write:
                with self.assertRaisesRegex(ValueError, "重查失敗"):
                    deploy.change(env.args, env.fd, self.identity, self.partition)
                self.assertEqual([call.args[2] for call in write.call_args_list], [RANGES[1][0]])
            attempt = env.root / "apply-0002"
            self.assertFalse((attempt / "result.json").exists())
            self.assertFalse(json.loads((attempt / "failure.json").read_bytes())["automatic_restore"])
            self.assertEqual((attempt / "before.bin").read_bytes(), self.before)
            current = sd.read_exact(env.fd, 0, sd.PREFIX)
            self.assertEqual((attempt / "failure-readback.bin").read_bytes(), current)
            self.assertEqual(current[8192:49152], self.before[8192:49152])
            env.inspect.side_effect = None
            with mock.patch.object(deploy, "write_ranges") as write, self.assertRaises(ValueError):
                deploy.change(env.args, env.fd, self.identity, self.partition)
            write.assert_not_called()
            self.assertEqual({name: (env.root / name).read_bytes() for name in backups}, backups)
            self.assert_partition_samples(env.fd)


if __name__ == "__main__":
    unittest.main()

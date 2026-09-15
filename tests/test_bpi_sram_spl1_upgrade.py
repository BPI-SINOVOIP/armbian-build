#!/usr/bin/env python3
"""SPL1 升級離線回歸；媒體僅為一般暫存檔，不開啟任何真實裝置。"""

from contextlib import ExitStack, contextmanager, redirect_stdout
import hashlib
import importlib.util
import io
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


TOOLS = Path(__file__).resolve().parents[1] / "tools"
with mock.patch.object(sys, "path", [str(TOOLS), *sys.path]):
    SPEC = importlib.util.spec_from_file_location(
        "bpi_sram_spl1_upgrade", TOOLS / "bpi_sram_spl1_upgrade.py")
    upgrade = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(upgrade)
sd = upgrade.sd


def sha(blob):
    return hashlib.sha256(blob).hexdigest()


def flipped(blob, offset=0):
    result = bytearray(blob)
    result[offset] ^= 1
    return bytes(result)


def snapshot(root):
    """同時核對內容、檔案身分與時間，避免相同內容掩蓋覆寫證據。"""
    return {str(path.relative_to(root)): (sha(path.read_bytes()), path.stat().st_ino,
                                        path.stat().st_mtime_ns)
            for path in root.rglob("*") if path.is_file()}


class UpgradeFixture(unittest.TestCase):
    def setUp(self):
        self.old = b"o" * upgrade.SPL_BYTES
        self.new = b"n" * upgrade.SPL_BYTES
        self.slot = sd.package.build_package(bytes(range(256)) * 2 + b"x", 528)
        prefix = bytearray(bytes(range(256)) * (upgrade.PREFIX // 256))
        prefix[446:510] = bytes(64)
        prefix[450] = 0x83
        prefix[510:512] = b"\x55\xaa"
        struct.pack_into("<II", prefix, 454, 8192, 8192)
        prefix[upgrade.SPL_OFFSET:upgrade.SPL_OFFSET + upgrade.SPL_BYTES] = self.old
        prefix[sd.SLOT_OFFSET:sd.SLOT_OFFSET + sd.SLOT_BYTES] = self.slot
        self.before = bytes(prefix)
        prefix[upgrade.SPL_OFFSET:upgrade.SPL_OFFSET + upgrade.SPL_BYTES] = self.new
        self.after = bytes(prefix)
        self.identity = {"cid": "1" * 32, "bytes": 64 * 1024**2, "devnum": "179:0"}
        self.partition = {"start": 8192, "sectors": 8192}
        self.partition_blob = b"a" * sd.GUARD + b"b" * (2 * sd.GUARD) + b"c" * sd.GUARD
        self.tail = b"0123456789abcdef"

    def hashes(self):
        """僅替換三個測試產物的建置雜湊；清單、前綴與封包內部校驗不替換。"""
        def fixture_digest(blob):
            for candidate, expected in ((self.old, upgrade.OLD_SHA),
                                        (self.new, upgrade.NEW_SHA), (self.slot, sd.SLOT_SHA)):
                if blob == candidate:
                    return expected
            return sha(blob)
        return mock.patch.object(upgrade, "digest", side_effect=fixture_digest)

    @contextmanager
    def media(self, initial=None):
        with tempfile.TemporaryFile() as stream:
            stream.write((self.before if initial is None else initial) + self.partition_blob)
            stream.seek(self.identity["bytes"] - len(self.tail))
            stream.write(self.tail)
            stream.flush()
            fd = stream.fileno()
            self.assertTrue(stat.S_ISREG(os.fstat(fd).st_mode))
            with mock.patch.object(upgrade.fcntl, "ioctl", return_value=0) as ioctl:
                yield fd, ioctl

    @contextmanager
    def environment(self, *, prepared=True, initial=None):
        with tempfile.TemporaryDirectory() as directory, self.media(initial) as (fd, ioctl):
            source = Path(directory) / "candidate.bin"
            source.write_bytes(self.new)
            root = Path(directory) / "evidence"
            args = SimpleNamespace(action="prepare", device="/mock/media", board_label="0845",
                                   evidence_dir=str(root), spl1=str(source), prepared_sha256=None)
            with self.hashes(), mock.patch.object(sd, "inspect_device", return_value=self.partition) as inspect:
                result = None
                if prepared:
                    result = upgrade.prepare(args, fd, self.identity, self.partition)
                    args.prepared_sha256 = result["prepared_sha256"]
                    args.action = "apply"
                    inspect.reset_mock()
                yield SimpleNamespace(fd=fd, ioctl=ioctl, inspect=inspect, args=args,
                                      root=root, source=source, result=result)

    def assert_media(self, fd, prefix):
        self.assertEqual(os.pread(fd, upgrade.PREFIX, 0), prefix)
        self.assertEqual(os.pread(fd, len(self.partition_blob), upgrade.PREFIX), self.partition_blob)
        self.assertEqual(os.pread(fd, len(self.tail), self.identity["bytes"] - len(self.tail)), self.tail)
        self.assertEqual(os.fstat(fd).st_size, self.identity["bytes"])

    def manifest(self, env):
        return json.loads((env.root / "prepared.json").read_bytes())

    def replace_manifest(self, env, record):
        blob = sd.json_bytes(record)
        (env.root / "prepared.json").write_bytes(blob)
        env.args.prepared_sha256 = sha(blob)

    def assert_failure(self, env, initial, current, attempt="apply-0001", *, saved=True):
        root = env.root / attempt
        failure = json.loads((root / "failure.json").read_bytes())
        self.assertIs(failure["automatic_restore"], False)
        self.assertIn("失敗", failure["status"])
        self.assertIn("restore", failure["next_action"])
        self.assertEqual((root / "before.bin").read_bytes(), initial)
        names = {"before.bin", "failure.json"}
        if saved:
            names.add("failure-readback.bin")
            self.assertEqual((root / "failure-readback.bin").read_bytes(), current)
            self.assertEqual(failure["readback_sha256"], sha(current))
        else:
            self.assertIs(failure["readback_saved"], False)
            self.assertNotIn("readback_sha256", failure)
        self.assertEqual({path.name for path in root.iterdir()}, names)
        if attempt.startswith("apply"):
            self.assertFalse(any(env.root.glob("restore-*")))


class PlanTests(UpgradeFixture):
    def test_fixed_build_hashes_offsets_and_single_range(self):
        self.assertEqual(upgrade.OLD_SHA, "aef7a1a8c4eb84eb73b4fee519b93561ef5722fc0fc442049e308836695f7f76")
        self.assertEqual(upgrade.NEW_SHA, "80e67d7ebaacb58d8b2b64a6a01a94b4d4329c5f11f8f9cb35e917d060f7e33d")
        self.assertEqual(sd.SLOT_SHA, "8f98a2e2f612341b0d108f147cd6c9dcfbb957c4c0577c5d6e171c6e898bd062")
        self.assertEqual((upgrade.PREFIX, upgrade.SPL_OFFSET, upgrade.SPL_BYTES), (4194304, 8192, 40960))
        self.assertEqual((sd.SLOT_OFFSET, sd.SLOT_BYTES), (3145728, 1536))
        self.assertEqual(upgrade.UNCHANGED, ((0, 8192), (49152, 4194304)))
        self.assertEqual(upgrade.PURPOSE, "0845-spl1-build009-to-v2-build003")

    def test_plan_changes_only_spl1_and_really_parses_slot(self):
        with self.hashes(), mock.patch.object(sd.package, "parse_package", wraps=sd.package.parse_package) as parse:
            actual = upgrade.planned_prefix(self.before, self.new)
        parse.assert_called_once_with(self.slot)
        self.assertEqual(actual, self.after)
        self.assertEqual(actual[:512], self.before[:512])
        self.assertEqual(actual[sd.SLOT_OFFSET:sd.SLOT_OFFSET + sd.SLOT_BYTES], self.slot)
        for start, end in upgrade.UNCHANGED:
            self.assertEqual(actual[start:end], self.before[start:end])
        self.assertEqual(self.before[upgrade.SPL_OFFSET:upgrade.SPL_OFFSET + upgrade.SPL_BYTES], self.old)

    def test_reject_wrong_old_spl_or_slot(self):
        for offset in (upgrade.SPL_OFFSET, upgrade.SPL_OFFSET + upgrade.SPL_BYTES - 1,
                       sd.SLOT_OFFSET, sd.SLOT_OFFSET + sd.SLOT_BYTES - 1):
            with self.subTest(offset=offset), self.hashes(), self.assertRaises(ValueError):
                upgrade.planned_prefix(flipped(self.before, offset), self.new)

    def test_reject_wrong_candidate_or_prefix_length(self):
        for before, new in ((self.before[:-1], self.new), (self.before + b"x", self.new),
                            (self.before, self.new[:-1]), (self.before, self.new + b"x"),
                            (self.before, self.old), (self.before, flipped(self.new))):
            with self.subTest(lengths=(len(before), len(new)), hash=sha(new)), self.hashes(), self.assertRaises(ValueError):
                upgrade.planned_prefix(before, new)

    def test_reject_corrupt_slot_even_with_matching_build_hash(self):
        for offset in (0, 508, 512, sd.SLOT_BYTES - 1):
            prefix = flipped(self.before, sd.SLOT_OFFSET + offset)
            with self.subTest(offset=offset), mock.patch.object(
                    upgrade, "digest", side_effect=(upgrade.OLD_SHA, sd.SLOT_SHA)), self.assertRaises(sd.package.PackageError):
                upgrade.planned_prefix(prefix, self.new)

    def test_unchanged_covers_mbr_slot_gaps_tail_and_lengths(self):
        for offset in (0, 446, 510, 8191, 49152, sd.SLOT_OFFSET - 1, sd.SLOT_OFFSET,
                       sd.SLOT_OFFSET + sd.SLOT_BYTES - 1, sd.SLOT_OFFSET + sd.SLOT_BYTES, upgrade.PREFIX - 1):
            with self.subTest(offset=offset):
                self.assertFalse(upgrade.unchanged_matches(self.before, flipped(self.before, offset)))
        self.assertTrue(upgrade.unchanged_matches(self.before, self.after))
        for before, current in ((self.before[:-1], self.before[:-1]), (self.before, self.before[:-1]),
                                (self.before + b"x", self.before + b"x")):
            self.assertFalse(upgrade.unchanged_matches(before, current))

    def test_unchanged_hashes_use_exact_two_regions(self):
        expected = [sha(self.before[:8192]), sha(self.before[49152:])]
        self.assertEqual(upgrade.unchanged_hashes(self.before), expected)
        self.assertEqual(upgrade.unchanged_hashes(self.after), expected)


class PrepareTests(UpgradeFixture):
    def test_prepare_persists_matching_backup_manifest_and_real_hashes(self):
        with self.environment(prepared=False) as env, mock.patch.object(os, "pwrite", wraps=os.pwrite) as write:
            result = upgrade.prepare(env.args, env.fd, self.identity, self.partition)
            write.assert_not_called()
            env.ioctl.assert_not_called()
            env.inspect.assert_called_once_with(env.args.device, self.identity, env.fd)
            self.assert_media(env.fd, self.before)
            self.assertEqual({p.name for p in env.root.iterdir()},
                             {"before.bin", "expected.bin", "spl1-egon.bin", "prepared.json"})
            self.assertEqual(stat.S_IMODE(env.root.stat().st_mode), 0o700)
            for name, expected in (("before.bin", self.before), ("expected.bin", self.after),
                                   ("spl1-egon.bin", self.new)):
                self.assertEqual((env.root / name).read_bytes(), expected)
                self.assertEqual(stat.S_IMODE((env.root / name).stat().st_mode), 0o600)
            record = self.manifest(env)
            self.assertEqual(record, {
                "schema": 1, "purpose": upgrade.PURPOSE, "board_label": "0845",
                "identity": self.identity, "partition": self.partition,
                "before_sha256": sha(self.before), "expected_sha256": sha(self.after),
                "old_spl1_sha256": upgrade.OLD_SHA, "new_spl1_sha256": upgrade.NEW_SHA,
                "slot0_sha256": sd.SLOT_SHA, "write_offset": 8192, "write_bytes": 40960,
                "partition_guard_sha256": [sha(b"a" * sd.GUARD), sha(b"c" * sd.GUARD)],
                "unchanged_sha256": [sha(self.before[:8192]), sha(self.before[49152:])],
            })
            self.assertEqual(result, {"status": "prepared", "media_written": False,
                                      "hardware_validated": False, "before_sha256": sha(self.before),
                                      "expected_sha256": sha(self.after),
                                      "prepared_sha256": sha((env.root / "prepared.json").read_bytes())})

    def test_prepare_rejects_wrong_candidate_without_media_or_evidence_writes(self):
        for candidate in (self.old, flipped(self.new), self.new[:-1], self.new + b"x"):
            with self.subTest(length=len(candidate), hash=sha(candidate)), self.environment(prepared=False) as env:
                env.source.write_bytes(candidate)
                with mock.patch.object(os, "pwrite") as write, self.assertRaises(ValueError):
                    upgrade.prepare(env.args, env.fd, self.identity, self.partition)
                write.assert_not_called()
                self.assertFalse(env.root.exists())
                self.assert_media(env.fd, self.before)

    def test_prepare_rejects_wrong_old_slot_and_kernel_layout(self):
        cases = [(flipped(self.before, upgrade.SPL_OFFSET), self.partition),
                 (flipped(self.before, sd.SLOT_OFFSET), self.partition),
                 (self.before, dict(self.partition, sectors=8193))]
        for initial, partition in cases:
            with self.subTest(hash=sha(initial), partition=partition), self.environment(prepared=False, initial=initial) as env:
                with mock.patch.object(os, "pwrite") as write, self.assertRaises(ValueError):
                    upgrade.prepare(env.args, env.fd, self.identity, partition)
                write.assert_not_called()
                self.assertFalse(env.root.exists())

    def test_prepare_rejects_identity_prefix_or_guard_races(self):
        real_read, real_guards = sd.read_exact, sd.guards
        for fault in ("identity", "layout", "prefix", "guards"):
            with self.subTest(fault=fault), self.environment(prepared=False) as env, ExitStack() as stack:
                if fault == "identity":
                    env.inspect.side_effect = ValueError("模擬身分改變")
                elif fault == "layout":
                    env.inspect.return_value = dict(self.partition, sectors=8193)
                elif fault == "prefix":
                    reads = 0

                    def read(fd, offset, size):
                        nonlocal reads
                        result = real_read(fd, offset, size)
                        if offset == 0 and size == upgrade.PREFIX:
                            reads += 1
                            if reads == 2:
                                return flipped(result)
                        return result
                    stack.enter_context(mock.patch.object(sd, "read_exact", side_effect=read))
                else:
                    guards = real_guards(env.fd, self.partition)
                    stack.enter_context(mock.patch.object(sd, "guards", side_effect=[guards, ["0" * 64, guards[1]]]))
                with self.assertRaises(ValueError):
                    upgrade.prepare(env.args, env.fd, self.identity, self.partition)
                self.assertFalse(env.root.exists())
                self.assert_media(env.fd, self.before)

    def test_prepare_verifies_every_persisted_file(self):
        real_read = sd.package.read_regular_file
        for name in ("before.bin", "expected.bin", "spl1-egon.bin", "prepared.json"):
            with self.subTest(name=name), self.environment(prepared=False) as env:
                def read(path, limit):
                    blob = real_read(path, limit)
                    return flipped(blob) if path == env.root / name else blob
                with mock.patch.object(sd.package, "read_regular_file", side_effect=read), self.assertRaisesRegex(ValueError, "持久化回讀"):
                    upgrade.prepare(env.args, env.fd, self.identity, self.partition)
                self.assert_media(env.fd, self.before)

    def test_prepare_never_overwrites_existing_evidence(self):
        with self.environment() as env:
            original = snapshot(env.root)
            with self.assertRaises(FileExistsError):
                upgrade.prepare(env.args, env.fd, self.identity, self.partition)
            self.assertEqual(snapshot(env.root), original)
            self.assert_media(env.fd, self.before)

    def test_prepare_rejects_symlink_candidate(self):
        with self.environment(prepared=False) as env:
            link = env.source.with_name("candidate-link.bin")
            link.symlink_to(env.source)
            env.args.spl1 = str(link)
            with self.assertRaises(ValueError):
                upgrade.prepare(env.args, env.fd, self.identity, self.partition)
            self.assertFalse(env.root.exists())


class LoadPreparedTests(UpgradeFixture):
    def test_load_round_trip(self):
        with self.environment() as env:
            original = snapshot(env.root)
            record, before, after = upgrade.load_prepared(env.args, self.identity, self.partition)
            self.assertEqual(record, self.manifest(env))
            self.assertEqual((before, after), (self.before, self.after))
            self.assertEqual(snapshot(env.root), original)

    def test_load_rejects_untrusted_manifest_hash(self):
        with self.environment() as env:
            env.args.prepared_sha256 = "0" * 64
            with self.assertRaisesRegex(ValueError, "可信雜湊"):
                upgrade.load_prepared(env.args, self.identity, self.partition)

    def test_load_rejects_corrupted_or_truncated_artifacts(self):
        for name in ("prepared.json", "before.bin", "expected.bin", "spl1-egon.bin"):
            for corruption in ("flip", "truncate", "append"):
                with self.subTest(name=name, corruption=corruption), self.environment() as env:
                    path = env.root / name
                    blob = path.read_bytes()
                    blob = flipped(blob) if corruption == "flip" else blob[:-1] if corruption == "truncate" else blob + b"x"
                    path.write_bytes(blob)
                    with self.assertRaises(ValueError):
                        upgrade.load_prepared(env.args, self.identity, self.partition)

    def test_load_rejects_symlink_and_missing_artifacts(self):
        for name in ("prepared.json", "before.bin", "expected.bin", "spl1-egon.bin"):
            for mode in ("symlink", "missing"):
                with self.subTest(name=name, mode=mode), self.environment() as env:
                    path = env.root / name
                    target = path.with_name("original-" + name)
                    path.rename(target)
                    if mode == "symlink":
                        path.symlink_to(target)
                    with self.assertRaises((ValueError, FileNotFoundError)):
                        upgrade.load_prepared(env.args, self.identity, self.partition)

    def test_load_rejects_unknown_missing_and_non_object_manifest(self):
        for mode in ("unknown", "missing", "list", "null", "string"):
            with self.subTest(mode=mode), self.environment() as env:
                record = self.manifest(env)
                if mode == "unknown":
                    record["scope"] = "只允許 SPL1"
                elif mode == "missing":
                    del record["unchanged_sha256"]
                else:
                    record = {"list": [], "null": None, "string": "清單"}[mode]
                self.replace_manifest(env, record)
                with self.assertRaisesRegex(ValueError, "欄位"):
                    upgrade.load_prepared(env.args, self.identity, self.partition)

    def test_load_rejects_invalid_json_duplicate_fields_and_encoding(self):
        for blob in (b"{", b"\xff", b'{"schema":1,"schema":1}'):
            with self.subTest(blob=blob), self.environment() as env:
                (env.root / "prepared.json").write_bytes(blob)
                env.args.prepared_sha256 = sha(blob)
                with self.assertRaises(ValueError):
                    upgrade.load_prepared(env.args, self.identity, self.partition)

    def test_load_rejects_wrong_schema_including_bool_and_float(self):
        for value in (0, 2, True, 1.0, "1", None):
            with self.subTest(value=value), self.environment() as env:
                self.replace_manifest(env, dict(self.manifest(env), schema=value))
                with self.assertRaisesRegex(ValueError, "身分、板號、布局或用途"):
                    upgrade.load_prepared(env.args, self.identity, self.partition)

    def test_load_rejects_wrong_identity_partition_board_or_purpose(self):
        cases = [("identity", dict(self.identity, cid="2" * 32)),
                 ("identity", dict(self.identity, bytes=self.identity["bytes"] + 512)),
                 ("identity", dict(self.identity, devnum="179:8")),
                 ("identity", dict(self.identity, unknown=True)),
                 ("partition", dict(self.partition, start=8193)),
                 ("partition", dict(self.partition, sectors=8193)),
                 ("partition", dict(self.partition, unknown=True)),
                 ("board_label", "0939"), ("board_label", 845), ("purpose", "其他用途")]
        for key, value in cases:
            with self.subTest(key=key, value=value), self.environment() as env:
                self.replace_manifest(env, dict(self.manifest(env), **{key: value}))
                with self.assertRaisesRegex(ValueError, "身分、板號、布局或用途"):
                    upgrade.load_prepared(env.args, self.identity, self.partition)

    def test_load_rejects_wrong_caller_identity_partition_or_board(self):
        for mode in ("identity", "partition", "board"):
            with self.subTest(mode=mode), self.environment() as env:
                identity, partition = self.identity, self.partition
                if mode == "identity":
                    identity = dict(identity, cid="2" * 32)
                elif mode == "partition":
                    partition = dict(partition, start=8193)
                else:
                    env.args.board_label = "0939"
                with self.assertRaises(ValueError):
                    upgrade.load_prepared(env.args, identity, partition)

    def test_load_rejects_expanded_scope_and_changed_fixed_hashes(self):
        cases = [("write_offset", 0), ("write_offset", sd.SLOT_OFFSET),
                 ("write_offset", 8192.0), ("write_offset", True), ("write_offset", "8192"),
                 ("write_bytes", 40961), ("write_bytes", 40959), ("write_bytes", 40960.0),
                 ("write_bytes", True), ("write_bytes", "40960"),
                 ("old_spl1_sha256", "0" * 64), ("new_spl1_sha256", upgrade.OLD_SHA),
                 ("slot0_sha256", "0" * 64)]
        for key, value in cases:
            with self.subTest(key=key, value=value), self.environment() as env:
                self.replace_manifest(env, dict(self.manifest(env), **{key: value}))
                with self.assertRaisesRegex(ValueError, "範圍或固定產物"):
                    upgrade.load_prepared(env.args, self.identity, self.partition)

    def test_load_rejects_invalid_guard_and_unchanged_hash_shapes(self):
        for key in ("partition_guard_sha256", "unchanged_sha256"):
            for value in (None, {}, "0" * 64, [], ["0" * 64], ["0" * 64] * 3,
                          [0, "0" * 64], ["A" * 64, "0" * 64], ["0" * 63, "0" * 64],
                          ["0" * 64 + "\n", "0" * 64]):
                with self.subTest(key=key, value=value), self.environment() as env:
                    self.replace_manifest(env, dict(self.manifest(env), **{key: value}))
                    with self.assertRaisesRegex(ValueError, "抽查雜湊格式"):
                        upgrade.load_prepared(env.args, self.identity, self.partition)

    def test_load_rechecks_backup_layout_old_slot_and_unchanged_hashes(self):
        for mode in ("mbr", "outside", "old", "slot", "unchanged", "before_hash", "expected_hash"):
            with self.subTest(mode=mode), self.environment() as env:
                record = self.manifest(env)
                if mode in ("mbr", "outside", "old", "slot"):
                    offset = {"mbr": 510, "outside": 100, "old": upgrade.SPL_OFFSET,
                              "slot": sd.SLOT_OFFSET}[mode]
                    before = flipped(self.before, offset)
                    (env.root / "before.bin").write_bytes(before)
                    record["before_sha256"] = sha(before)
                    if mode != "outside":
                        record["unchanged_sha256"] = upgrade.unchanged_hashes(before)
                elif mode == "unchanged":
                    record["unchanged_sha256"] = ["0" * 64] * 2
                else:
                    record["before_sha256" if mode == "before_hash" else "expected_sha256"] = "0" * 64
                self.replace_manifest(env, record)
                with self.assertRaises(ValueError):
                    upgrade.load_prepared(env.args, self.identity, self.partition)


class WriteTests(UpgradeFixture):
    def test_write_single_range_with_sync_flush_and_rechecks(self):
        events = []
        real_write, real_sync = os.pwrite, os.fsync
        with self.media() as (fd, ioctl), mock.patch.object(os, "pwrite", side_effect=lambda f, b, o:
                (events.append(("write", f, o, len(b))), real_write(f, b, o))[1]), mock.patch.object(
                os, "fsync", side_effect=lambda f: (events.append(("fsync", f)), real_sync(f))[1]):
            ioctl.side_effect = lambda *args: events.append(("ioctl", *args))
            upgrade.write_spl1(fd, self.before, self.after, recheck=lambda: events.append("recheck"))
            self.assert_media(fd, self.after)
        self.assertEqual(events, ["recheck", "recheck", ("write", fd, 8192, 40960),
                                  ("fsync", fd), ("ioctl", fd, 0x1261), "recheck"])

    def test_short_writes_and_reads_finish_contiguously_within_spl1(self):
        real_write, real_read = os.pwrite, os.pread
        with self.media() as (fd, ioctl), mock.patch.object(os, "pwrite", side_effect=lambda f, b, o:
                real_write(f, b[:257], o)) as write, mock.patch.object(os, "pread", side_effect=lambda f, n, o:
                real_read(f, min(n, 4093), o)):
            recheck = mock.Mock()
            upgrade.write_spl1(fd, self.before, self.after, recheck=recheck)
            position = upgrade.SPL_OFFSET
            for call in write.call_args_list:
                descriptor, blob, offset = call.args
                self.assertEqual((descriptor, offset), (fd, position))
                self.assertEqual(blob, self.after[offset:49152])
                position += min(257, len(blob))
            self.assertEqual(position, 49152)
            self.assertEqual(recheck.call_count, write.call_count + 2)
            ioctl.assert_called_once_with(fd, 0x1261)
            self.assertEqual(real_read(fd, upgrade.PREFIX, 0), self.after)
            self.assertEqual(real_read(fd, len(self.partition_blob), upgrade.PREFIX), self.partition_blob)
            self.assertEqual(real_read(fd, len(self.tail), self.identity["bytes"] - len(self.tail)), self.tail)

    def test_reject_invalid_write_counts_without_sync_or_second_write(self):
        for count in (0, -1, upgrade.SPL_BYTES + 1, True, False, 1.0, None, "1"):
            with self.subTest(count=count), self.media() as (fd, ioctl), mock.patch.object(
                    os, "pwrite", return_value=count) as write, mock.patch.object(os, "fsync") as sync:
                with self.assertRaises(ValueError):
                    upgrade.write_spl1(fd, self.before, self.after, recheck=mock.Mock())
                write.assert_called_once_with(fd, self.new, upgrade.SPL_OFFSET)
                sync.assert_not_called()
                ioctl.assert_not_called()
                self.assert_media(fd, self.before)

    def test_reject_changed_media_before_first_write(self):
        with self.media(flipped(self.before, upgrade.SPL_OFFSET)) as (fd, ioctl), mock.patch.object(os, "pwrite") as write:
            with self.assertRaisesRegex(ValueError, "媒體內容已改變"):
                upgrade.write_spl1(fd, self.before, self.after, recheck=mock.Mock())
            write.assert_not_called()
            ioctl.assert_not_called()

    def test_reject_outside_scope_before_identity_or_writes(self):
        for offset in (0, 8191, 49152, sd.SLOT_OFFSET, upgrade.PREFIX - 1):
            with self.subTest(offset=offset), self.media() as (fd, ioctl), mock.patch.object(os, "pwrite") as write:
                recheck = mock.Mock()
                with self.assertRaises(ValueError):
                    upgrade.write_spl1(fd, self.before, flipped(self.after, offset), recheck=recheck)
                write.assert_not_called()
                recheck.assert_not_called()
                ioctl.assert_not_called()

    def test_recheck_failure_before_first_or_later_short_write(self):
        real_write = os.pwrite
        for fail_at in (1, 2, 3):
            with self.subTest(fail_at=fail_at), self.media() as (fd, ioctl), mock.patch.object(
                    os, "pwrite", side_effect=lambda f, b, o: real_write(f, b[:257], o)) as write:
                recheck = mock.Mock(side_effect=[None] * (fail_at - 1) + [ValueError("模擬身分改變")])
                with self.assertRaisesRegex(ValueError, "身分改變"):
                    upgrade.write_spl1(fd, self.before, self.after, recheck=recheck)
                self.assertEqual(write.call_count, max(0, fail_at - 2))
                ioctl.assert_not_called()


class ChangeTests(UpgradeFixture):
    def test_apply_writes_only_spl1_and_records_result(self):
        with self.environment() as env, mock.patch.object(os, "pwrite", wraps=os.pwrite) as write:
            original = snapshot(env.root)
            result = upgrade.change(env.args, env.fd, self.identity, self.partition)
            write.assert_called_once_with(env.fd, self.new, 8192)
            env.ioctl.assert_called_once_with(env.fd, 0x1261)
            self.assert_media(env.fd, self.after)
            attempt = env.root / "apply-0001"
            self.assertEqual(result, {
                "status": "applied", "media_written": True, "hardware_validated": False,
                "attempt_directory": "apply-0001", "readback_sha256": sha(self.after),
                "spl1_sha256": upgrade.NEW_SHA, "slot0_sha256": sd.SLOT_SHA,
                "unchanged_sha256": upgrade.unchanged_hashes(self.before),
                "partition_guard_sha256": sd.guards(env.fd, self.partition),
                "write_offset": 8192, "write_bytes": 40960,
            })
            self.assertEqual((attempt / "before.bin").read_bytes(), self.before)
            self.assertEqual((attempt / "after.bin").read_bytes(), self.after)
            self.assertEqual(json.loads((attempt / "result.json").read_bytes()), result)
            current = snapshot(env.root)
            self.assertEqual({key: current[key] for key in original}, original)
            self.assertGreaterEqual(env.inspect.call_count, 3)
            for call in env.inspect.call_args_list:
                self.assertEqual(call, mock.call(env.args.device, self.identity, env.fd))

    def test_repeated_apply_is_noop_and_preserves_first_attempt(self):
        with self.environment() as env:
            upgrade.change(env.args, env.fd, self.identity, self.partition)
            original = snapshot(env.root)
            env.ioctl.reset_mock()
            with mock.patch.object(os, "pwrite") as write:
                result = upgrade.change(env.args, env.fd, self.identity, self.partition)
            write.assert_not_called()
            env.ioctl.assert_not_called()
            self.assertEqual((result["status"], result["write_bytes"], result["attempt_directory"]),
                             ("already_matches", 0, "apply-0002"))
            self.assertIs(result["media_written"], False)
            self.assertIs(result["hardware_validated"], False)
            self.assert_media(env.fd, self.after)
            current = snapshot(env.root)
            self.assertEqual({key: current[key] for key in original}, original)
            self.assertEqual((env.root / "apply-0002/before.bin").read_bytes(), self.after)
            self.assertEqual((env.root / "apply-0002/after.bin").read_bytes(), self.after)

    def test_restore_original_is_noop(self):
        with self.environment() as env, mock.patch.object(os, "pwrite") as write:
            env.args.action = "restore"
            result = upgrade.change(env.args, env.fd, self.identity, self.partition)
            self.assertEqual((result["status"], result["write_bytes"]), ("already_matches", 0))
            self.assertIs(result["media_written"], False)
            write.assert_not_called()
            env.ioctl.assert_not_called()
            self.assert_media(env.fd, self.before)

    def test_restore_upgraded_media_writes_only_old_spl(self):
        with self.environment() as env:
            os.pwrite(env.fd, self.new, upgrade.SPL_OFFSET)
            env.args.action = "restore"
            with mock.patch.object(os, "pwrite", wraps=os.pwrite) as write:
                result = upgrade.change(env.args, env.fd, self.identity, self.partition)
            write.assert_called_once_with(env.fd, self.old, 8192)
            self.assertEqual((result["status"], result["spl1_sha256"]), ("restored", upgrade.OLD_SHA))
            self.assert_media(env.fd, self.before)

    def test_apply_rejects_partial_spl_without_attempt_or_writes(self):
        with self.environment() as env:
            os.pwrite(env.fd, self.new[:257], upgrade.SPL_OFFSET)
            original = snapshot(env.root)
            with mock.patch.object(os, "pwrite") as write, self.assertRaisesRegex(ValueError, "拒絕再次升級"):
                upgrade.change(env.args, env.fd, self.identity, self.partition)
            write.assert_not_called()
            self.assertEqual(snapshot(env.root), original)

    def test_apply_and_restore_reject_guard_changes_before_attempt(self):
        for action in ("apply", "restore"):
            for offset in (upgrade.PREFIX, 8 * 1024**2 - 1):
                with self.subTest(action=action, offset=offset), self.environment() as env:
                    os.pwrite(env.fd, b"z", offset)
                    env.args.action = action
                    original = snapshot(env.root)
                    with mock.patch.object(os, "pwrite") as write, self.assertRaisesRegex(ValueError, "抽查已變動"):
                        upgrade.change(env.args, env.fd, self.identity, self.partition)
                    write.assert_not_called()
                    self.assertEqual(snapshot(env.root), original)

    def test_apply_and_restore_reject_mbr_slot_and_gap_changes(self):
        for action in ("apply", "restore"):
            for offset in (0, 446, 510, 8191, 49152, sd.SLOT_OFFSET - 1, sd.SLOT_OFFSET,
                           sd.SLOT_OFFSET + sd.SLOT_BYTES - 1, sd.SLOT_OFFSET + sd.SLOT_BYTES,
                           upgrade.PREFIX - 1):
                with self.subTest(action=action, offset=offset), self.environment() as env:
                    current = flipped(self.before, offset)
                    os.pwrite(env.fd, current[offset:offset + 1], offset)
                    env.args.action = action
                    original = snapshot(env.root)
                    with mock.patch.object(os, "pwrite") as write, self.assertRaisesRegex(ValueError, "白名單外"):
                        upgrade.change(env.args, env.fd, self.identity, self.partition)
                    write.assert_not_called()
                    self.assertEqual(snapshot(env.root), original)
                    self.assert_media(env.fd, current)

    def test_reject_unknown_action(self):
        with self.environment() as env, mock.patch.object(upgrade, "load_prepared") as load:
            env.args.action = "unknown"
            with self.assertRaises(ValueError):
                upgrade.change(env.args, env.fd, self.identity, self.partition)
            load.assert_not_called()

    def test_write_sync_flush_and_readback_failures_save_evidence_without_autorestore(self):
        real_write, real_sync, real_read = os.pwrite, os.fsync, sd.read_exact
        faults = ("partial", "pwrite", "zero", "fsync", "ioctl", "readback", "eof", "read_error")
        for fault in faults:
            with self.subTest(fault=fault), self.environment() as env, ExitStack() as stack:
                original = snapshot(env.root)
                writes = 0
                written = False

                def write(fd, blob, offset):
                    nonlocal writes, written
                    self.assertEqual(fd, env.fd)
                    self.assertGreaterEqual(offset, upgrade.SPL_OFFSET)
                    self.assertLessEqual(offset + len(blob), 49152)
                    writes += 1
                    if fault == "pwrite" or fault == "partial" and writes == 2:
                        raise OSError("模擬寫入失敗")
                    if fault == "zero":
                        return 0
                    count = real_write(fd, blob[:257] if fault == "partial" else blob, offset)
                    written = True
                    return count

                stack.enter_context(mock.patch.object(os, "pwrite", side_effect=write))
                if fault == "fsync":
                    def sync(fd):
                        if fd == env.fd:
                            raise OSError("模擬媒體同步失敗")
                        return real_sync(fd)
                    stack.enter_context(mock.patch.object(os, "fsync", side_effect=sync))
                elif fault == "ioctl":
                    env.ioctl.side_effect = OSError("模擬媒體刷新失敗")
                elif fault in ("readback", "eof", "read_error"):
                    failed_once = False

                    def read(fd, offset, size):
                        nonlocal failed_once
                        if written and offset == 0 and size == upgrade.PREFIX:
                            if fault == "read_error":
                                raise OSError("模擬持續回讀失敗")
                            if fault == "eof":
                                raise ValueError("媒體讀取不完整")
                            if not failed_once:
                                failed_once = True
                                return flipped(real_read(fd, offset, size), upgrade.SPL_OFFSET)
                        return real_read(fd, offset, size)
                    stack.enter_context(mock.patch.object(sd, "read_exact", side_effect=read))
                with self.assertRaises((ValueError, OSError)):
                    upgrade.change(env.args, env.fd, self.identity, self.partition)
                self.assertEqual(writes, 2 if fault == "partial" else 1)
                current = self.before
                if fault == "partial":
                    current = self.before[:8192] + self.new[:257] + self.before[8192 + 257:]
                elif fault not in ("pwrite", "zero"):
                    current = self.after
                self.assert_media(env.fd, current)
                self.assert_failure(env, self.before, current, saved=fault not in ("eof", "read_error"))
                saved = snapshot(env.root)
                self.assertEqual({key: saved[key] for key in original}, original)

    def test_guard_readback_change_is_failure_without_autorestore(self):
        with self.environment() as env:
            guards = sd.guards(env.fd, self.partition)
            with mock.patch.object(sd, "guards", side_effect=[guards, ["0" * 64, guards[1]]]), self.assertRaisesRegex(ValueError, "抽查回讀"):
                upgrade.change(env.args, env.fd, self.identity, self.partition)
            self.assert_media(env.fd, self.after)
            self.assert_failure(env, self.before, self.after)

    def test_identity_or_prefix_race_saves_failure_before_writes(self):
        real_read = sd.read_exact
        for fault in ("identity", "layout", "prefix"):
            with self.subTest(fault=fault), self.environment() as env:
                with ExitStack() as stack:
                    if fault == "identity":
                        env.inspect.side_effect = ValueError("模擬身分改變")
                    elif fault == "layout":
                        env.inspect.return_value = dict(self.partition, sectors=8193)
                    else:
                        reads = 0

                        def read(fd, offset, size):
                            nonlocal reads
                            blob = real_read(fd, offset, size)
                            if offset == 0 and size == upgrade.PREFIX:
                                reads += 1
                                if reads == 2:
                                    return flipped(blob, upgrade.SPL_OFFSET)
                            return blob
                        stack.enter_context(mock.patch.object(sd, "read_exact", side_effect=read))
                    write = stack.enter_context(mock.patch.object(os, "pwrite"))
                    with self.assertRaises(ValueError):
                        upgrade.change(env.args, env.fd, self.identity, self.partition)
                    write.assert_not_called()
                    self.assert_failure(env, self.before, self.before)
                    self.assert_media(env.fd, self.before)

    def test_partial_restore_can_retry_without_overwriting_any_previous_evidence(self):
        real_write = os.pwrite
        with self.environment() as env:
            original = snapshot(env.root)
            partial = self.before[:8192] + self.new[:1024] + self.before[8192 + 1024:]
            real_write(env.fd, self.new[:1024], upgrade.SPL_OFFSET)
            env.args.action = "restore"
            writes = 0

            def write(fd, blob, offset):
                nonlocal writes
                writes += 1
                if writes == 2:
                    raise OSError("模擬復原途中失敗")
                return real_write(fd, blob[:257], offset)

            with mock.patch.object(os, "pwrite", side_effect=write), self.assertRaisesRegex(OSError, "復原途中"):
                upgrade.change(env.args, env.fd, self.identity, self.partition)
            self.assertEqual(writes, 2)
            still_partial = self.before[:8192 + 257] + self.new[257:1024] + self.before[8192 + 1024:]
            self.assert_media(env.fd, still_partial)
            self.assert_failure(env, partial, still_partial, "restore-0001")
            first_snapshot = snapshot(env.root)
            with mock.patch.object(os, "pwrite", wraps=real_write) as retry_write:
                result = upgrade.change(env.args, env.fd, self.identity, self.partition)
            retry_write.assert_called_once_with(env.fd, self.old, 8192)
            self.assertEqual((result["status"], result["attempt_directory"]), ("restored", "restore-0002"))
            self.assert_media(env.fd, self.before)
            saved = snapshot(env.root)
            self.assertEqual({key: saved[key] for key in first_snapshot}, first_snapshot)
            self.assertEqual({key: saved[key] for key in original}, original)
            self.assertEqual((env.root / "restore-0002/before.bin").read_bytes(), still_partial)
            self.assertEqual((env.root / "restore-0002/after.bin").read_bytes(), self.before)
            self.assertEqual(json.loads((env.root / "restore-0002/result.json").read_bytes()), result)
            with mock.patch.object(os, "pwrite") as noop_write:
                noop = upgrade.change(env.args, env.fd, self.identity, self.partition)
            noop_write.assert_not_called()
            self.assertEqual((noop["status"], noop["attempt_directory"]), ("already_matches", "restore-0003"))
            newest = snapshot(env.root)
            self.assertEqual({key: newest[key] for key in saved}, saved)


class MainTests(unittest.TestCase):
    @contextmanager
    def environment(self, updates=None, *, holders=None, root=False, swap=None, partitions=1,
                    fd_dev=0, ioctl_bytes=None, node_mode=stat.S_IFBLK, euid=0):
        """封閉替換 sysfs、proc、stat、ioctl；整碟路徑只轉接至一般暫存檔。"""
        identity = {"cid": "1" * 32, "bytes": 64 * 1024**2, "devnum": "179:0"}
        partition = {"start": 8192, "sectors": 8192}
        base = Path("/sys/class/block/mmcblk0")
        values = {str(base / key): value for key, value in {
            "device/cid": identity["cid"], "size": "131072", "device/type": "SD",
            "dev": "179:0", "queue/logical_block_size": "512", "ro": "0",
            "mmcblk0p1/dev": "179:1", "mmcblk0p1/start": "8192", "mmcblk0p1/size": "8192",
            "mmcblk0p2/dev": "179:2",
        }.items()}
        values.update({"/proc/self/mountinfo": "", "/proc/swaps": "Filename Type Size Used Priority\n"})
        values.update(updates or {})
        if swap:
            values["/proc/swaps"] += "/mock/swap file 1 0 -2\n"

        def fake_stat(path, **kwargs):
            if str(path) == "/dev/mmcblk0":
                return SimpleNamespace(st_mode=node_mode, st_rdev=os.makedev(179, 0))
            if str(path) == "/":
                return SimpleNamespace(st_dev=os.makedev(179 if root else 8, 1))
            if str(path) == "/mock/swap":
                return SimpleNamespace(st_mode=stat.S_IFBLK if swap == "block" else stat.S_IFREG,
                                       st_rdev=os.makedev(179, 1), st_dev=os.makedev(179, 1))
            raise AssertionError("禁止查詢未模擬的路徑：" + str(path))

        with tempfile.TemporaryFile() as media, ExitStack() as stack:
            self.assertTrue(stat.S_ISREG(os.fstat(media.fileno()).st_mode))
            descriptors = []
            real_fstat = os.fstat

            def open_media(path, flags, *args, **kwargs):
                self.assertEqual(path, "/dev/mmcblk0", "禁止開啟未模擬的路徑")
                descriptor = os.dup(media.fileno())
                descriptors.append(descriptor)
                return descriptor

            def fstat(descriptor):
                if descriptor in descriptors:
                    return SimpleNamespace(st_rdev=os.makedev(179, fd_dev))
                return real_fstat(descriptor)

            stack.enter_context(mock.patch("gettext.find", return_value=[]))
            stack.enter_context(mock.patch.object(os, "stat", side_effect=fake_stat))
            stack.enter_context(mock.patch.object(os, "fstat", side_effect=fstat))
            stack.enter_context(mock.patch.object(os, "geteuid", return_value=euid))
            opener = stack.enter_context(mock.patch.object(os, "open", side_effect=open_media))
            closer = stack.enter_context(mock.patch.object(os, "close", wraps=os.close))
            stack.enter_context(mock.patch.object(Path, "read_text", autospec=True,
                                                 side_effect=lambda path, *a, **k: values[str(path)]))
            stack.enter_context(mock.patch.object(Path, "glob", autospec=True,
                                                 side_effect=lambda *a: [base / f"mmcblk0p{i + 1}" for i in range(partitions)]))
            stack.enter_context(mock.patch.object(Path, "iterdir", autospec=True,
                                                 side_effect=lambda path: [Path("/mock/holder")]
                                                 if holders == str(path.parent) else []))
            ioctl = stack.enter_context(mock.patch.object(sd.fcntl, "ioctl", return_value=struct.pack(
                "=Q", identity["bytes"] if ioctl_bytes is None else ioctl_bytes)))
            inspect = stack.enter_context(mock.patch.object(sd, "inspect_device", wraps=sd.inspect_device))
            prepare = stack.enter_context(mock.patch.object(upgrade, "prepare", return_value={"status": "prepared"}))
            change = stack.enter_context(mock.patch.object(upgrade, "change", return_value={"status": "applied"}))
            stdout = stack.enter_context(redirect_stdout(io.StringIO()))
            yield SimpleNamespace(identity=identity, partition=partition, opener=opener, closer=closer,
                                  inspect=inspect, prepare=prepare, change=change, ioctl=ioctl,
                                  stdout=stdout, descriptors=descriptors)

    def argv(self, env, action="prepare", **updates):
        options = {"device": "/dev/mmcblk0", "cid": env.identity["cid"],
                   "bytes": str(env.identity["bytes"]), "devnum": env.identity["devnum"],
                   "board-label": "0845", "evidence-dir": "/mock/evidence"}
        options.update({"spl1": "/mock/candidate.bin"} if action == "prepare" else
                       {"prepared-sha256": "a" * 64, "confirm-write": True})
        options.update(updates)
        result = [action]
        for key, value in options.items():
            if value is not None and value is not False:
                result.append("--" + key)
                if value is not True:
                    result.append(str(value))
        return result

    def assert_rejected(self, env, argv, *, opened=False):
        self.assertEqual(upgrade.main(argv), 1)
        result = json.loads(env.stdout.getvalue())
        self.assertEqual(result["status"], "error")
        self.assertIs(result["hardware_validated"], False)
        env.prepare.assert_not_called()
        env.change.assert_not_called()
        if opened:
            self.assertEqual(env.opener.call_count, 1)
            env.closer.assert_called_once_with(env.descriptors[0])
        else:
            env.opener.assert_not_called()
        return result

    def test_main_runs_real_inspection_and_opens_exclusively_in_correct_mode(self):
        for action in ("prepare", "apply", "restore"):
            with self.subTest(action=action), self.environment() as env:
                self.assertEqual(upgrade.main(self.argv(env, action)), 0)
                mode = os.O_RDONLY if action == "prepare" else os.O_RDWR
                env.opener.assert_called_once_with("/dev/mmcblk0", mode | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC)
                descriptor = env.descriptors[0]
                self.assertEqual(env.inspect.call_args_list, [mock.call("/dev/mmcblk0", env.identity),
                                                              mock.call("/dev/mmcblk0", env.identity, descriptor)])
                env.ioctl.assert_called_once_with(descriptor, 0x80081272, bytes(8))
                selected = env.prepare if action == "prepare" else env.change
                self.assertEqual(selected.call_args.args[1:], (descriptor, env.identity, env.partition))
                self.assertEqual(selected.call_args.args[0].action, action)
                (env.change if action == "prepare" else env.prepare).assert_not_called()
                env.closer.assert_called_once_with(descriptor)

    def test_main_rejects_cid_capacity_emmc_devnum_sector_size_and_readonly(self):
        cases = [("device/cid", "2" * 32), ("size", "131073"), ("device/type", "MMC"),
                 ("device/type", "未知"), ("dev", "179:8"),
                 ("queue/logical_block_size", "4096"), ("ro", "1")]
        for action in ("prepare", "apply", "restore"):
            for key, value in cases:
                with self.subTest(action=action, key=key), self.environment({"/sys/class/block/mmcblk0/" + key: value}) as env:
                    self.assert_rejected(env, self.argv(env, action))
                    self.assertEqual(env.inspect.call_count, 1)
            with self.subTest(action=action, key="expected_devnum"), self.environment() as env:
                self.assert_rejected(env, self.argv(env, action, devnum="179:8"))

    def test_main_rejects_mounts_holders_root_swap_and_partition_counts(self):
        cases = [{"holders": "/sys/class/block/mmcblk0"},
                 {"holders": "/sys/class/block/mmcblk0/mmcblk0p1"},
                 {"root": True}, {"swap": "block"}, {"swap": "file"},
                 {"partitions": 0}, {"partitions": 2}]
        for number in ("179:0", "179:1"):
            cases.append({"updates": {"/proc/self/mountinfo": f"1 0 {number} / /mnt rw - ext4 /mock rw\n"}})
        for action in ("prepare", "apply", "restore"):
            for kwargs in cases:
                with self.subTest(action=action, kwargs=kwargs), self.environment(**kwargs) as env:
                    self.assert_rejected(env, self.argv(env, action))
                    self.assertEqual(env.inspect.call_count, 1)

    def test_main_rejects_partition_path_other_disk_symlink_and_regular_node(self):
        for device in ("/dev/mmcblk0p1", "/dev/sda", "/tmp/media", "/dev/mmcblk0/../mmcblk1"):
            with self.subTest(device=device), self.environment() as env:
                self.assert_rejected(env, self.argv(env, device=device))
        for mode in (stat.S_IFREG, stat.S_IFLNK, stat.S_IFCHR):
            with self.subTest(mode=mode), self.environment(node_mode=mode) as env:
                self.assert_rejected(env, self.argv(env))

    def test_main_rejects_descriptor_devnum_capacity_and_ioctl_failure(self):
        for action in ("prepare", "apply", "restore"):
            for kwargs in ({"fd_dev": 1}, {"ioctl_bytes": 0}, {"ioctl_bytes": 64 * 1024**2 + 512}):
                with self.subTest(action=action, kwargs=kwargs), self.environment(**kwargs) as env:
                    self.assert_rejected(env, self.argv(env, action), opened=True)
            with self.subTest(action=action, fault="ioctl"), self.environment() as env:
                env.ioctl.side_effect = OSError("模擬容量查詢失敗")
                result = self.assert_rejected(env, self.argv(env, action), opened=True)
                self.assertEqual(result["error_type"], "OSError")

    def test_main_rejects_layout_change_after_open(self):
        with self.environment() as env:
            real_inspect = sd.inspect_device._mock_wraps

            def inspect(device, expected, fd=None):
                result = real_inspect(device, expected, fd)
                return result if fd is None else dict(result, sectors=8193)
            env.inspect.side_effect = inspect
            self.assert_rejected(env, self.argv(env), opened=True)

    def test_main_requires_root_before_inspection(self):
        with self.environment(euid=1000) as env:
            self.assert_rejected(env, self.argv(env))
            env.inspect.assert_not_called()

    def test_main_rejects_invalid_identity_format_before_inspection(self):
        cases = ({"cid": "1" * 31}, {"cid": "A" * 32}, {"cid": "1" * 32 + "\n"},
                 {"bytes": 64 * 1024**2 - 1}, {"devnum": "179"}, {"devnum": "179:-1"})
        for kwargs in cases:
            with self.subTest(kwargs=kwargs), self.environment() as env:
                self.assert_rejected(env, self.argv(env, **kwargs))
                env.inspect.assert_not_called()

    def test_main_rejects_mixed_prepare_and_write_arguments(self):
        cases = [("prepare", {"spl1": None}), ("prepare", {"confirm-write": True}),
                 ("prepare", {"prepared-sha256": "a" * 64})]
        for action in ("apply", "restore"):
            cases.extend((action, options) for options in (
                {"confirm-write": False}, {"prepared-sha256": None}, {"prepared-sha256": "A" * 64},
                {"prepared-sha256": "a" * 63}, {"prepared-sha256": "a" * 64 + "\n"},
                {"spl1": "/mock/candidate.bin"}))
        for action, kwargs in cases:
            with self.subTest(action=action, kwargs=kwargs), self.environment() as env:
                self.assert_rejected(env, self.argv(env, action, **kwargs))
                env.inspect.assert_not_called()

    def test_main_closes_media_and_reports_handler_failures(self):
        for action in ("prepare", "apply", "restore"):
            with self.subTest(action=action), self.environment() as env:
                handler = env.prepare if action == "prepare" else env.change
                handler.side_effect = OSError("模擬證據操作失敗")
                self.assertEqual(upgrade.main(self.argv(env, action)), 1)
                self.assertEqual(json.loads(env.stdout.getvalue())["error_type"], "OSError")
                env.closer.assert_called_once_with(env.descriptors[0])


if __name__ == "__main__":
    unittest.main()

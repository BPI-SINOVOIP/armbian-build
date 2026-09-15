#!/usr/bin/env python3
"""最小 SD 部署離線回歸；只使用一般暫存檔，不開啟任何真實裝置。"""

from contextlib import ExitStack, contextmanager
import importlib.util
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
    SPEC = importlib.util.spec_from_file_location("bpi_sram_sd_minimal", TOOLS / "bpi_sram_sd_minimal.py")
    sd = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(sd)


def mbr(start=8192, sectors=8192, kind=0x83):
    """建立只有單一分割區的完整前綴。"""
    blob = bytearray(sd.PREFIX)
    blob[510:512] = b"\x55\xaa"
    blob[450] = kind
    struct.pack_into("<II", blob, 454, start, sectors)
    return bytes(blob)


class LayoutTests(unittest.TestCase):
    def test_single_linux_partition(self):
        self.assertEqual(sd.layout(mbr(), 8 * 1024**2), {"start": 8192, "sectors": 8192})

    def test_reject_gpt_extended_and_other_types(self):
        for kind in (0, 0xEE, 0x05, 0x0F, 0x85, 0x07):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                sd.layout(mbr(kind=kind), 16 * 1024**2)

    def test_reject_extra_or_overlapping_partition(self):
        for start in (8192, 10000, 16384):
            blob = bytearray(mbr())
            blob[466] = 0x83
            struct.pack_into("<II", blob, 470, start, 4096)
            with self.subTest(start=start), self.assertRaises(ValueError):
                sd.layout(bytes(blob), 32 * 1024**2)

    def test_reject_wrong_start_length_and_capacity(self):
        for start, sectors, capacity in ((0, 8192, 2**24), (8191, 8192, 2**24),
                                         (8193, 8192, 2**24), (8192, 0, 2**24),
                                         (8192, 4095, 2**24), (8192, 8192, 2**23 - 1),
                                         (8192, 0xFFFFFFFF, 2**24)):
            with self.subTest(start=start, sectors=sectors, capacity=capacity), self.assertRaises(ValueError):
                sd.layout(mbr(start, sectors), capacity)

    def test_reject_bad_signature_prefix_length_and_empty_table(self):
        empty = bytearray(sd.PREFIX)
        empty[510:512] = b"\x55\xaa"
        for blob in (mbr()[:-1], mbr() + b"x", bytes(sd.PREFIX), bytes(empty)):
            with self.subTest(length=len(blob)), self.assertRaises(ValueError):
                sd.layout(blob, 2**24)


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.before = mbr()
        self.spl = b"s" * sd.SPL_BYTES
        self.slot = sd.package.build_package(bytes(range(256)) * 2 + b"x", 528)
        self.assertEqual(len(self.slot), sd.SLOT_BYTES)

    def hashes(self):
        """只替換整體建置雜湊，封包內部 CRC 與 SHA 仍由真實解析器檢查。"""
        real = sd.digest
        return mock.patch.object(sd, "digest", side_effect=lambda blob:
                                 sd.SPL_SHA if blob == self.spl else
                                 sd.SLOT_SHA if blob == self.slot else real(blob))

    def test_fixed_build_contract(self):
        self.assertEqual((sd.SPL_BYTES, sd.SLOT_BYTES), (40960, 1536))
        self.assertEqual(sd.SPL_SHA, "aef7a1a8c4eb84eb73b4fee519b93561ef5722fc0fc442049e308836695f7f76")
        self.assertEqual(sd.SLOT_SHA, "8f98a2e2f612341b0d108f147cd6c9dcfbb957c4c0577c5d6e171c6e898bd062")
        self.assertEqual(sd.EXTENTS, ((3 * 1024**2, 1536), (8192, 40960)))

    def test_plan_changes_only_two_extents_and_parses_real_package(self):
        with self.hashes(), mock.patch.object(sd.package, "parse_package", wraps=sd.package.parse_package) as parse:
            after = sd.planned_prefix(self.before, self.spl, self.slot)
        parse.assert_called_once_with(self.slot)
        expected = bytearray(self.before)
        expected[8192:8192 + 40960] = self.spl
        expected[3 * 1024**2:3 * 1024**2 + 1536] = self.slot
        self.assertEqual(after, bytes(expected))
        self.assertEqual(self.before, mbr())
        self.assertTrue(sd.unchanged_matches(self.before, after))

    def test_reject_incorrect_hashes(self):
        for hashes in (("0" * 64,), (sd.SPL_SHA, "0" * 64)):
            with self.subTest(hashes=hashes), mock.patch.object(sd, "digest", side_effect=hashes), self.assertRaises(ValueError):
                sd.planned_prefix(self.before, self.spl, self.slot)

    def test_reject_incorrect_lengths_even_with_matching_hash(self):
        for before, spl, slot in ((self.before[:-1], self.spl, self.slot),
                                  (self.before, self.spl[:-1], self.slot),
                                  (self.before, self.spl + b"x", self.slot),
                                  (self.before, self.spl, self.slot[:-1]),
                                  (self.before, self.spl, self.slot + b"x")):
            with self.subTest(lengths=(len(before), len(spl), len(slot))), mock.patch.object(
                    sd, "digest", side_effect=(sd.SPL_SHA, sd.SLOT_SHA)), self.assertRaises(ValueError):
                sd.planned_prefix(before, spl, slot)

    def test_reject_corrupt_package_despite_mocked_build_hash(self):
        for offset in (0, 508, 512, len(self.slot) - 1):
            slot = bytearray(self.slot)
            slot[offset] ^= 1
            with self.subTest(offset=offset), mock.patch.object(sd, "digest", side_effect=(sd.SPL_SHA, sd.SLOT_SHA)), self.assertRaises(sd.package.PackageError):
                sd.planned_prefix(self.before, self.spl, bytes(slot))

    def test_unchanged_checks_mbr_gaps_tail_and_lengths(self):
        for offset in (0, 446, 510, 8191, 8192 + 40960, 3 * 1024**2 - 1,
                       3 * 1024**2 + 1536, sd.PREFIX - 1):
            current = bytearray(self.before)
            current[offset] ^= 1
            with self.subTest(offset=offset):
                self.assertFalse(sd.unchanged_matches(self.before, bytes(current)))
        self.assertFalse(sd.unchanged_matches(self.before, self.before[:-1]))
        self.assertFalse(sd.unchanged_matches(self.before[:-1], self.before[:-1]))
        for start, length in sd.EXTENTS:
            current = bytearray(self.before)
            current[start:start + length] = b"z" * length
            self.assertTrue(sd.unchanged_matches(self.before, bytes(current)))


class WriteTests(unittest.TestCase):
    def setUp(self):
        self.before = mbr()
        after = bytearray(self.before)
        for start, length in sd.EXTENTS:
            after[start:start + length] = b"w" * length
        self.after = bytes(after)

    @contextmanager
    def media(self, initial=None):
        """唯一可寫媒體是一般暫存檔，區塊裝置 ioctl 一律替換。"""
        with tempfile.TemporaryFile() as stream:
            stream.write((self.before if initial is None else initial) + b"partition-guard")
            stream.flush()
            self.assertTrue(stat.S_ISREG(os.fstat(stream.fileno()).st_mode))
            with mock.patch.object(sd.fcntl, "ioctl", return_value=0) as ioctl:
                yield stream.fileno(), ioctl

    def test_order_slot_then_spl_with_flush_and_recheck(self):
        events = []
        real_write, real_sync = os.pwrite, os.fsync
        with self.media() as (fd, ioctl), mock.patch.object(sd.os, "pwrite", side_effect=lambda f, b, o:
                (events.append(("write", o)), real_write(f, b, o))[1]), mock.patch.object(
                sd.os, "fsync", side_effect=lambda f: (events.append("fsync"), real_sync(f))[1]):
            ioctl.side_effect = lambda *args: events.append("ioctl")
            sd.write_extents(fd, self.before, self.after, recheck=lambda: events.append("recheck"))
            self.assertEqual(os.pread(fd, sd.PREFIX + 15, 0), self.after + b"partition-guard")
        self.assertEqual(events, ["recheck", ("write", sd.SLOT_OFFSET), "fsync", "ioctl",
                                  "recheck", ("write", sd.SPL_OFFSET), "fsync", "ioctl", "recheck"])

    def test_restore_reverses_order(self):
        with self.media(self.after) as (fd, ioctl), mock.patch.object(sd.os, "pwrite", wraps=os.pwrite) as write:
            sd.write_extents(fd, self.after, self.before, restore=True)
            self.assertEqual([call.args[2] for call in write.call_args_list], [sd.SPL_OFFSET, sd.SLOT_OFFSET])
            self.assertEqual(os.pread(fd, sd.PREFIX, 0), self.before)
            self.assertEqual(ioctl.call_count, 2)

    def test_change_restore_retry_preserves_both_attempts_and_backup(self):
        """SPL1 復原後故障，同一備份可重試，兩次證據皆保留且不越界寫入。"""
        spl = b"s" * sd.SPL_BYTES
        slot = sd.package.build_package(bytes(range(256)) * 2 + b"x", 528)
        after = bytearray(self.before)
        after[sd.SPL_OFFSET:sd.SPL_OFFSET + sd.SPL_BYTES] = spl
        after[sd.SLOT_OFFSET:sd.SLOT_OFFSET + sd.SLOT_BYTES] = slot
        after = bytes(after)
        partial = bytearray(after)
        partial[sd.SPL_OFFSET:sd.SPL_OFFSET + sd.SPL_BYTES] = self.before[sd.SPL_OFFSET:sd.SPL_OFFSET + sd.SPL_BYTES]
        partial = bytes(partial)
        expected = {"cid": "1" * 32, "bytes": 8 * 1024**2, "devnum": "179:0"}
        partition = sd.layout(self.before, expected["bytes"])
        guard_hashes = [sd.digest(b"a"), sd.digest(b"b")]
        record = sd.json_bytes({
            "schema": 1, "board_label": "0845", "identity": expected,
            "partition": partition, "before_sha256": sd.digest(self.before),
            "expected_sha256": sd.digest(after), "partition_guard_sha256": guard_hashes,
            "unchanged_sha256": [sd.digest(self.before[start:end]) for start, end in sd.UNCHANGED],
            "scope": "離線一般檔案測試，分割區抽查由模擬資料提供",
        })
        originals = {"prepared.json": record, "before.bin": self.before, "expected.bin": after,
                     "spl1-egon.bin": spl, "spl2-smoke.sram": slot}
        real_digest = sd.digest

        def fixture_digest(blob):
            """僅替換測試產物的固定建置雜湊，備份清單及封包內部校驗仍為真實值。"""
            return sd.SPL_SHA if blob == spl else sd.SLOT_SHA if blob == slot else real_digest(blob)

        with tempfile.TemporaryDirectory() as directory, self.media(after) as (fd, ioctl):
            root = Path(directory)
            for name, blob in originals.items():
                sd.save(root, name, blob)
            args = SimpleNamespace(action="restore", evidence_dir=directory, board_label="0845",
                                   device="/mock/media", prepared_sha256=sd.digest(record))
            with mock.patch.object(sd, "digest", side_effect=fixture_digest), mock.patch.object(
                    sd, "guards", return_value=guard_hashes), mock.patch.object(
                    sd, "inspect_device", return_value=partition) as inspect, mock.patch.object(
                    sd.os, "pwrite", wraps=os.pwrite) as write:
                inspect.side_effect = [partition, partition, OSError("模擬 SPL1 完成後身分重查失敗")]
                with self.assertRaisesRegex(OSError, "SPL1 完成後"):
                    sd.change(args, fd, expected, partition)
                self.assertEqual(inspect.call_count, 3)
                self.assertEqual([(c.args[2], len(c.args[1])) for c in write.call_args_list],
                                 [(sd.SPL_OFFSET, sd.SPL_BYTES)])
                ioctl.assert_called_once_with(fd, 0x1261)
                self.assertEqual(os.pread(fd, sd.PREFIX + 15, 0), partial + b"partition-guard")
                first = root / "restore-0001"
                self.assertEqual({path.name for path in first.iterdir()}, {"before.bin", "failure.json"})
                self.assertEqual((first / "before.bin").read_bytes(), after)
                self.assertIs(sd.files.parse_manifest((first / "failure.json").read_bytes())["automatic_restore"], False)
                first_snapshot = {path.name: (path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns)
                                  for path in first.iterdir()}

                inspect.side_effect = None
                inspect.reset_mock()
                write.reset_mock()
                ioctl.reset_mock()
                result = sd.change(args, fd, expected, partition)
                self.assertEqual(inspect.call_count, 4)
                inspect.assert_called_with(args.device, expected, fd)
                self.assertEqual([(c.args[0], c.args[2], len(c.args[1])) for c in write.call_args_list],
                                 [(fd, sd.SPL_OFFSET, sd.SPL_BYTES), (fd, sd.SLOT_OFFSET, sd.SLOT_BYTES)])
                self.assertEqual(ioctl.call_args_list, [mock.call(fd, 0x1261), mock.call(fd, 0x1261)])
                self.assertEqual(os.pread(fd, sd.PREFIX + 15, 0), self.before + b"partition-guard")
                self.assertEqual(result["status"], "restored")
                self.assertIs(result["media_written"], True)
                self.assertIs(result["hardware_validated"], False)
                self.assertEqual(result["attempt_directory"], "restore-0002")
                self.assertEqual(result["readback_sha256"], sd.digest(self.before))
                second = root / "restore-0002"
                self.assertEqual({path.name for path in second.iterdir()}, {"before.bin", "after.bin", "result.json"})
                self.assertEqual((second / "before.bin").read_bytes(), partial)
                self.assertEqual((second / "after.bin").read_bytes(), self.before)
                self.assertEqual(sd.files.parse_manifest((second / "result.json").read_bytes()), result)
                self.assertEqual({path.name: (path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns)
                                  for path in first.iterdir()}, first_snapshot)
                self.assertEqual({path.name for path in root.iterdir()},
                                 set(originals) | {"restore-0001", "restore-0002"})
                for name, blob in originals.items():
                    self.assertEqual((root / name).read_bytes(), blob, "重試不得改寫原始備份：" + name)

    def test_short_reads_and_writes_complete_without_gaps(self):
        real_write, real_read = os.pwrite, os.pread
        with self.media() as (fd, ioctl), mock.patch.object(sd.os, "pwrite", side_effect=lambda f, b, o:
                real_write(f, b[:257], o)) as write, mock.patch.object(sd.os, "pread", side_effect=lambda f, n, o:
                real_read(f, min(n, 4093), o)):
            sd.write_extents(fd, self.before, self.after)
            self.assertEqual(sd.read_exact(fd, 0, sd.PREFIX), self.after)
            self.assertGreater(write.call_count, 2)
            for call in write.call_args_list:
                offset = call.args[2]
                self.assertTrue(any(start <= offset < start + size for start, size in sd.EXTENTS))

    def test_invalid_write_results_stop_before_spl(self):
        for count in (0, -1, sd.SLOT_BYTES + 1):
            with self.subTest(count=count), self.media() as (fd, ioctl), mock.patch.object(
                    sd.os, "pwrite", return_value=count) as write, self.assertRaises(ValueError):
                try:
                    sd.write_extents(fd, self.before, self.after)
                finally:
                    self.assertEqual([c.args[2] for c in write.call_args_list], [sd.SLOT_OFFSET])
                    ioctl.assert_not_called()

    def test_flush_readback_and_recheck_failures_stop_before_spl(self):
        real_read = os.pread
        for failure in ("pwrite", "fsync", "ioctl", "eof", "mismatch", "recheck"):
            with self.subTest(failure=failure), self.media() as (fd, ioctl), ExitStack() as stack:
                write = stack.enter_context(mock.patch.object(sd.os, "pwrite", wraps=os.pwrite))
                recheck = mock.Mock()
                if failure == "pwrite":
                    write.side_effect = OSError("模擬寫入失敗")
                elif failure == "fsync":
                    stack.enter_context(mock.patch.object(sd.os, "fsync", side_effect=OSError("模擬同步失敗")))
                elif failure == "ioctl":
                    ioctl.side_effect = OSError("模擬刷新失敗")
                elif failure in ("eof", "mismatch"):
                    stack.enter_context(mock.patch.object(sd.os, "pread", return_value=b"" if failure == "eof" else bytes(sd.SLOT_BYTES)))
                else:
                    recheck.side_effect = [None, ValueError("模擬身分改變")]
                with self.assertRaises((ValueError, OSError)):
                    sd.write_extents(fd, self.before, self.after, recheck=recheck)
                self.assertEqual([call.args[2] for call in write.call_args_list], [sd.SLOT_OFFSET])
                self.assertEqual(real_read(fd, sd.SPL_BYTES, sd.SPL_OFFSET), bytes(sd.SPL_BYTES))

    def test_outside_changes_and_identity_failure_produce_zero_writes(self):
        for restore in (False, True):
            for offset in (0, 8191, sd.SPL_OFFSET + sd.SPL_BYTES, sd.SLOT_OFFSET - 1,
                           sd.SLOT_OFFSET + sd.SLOT_BYTES, sd.PREFIX - 1, None):
                current = bytearray(self.before)
                if offset is not None:
                    current[offset] ^= 1
                recheck = mock.Mock(side_effect=ValueError("模擬身分不符"))
                with self.subTest(offset=offset, restore=restore), self.media(bytes(current)) as (fd, ioctl), mock.patch.object(
                        sd.os, "pwrite", wraps=os.pwrite) as write, self.assertRaises(ValueError):
                    try:
                        sd.write_extents(fd, bytes(current), self.after, restore=restore, recheck=recheck)
                    finally:
                        write.assert_not_called()
                        ioctl.assert_not_called()
                        self.assertEqual(os.pread(fd, sd.PREFIX, 0), bytes(current))
                        self.assertEqual(recheck.call_count, int(offset is None))

    def test_read_exact_rejects_truncated_regular_file(self):
        with tempfile.TemporaryFile() as stream:
            stream.write(b"abc")
            stream.flush()
            with self.assertRaises(ValueError):
                sd.read_exact(stream.fileno(), 0, 4)


class InspectTests(unittest.TestCase):
    @contextmanager
    def environment(self, updates=None, *, holders=False, root=False, swap=None,
                    fd_dev=0, ioctl_bytes=None, partitions=1):
        """所有 sysfs、proc、stat 與 ioctl 都封閉替換，不查詢真實 MMC。"""
        expected = {"cid": "1" * 32, "bytes": 8 * 1024**2, "devnum": "179:0"}
        base = Path("/sys/class/block/mmcblk0")
        values = {str(base / key): value for key, value in {
            "device/cid": expected["cid"], "size": "16384", "device/type": "SD",
            "dev": "179:0", "queue/logical_block_size": "512", "ro": "0",
            "mmcblk0p1/dev": "179:1", "mmcblk0p1/start": "8192", "mmcblk0p1/size": "8192",
        }.items()}
        values.update({"/proc/self/mountinfo": "", "/proc/swaps": "Filename Type Size Used Priority\n"})
        values.update(updates or {})
        if swap:
            values["/proc/swaps"] += "/mock/swap file 1 0 -2\n"

        def fake_stat(path, **kwargs):
            if str(path) == "/dev/mmcblk0":
                return SimpleNamespace(st_mode=stat.S_IFBLK, st_rdev=os.makedev(179, 0))
            if str(path) == "/":
                return SimpleNamespace(st_dev=os.makedev(179 if root else 8, 1))
            if str(path) == "/mock/swap":
                return SimpleNamespace(st_mode=stat.S_IFBLK if swap == "block" else stat.S_IFREG,
                                       st_rdev=os.makedev(179, 1), st_dev=os.makedev(179, 1))
            raise AssertionError("禁止查詢未模擬的路徑：" + str(path))

        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(sd.os, "stat", side_effect=fake_stat))
            stack.enter_context(mock.patch.object(sd.os, "fstat", return_value=SimpleNamespace(st_rdev=os.makedev(179, fd_dev))))
            stack.enter_context(mock.patch.object(Path, "read_text", autospec=True, side_effect=lambda path, *a, **k: values[str(path)]))
            stack.enter_context(mock.patch.object(Path, "glob", autospec=True, side_effect=lambda *a: [base / f"mmcblk0p{i + 1}" for i in range(partitions)]))
            stack.enter_context(mock.patch.object(Path, "iterdir", autospec=True, side_effect=lambda path: [Path("/mock/holder")] if holders else []))
            ioctl = stack.enter_context(mock.patch.object(sd.fcntl, "ioctl", return_value=struct.pack("=Q", expected["bytes"] if ioctl_bytes is None else ioctl_bytes)))
            yield expected, ioctl

    def test_accepts_mocked_identity_and_open_descriptor(self):
        with self.environment() as (expected, ioctl):
            self.assertEqual(sd.inspect_device("/dev/mmcblk0", expected, 99), {"start": 8192, "sectors": 8192})
            ioctl.assert_called_once_with(99, 0x80081272, bytes(8))

    def test_rejects_cid_size_devnum_and_sysfs_properties(self):
        for key, value in (("device/cid", "2" * 32), ("size", "16385"), ("dev", "179:9"),
                           ("device/type", "MMC"), ("queue/logical_block_size", "4096"), ("ro", "1")):
            with self.subTest(key=key), self.environment({"/sys/class/block/mmcblk0/" + key: value}) as (expected, ioctl), self.assertRaises(ValueError):
                sd.inspect_device("/dev/mmcblk0", expected)
        with self.environment() as (expected, ioctl), self.assertRaises(ValueError):
            sd.inspect_device("/dev/mmcblk0", dict(expected, devnum="179:8"))

    def test_rejects_mount_swap_holders_root_and_partition_counts(self):
        cases = [{"holders": True}, {"root": True}, {"swap": "block"}, {"swap": "file"},
                 {"partitions": 0}, {"partitions": 2}]
        for number in ("179:0", "179:1"):
            cases.append({"updates": {"/proc/self/mountinfo": f"1 0 {number} / /mnt rw - ext4 /mock rw\n"}})
        for kwargs in cases:
            with self.subTest(kwargs=kwargs), self.environment(**kwargs) as (expected, ioctl), self.assertRaises(ValueError):
                sd.inspect_device("/dev/mmcblk0", expected)

    def test_rejects_descriptor_identity_capacity_and_ioctl_failure(self):
        for kwargs in ({"fd_dev": 1}, {"ioctl_bytes": 0}):
            with self.subTest(kwargs=kwargs), self.environment(**kwargs) as (expected, ioctl), self.assertRaises(ValueError):
                sd.inspect_device("/dev/mmcblk0", expected, 99)
        with self.environment() as (expected, ioctl), self.assertRaises(OSError):
            ioctl.side_effect = OSError("模擬容量查詢失敗")
            sd.inspect_device("/dev/mmcblk0", expected, 99)

    def test_rejects_non_whole_device_paths_before_stat(self):
        for device in ("/dev/mmcblk0p1", "/dev/sda", "/tmp/media", "/dev/mmcblk0/../mmcblk1"):
            with self.subTest(device=device), mock.patch.object(sd.os, "stat") as node, self.assertRaises(ValueError):
                try:
                    sd.inspect_device(device, {})
                finally:
                    node.assert_not_called()


if __name__ == "__main__":
    unittest.main()

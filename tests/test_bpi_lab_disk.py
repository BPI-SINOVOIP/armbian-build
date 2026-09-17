"""MBR／GPT 與獨立 FAT 開機區的唯讀回歸；只建立暫存一般檔案。"""

import copy
from contextlib import contextmanager
import hashlib
import json
import lzma
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import bpi_lab_disk as disk
import bpi_lab_image as image


UUID = "12345678-1234-4234-8234-123456789abc"


def sha(blob):
    return hashlib.sha256(blob).hexdigest()


class TableTests(unittest.TestCase):
    def document(self):
        return {"partitiontable": {"label": "dos", "id": "0x12345678", "device": "/proc/self/fd/7",
            "unit": "sectors", "sectorsize": 512,
            "partitions": [{"node": "/proc/self/fd/7p1", "start": 2048, "size": 4096, "type": "83"}]}}

    def test_valid(self):
        self.assertEqual(disk.partitions(self.document(), "/proc/self/fd/7", 4 * 1024**2)[0]["partuuid"],
                         "12345678-01")

    def test_reject_table_changes(self):
        for field, value in (("sectorsize", 4096), ("unit", "bytes"), ("label", "sun"), ("id", "bad")):
            doc = self.document()
            doc["partitiontable"][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                disk.partitions(doc, "/proc/self/fd/7", 4 * 1024**2)

    def test_reject_partition_changes(self):
        for field, value in (("start", 0), ("size", True), ("size", 100000), ("type", "5"),
                             ("node", "/dev/sda1"), ("node", "/proc/self/fd/7p5")):
            doc = self.document()
            doc["partitiontable"]["partitions"][0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                disk.partitions(doc, "/proc/self/fd/7", 4 * 1024**2)

    def test_overlap(self):
        doc = self.document()
        row = copy.deepcopy(doc["partitiontable"]["partitions"][0])
        row["node"] = "/proc/self/fd/7p2"
        doc["partitiontable"]["partitions"].append(row)
        with self.assertRaisesRegex(ValueError, "重疊"):
            disk.partitions(doc, "/proc/self/fd/7", 4 * 1024**2)

    def test_fstab(self):
        result = disk.fstab_mounts(f"UUID={UUID} / ext4 defaults 0 1\nUUID=ABCD-1234 /boot vfat defaults 0 2\n".encode())
        self.assertEqual(result["/boot"]["source"], "UUID=ABCD-1234")

    def test_fstab_reject(self):
        for blob in (b"", b"/dev/mmcblk0p1 / ext4 defaults 0 1", b"UUID=x / vfat defaults 0 1",
                     b"UUID=x / ext4 defaults\nUUID=y / ext4 defaults",
                     b"UUID=x / ext4 defaults\nUUID=y /boot/efi vfat defaults"):
            with self.subTest(blob=blob), self.assertRaises(ValueError):
                disk.fstab_mounts(blob)

    def test_fstab_noncanonical_not_ignored(self):
        for path in ("/./boot", "/boot/", "//boot", "/x/../boot"):
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, "正規"):
                disk.fstab_mounts(f"UUID={UUID} / ext4 defaults\nUUID=x {path} vfat defaults\n".encode())


@unittest.skipUnless(all(Path(path).exists() for path in (
    "/usr/sbin/sfdisk", "/usr/sbin/sgdisk", "/usr/sbin/mkfs.fat", "/usr/sbin/mke2fs",
    "/usr/bin/mcopy", "/usr/bin/mtype", "/usr/sbin/debugfs", "/usr/sbin/blkid")), "需要本機檔案系統工具")
class DiskTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        root = Path(cls.temp.name)
        files = root / "files"
        (files / "etc").mkdir(parents=True)
        (files / "boot").mkdir()
        (files / "etc/armbian-release").write_bytes(b"BOARD=bananapim7\n")
        (files / "boot/Image").write_bytes(b"wrong-root-kernel")
        (files / "vmlinuz").write_bytes(b"root-kernel-link-target")
        (files / "boot-link").symlink_to("/boot/Image")
        fat = root / "fat"
        with fat.open("wb") as out:
            out.truncate(8 * 1024**2)
        cls.run_tool(["/usr/sbin/mkfs.fat", "-F", "16", "-s", "1", "-i", "ABCD1234", str(fat)])
        kernel = root / "Image"
        kernel.write_bytes(b"correct-boot-kernel")
        cls.run_tool(["/usr/bin/mcopy", "-i", str(fat), str(kernel), "::/Image"])
        cls.raws = {}
        for label in ("dos", "gpt"):
            fstab = f"UUID={UUID} / ext4 defaults 0 1\nUUID=ABCD-1234 /boot vfat defaults 0 2\n"
            (files / "etc/fstab").write_text(fstab)
            ext = root / (label + ".ext")
            with ext.open("wb") as out:
                out.truncate(16 * 1024**2)
            cls.run_tool(["/usr/sbin/mke2fs", "-q", "-F", "-t", "ext4", "-U", UUID, "-d", str(files), str(ext)])
            raw = root / (label + ".img")
            with raw.open("wb") as out:
                out.truncate(32 * 1024**2)
            script = f"label: {label}\nunit: sectors\nstart=2048,size=16384,type={'c' if label == 'dos' else 'U'}\nstart=20480,size=32768,type=L\n"
            cls.run_tool(["/usr/sbin/sfdisk", str(raw)], script.encode())
            with raw.open("r+b") as out:
                out.seek(2048 * 512)
                out.write(fat.read_bytes())
                out.seek(20480 * 512)
                out.write(ext.read_bytes())
            cls.raws[label] = raw.read_bytes()
        boot_files = root / "boot-files"
        boot_files.mkdir()
        (boot_files / "Image").symlink_to("/vmlinuz")
        boot = root / "boot-ext"
        boot_uuid = "12345678-1234-4234-8234-123456789abd"
        with boot.open("wb") as out:
            out.truncate(8 * 1024**2)
        cls.run_tool(["/usr/sbin/mke2fs", "-q", "-F", "-t", "ext4", "-U", boot_uuid, "-d", str(boot_files), str(boot)])
        (files / "etc/fstab").write_text(f"UUID={UUID} / ext4 defaults 0 1\nUUID={boot_uuid} /boot ext4 defaults 0 2\n")
        cls.run_tool(["/usr/sbin/mke2fs", "-q", "-F", "-t", "ext4", "-U", UUID, "-d", str(files), str(ext)])
        raw_ext = bytearray(cls.raws["gpt"])
        raw_ext[2048 * 512:2048 * 512 + boot.stat().st_size] = boot.read_bytes()
        raw_ext[20480 * 512:20480 * 512 + ext.stat().st_size] = ext.read_bytes()
        cls.raws["gpt-ext"] = bytes(raw_ext)

    @staticmethod
    def run_tool(argv, data=None):
        result = subprocess.run(argv, input=data, capture_output=True)
        if result.returncode:
            raise RuntimeError(result.stderr.decode())

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def reader(self, label="gpt", *, raw=None, compress=False, expected=None):
        blob = self.raws[label] if raw is None else raw
        blob = lzma.compress(blob) if compress else blob
        source = self.root / "source.img"
        source.write_bytes(blob)
        return disk.DiskReader(source, expected or sha(blob), self.root / "evidence", max_raw_bytes=64 * 1024**2)

    def test_gpt_fat_boot_and_ext_root(self):
        reader = self.reader()
        with reader:
            self.assertEqual(reader.read_file("/boot/Image"), b"correct-boot-kernel")
            self.assertEqual(reader.read_file("/etc/armbian-release"), b"BOARD=bananapim7\n")
            self.assertEqual(reader.filesystem_uuid, UUID)
            self.assertEqual(reader.report["boot_partition"]["index"], 1)
            self.assertEqual(reader.report["partition"]["index"], 2)
        report = json.loads((reader.output / "extraction.json").read_bytes())
        self.assertTrue(report["ok"])
        self.assertEqual(list(reader.output.rglob("partition.tmp")), [])
        self.assertEqual(sha(reader.source.read_bytes()), sha(self.raws["gpt"]))

    def test_mbr_fat_boot(self):
        with self.reader("dos") as reader:
            self.assertEqual(reader.read_file("/boot/Image"), b"correct-boot-kernel")

    def test_xz_gpt(self):
        with self.reader(compress=True) as reader:
            self.assertEqual(reader.read_file("/boot/Image"), b"correct-boot-kernel")

    def test_replay_missing_fat_and_ext(self):
        with self.reader() as reader:
            reader.read_file("/boot/Image")
            for path in ("/boot/no-such-file", "/etc/no-such-file"):
                with self.assertRaises(FileNotFoundError):
                    reader.read_file(path)
        evidence = self.root / "evidence/extraction.json"
        with image.SnapshotReader(evidence, sha(evidence.read_bytes()), self.root / "replay") as replay:
            self.assertEqual(replay.read_file("/boot/Image"), b"correct-boot-kernel")
            for path in ("/boot/no-such-file", "/etc/no-such-file"):
                with self.assertRaises(FileNotFoundError):
                    replay.read_file(path)

    def test_cross_mount_link_not_wrong_file(self):
        with self.reader() as reader:
            with self.assertRaisesRegex(ValueError, "跨開機掛載"):
                reader.read_file("/boot-link")

    def test_boot_absolute_symlink_is_not_missing(self):
        with self.reader("gpt-ext") as reader:
            with self.assertRaisesRegex(ValueError, "絕對連結"):
                reader.read_file("/boot/Image")
            self.assertEqual(reader.records, [])

    def test_reopened_temporary_inode_is_pinned(self):
        original = image.safe.open_file
        for target in (1, 2):
            count = 0
            @contextmanager
            def intercept(parent, name, **kwargs):
                nonlocal count
                if name == "partition.tmp" and not kwargs.get("create"):
                    count += 1
                    if count == target:
                        os.rename(name, "original.tmp", src_dir_fd=parent, dst_dir_fd=parent)
                        with original(parent, name, create=True) as replacement:
                            replacement.write(bytes(512))
                        with original(parent, name) as stream:
                            os.unlink(name, dir_fd=parent)
                            os.rename("original.tmp", name, src_dir_fd=parent, dst_dir_fd=parent)
                            yield stream
                        return
                with original(parent, name, **kwargs) as stream:
                    yield stream
            with tempfile.TemporaryDirectory() as tmp:
                source = Path(tmp) / "source.img"
                source.write_bytes(self.raws["gpt"])
                reader = disk.DiskReader(source, sha(self.raws["gpt"]), Path(tmp) / "result", max_raw_bytes=64 * 1024**2)
                with self.subTest(target=target), mock.patch.object(image.safe, "open_file", intercept):
                    with self.assertRaisesRegex(ValueError, "置換"):
                        with reader:
                            pass
                self.assertFalse(json.loads((reader.output / "extraction.json").read_bytes())["ok"])

    def test_hybrid_gpt_rejected(self):
        raw = bytearray(self.raws["gpt"])
        entry = bytearray(16)
        entry[4] = 0x83
        disk.struct.pack_into("<II", entry, 8, 20480, 32768)
        raw[462:478] = entry
        with self.assertRaises(ValueError):
            with self.reader(raw=bytes(raw)):
                pass

    def test_source_growth_stops_at_original_size(self):
        reader = self.reader()
        original = reader.check
        appended = False
        def check():
            nonlocal appended
            if not appended:
                with reader.source.open("ab") as out:
                    out.write(bytes(image.CHUNK))
                appended = True
            return original()
        with mock.patch.object(reader, "check", check):
            with self.assertRaisesRegex(ValueError, "增長"):
                with reader:
                    pass

    def test_bad_source_digest(self):
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            with self.reader(expected="0" * 64):
                pass
        self.assertFalse((self.root / "evidence/partition.tmp").exists())

    def test_bad_gpt_crc(self):
        raw = bytearray(self.raws["gpt"])
        raw[528] ^= 1
        with self.assertRaises(ValueError):
            with self.reader(raw=bytes(raw)):
                pass

    def test_backup_gpt_damage(self):
        raw = bytearray(self.raws["gpt"])
        raw[-512] ^= 1
        with self.assertRaises(ValueError):
            with self.reader(raw=bytes(raw)):
                pass

    def test_source_is_not_block_device(self):
        with self.assertRaises(ValueError):
            with disk.DiskReader("/dev/null", "0" * 64, self.root / "bad"):
                pass


if __name__ == "__main__":
    unittest.main()

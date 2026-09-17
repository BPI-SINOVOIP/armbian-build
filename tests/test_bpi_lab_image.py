"""共用映像唯讀擷取及拒絕回歸，不接觸實體媒體。"""

import hashlib
import json
import lzma
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import bpi_lab_image as image


def sha(blob):
    return hashlib.sha256(blob).hexdigest()


def header(size):
    data = bytearray(512)
    data[510:] = b"\x55\xaa"
    struct.pack_into("<I", data, 440, 0x12345678)
    data[450] = 0x83
    struct.pack_into("<II", data, 454, 2048, size // 512)
    return data


class LayoutTests(unittest.TestCase):
    def test_single_mbr(self):
        result = image.mbr_partition(header(8 * 1024**2), 16 * 1024**2)
        self.assertEqual(result["partuuid"], "12345678-01")
        self.assertEqual(result["sectors"], 16384)

    def test_reject_layouts(self):
        for offset, value in ((450, 0xee), (446, 1), (450, 5), (510, 0)):
            with self.subTest(offset=offset, value=value):
                blob = header(8 * 1024**2)
                blob[offset] = value
                with self.assertRaises(ValueError):
                    image.mbr_partition(blob, 16 * 1024**2)

    def test_reject_multiple(self):
        blob = header(8 * 1024**2)
        blob[462:478] = blob[446:462]
        with self.assertRaises(ValueError):
            image.mbr_partition(blob, 32 * 1024**2)

    def test_bounds(self):
        with self.assertRaises(ValueError):
            image.mbr_partition(header(16 * 1024**2), 8 * 1024**2)

    def test_paths(self):
        for path in ("boot/Image", "/boot/../etc/passwd", "/boot/x;quit", "/boot/x\ny", "/dev//foo"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                image.image_path(path)

    def test_query_output_limit(self):
        with self.assertRaisesRegex(ValueError, "超界"):
            image.bounded_run([sys.executable, "-c", "print('a'*10000)"], timeout=5, stdout_limit=100)

    def test_query_timeout(self):
        with self.assertRaisesRegex(ValueError, "逾時"):
            image.bounded_run([sys.executable, "-c", "import time; time.sleep(30)"],
                              timeout=0.1, stdout_limit=100)

    def test_no_shell(self):
        code, out, _ = image.bounded_run([sys.executable, "-c", "print('資料')"], timeout=5, stdout_limit=100)
        self.assertEqual(code, 0)
        self.assertEqual(out, "資料\n".encode())


@unittest.skipUnless(Path("/usr/sbin/mke2fs").is_file() and Path("/usr/sbin/debugfs").is_file(),
                     "需要本機 e2fsprogs")
class ImageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tempfile.TemporaryDirectory()
        base = Path(cls.root.name)
        files = base / "files"
        (files / "boot/real").mkdir(parents=True)
        (files / "etc").mkdir()
        (files / "etc/sub").mkdir()
        (files / "boot/real/Image").write_bytes(b"kernel-data")
        (files / "boot/empty").write_bytes(b"")
        (files / "boot/.next").write_bytes(b"")
        (files / "etc/identity").write_bytes(b"identity")
        (files / "boot/identity").write_bytes(b"wrong-identity")
        (files / "boot/shortcut").symlink_to("../etc/sub", target_is_directory=True)
        (files / "boot/nested-parent").symlink_to("shortcut/../identity")
        (files / "boot/missing-parent").symlink_to("absent/../identity")
        (files / "boot/file-parent").symlink_to("identity/../identity")
        (files / "boot/Image").symlink_to("real/Image")
        (files / "boot/dtb").symlink_to("real", target_is_directory=True)
        (files / "boot/abs").symlink_to("/etc/identity")
        (files / "boot/parent").symlink_to("../etc/identity")
        (files / "boot/outside").symlink_to("../../etc/passwd")
        (files / "boot/cycle").symlink_to("cycle")
        (files / "boot/commands").symlink_to("x;quit")
        (files / "boot/missing").symlink_to("no-such-file")
        partition = base / "partition"
        with partition.open("wb") as stream:
            stream.truncate(16 * 1024**2)
        subprocess.run(["/usr/sbin/mke2fs", "-q", "-F", "-t", "ext4", "-d", str(files), str(partition)],
                       check=True, capture_output=True)
        cls.raw = bytes(header(partition.stat().st_size)) + bytes(1024**2 - 512) + partition.read_bytes()
        cls.xz = lzma.compress(cls.raw, format=lzma.FORMAT_XZ)

    @classmethod
    def tearDownClass(cls):
        cls.root.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)

    def reader(self, blob=None, **kwargs):
        blob = self.xz if blob is None else blob
        source = self.base / "source.img.xz"
        source.write_bytes(blob)
        return image.ImageReader(source, kwargs.pop("sha256", sha(blob)), self.base / "result",
                                 max_raw_bytes=32 * 1024**2, **kwargs)

    def test_xz_and_symlinks(self):
        with self.reader() as reader:
            for path, expected in (("/boot/Image", b"kernel-data"), ("/boot/dtb/Image", b"kernel-data"),
                                   ("/boot/abs", b"identity"), ("/boot/parent", b"identity")):
                self.assertEqual(reader.read_file(path), expected)
            self.assertEqual(reader.read_file("/boot/Image"), b"kernel-data")
            self.assertEqual(reader.read_file("/boot/empty"), b"")
            self.assertEqual(reader.read_file("/boot/empty"), b"")
            self.assertEqual(reader.read_file("/boot/.next"), b"")
            self.assertEqual(reader.report["raw"]["sha256"], sha(self.raw))
        report = json.loads((self.base / "result/extraction.json").read_bytes())
        self.assertTrue(report["ok"])
        self.assertFalse(report["hardware_validated"])
        self.assertFalse((self.base / "result/partition.tmp").exists())

    def test_raw_image(self):
        with self.reader(self.raw) as reader:
            self.assertEqual(reader.read_file("/etc/identity"), b"identity")
            self.assertEqual(reader.report["source_kind"], "raw")

    def test_missing(self):
        with self.reader() as reader:
            for path in ("/no-file", "/boot/missing"):
                with self.assertRaises(FileNotFoundError):
                    reader.read_file(path)

    def test_reject_link_escape(self):
        with self.reader() as reader:
            with self.assertRaisesRegex(ValueError, "超出根目錄"):
                reader.read_file("/boot/outside")

    def test_parent_after_directory_symlink(self):
        with self.reader() as reader:
            self.assertEqual(reader.read_file("/boot/nested-parent"), b"identity")

    def test_parent_after_missing_directory(self):
        with self.reader() as reader:
            with self.assertRaises(FileNotFoundError):
                reader.read_file("/boot/missing-parent")

    def test_parent_after_regular_file(self):
        with self.reader() as reader:
            with self.assertRaisesRegex(ValueError, "中間不是目錄"):
                reader.read_file("/boot/file-parent")

    def test_reject_link_cycle(self):
        with self.reader() as reader:
            with self.assertRaisesRegex(ValueError, "循環"):
                reader.read_file("/boot/cycle")

    def test_reject_link_commands(self):
        with self.reader() as reader:
            with self.assertRaises(ValueError):
                reader.read_file("/boot/commands")

    def test_directory_not_file(self):
        with self.reader() as reader:
            with self.assertRaisesRegex(ValueError, "一般檔案"):
                reader.read_file("/boot/real")

    def test_cache_mutation(self):
        with self.reader() as reader:
            reader.read_file("/boot/Image")
            (reader.output / "file-0000.bin").write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "被改變"):
                reader.read_file("/boot/Image")

    def test_partition_mutation(self):
        with self.reader() as reader:
            with reader.partition.open("r+b") as stream:
                stream.write(b"x")
            with self.assertRaisesRegex(ValueError, "變動"):
                reader.read_file("/boot/Image")

    def test_wrong_hash_before_decode(self):
        with mock.patch.object(image.ImageReader, "_xz") as decode:
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                with self.reader(sha256="0" * 64):
                    pass
            decode.assert_not_called()
        self.assertFalse(json.loads((self.base / "result/extraction.json").read_bytes())["ok"])

    def test_truncated_xz(self):
        with self.assertRaisesRegex(ValueError, "截斷"):
            with self.reader(self.xz[:-12]):
                pass
        self.assertFalse((self.base / "result/partition.tmp").exists())

    def test_trailing_data(self):
        with self.assertRaisesRegex(ValueError, "附加資料"):
            with self.reader(self.xz + b"bad"):
                pass

    def test_concatenated_xz(self):
        with self.assertRaisesRegex(ValueError, "多串流"):
            with self.reader(self.xz + self.xz):
                pass

    def test_not_ext(self):
        raw = bytearray(self.raw)
        raw[1024**2 + 1080:1024**2 + 1082] = b"xx"
        with self.assertRaisesRegex(ValueError, "ext"):
            with self.reader(bytes(raw)):
                pass

    def test_short_partition(self):
        with self.assertRaisesRegex(ValueError, "截斷"):
            with self.reader(self.raw[:-512]):
                pass

    def test_existing_output_untouched(self):
        (self.base / "result").mkdir()
        marker = self.base / "result/partition.tmp"
        marker.write_bytes(b"keep")
        with self.assertRaises(FileExistsError):
            with self.reader():
                pass
        self.assertEqual(marker.read_bytes(), b"keep")

    def test_source_symlink_rejected(self):
        reader = self.reader()
        reader.source.rename(self.base / "real.img.xz")
        reader.source.symlink_to("real.img.xz")
        with self.assertRaises(ValueError):
            with reader:
                pass

    def test_source_unchanged(self):
        reader = self.reader()
        before = reader.source.stat()
        with reader:
            reader.read_file("/boot/Image")
        after = reader.source.stat()
        self.assertEqual(before.st_mtime_ns, after.st_mtime_ns)
        self.assertEqual(sha(reader.source.read_bytes()), sha(self.xz))

    def test_output_directory_replaced(self):
        with self.assertRaisesRegex(ValueError, "目錄路徑已變動"):
            with self.reader() as reader:
                original = self.base / "original"
                reader.output.rename(original)
                reader.output.mkdir()
                replacement = reader.output / "partition.tmp"
                replacement.write_bytes(b"keep")
                self.assertEqual(reader.read_file("/boot/Image"), b"kernel-data")
        self.assertEqual(replacement.read_bytes(), b"keep")
        self.assertFalse((original / "partition.tmp").exists())
        self.assertFalse((self.base / "result/extraction.json").exists())
        self.assertFalse(json.loads((original / "extraction.json").read_bytes())["ok"])

    def test_partition_name_replaced(self):
        with self.assertRaisesRegex(ValueError, "拒絕刪除"):
            with self.reader() as reader:
                reader.partition.rename(reader.output / "old-partition.tmp")
                reader.partition.write_bytes(b"keep")
        self.assertEqual((self.base / "result/partition.tmp").read_bytes(), b"keep")
        self.assertTrue((self.base / "result/old-partition.tmp").is_file())
        report = json.loads((self.base / "result/extraction.json").read_bytes())
        self.assertFalse(report["temporary_partition_removed"])
        self.assertFalse(report["ok"])


if __name__ == "__main__":
    unittest.main()

"""固定 R2 衍生實驗的位元組界線回歸，不使用真映像或硬體。"""

import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import bpi_lab_r2_repack as repack


class RepackTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_replacement_preserves_length_and_other_assignments(self):
        new = repack.replacement(repack.OLD_ENV)
        self.assertEqual(len(new), len(repack.OLD_ENV))
        self.assertIn(b"fdtfile=mt7623n-bananapi-bpi-r2.dtb\n", new)
        original_other = [line for line in repack.OLD_ENV.splitlines() if not line.startswith(b"fdtfile=")]
        new_other = [line for line in new.splitlines() if line and not line.startswith((b"fdtfile=", b"#"))]
        self.assertEqual(original_other, new_other)

    def test_unknown_environment_is_not_modified(self):
        for value in (b"", repack.OLD_ENV + b"x=1\n", repack.OLD_ENV.replace(b"verbosity=1", b"verbosity=8")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                repack.replacement(value)

    def test_fixed_inode_permissions_flags_links_and_size(self):
        description = b"Inode: 29293   Type: regular    Mode:  0600   Flags: 0x80000\nSize: 141\nLinks: 1\n"
        repack.validate_environment_inode(description)
        for old, new in ((b"0600", b"0644"), (b"0x80000", b"0x180000"),
                         (b"Links: 1", b"Links: 2"), (b"Size: 141", b"Size: 140")):
            with self.subTest(old=old), self.assertRaises(ValueError):
                repack.validate_environment_inode(description.replace(old, new))

    def test_copy_changes_only_range_even_across_chunks(self):
        before = b"12345" + repack.OLD_ENV + b"abcdefghij"
        source = self.root / "source.img"
        source.write_bytes(before)
        new = repack.replacement(repack.OLD_ENV)
        for chunk in (7, 256, 1048576):
            with self.subTest(chunk=chunk), mock.patch.object(repack.image, "CHUNK", chunk):
                output = self.root / f"copy-{chunk}.img"
                with source.open("rb") as inp, output.open("xb") as out:
                    result = repack.copy_patched(inp.fileno(), out, len(before), 5, repack.OLD_ENV, new, lambda: None)
                wanted = before[:5] + new + before[5 + len(new):]
                self.assertEqual(output.read_bytes(), wanted)
                self.assertEqual(result["sha256"], hashlib.sha256(wanted).hexdigest())
                self.assertEqual(result["original_sha256"], hashlib.sha256(before).hexdigest())
                self.assertEqual(source.read_bytes(), before)

    def test_invalid_ranges_and_wrong_mapping_refused(self):
        source = self.root / "source.img"
        source.write_bytes(b"0123456789")
        cases = [(10, -1, b"1", b"2"), (10, 9, b"99", b"22"),
                 (10, 0, b"x", b"y"), (10, 0, b"0", b"22"), (True, 0, b"0", b"1")]
        for number, args in enumerate(cases):
            with source.open("rb") as inp, (self.root / f"bad-{number}").open("xb") as out:
                with self.subTest(args=args), self.assertRaises(ValueError):
                    repack.copy_patched(inp.fileno(), out, *args, lambda: None)

    def test_truncated_copy_is_not_success(self):
        source = self.root / "source.img"
        source.write_bytes(b"0123")
        with source.open("rb") as inp, (self.root / "copy.img").open("xb") as out:
            with self.assertRaisesRegex(ValueError, "截斷"):
                repack.copy_patched(inp.fileno(), out, 8, 0, b"0", b"1", lambda: None)

    def test_deadline_and_short_write_are_not_success(self):
        source = self.root / "source.img"
        source.write_bytes(b"0123")
        with source.open("rb") as inp:
            with self.assertRaisesRegex(ValueError, "期限"):
                repack.copy_patched(inp.fileno(), None, 4, 0, b"0", b"1",
                                    mock.Mock(side_effect=ValueError("期限")))
            with self.assertRaisesRegex(ValueError, "寫入截斷"):
                repack.copy_patched(inp.fileno(), mock.Mock(write=lambda _: 0), 4, 0, b"0", b"1", lambda: None)

    def test_unknown_source_is_not_repacked_and_original_unchanged(self):
        source = self.root / "unknown.img"
        source.write_bytes(b"not-the-fixed-image")
        with self.assertRaises(ValueError):
            repack.create_candidate(source, self.root / "candidate")
        self.assertEqual(source.read_bytes(), b"not-the-fixed-image")
        self.assertFalse((self.root / "candidate" / repack.NAME).exists())

    def test_existing_output_is_never_reused(self):
        output = self.root / "candidate"
        output.mkdir()
        sentinel = output / "keep"
        sentinel.write_bytes(b"original")
        with self.assertRaises(FileExistsError):
            repack.create_candidate(self.root / "absent", output)
        self.assertEqual(sentinel.read_bytes(), b"original")


if __name__ == "__main__":
    unittest.main()

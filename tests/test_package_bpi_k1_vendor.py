#!/usr/bin/env python3
"""驗證官方封裝的媒體邊界、來源完整性與離線交付檢查。"""

import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import uuid
import zipfile
import zlib


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "package_bpi_k1_vendor", ROOT / "tools/package_bpi_k1_vendor.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def sha(data):
    return hashlib.sha256(data).hexdigest()


class VendorPackageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)

    def payload(self):
        """建立一般檔案載荷，僅供磁碟配置測試。"""
        root = self.base / "payload"
        (root / "factory").mkdir(parents=True)
        for name in ("FSBL.bin",):
            (root / "factory" / name).write_bytes(bytes(range(256)) * 4)
        for media, flash_type, offset in (("sd", b"SDC\0", 131072),
                                          ("emmc", b"eMMC", 512)):
            header = bytearray(80)
            struct.pack_into("<II", header, 0, 0xB00714F0, 0x10001)
            header[8:12] = flash_type
            struct.pack_into("<III", header, 16, 512, 65536, 0x10000000)
            struct.pack_into("<III", header, 32, offset, 0, 221184)
            struct.pack_into("<I", header, 64, zlib.crc32(header[:64]))
            (root / "factory" / f"bootinfo_{media}.bin").write_bytes(header)
        env = b"bootcmd=run autoboot\0\0".ljust(16380, b"\xff")
        (root / "env.bin").write_bytes(struct.pack("<I", zlib.crc32(env)) + env)
        for name in ("fw_dynamic.itb", "u-boot.itb", "bootfs.ext4", "rootfs.ext4"):
            (root / name).write_bytes(name.encode().ljust(4096, b"\0"))
        return root

    def reference(self):
        root = self.payload()
        entries = []
        for path in sorted(root.rglob("*")):
            if path.is_file() and path.name not in ("bootfs.ext4", "rootfs.ext4"):
                data = path.read_bytes()
                entries.append({"path": path.relative_to(root).as_posix(),
                                "size": len(data), "sha256": sha(data),
                                "crc32": f"{zlib.crc32(data):08x}",
                                "header_offset": 0, "compressed_size": len(data)})
        record = {"url": "https://example.invalid/reference.zip",
                  "archive_bytes": 12345678, "files": entries}
        (root / "retrieval.json").write_text(json.dumps(record))
        repo = self.base / "repo"
        lock = repo / "config/spacemit-k1-vendor/sources.lock.json"
        lock.parent.mkdir(parents=True)
        lock.write_text(json.dumps(record))
        return root, repo, record

    def test_layout_uses_official_units_and_media_headers(self):
        official = json.loads((ROOT / "config/spacemit-k1-vendor/partition_universal.json").read_text())
        expected = {p["name"]: p for p in official["partitions"]}
        for emmc in (False, True):
            actual = MODULE.layout(4096, emmc)
            for part in actual["partitions"][1:]:
                reference = expected[part["name"]]
                self.assertEqual(part["offset"], reference["offset"])
                self.assertEqual(part["size"], reference["size"])
                if "compress" in reference:
                    self.assertEqual(part.get("compress"), reference["compress"])
            bootinfo = actual["partitions"][0]
            self.assertEqual(bootinfo["image"],
                             f"factory/bootinfo_{'emmc' if emmc else 'sd'}.bin")
            self.assertEqual(bootinfo["holes"], '{"(80;512)"}')

    def test_layout_rejects_unaligned_or_empty_rootfs(self):
        for size in (0, -4096, 4097):
            with self.subTest(size=size), self.assertRaises(ValueError):
                MODULE.layout(size)

    def test_extlinux_pins_board_dtb_and_valid_uuid(self):
        value = str(uuid.uuid4())
        for board, dtb in MODULE.DTBS.items():
            text = MODULE.extlinux(board, value)
            self.assertIn(f"FDT /dtb/spacemit/{dtb}\n", text)
            self.assertIn(f"root=UUID={value} ", text)
        with self.assertRaises(ValueError):
            MODULE.extlinux("bpi-cm6", value + "\nAPPEND bad")
        with self.assertRaises(ValueError):
            MODULE.extlinux("../board", value)

    def kernel(self, data):
        boot = self.base / "boot"
        boot.mkdir(exist_ok=True)
        (boot / "Image").write_bytes(data)
        return boot

    def raw_kernel(self):
        """僅建立 RISC-V 開機格式必要標頭，避免使用大型核心測試。"""
        data = bytearray(128)
        data[56:60] = b"RSC\x05"
        return bytes(data)

    def test_kernel_raw_preserves_content_and_digest(self):
        data = self.raw_kernel()
        boot = self.kernel(data)
        result = MODULE.normalize_kernel(boot)
        self.assertEqual(result["format"], "raw")
        self.assertFalse(result["source_was_gzip"])
        self.assertEqual(result["image_sha256"], sha(data))
        self.assertEqual((boot / "Image").read_bytes(), data)

    def test_f3_gzip_kernel_becomes_raw_without_changing_package_file(self):
        data = self.raw_kernel()
        compressed = gzip.compress(data, mtime=0)
        boot = self.base / "boot"
        boot.mkdir()
        source = boot / "vmlinuz-6.18.37-current-spacemit"
        source.write_bytes(compressed)
        (boot / "Image").symlink_to(source.name)
        result = MODULE.normalize_kernel(boot)
        self.assertTrue(result["source_was_gzip"])
        self.assertEqual(result["format"], "raw")
        self.assertEqual(result["image_size"], len(data))
        self.assertEqual((boot / "Image").read_bytes(), data)
        self.assertFalse((boot / "Image").is_symlink())
        self.assertEqual(source.read_bytes(), compressed)

    def test_cm6_riscv_fit_is_preserved_for_bootm(self):
        data = b"\xd0\x0d\xfe\xed" + bytes(124)
        boot = self.kernel(data)
        # 以下欄位是 dumpimage 的固定機器介面，必須保留原字串。
        listing = ("FIT description: 核心\n"
                   " Image 0 (kernel-1)\n"
                   "  Type:         Kernel Image\n"
                   "  Compression:  uncompressed\n"
                   "  Data Size:    45753856 Bytes\n"
                   "  Architecture: RISC-V\n"
                   "  Load Address: 0x00200000\n")
        with patch.object(MODULE.subprocess, "check_output", return_value=listing) as inspect:
            result = MODULE.normalize_kernel(boot)
        inspect.assert_called_once_with(["dumpimage", "-l", str(boot / "Image")], text=True)
        self.assertEqual(result["format"], "fit")
        self.assertFalse(result["source_was_gzip"])
        self.assertEqual((boot / "Image").read_bytes(), data)
        self.assertEqual(result["image_sha256"], sha(data))

    def test_fit_rejects_wrong_architecture_or_non_kernel_tree(self):
        data = b"\xd0\x0d\xfe\xed" + bytes(124)
        boot = self.kernel(data)
        listings = [
            "FIT description: 核心\nArchitecture: AArch64\nType:         Kernel Image\n",
            "FIT description: 裝置樹\nArchitecture: RISC-V\nType:         Flat Device Tree\n",
            "Architecture: RISC-V\nType:         Kernel Image\n",
        ]
        for listing in listings:
            with self.subTest(listing=listing), patch.object(MODULE.subprocess, "check_output", return_value=listing):
                with self.assertRaisesRegex(ValueError, "FIT"):
                    MODULE.normalize_kernel(boot)

    def test_fit_inspector_failure_blocks_packaging(self):
        boot = self.kernel(b"\xd0\x0d\xfe\xed" + bytes(124))
        with patch.object(MODULE.subprocess, "check_output", side_effect=subprocess.CalledProcessError(1, "dumpimage")):
            with self.assertRaises(subprocess.CalledProcessError):
                MODULE.normalize_kernel(boot)

    def test_raw_kernel_rejects_truncated_or_wrong_header(self):
        for data in (bytes(63), bytes(128)):
            with self.subTest(size=len(data)):
                boot = self.kernel(data)
                with self.assertRaisesRegex(ValueError, "RISC-V"):
                    MODULE.normalize_kernel(boot)

    def test_kernel_rejects_expansion_beyond_official_load_region(self):
        boot = self.kernel(self.raw_kernel())
        # 稀疏檔可涵蓋邊界，不需分配或壓縮大型測試資料。
        with (boot / "Image").open("r+b") as stream:
            stream.truncate(0x0c200000 - 0x08000000 + 1)
        with self.assertRaisesRegex(ValueError, "記憶體區間"):
            MODULE.normalize_kernel(boot)

    def test_corrupted_gzip_cannot_replace_original_image(self):
        truncated = gzip.compress(self.raw_kernel(), mtime=0)[:-5]
        boot = self.kernel(truncated)
        with self.assertRaises((EOFError, gzip.BadGzipFile)):
            MODULE.normalize_kernel(boot)
        self.assertEqual((boot / "Image").read_bytes(), truncated)

    def test_reference_accepts_locked_payloads(self):
        root, repo, _ = self.reference()
        with patch.object(MODULE, "REPO", repo):
            self.assertIsInstance(MODULE.source_reference(root), dict)

    def test_reference_rejects_changed_binary(self):
        root, repo, _ = self.reference()
        (root / "u-boot.itb").write_bytes(b"\0" * 4096)
        with patch.object(MODULE, "REPO", repo), self.assertRaises((ValueError, RuntimeError)):
            MODULE.source_reference(root)

    def test_reference_cannot_trust_rewritten_retrieval(self):
        root, repo, record = self.reference()
        data = b"\x01" * 4096
        (root / "u-boot.itb").write_bytes(data)
        for entry in record["files"]:
            if entry["path"] == "u-boot.itb":
                entry["sha256"] = sha(data)
                entry["crc32"] = f"{zlib.crc32(data):08x}"
        (root / "retrieval.json").write_text(json.dumps(record))
        with patch.object(MODULE, "REPO", repo), self.assertRaises((ValueError, RuntimeError)):
            MODULE.source_reference(root)

    def test_reference_rejects_changed_local_template(self):
        root, repo, _ = self.reference()
        config = repo / "config/spacemit-k1-vendor"
        template = config / "fastboot.yaml"
        template.write_text("version: 1.0\n")
        lock_path = config / "sources.lock.json"
        lock = json.loads(lock_path.read_text())
        lock["local_templates"] = [{"path": template.name, "sha256": MODULE.digest(template)}]
        lock_path.write_text(json.dumps(lock))
        template.write_text("version: 2.0\n")
        with patch.object(MODULE, "REPO", repo), self.assertRaises(ValueError):
            MODULE.source_reference(root)

    def test_payload_rejects_bootinfo_sector_overwrite_and_oversized_uboot(self):
        root = self.payload()
        path = root / "factory/bootinfo_sd.bin"
        original = path.read_bytes()
        path.write_bytes(original.ljust(512, b"\0"))
        with self.assertRaises(ValueError):
            MODULE.check_payloads(root)
        path.write_bytes(original)
        with (root / "u-boot.itb").open("wb") as stream:
            stream.truncate(2 * MODULE.MIB + 1)
        with self.assertRaises(ValueError):
            MODULE.check_payloads(root)

    @unittest.skipUnless(shutil.which("sgdisk"), "需要本機 sgdisk")
    def test_sd_preserves_protective_mbr_and_valid_gpt(self):
        root = self.payload()
        target = self.base / "candidate.img"
        total = MODULE.make_sd(root, target, {"board": "bpi-cm6", "storage": "sd"})
        with target.open("rb") as stream:
            mbr = stream.read(512)
            self.assertEqual(mbr[:80], (root / "factory/bootinfo_sd.bin").read_bytes())
            self.assertEqual(mbr[450], 0xEE)
            self.assertEqual(mbr[510:512], b"\x55\xaa")
            primary = stream.read(512)
            self.assertEqual(primary[:8], b"EFI PART")
            header_size, header_crc = struct.unpack_from("<II", primary, 12)
            header = bytearray(primary[:header_size])
            header[16:20] = b"\0" * 4
            self.assertEqual(zlib.crc32(header), header_crc)
            backup_lba = struct.unpack_from("<Q", primary, 32)[0]
            self.assertEqual(backup_lba, total // 512 - 1)
            entries_lba, count, entry_size, table_crc = struct.unpack_from("<QIII", primary, 72)
            stream.seek(entries_lba * 512)
            table = stream.read(count * entry_size)
            self.assertEqual(zlib.crc32(table), table_crc)
            for index, (name, offset, maximum, payload_name) in enumerate(MODULE.PARTS):
                entry = table[index * entry_size:(index + 1) * entry_size]
                first, last = struct.unpack_from("<QQ", entry, 32)
                size = maximum or (root / payload_name).stat().st_size
                self.assertEqual((first, last), (offset // 512, (offset + size) // 512 - 1))
                self.assertEqual(entry[56:128].decode("utf-16-le").rstrip("\0"), name)
                stream.seek(offset)
                self.assertEqual(stream.read((root / payload_name).stat().st_size),
                                 (root / payload_name).read_bytes())
            stream.seek(backup_lba * 512)
            backup = stream.read(512)
            self.assertEqual(backup[:8], b"EFI PART")
            length, crc = struct.unpack_from("<II", backup, 12)
            header = bytearray(backup[:length])
            header[16:20] = b"\0" * 4
            self.assertEqual(zlib.crc32(header), crc)
        with target.open("r+b") as stream:
            stream.seek(2 * MODULE.MIB)
            stream.write(b"\xff")
        with self.assertRaises(ValueError):
            MODULE.verify_sd_image(target, root)

    def archive_manifest(self, members, expected=None):
        artifact = self.base / "candidate.zip"
        with zipfile.ZipFile(artifact, "w") as archive:
            for name, data in members:
                archive.writestr(name, data)
        record = {"board": "bpi-cm6", "storage": "emmc",
                  "artifact": {"name": artifact.name, "sha256": MODULE.digest(artifact)},
                  "archive_members": expected or {
                      name: {"size": len(data), "sha256": sha(data)} for name, data in members}}
        path = self.base / "manifest.json"
        path.write_text(json.dumps(record))
        return path

    def test_verify_rejects_internal_change_even_with_matching_zip_hash(self):
        manifest = self.archive_manifest([("env.bin", b"changed")],
                                         {"env.bin": {"size": 7, "sha256": sha(b"correct")}})
        with self.assertRaises(ValueError):
            MODULE.verify(manifest)

    def test_verify_rejects_unsafe_member_paths(self):
        manifest = self.archive_manifest([("../env.bin", b"data")])
        with self.assertRaises(ValueError):
            MODULE.verify(manifest)

    def test_verify_rejects_manifest_artifact_escape(self):
        manifest = self.archive_manifest([("env.bin", b"data")])
        record = json.loads(manifest.read_text())
        record["artifact"]["name"] = "../candidate.zip"
        manifest.write_text(json.dumps(record))
        with self.assertRaises(ValueError):
            MODULE.verify(manifest)

    @unittest.skipUnless(shutil.which("mkfs.ext4"), "需要本機 mkfs.ext4")
    def test_verify_reads_real_ext4_uuids_from_archive(self):
        identities = {part: str(uuid.uuid4()) for part in ("boot", "root")}
        members = []
        for part, value in identities.items():
            path = self.base / (part + "fs.ext4")
            with path.open("wb") as stream:
                stream.truncate(8 * MODULE.MIB)
            subprocess.run(["mkfs.ext4", "-q", "-F", "-U", value, str(path)], check=True)
            members.append((path.name, path.read_bytes()))
        manifest = self.archive_manifest(members)
        record = json.loads(manifest.read_text())
        record.update({part + "_uuid": value for part, value in identities.items()})
        manifest.write_text(json.dumps(record))
        self.assertEqual(MODULE.verify(manifest)["status"], "passed")
        record["root_uuid"] = str(uuid.uuid4())
        manifest.write_text(json.dumps(record))
        with self.assertRaises(ValueError):
            MODULE.verify(manifest)


if __name__ == "__main__":
    unittest.main()

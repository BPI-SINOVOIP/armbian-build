#!/usr/bin/env python3
"""驗證官方封裝的媒體邊界、來源完整性與離線交付檢查。"""

import gzip
import hashlib
import importlib.util
import json
import os
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

    def test_native_target_enforces_board_and_media_before_packaging(self):
        for base, physical in (("bananapicm6", "bpi-cm6"), ("bananapif3", "bpi-f3")):
            for suffix, storage in (("-titan-emmc", "emmc"), ("-vendor-sd", "sd")):
                identity = {"armbian_board": base + suffix}
                MODULE.native_target_contract(identity, physical, storage)
                with self.assertRaisesRegex(ValueError, "媒體不符"):
                    MODULE.native_target_contract(identity, physical, "sd" if storage == "emmc" else "emmc")
                with self.assertRaisesRegex(ValueError, "板型"):
                    MODULE.native_target_contract(identity, "bpi-f3" if physical == "bpi-cm6" else "bpi-cm6", storage)
            for storage in ("sd", "emmc"):
                MODULE.native_target_contract({"armbian_board": base}, physical, storage)
                MODULE.native_target_contract({}, physical, storage)
        with self.assertRaises(ValueError):
            MODULE.native_target_contract({"armbian_board": "bananapicm6-titan-emmc-extra"}, "bpi-cm6", "emmc")

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

    def test_layout_preserves_official_units_and_bootinfo_contract(self):
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
            self.assertEqual(bootinfo["image"], "factory/bootinfo_sd.bin")
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

    def test_cm6_connectivity_keeps_separate_boot_path_and_rejects_f3(self):
        value = str(uuid.uuid4())
        original = MODULE.extlinux("bpi-cm6", value)
        revised = MODULE.extlinux("bpi-cm6", value, cm6_connectivity=True)
        self.assertEqual(revised.replace(MODULE.CM6_CONNECTIVITY_PATH, "/dtb/spacemit/" + MODULE.DTBS["bpi-cm6"]), original)
        with self.assertRaises(ValueError):
            MODULE.extlinux("bpi-f3", value, cm6_connectivity=True)

    def test_dual_camera_explicit_option_preserves_default_and_rejects_f3(self):
        value = str(uuid.uuid4())
        original = MODULE.extlinux("bpi-cm6", value, True)
        revised = MODULE.extlinux("bpi-cm6", value, True, True)
        self.assertEqual(revised.replace(MODULE.CM6_CAMERA_PATH, MODULE.CM6_CONNECTIVITY_PATH), original)
        self.assertNotIn(MODULE.CM6_CAMERA_PATH, original)
        with self.assertRaisesRegex(ValueError, "只允許 BPI-CM6"):
            MODULE.extlinux("bpi-f3", value, cm6_dual_imx415=True)
        args = MODULE.argparse.Namespace(board="bpi-f3", release_id="20260924-rc4", cm6_dual_imx415=True)
        with patch.object(MODULE.os, "execvp") as execute:
            with self.assertRaisesRegex(ValueError, "只允許 BPI-CM6"):
                MODULE.build(args)
            execute.assert_not_called()

    def camera_record(self):
        return {"board": "bpi-cm6",
                "source": {"sha256": MODULE.CM6_CONNECTIVITY_SHA256},
                "candidate": {"name": MODULE.CM6_CAMERA_DTB, "sha256": MODULE.CM6_CAMERA_SHA256},
                "hardware_validation": "仍待實機驗收"}

    def test_camera_build_rejects_missing_or_changed_sdk_before_packaging(self):
        prepared = self.base / "prepared"
        prepared.mkdir()
        args = MODULE.argparse.Namespace(board="bpi-cm6", release_id="20260924-rc4",
                                         cm6_dual_imx415=True, inside=True, prepared=prepared)
        fixed = {"board": "bpi-cm6", "cm6_camera_packages": MODULE.CM6_CAMERA_PACKAGES}
        MODULE.verify_camera_packages(fixed)
        for case in ("missing", "missing_library", "version", "hash"):
            with self.subTest(case=case):
                identity = json.loads(json.dumps(fixed))
                if case == "missing":
                    del identity["cm6_camera_packages"]
                elif case == "missing_library":
                    del identity["cm6_camera_packages"]["k1x-cam-lib"]
                else:
                    field = "version" if case == "version" else "sha256"
                    identity["cm6_camera_packages"]["k1x-cam"][field] = "changed"
                (prepared / "preparation.json").write_text(json.dumps({"status": "complete", "identity": identity}))
                with patch.object(MODULE.os, "geteuid", return_value=0), \
                        patch.object(MODULE, "run") as execute:
                    with self.assertRaisesRegex(ValueError, "相機配套身分"):
                        MODULE.build(args)
                    execute.assert_not_called()
        self.assertEqual(list(prepared.iterdir()), [prepared / "preparation.json"])

    def test_camera_preparation_rejects_unfixed_eth0_before_running_tool(self):
        boot = self.base / "boot"
        source = boot / MODULE.CM6_CONNECTIVITY_PATH.lstrip("/")
        source.parent.mkdir(parents=True)
        source.write_bytes(b"wrong-source")
        with patch.object(MODULE, "run") as execute:
            with self.assertRaisesRegex(ValueError, "固定 eth0"):
                MODULE.prepare_cm6_camera(boot, self.base)
            execute.assert_not_called()

    def test_camera_preparation_checks_actual_candidate_and_manifest_hashes(self):
        boot = self.base / "boot"
        source = boot / MODULE.CM6_CONNECTIVITY_PATH.lstrip("/")
        source.parent.mkdir(parents=True)
        source.write_bytes(b"eth0-source")
        data = b"dual-camera"
        def execute(argv, **kwargs):
            self.assertEqual(Path(argv[1]).name, "bpi_cm6_camera_dtb.py")
            self.assertEqual(argv[argv.index("--source") + 1], source)
            Path(argv[argv.index("--output") + 1]).write_bytes(data)
            Path(argv[argv.index("--manifest") + 1]).write_text(json.dumps(record))
        with patch.object(MODULE, "CM6_CONNECTIVITY_SHA256", sha(source.read_bytes())), \
                patch.object(MODULE, "CM6_CAMERA_SHA256", sha(data)), \
                patch.object(MODULE, "run", side_effect=execute):
            record = self.camera_record()
            self.assertEqual(MODULE.prepare_cm6_camera(boot, self.base), record)
            del record["candidate"]["sha256"]
            with self.assertRaisesRegex(ValueError, "SHA-256 缺失"):
                MODULE.prepare_cm6_camera(boot, self.base)
            record = self.camera_record()
            data = b"changed-candidate"
            with self.assertRaisesRegex(ValueError, "已實測候選"):
                MODULE.prepare_cm6_camera(boot, self.base)
        self.assertEqual(source.read_bytes(), b"eth0-source")

    def test_camera_boot_contract_checks_path_marker_hash_and_both_dtbs(self):
        root_uuid, boot_uuid = str(uuid.uuid4()), str(uuid.uuid4())
        data = {MODULE.CM6_CONNECTIVITY_PATH: b"eth0", MODULE.CM6_CAMERA_PATH: b"camera"}
        marker = {"board": "bpi-cm6", "root_uuid": root_uuid, "boot_uuid": boot_uuid,
                  "boot_dtb_path": MODULE.CM6_CAMERA_PATH, "cm6_camera_dtb_sha256": sha(b"camera"),
                  "cm6_camera_packages": MODULE.CM6_CAMERA_PACKAGES}
        config = MODULE.extlinux("bpi-cm6", root_uuid, True, True)
        def content(argv, **kwargs):
            name = argv[2].removeprefix("cat ")
            if name == "/extlinux/extlinux.conf":
                return config
            if name == "/env_k1-x.txt":
                return "bootcmd=sysboot ${bootfs_devname} ${boot_devnum}:${bootfs_part} any ${pxefile_addr_r} /extlinux/extlinux.conf"
            if name == "/etc/fstab":
                return f"UUID={root_uuid} / ext4 defaults 0 1\nUUID={boot_uuid} /boot ext4 defaults 0 2\n"
            if name == "/etc/bpi-k1-vendor.json":
                return json.dumps(marker)
            return data[name]
        with patch.object(MODULE.subprocess, "check_output", side_effect=content), \
                patch.object(MODULE, "CM6_CONNECTIVITY_SHA256", sha(b"eth0")), \
                patch.object(MODULE, "CM6_CAMERA_SHA256", sha(b"camera")):
            result = MODULE.verify_boot_contract(self.base, "bpi-cm6", root_uuid, boot_uuid, True, True)
            self.assertEqual(result, {"status": "passed", "scope": "bootfs-extlinux-env-rootfs-fstab-marker"})
            config = MODULE.extlinux("bpi-cm6", root_uuid, True)
            with self.assertRaisesRegex(ValueError, "開機選項"):
                MODULE.verify_boot_contract(self.base, "bpi-cm6", root_uuid, boot_uuid, True, True)
            config = MODULE.extlinux("bpi-cm6", root_uuid, True, True)
            marker["boot_dtb_path"] = MODULE.CM6_CONNECTIVITY_PATH
            with self.assertRaisesRegex(ValueError, "核心更新入口"):
                MODULE.verify_boot_contract(self.base, "bpi-cm6", root_uuid, boot_uuid, True, True)
            marker["boot_dtb_path"] = MODULE.CM6_CAMERA_PATH
            del marker["cm6_camera_packages"]
            with self.assertRaisesRegex(ValueError, "相機配套身分"):
                MODULE.verify_boot_contract(self.base, "bpi-cm6", root_uuid, boot_uuid, True, True)
            marker["cm6_camera_packages"] = MODULE.CM6_CAMERA_PACKAGES
            del marker["cm6_camera_dtb_sha256"]
            with self.assertRaisesRegex(ValueError, "身分標記"):
                MODULE.verify_boot_contract(self.base, "bpi-cm6", root_uuid, boot_uuid, True, True)
            marker["cm6_camera_dtb_sha256"] = sha(b"camera")
            for name in data:
                with self.subTest(path=name):
                    original = data[name]
                    data[name] = b"changed"
                    with self.assertRaisesRegex(ValueError, "實機驗證內容"):
                        MODULE.verify_boot_contract(self.base, "bpi-cm6", root_uuid, boot_uuid, True, True)
                    data[name] = original

    def test_connectivity_boot_verifier_rejects_wrong_update_path_or_dtb(self):
        root_uuid, boot_uuid = str(uuid.uuid4()), str(uuid.uuid4())
        payload = self.base / "payload"
        data = b"CM6-DTB"
        marker = {"board": "bpi-cm6", "root_uuid": root_uuid, "boot_uuid": boot_uuid,
                  "dtb": MODULE.DTBS["bpi-cm6"], "boot_dtb_path": MODULE.CM6_CONNECTIVITY_PATH}
        def content(argv, **kwargs):
            name = argv[2]
            if name == "cat /extlinux/extlinux.conf":
                return MODULE.extlinux("bpi-cm6", root_uuid, True)
            if name == "cat /env_k1-x.txt":
                return "bootcmd=sysboot ${bootfs_devname} ${boot_devnum}:${bootfs_part} any ${pxefile_addr_r} /extlinux/extlinux.conf"
            if name == "cat /etc/fstab":
                return f"UUID={root_uuid} / ext4 defaults 0 1\nUUID={boot_uuid} /boot ext4 defaults 0 2\n"
            if name == "cat /etc/bpi-k1-vendor.json":
                return json.dumps(marker)
            return data
        with patch.object(MODULE.subprocess, "check_output", side_effect=content), \
                patch.object(MODULE, "CM6_CONNECTIVITY_SHA256", sha(data)):
            self.assertEqual(MODULE.verify_boot_contract(payload, "bpi-cm6", root_uuid, boot_uuid, True)["status"], "passed")
            marker["boot_dtb_path"] = "/dtb/spacemit/" + MODULE.DTBS["bpi-cm6"]
            with self.assertRaisesRegex(ValueError, "核心更新入口"):
                MODULE.verify_boot_contract(payload, "bpi-cm6", root_uuid, boot_uuid, True)
            marker["boot_dtb_path"] = MODULE.CM6_CONNECTIVITY_PATH
            data = b"changed"
            with self.assertRaisesRegex(ValueError, "實機驗證內容"):
                MODULE.verify_boot_contract(payload, "bpi-cm6", root_uuid, boot_uuid, True)

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

    def titan_members(self):
        payload = self.payload()
        members = {p.relative_to(payload).as_posix(): p.read_bytes()
                   for p in payload.rglob("*") if p.is_file()}
        members["fastboot.yaml"] = MODULE.fastboot_config().encode()
        members["partition_universal.json"] = json.dumps(MODULE.layout(4096, True)).encode()
        return members

    def test_explicit_candidate_version_rejects_reused_or_unsafe_names(self):
        self.assertEqual(MODULE.release_id("20260918-rc2"), "20260918-rc2")
        for value in ("20260916", "../20260918-rc2", "20260918-rc0", "20260918-rc2.zip"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                MODULE.release_id(value)

    def test_titan_rejects_hash_consistent_archive_without_required_payloads(self):
        manifest = self.archive_manifest([("env.bin", b"data")])
        with self.assertRaisesRegex(ValueError, "缺少必要元件"):
            MODULE.verify(manifest)

    def test_titan_rejects_old_literal_selector_even_when_archive_hash_matches(self):
        members = self.titan_members()
        members["fastboot.yaml"] = members["fastboot.yaml"].replace(b"partition_{size1}.json", b"partition_universal.json")
        manifest = self.archive_manifest(list(members.items()))
        with self.assertRaisesRegex(ValueError, "變數匹配契約"):
            MODULE.verify(manifest)

    def test_titan_rejects_missing_media_query(self):
        members = self.titan_members()
        members["fastboot.yaml"] = members["fastboot.yaml"].replace(b"args: 'blk-size'", b"args: 'mtd-size'")
        manifest = self.archive_manifest(list(members.items()))
        with self.assertRaisesRegex(ValueError, "變數匹配契約"):
            MODULE.verify(manifest)

    def test_titan_rejects_extra_mtd_table(self):
        members = self.titan_members()
        members["partition_2M.json"] = b"{}"
        manifest = self.archive_manifest(list(members.items()))
        with self.assertRaisesRegex(ValueError, "額外分區表"):
            MODULE.verify(manifest)

    def test_titan_rejects_unreviewed_media_header_change(self):
        members = self.titan_members()
        members["partition_universal.json"] = members["partition_universal.json"].replace(b"bootinfo_sd.bin", b"bootinfo_emmc.bin")
        manifest = self.archive_manifest(list(members.items()))
        with self.assertRaisesRegex(ValueError, "官方通用表"):
            MODULE.verify(manifest)

    def test_integrity_check_preserves_recall_and_real_failure_status(self):
        manifest = self.archive_manifest(list(self.titan_members().items()))
        record = json.loads(manifest.read_text())
        (self.base / "release-status.json").write_text(json.dumps({
            "historical_artifact_sha256": record["artifact"]["sha256"],
            "release_status": "recalled_unusable", "hardware_validation": "reported_failed"}))
        result = MODULE.verify(manifest)
        self.assertEqual(result["release_status"], "recalled_unusable")
        self.assertEqual(result["hardware_validation"], "reported_failed")

    def test_camera_archive_requires_consistent_identity_and_candidate_hash(self):
        manifest = self.archive_manifest(list(self.titan_members().items()))
        record = json.loads(manifest.read_text())
        record.update(identity={"board": "bpi-cm6", "cm6_camera_dtb_sha256": MODULE.CM6_CAMERA_SHA256,
                                "cm6_camera_packages": MODULE.CM6_CAMERA_PACKAGES},
                      cm6_camera_dtb=self.camera_record())
        manifest.write_text(json.dumps(record))
        result = MODULE.verify(manifest)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["hardware_validation"], "pending")
        self.assertEqual(result["titan_control_contract"]["hardware_validation"], "pending")
        for case in ("board", "identity", "candidate", "record", "null_hash"):
            with self.subTest(case=case):
                changed = json.loads(json.dumps(record))
                if case == "board":
                    changed["board"] = "bpi-f3"
                elif case == "identity":
                    changed["identity"].clear()
                elif case == "candidate":
                    del changed["cm6_camera_dtb"]["candidate"]["sha256"]
                else:
                    del changed["cm6_camera_dtb"]
                    if case == "null_hash":
                        changed["identity"]["cm6_camera_dtb_sha256"] = None
                manifest.write_text(json.dumps(changed))
                with self.assertRaisesRegex(ValueError, "雙相機"):
                    MODULE.verify(manifest)

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
        members = self.titan_members()
        for part, value in identities.items():
            path = self.base / (part + "fs.ext4")
            with path.open("wb") as stream:
                stream.truncate(8 * MODULE.MIB)
            subprocess.run(["mkfs.ext4", "-q", "-F", "-U", value, str(path)], check=True)
            members[path.name] = path.read_bytes()
        manifest = self.archive_manifest(list(members.items()))
        record = json.loads(manifest.read_text())
        record.update({part + "_uuid": value for part, value in identities.items()})
        manifest.write_text(json.dumps(record))
        self.assertEqual(MODULE.verify(manifest)["status"], "passed")
        record["root_uuid"] = str(uuid.uuid4())
        manifest.write_text(json.dumps(record))
        with self.assertRaises(ValueError):
            MODULE.verify(manifest)


class CameraPackageContractTests(unittest.TestCase):
    setUp = VendorPackageTests.setUp

    def identity(self):
        pinned = MODULE.CM6_CAMERA_DERIVED
        lock = json.loads((ROOT / "config/spacemit-k1-camera/source-lock.json").read_text())
        provenance = {key: pinned[key] for key in ("source_lock_sha256", "builder_sha256", "library_sha256")}
        provenance.update(source_sha256=lock["source"]["sha256"], patches=lock["patches"],
                          streamoff_fixed=False, stop_order_patch_applied=True, hardware_validation="pending", scope=lock["scope"])
        records = {"k1x-cam": {**{key: pinned[key] for key in ("version", "size", "sha256")},
                   "filename": "k1x-cam_0.2.34+cm6.2_riscv64.deb", "provenance": provenance},
                   "k1x-cam-lib": {**MODULE.CM6_CAMERA_PACKAGES["k1x-cam-lib"],
                   "filename": "k1x-cam-lib_0.1.8_riscv64.deb", "provenance": {
                   "signature_verified": False, "scope": "保持固定官方閉源 SDK，未變更內容。"}}}
        return {"schema_version": 2, "source_kind": "armbian-native-rootfs", "board": "bpi-cm6",
                "camera_profile": "dual-imx415", "cm6_camera_packages": records}

    def test_official_legacy_and_complete_records_remain_accepted(self):
        for records in (MODULE.CM6_CAMERA_PACKAGES, MODULE.camera_package_records(None)):
            self.assertIsNone(MODULE.verify_camera_packages({"board": "bpi-cm6", "cm6_camera_packages": records}))
            changed = json.loads(json.dumps(records)); changed["k1x-cam"]["sha256"] = "0" * 64
            with self.assertRaisesRegex(ValueError, "相機配套身分"):
                MODULE.verify_camera_packages({"board": "bpi-cm6", "cm6_camera_packages": changed})

    def test_derived_contract_requires_exact_package_and_provenance(self):
        identity = self.identity()
        self.assertEqual(MODULE.verify_camera_packages(identity)["packages"], identity["cm6_camera_packages"])
        mutations = [("version", "0.2.34+cm6.1"), ("size", 1), ("sha256", "0" * 64),
                     ("filename", "other.deb"), ("provenance", {})]
        for key, value in mutations:
            changed = self.identity(); changed["cm6_camera_packages"]["k1x-cam"][key] = value
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "相機配套身分"):
                MODULE.verify_camera_packages(changed)
        for key in ("source_lock_sha256", "builder_sha256", "source_sha256", "library_sha256"):
            changed = self.identity(); changed["cm6_camera_packages"]["k1x-cam"]["provenance"][key] = "0" * 64
            with self.subTest(provenance=key), self.assertRaisesRegex(ValueError, "相機配套身分"):
                MODULE.verify_camera_packages(changed)
        for mutate in (lambda p: p["patches"].reverse(), lambda p: p.update(hardware_validation="passed"),
                       lambda p: p.update(streamoff_fixed=True), lambda p: p.update(stop_order_patch_applied=False)):
            changed = self.identity(); mutate(changed["cm6_camera_packages"]["k1x-cam"]["provenance"])
            with self.assertRaisesRegex(ValueError, "相機配套身分"):
                MODULE.verify_camera_packages(changed)
        changed = self.identity(); changed["cm6_camera_packages"]["k1x-cam-lib"]["sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "相機配套身分"):
            MODULE.verify_camera_packages(changed)

    def test_derived_rejects_other_board_profile_and_legacy_source(self):
        for key, value in (("board", "bpi-f3"), ("camera_profile", "none"),
                           ("schema_version", 1), ("source_kind", "repacked")):
            changed = self.identity(); changed[key] = value
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "相機配套身分"):
                MODULE.verify_camera_packages(changed)

    def test_derived_rejects_changed_local_source_inputs(self):
        config = self.base / "config/spacemit-k1-camera"
        shutil.copytree(ROOT / "config/spacemit-k1-camera", config)
        builder = self.base / "tools/build_bpi_cm6_camera.py"
        builder.parent.mkdir(); shutil.copyfile(ROOT / "tools/build_bpi_cm6_camera.py", builder)
        identity = self.identity()
        paths = [config / "source-lock.json", builder, config / "0001-dual-sensor-work-mode.patch",
                 config / "0002-dual-vi-stop-before-isp.patch", config / "cm6-dual-imx415-mode2.json"]
        with patch.object(MODULE, "REPO", self.base):
            MODULE.verify_camera_packages(identity)
            for path in paths:
                original = path.read_bytes(); path.write_bytes(original + b"\n")
                with self.subTest(path=path.name), self.assertRaisesRegex(ValueError, "相機配套身分"):
                    MODULE.verify_camera_packages(identity)
                path.write_bytes(original)

    def test_derived_rootfs_rejects_wrong_payload_provenance_or_installation(self):
        identity = self.identity(); contract = MODULE.verify_camera_packages(identity)
        data = {"usr/lib/libsdkcam.so": b"elf-fixture", contract["config"]["installed_path"]: b"config-fixture"}
        contract["provenance"]["library_sha256"] = sha(data["usr/lib/libsdkcam.so"])
        contract["config"]["sha256"] = sha(data[contract["config"]["installed_path"]])
        data["usr/share/doc/k1x-cam/cm6-source.json"] = json.dumps(contract["provenance"]).encode()
        data["var/lib/dpkg/status"] = b"Package: k1x-cam\nVersion: 0.2.34+cm6.2\nArchitecture: riscv64\nStatus: install ok installed\n\nPackage: k1x-cam-lib\nVersion: 0.1.8\nArchitecture: riscv64\nStatus: install ok installed\n"
        def content(argv, **kwargs):
            self.assertEqual(argv[:2], ["debugfs", "-R"])
            self.assertTrue(argv[2].startswith("cat /"))
            return data[argv[2][5:]]
        with patch.object(MODULE, "verify_camera_packages", return_value=contract), \
                patch.object(MODULE.subprocess, "check_output", side_effect=content):
            MODULE.verify_camera_rootfs(identity, self.base / "rootfs.ext4")
            for name in data:
                original = data[name]
                data[name] = b"{}" if name.endswith("json") else b"changed"
                with self.subTest(name=name), self.assertRaises(ValueError):
                    MODULE.verify_camera_rootfs(identity, self.base / "rootfs.ext4")
                data[name] = original
            original = data["var/lib/dpkg/status"]
            for wrong in (b"0.2.34+cm6.1", b"arm64", b"deinstall ok config-files"):
                key = b"0.2.34+cm6.2" if b"cm6" in wrong else b"riscv64" if wrong == b"arm64" else b"install ok installed"
                data["var/lib/dpkg/status"] = original.replace(key, wrong)
                with self.assertRaisesRegex(ValueError, "版本、架構或安裝狀態"):
                    MODULE.verify_camera_rootfs(identity, self.base / "rootfs.ext4")


@unittest.skipUnless(shutil.which("dtc"), "需要本機 dtc")
class NativeVendorPackageTests(unittest.TestCase):
    setUp = VendorPackageTests.setUp
    payload = VendorPackageTests.payload
    titan_members = VendorPackageTests.titan_members
    archive_manifest = VendorPackageTests.archive_manifest

    def native_prepared(self, dual=False):
        spec = importlib.util.spec_from_file_location("native_dtb_test_fixture", ROOT / "tests/test_bpi_cm6_native_dtb.py")
        fixture = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fixture)
        prepared = self.base / "prepared"
        boot = prepared / "boot-tree"
        path = boot / "dtb/spacemit/k1-x_bpi_cm6.dtb"
        path.parent.mkdir(parents=True)
        dts = prepared / "fixture.dts"
        dts.write_text(fixture.minimal_dts(dual=dual, handle_base=0x700))
        fixture.compile_dts(dts, path)
        camera = "dual-imx415" if dual else "none"
        identity = {"schema_version": 2, "source_kind": "armbian-native-rootfs", "board": "bpi-cm6",
                    "camera_profile": camera,
                    "native_dtb": {"path": "/dtb/spacemit/k1-x_bpi_cm6.dtb", "sha256": MODULE.digest(path),
                                   "validation": fixture.native.validate(path, camera)}}
        if dual:
            identity["cm6_camera_packages"] = MODULE.CM6_CAMERA_PACKAGES
        prep = {"schema_version": 2, "status": "complete", "identity": identity, "boot_tree": MODULE.boot_inventory(boot)}
        return prepared, prep

    def test_native_camera_is_inferred_and_legacy_sha_is_not_used(self):
        prepared, prep = self.native_prepared(dual=True)
        self.assertNotEqual(prep["identity"]["native_dtb"]["sha256"], MODULE.CM6_CAMERA_SHA256)
        self.assertEqual(MODULE.prepared_contract(prepared, prep, "bpi-cm6"), (True, True))
        self.assertEqual(MODULE.prepared_contract(prepared, prep, "bpi-cm6", True), (True, True))
        with self.assertRaisesRegex(ValueError, "選項.*不一致"):
            MODULE.prepared_contract(prepared, prep, "bpi-cm6", False)
        text = MODULE.extlinux("bpi-cm6", str(uuid.uuid4()), cm6_dual_imx415=True, native=True)
        self.assertIn("FDT /dtb/spacemit/k1-x_bpi_cm6.dtb\n", text)
        self.assertNotIn(MODULE.CM6_CAMERA_PATH, text)

    def test_native_prepared_accepts_locked_cm6_2_and_rejects_provenance_drift(self):
        prepared, prep = self.native_prepared(dual=True)
        prep["identity"]["cm6_camera_packages"] = CameraPackageContractTests.identity(self)["cm6_camera_packages"]
        self.assertEqual(MODULE.prepared_contract(prepared, prep, "bpi-cm6"), (True, True))
        prep["identity"]["cm6_camera_packages"]["k1x-cam"]["provenance"]["patches"].pop()
        with self.assertRaisesRegex(ValueError, "相機配套身分"):
            MODULE.prepared_contract(prepared, prep, "bpi-cm6")

    def test_native_rejects_boot_tree_drift_and_changed_semantics(self):
        prepared, prep = self.native_prepared()
        extra = prepared / "boot-tree/untracked-file"
        extra.write_text("額外內容")
        with self.assertRaisesRegex(ValueError, "boot-tree"):
            MODULE.prepared_contract(prepared, prep, "bpi-cm6")
        extra.unlink()
        prep["identity"]["native_dtb"]["validation"]["checked_properties"] = []
        with self.assertRaisesRegex(ValueError, "語義"):
            MODULE.prepared_contract(prepared, prep, "bpi-cm6")

    def test_absolute_boot_symlinks_compare_with_normalized_export(self):
        prepared, prep = self.native_prepared()
        boot = prepared / "boot-tree"
        (boot / "Image").symlink_to("/boot/kernel")
        absolute = MODULE.boot_inventory(boot)
        (boot / "Image").unlink()
        (boot / "Image").symlink_to("kernel")
        self.assertEqual(MODULE.boot_inventory(boot), absolute)

    @unittest.skipUnless(shutil.which("mkfs.ext4") and shutil.which("debugfs"), "需要 ext4 工具")
    def test_schema2_archive_verifies_actual_native_dtb(self):
        prepared, prep = self.native_prepared(dual=True)
        image = self.base / "bootfs.ext4"
        with image.open("wb") as stream:
            stream.truncate(8 * MODULE.MIB)
        subprocess.run(["mkfs.ext4", "-q", "-F", "-d", str(prepared / "boot-tree"), str(image)], check=True)
        members = self.titan_members()
        members["bootfs.ext4"] = image.read_bytes()
        manifest_path = self.archive_manifest(list(members.items()))
        manifest = json.loads(manifest_path.read_text())
        manifest.update(schema_version=2, identity=prep["identity"], native_dtb_sha256=prep["identity"]["native_dtb"]["sha256"])
        manifest_path.write_text(json.dumps(manifest))
        self.assertEqual(MODULE.verify(manifest_path)["status"], "passed")
        manifest["native_dtb_sha256"] = "0" * 64
        manifest_path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, "獨立 DTB"):
            MODULE.verify(manifest_path)

    @unittest.skipUnless(os.geteuid() == 0 and os.environ.get("BPI_K1_TEST_MOUNT") == "yes", "需明確允許隔離命名空間的小型檔案系統測試")
    def test_schema2_small_ext4_media_copy_keeps_prepared_unchanged(self):
        spec = importlib.util.spec_from_file_location("policy_test_fixture", ROOT / "tests/test_bpi_k1_vendor_policy.py")
        fixture = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fixture)
        prepared, prep = self.native_prepared(dual=True)
        boot = prepared / "boot-tree"
        kernel = "6.6.36-legacy-spacemit"
        raw = bytearray(128)
        raw[56:60] = b"RSC\x05"
        (boot / ("vmlinuz-" + kernel)).write_bytes(raw)
        (boot / "Image").symlink_to("vmlinuz-" + kernel)
        (boot / "uInitrd").write_bytes(b"fixture-initramfs")
        tree = self.base / "root-tree"
        tree.mkdir()
        fixture.policy_root(tree)
        (tree / "usr/local/sbin").mkdir(parents=True)
        shutil.copytree(boot, tree / "boot", symlinks=True)
        rootfs = prepared / "rootfs.ext4"
        with rootfs.open("wb") as stream:
            stream.truncate(32 * MODULE.MIB)
        subprocess.run(["mkfs.ext4", "-q", "-F", "-d", str(tree), str(rootfs)], check=True)
        prep.update(kernel=kernel, boot_tree=MODULE.boot_inventory(boot), rootfs_sha256=MODULE.digest(rootfs))
        prep["identity"]["acceleration_lock_sha256"] = MODULE.digest(ROOT / "config/spacemit-k1-acceleration/noble.lock.json")
        (prepared / "preparation.json").write_text(json.dumps(prep))
        (prepared / "acceleration-preflight.json").write_text(json.dumps({"passed": True, "stage": "installed", "board": "bpi-cm6"}))
        reference = self.payload()
        original = MODULE.digest(rootfs)
        for media in ("sd", "emmc"):
            args = MODULE.argparse.Namespace(board="bpi-cm6", storage=media, release_id="20260924-rc6", inside=True,
                                             prepared=prepared, reference=reference, output=self.base / media, cm6_dual_imx415=None)
            with patch.object(MODULE, "source_reference", return_value={"sources_lock_sha256": "f" * 64}):
                MODULE.build(args)
            result = json.loads((args.output / "manifest.json").read_text())
            self.assertEqual(result["schema_version"], 2)
            self.assertEqual(result["native_dtb_sha256"], prep["identity"]["native_dtb"]["sha256"])
            self.assertNotIn("cm6_camera_dtb_sha256", result["identity"])
            self.assertIn("vendor_policy", result)
            self.assertEqual(MODULE.digest(rootfs), original)
            self.assertEqual(MODULE.boot_inventory(boot), prep["boot_tree"])


if __name__ == "__main__":
    unittest.main()

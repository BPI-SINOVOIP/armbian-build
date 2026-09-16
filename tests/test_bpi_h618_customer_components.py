#!/usr/bin/env python3
"""客戶組件靜態檢查回歸；只用小型記憶體映像與暫存普通檔。"""

from contextlib import redirect_stdout
import gzip
import importlib.util
import io
import json
import lzma
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest import mock
import zlib


TOOLS = Path(__file__).resolve().parents[1] / "tools"
SPEC = importlib.util.spec_from_file_location("customer_components", TOOLS / "bpi_h618_customer_components.py")
c = importlib.util.module_from_spec(SPEC)
with mock.patch.object(sys, "path", [str(TOOLS), *sys.path]):
    SPEC.loader.exec_module(c)

UUID = "62cd58de-498d-4325-8528-96a15da7d902"


def uimage(payload, kind=3, arch=22, compression=1):
    header = bytearray(struct.pack(">7I4B32s", 0x27051956, 0, 0, len(payload), 0, 0,
                                  zlib.crc32(payload), 5, arch, kind, compression, bytes(32)))
    struct.pack_into(">I", header, 4, zlib.crc32(header))
    return bytes(header) + payload


def sample_image():
    raw = bytearray(8192)
    struct.pack_into("<I", raw, 440, 0x12345678)
    struct.pack_into("<B3sB3sII", raw, 446, 0, bytes(3), 0x83, bytes(3), 2, 14)
    raw[510:512] = b"\x55\xaa"
    raw[1024:] = bytes(range(256)) * 28
    compressed = lzma.compress(raw)
    expected = {"raw": {key: c.digest(raw)[key] for key in ("bytes", "sha256")},
                "compressed": {key: c.digest(compressed)[key] for key in ("bytes", "sha256")},
                "mbr": c.matrix.check_mbr(raw[:512], len(raw), 31289507840)}
    return bytes(raw), compressed, expected


class ComponentsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)

    def test_env_preserves_literal_shell_syntax(self):
        value = b"#\nA=$(touch /tmp/not-run)\nB='${HOME}'\nC=a=b\nD=\n"
        self.assertEqual(c.parse_env(value), {"A": "$(touch /tmp/not-run)", "B": "'${HOME}'", "C": "a=b", "D": ""})

    def test_env_rejects_duplicate(self):
        with self.assertRaises(c.safe.ArtifactError):
            c.parse_env(b"A=1\nA=2\n")

    def test_env_rejects_commands(self):
        for value in (b"source /etc/profile", b"A=1\nexit 0", b"A;touch=1", b" A=1"):
            with self.subTest(value=value), self.assertRaises(c.safe.ArtifactError):
                c.parse_env(value)

    def test_legacy_initrd_crc_and_payload(self):
        payload = gzip.compress(b"fixture", mtime=0)
        self.assertEqual(c.legacy(uimage(payload), 3), payload)

    def test_legacy_rejects_header_damage(self):
        blob = bytearray(uimage(gzip.compress(b"fixture")))
        blob[12] ^= 1
        with self.assertRaises(c.safe.ArtifactError):
            c.legacy(blob, 3)

    def test_legacy_rejects_payload_damage(self):
        blob = bytearray(uimage(gzip.compress(b"fixture")))
        blob[-1] ^= 1
        with self.assertRaises(c.safe.ArtifactError):
            c.legacy(blob, 3)

    def test_legacy_rejects_truncation_and_trailing(self):
        blob = uimage(gzip.compress(b"fixture"))
        for changed in (blob[:63], blob[:-1], blob + b"x"):
            with self.subTest(size=len(changed)), self.assertRaises(c.safe.ArtifactError):
                c.legacy(changed, 3)

    def test_legacy_rejects_arch_and_compression(self):
        for kwargs in ({"arch": 2}, {"compression": 0}, {"kind": 2}):
            with self.subTest(kwargs=kwargs), self.assertRaises(c.safe.ArtifactError):
                c.legacy(uimage(gzip.compress(b"fixture"), **kwargs), 3)

    def test_legacy_script_table(self):
        script = b"echo fixture\n"
        wrapped = uimage(struct.pack(">II", len(script), 0) + script, kind=6, arch=2, compression=0)
        self.assertEqual(c.legacy(wrapped, 6), script)

    def test_legacy_rejects_multiple_script_items(self):
        wrapped = uimage(struct.pack(">II", 1, 1) + b"x", kind=6, compression=0)
        with self.assertRaises(c.safe.ArtifactError):
            c.legacy(wrapped, 6)

    def test_uuid_three_way_match(self):
        self.assertEqual(c.root_uuid("Filesystem UUID: " + UUID, {"rootdev": "UUID=" + UUID, "rootfstype": "ext4"},
                                    f"UUID={UUID} / ext4 defaults 0 1\n".encode()), UUID)

    def test_uuid_rejects_wrong_env(self):
        with self.assertRaises(c.safe.ArtifactError):
            c.root_uuid("Filesystem UUID: " + UUID, {"rootdev": "/dev/mmcblk0p1", "rootfstype": "ext4"},
                        f"UUID={UUID} / ext4 defaults 0 1\n".encode())

    def test_uuid_rejects_duplicate_root(self):
        with self.assertRaises(c.safe.ArtifactError):
            c.root_uuid("Filesystem UUID: " + UUID, {"rootdev": "UUID=" + UUID, "rootfstype": "ext4"},
                        (f"UUID={UUID} / ext4 defaults 0 1\n" * 2).encode())

    def test_uuid_rejects_wrong_filesystem(self):
        with self.assertRaises(c.safe.ArtifactError):
            c.root_uuid("Filesystem UUID: " + UUID, {"rootdev": "UUID=" + UUID, "rootfstype": "ext4"},
                        f"UUID={UUID} / xfs defaults 0 1\n".encode())

    def test_uuid_rejects_missing_superblock(self):
        with self.assertRaises(c.safe.ArtifactError):
            c.root_uuid("", {}, b"")

    def test_module_directory_listing(self):
        listing = f"/1/40755/0/0/./4096/\n/2/40755/0/0/../4096/\n/3/40755/0/0/{c.RELEASE}/4096/\n"
        self.assertEqual(c.module_versions(listing), [c.RELEASE])

    def test_module_rejects_additional_version(self):
        with self.assertRaises(c.safe.ArtifactError):
            c.module_versions(c.RELEASE + "\n5.15-other\n")

    def test_kernel_requires_builtin_not_module(self):
        kernel = bytearray(64)
        kernel[56:60] = b"ARM\x64"
        kernel.extend(b"Linux version " + c.RELEASE.encode() + b" ")
        config = b"\n".join(b"CONFIG_" + name.encode() + b"=y" for name in
                            ("MMC", "MMC_BLOCK", "MMC_SUNXI", "PWRSEQ_EMMC", "EXT4_FS", "BLK_DEV_INITRD", "RD_GZIP"))
        builtin = b"kernel/drivers/mmc/host/sunxi-mmc.ko\nkernel/drivers/mmc/core/mmc_core.ko\n" \
                  b"kernel/drivers/mmc/core/mmc_block.ko\nkernel/drivers/mmc/core/pwrseq_emmc.ko\n"
        self.assertEqual(c.kernel_support(config, builtin, kernel)["MMC"], "y")
        with self.assertRaises(c.safe.ArtifactError):
            c.kernel_support(config.replace(b"CONFIG_MMC=y", b"CONFIG_MMC=m"), builtin, kernel)
        with self.assertRaises(c.safe.ArtifactError):
            c.kernel_support(config, b"", kernel)
        with self.assertRaises(c.safe.ArtifactError):
            c.kernel_support(config, builtin, kernel.replace(c.RELEASE.encode(), b"wrong"))

    def test_stream_writes_only_partition(self):
        raw, compressed, expected = sample_image()
        target = io.BytesIO()
        report, prefix = c.stream_partition(io.BytesIO(compressed), target, expected)
        self.assertEqual(target.getvalue(), raw[1024:])
        self.assertEqual(prefix, raw[:1024])
        self.assertEqual(report["partuuid"], "12345678-01")
        self.assertEqual(report["root_partition"]["sha256"], c.digest(raw[1024:])["sha256"])

    def test_stream_rejects_raw_hash(self):
        _, compressed, expected = sample_image()
        expected["raw"]["sha256"] = "0" * 64
        with self.assertRaises(c.safe.ArtifactError):
            c.stream_partition(io.BytesIO(compressed), io.BytesIO(), expected)

    def test_stream_rejects_compressed_hash(self):
        _, compressed, expected = sample_image()
        expected["compressed"]["sha256"] = "0" * 64
        with self.assertRaises(c.safe.ArtifactError):
            c.stream_partition(io.BytesIO(compressed), io.BytesIO(), expected)

    def test_stream_rejects_truncated_xz(self):
        _, compressed, expected = sample_image()
        with self.assertRaises((EOFError, lzma.LZMAError, c.safe.ArtifactError)):
            c.stream_partition(io.BytesIO(compressed[:-8]), io.BytesIO(), expected)

    def test_stream_rejects_changed_mbr(self):
        _, compressed, expected = sample_image()
        expected["mbr"]["partitions"][0]["bootable"] = True
        with self.assertRaises(c.safe.ArtifactError):
            c.stream_partition(io.BytesIO(compressed), io.BytesIO(), expected)

    def test_stream_rejects_multiple_partitions(self):
        _, compressed, expected = sample_image()
        expected["mbr"]["partitions"] *= 2
        with self.assertRaises(c.safe.ArtifactError):
            c.stream_partition(io.BytesIO(compressed), io.BytesIO(), expected)

    def test_stream_rejects_extra_raw_bytes(self):
        raw, _, expected = sample_image()
        with self.assertRaises(c.safe.ArtifactError):
            c.stream_partition(io.BytesIO(lzma.compress(raw + bytes(512))), io.BytesIO(), expected)

    def test_safe_read_rejects_symlink(self):
        (self.base / "real").write_bytes(b"fixture")
        (self.base / "link").symlink_to("real")
        with self.assertRaises(c.safe.ArtifactError):
            c.read(self.base / "link")

    def test_safe_read_rejects_fifo(self):
        os.mkfifo(self.base / "fifo")
        with self.assertRaises(c.safe.ArtifactError):
            c.read(self.base / "fifo")

    def test_save_refuses_overwrite(self):
        c.save(self.base / "once", b"fixture")
        with self.assertRaises(FileExistsError):
            c.save(self.base / "once", b"changed")

    def test_read_refuses_oversize(self):
        c.save(self.base / "large", b"12345")
        with self.assertRaises(c.safe.ArtifactError):
            c.read(self.base / "large", limit=4)

    def make_debug(self):
        partition = self.base / "root-partition.img"
        partition.write_bytes(bytes(2048))
        output = self.base / "evidence"
        c.mkdir(output)
        return c.Debugfs(partition, output)

    def test_debugfs_only_readonly_fd(self):
        debug = self.make_debug()
        result = mock.Mock(returncode=0, stdout=b"fixture", stderr=b"debugfs 1.47.0 (fixture)\n")
        with mock.patch.object(c.subprocess, "run", return_value=result) as run:
            self.assertEqual(debug.query("query", "stats"), b"fixture")
        args = run.call_args.args[0]
        self.assertEqual(args[:3], ["/usr/sbin/debugfs", "-R", "stats"])
        self.assertTrue(args[3].startswith("/proc/self/fd/"))
        self.assertNotIn("-w", args)
        self.assertTrue(run.call_args.kwargs["pass_fds"])
        self.assertNotIn("shell", run.call_args.kwargs)

    def test_debugfs_refuses_write_command_and_injection(self):
        debug = self.make_debug()
        with mock.patch.object(c.subprocess, "run") as run:
            for operation, path in (("write", "/boot/test"), ("cat", "/boot/../etc/shadow"),
                                    ("cat", "/boot/a\nwrite"), ("cat", "/boot/a;quit")):
                with self.subTest(operation=operation, path=path), self.assertRaises(c.safe.ArtifactError):
                    debug.query("query", operation, path)
            run.assert_not_called()

    def test_debugfs_rc_zero_does_not_mean_success(self):
        debug = self.make_debug()
        result = mock.Mock(returncode=0, stdout=b"", stderr=b"debugfs 1.47.0\nFile not found by ext2_lookup\n")
        with mock.patch.object(c.subprocess, "run", return_value=result):
            with self.assertRaises(c.safe.ArtifactError):
                debug.query("query", "stat", "/missing")
            self.assertIsNone(debug.query("optional", "stat", "/missing", optional=True))

    def test_debugfs_optional_does_not_hide_corruption(self):
        debug = self.make_debug()
        result = mock.Mock(returncode=0, stdout=b"", stderr=b"debugfs 1.47.0\nFilesystem checksum invalid\n")
        with mock.patch.object(c.subprocess, "run", return_value=result), self.assertRaises(c.safe.ArtifactError):
            debug.query("query", "stat", "/missing", optional=True)

    def test_debugfs_rejects_nonregular_dump(self):
        debug = self.make_debug()
        with mock.patch.object(debug, "query", return_value=b"Type: symlink Size: 10"), self.assertRaises(c.safe.ArtifactError):
            debug.file("file", "/boot/link")

    def test_parse_json_rejects_duplicate_fields(self):
        with self.assertRaises(c.safe.ArtifactError):
            c.parse_json(b"{\"ok\":false,\"ok\":true}")

    def test_cli_rejects_unknown_board_before_io(self):
        stdout = io.StringIO()
        with redirect_stdout(stdout), mock.patch.object(c, "trusted_matrix") as load:
            self.assertEqual(c.main(["--only", "berry-minimal"]), 1)
            load.assert_not_called()
        self.assertFalse(json.loads(stdout.getvalue())["ok"])

    def test_digest_crc_lowercase_fixed_width(self):
        self.assertEqual(c.digest(b"123456789")["crc32"], "cbf43926")
        self.assertEqual(c.digest(b"")["crc32"], "00000000")

    def manifest_fixture(self):
        _, _, result = sample_image()
        inventory = {"root": "/source"}
        entry = {"path": "fixed.img.xz", "release": "bookworm", "variant": "minimal"}
        manifest = {"schema": "bpi-h618-customer-components-v1", "board": c.BOARD,
                    "os": "bookworm", "desktop": "minimal", "kernel_release": c.RELEASE,
                    "root_uuid": UUID, "partuuid": "12345678-01", "hardware_validated": False,
                    "preflight": {"source_verified": True, "legacy_initrd_verified": True, "mmc_support_verified": True},
                    "image": {"path": "/source/fixed.img.xz", "compressed": result["compressed"], "raw": result["raw"]},
                    "original_env": {"rootdev": "UUID=" + UUID},
                    "files": {role: {"path": c.PATHS[name], **c.digest(b"fixture")} for role, name in c.ROLES.items()}}
        return manifest, inventory, entry, result

    def test_shared_schema_accepts_complete_record(self):
        c.validate_manifest(*self.manifest_fixture())

    def test_shared_schema_rejects_missing_condition(self):
        for key in ("source_verified", "legacy_initrd_verified", "mmc_support_verified"):
            for value in (False, None, 1):
                values = self.manifest_fixture()
                values[0]["preflight"][key] = value
                with self.subTest(key=key, value=value), self.assertRaises(c.safe.ArtifactError):
                    c.validate_manifest(*values)

    def test_shared_schema_rejects_hardware_pass(self):
        values = self.manifest_fixture()
        values[0]["hardware_validated"] = True
        with self.assertRaises(c.safe.ArtifactError):
            c.validate_manifest(*values)

    def test_shared_schema_rejects_other_board(self):
        values = self.manifest_fixture()
        values[0]["board"] = "bananapim4berry"
        with self.assertRaises(c.safe.ArtifactError):
            c.validate_manifest(*values)

    def test_shared_schema_rejects_changed_source(self):
        values = self.manifest_fixture()
        values[0]["image"]["path"] = "/source/other.img.xz"
        with self.assertRaises(c.safe.ArtifactError):
            c.validate_manifest(*values)

    def test_shared_schema_requires_uinitrd_not_raw_initrd(self):
        values = self.manifest_fixture()
        values[0]["files"]["initrd"]["path"] = c.PATHS["initrd.img"]
        with self.assertRaises(c.safe.ArtifactError):
            c.validate_manifest(*values)

    def test_shared_schema_rejects_noncanonical_crc(self):
        values = self.manifest_fixture()
        values[0]["files"]["kernel"]["crc32"] = "CBF43926"
        with self.assertRaises(c.safe.ArtifactError):
            c.validate_manifest(*values)

    def test_publish_refuses_existing_manifest(self):
        directory = self.base / "bookworm-minimal"
        c.mkdir(directory)
        original = self.base / "bookworm-minimal.json"
        c.save(original, b"original")
        with mock.patch.object(c, "OUTPUT", self.base), self.assertRaises(FileExistsError):
            c.publish(directory, {"ok": True}, "bookworm-minimal")
        self.assertEqual(c.read(original), b"original")

    def test_publish_preserves_replaced_partition(self):
        directory = self.base / "bookworm-minimal"
        c.mkdir(directory)
        partition = directory / "root-partition.img"
        c.save(partition, b"fixture")
        manifest = {"source_evidence": {"temporary_identity": {}}}
        with mock.patch.object(c, "OUTPUT", self.base), self.assertRaises(c.safe.ArtifactError):
            c.publish(directory, manifest, "bookworm-minimal", partition)
        self.assertTrue(partition.exists())


if __name__ == "__main__":
    unittest.main()

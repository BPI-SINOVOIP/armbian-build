#!/usr/bin/env python3
"""SpacemiT K1 原配解析、K3 SDK 入口與安全條件。"""

from pathlib import Path
import gzip
import hashlib
import struct
import sys
import unittest
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_bpi_lab_extlinux import FixtureCase, UUID, RELEASE, core, spacemit, template, tree, dtc


def fit(payload, *, algo="crc32", bad_hash=False, config_extra="", image_extra="", root_extra="", compression="none"):
    value = struct.pack(">I", zlib.crc32(payload)) if algo == "crc32" else hashlib.sha256(payload).digest()
    if bad_hash:
        value = bytes(len(value))
    return dtc('/dts-v1/; / { description = "測試核心"; #address-cells = <1>; '
               'images { kernel { description = "測試核心"; data = [' + payload.hex(" ") + ']; '
               'type = "kernel"; arch = "riscv"; os = "linux"; compression = "' + compression + '"; '
               'load = <0x200000>; entry = <0x200000>; ' + image_extra
               + ' hash { algo = "' + algo + '"; value = [' + value.hex(" ") + ']; }; '
               + ' }; }; configurations { default = "conf-default"; conf-default { kernel = "kernel"; '
               + config_extra + ' }; }; ' + root_extra + ' };')


class SpacemiTTests(FixtureCase):
    def test_k1_boards(self):
        self.assertEqual(len(spacemit.POLICIES), 3)
        for board in ("bpi-cm6", "bpi-f3"):
            with self.subTest(board=board):
                self.select(board)
                m = self.prepare()
                self.assertEqual(m["status"], "prepared", m["blockers"])
                self.assertEqual(m["arch"], "riscv64")
                self.assertEqual(m["checks"]["kernel"]["format"], "Image")
                spacemit.validate_template(m, template=template(m), artifact_root=self.output)

    def test_k3_original_mmc_route_and_template(self):
        self.select("bpi-sm10")
        m = self.prepare()
        self.assertEqual(m["status"], "prepared", m["blockers"])
        self.assertTrue(m["components_available"])
        self.assertIn("vendor_initrd_alias", m["checks"])
        self.assertEqual(m["root_uuid"], UUID)
        self.assertIn("root=PARTUUID=${rootfs_guid}", m["bootargs_template"])
        self.assertIn("mmc_boot", m["vendor_route"])
        self.assertIn("booti", m["vendor_route"])
        self.assertFalse(spacemit.validate_template(m, template=template(m), artifact_root=self.output)["execution_ready"])

    def test_k3_default_bootcmd_overrides_empty_config(self):
        data = (core.ROOT / "packages/blobs/riscv64/spacemit-k3/bpi-sm10/env.bin").read_bytes()
        parsed = spacemit.parse_vendor_environment(data)
        self.assertTrue(parsed["values"]["bootcmd"].startswith("run autoboot;"))
        self.assertIn("bootcmd", parsed["overwritten_keys"])
        with self.assertRaises(core.Error):
            spacemit.parse_vendor_environment(data[:100] + b"x" + data[101:])

    def test_k3_root_uuid_and_grub_requirements(self):
        self.select("bpi-sm10")
        del self.files["/etc/fstab"]
        self.blocked("missing_file")
        self.select("bpi-sm10")
        self.files["/boot/EFI/BOOT/BOOTRISCV64.EFI"] = b"BPI"
        self.blocked("vendor_grub")

    def test_k3_chosen_merge_preserves_environment_precedence(self):
        self.select("bpi-sm10")
        self.files["/boot/dtb/" + self.policy["dtb"]] = tree(self.policy, 'chosen { bootargs = "root=/dev/ram console=ttyS1,9600 cma=256M"; };')
        m = self.prepare()
        self.assertEqual(m["status"], "prepared", m["blockers"])
        self.assertIn("cma=256M", m["bootargs_template"])
        self.assertNotIn("root=/dev/ram", m["bootargs_template"])
        self.assertNotIn("console=ttyS1,9600", m["bootargs_template"])

    def test_k3_alias_mismatch(self):
        self.select("bpi-sm10")
        self.files["/boot/uInitrd"] = b"wrong"
        self.assertFalse(self.blocked("initrd_alias")["components_available"])

    def test_k3_environment_changes_never_executed(self):
        self.select("bpi-sm10")
        self.files["/boot/env_k3.txt"] += b"bootcmd=run unknown\n"
        self.blocked("vendor_env")

    def test_riscv_header_not_arm_image(self):
        self.select("bpi-f3")
        self.files["/boot/Image"] = self.files["/boot/Image"][:56] + b"ARM\x64" + self.files["/boot/Image"][60:]
        self.blocked("kernel_format")

    def test_gzip_riscv_preserves_file_and_accounts_for_bss(self):
        for board in ("bpi-f3", "bpi-sm10"):
            self.select(board)
            raw = bytearray(self.files["/boot/Image"])
            raw[512:536] = b"Linux version %s (%s)\0\0\0\0"
            struct.pack_into("<Q", raw, 16, len(raw) + 8192)
            compressed = gzip.compress(raw, mtime=0)
            self.files["/boot/Image"] = self.files["/boot/vmlinuz-" + RELEASE] = compressed
            m = self.prepare()
            self.assertEqual(m["status"], "prepared", m["blockers"])
            self.assertEqual(m["checks"]["kernel"]["bss_bytes"], 8192)
            self.assertEqual(m["runtime_requirements"]["kernel_decompression"]["compressed_bytes"], len(compressed))
            self.assertEqual((self.output / m["files"]["kernel"]["evidence_path"]).read_bytes(), compressed)

    def test_gzip_rejects_tail_truncation_wrong_arch_and_small_size(self):
        self.select("bpi-f3")
        raw = self.files["/boot/Image"]
        bad = bytearray(raw)
        struct.pack_into("<Q", bad, 16, len(raw) - 1)
        for blob in (gzip.compress(raw) + b"x", gzip.compress(raw)[:-4], gzip.compress(bad),
                     gzip.compress(raw[:48] + bytes(12) + raw[60:])):
            with self.subTest(blob=blob[:8]), self.assertRaises((core.Error, ValueError)):
                core.kernel(blob, "riscv64", RELEASE, self.files["/boot/config-" + RELEASE])
        with self.assertRaises(core.Error):
            core.kernel(gzip.compress(raw), "arm64", RELEASE, b"CONFIG_ARM64=y\n")
        wrong = bytearray(raw)
        wrong[512:536] = b"Linux version 6.1-wrong\0\0"
        with self.assertRaises(core.Error):
            core.kernel(gzip.compress(wrong), "riscv64", RELEASE, self.files["/boot/config-" + RELEASE])

    def test_k3_inactive_armbian_environment_is_evidence_not_entry(self):
        self.select("bpi-sm10")
        self.files["/boot/armbianEnv.txt"] = b"rootdev=UUID=not-used\nbootcmd=saveenv\n"
        m = self.prepare()
        self.assertEqual(m["status"], "prepared", m["blockers"])
        self.assertEqual(m["inactive_files"][0]["path"], "/boot/armbianEnv.txt")
        self.assertEqual(m["entry"]["path"], "/boot/env_k3.txt")
        self.assertEqual(m["root_uuid"], UUID)

    def test_cm6_original_single_kernel_fit(self):
        self.select("bpi-cm6")
        raw = self.files["/boot/Image"]
        for algo in ("crc32", "sha256"):
            with self.subTest(algo=algo):
                blob = fit(raw, algo=algo)
                self.files["/boot/Image"] = self.files["/boot/vmlinuz-" + RELEASE] = blob
                m = self.prepare()
                self.assertEqual(m["status"], "prepared", m["blockers"])
                k = m["checks"]["kernel"]
                self.assertEqual(k["format"], "FIT")
                self.assertEqual(k["payload"]["sha256"], core.digest(raw)["sha256"])
                self.assertEqual(k["payload"]["kernel_release"], RELEASE)
                self.assertFalse(k["fit"]["authenticated"])
                self.assertEqual(m["runtime_requirements"]["kernel_fit"]["command"], "bootm")
                self.assertEqual((self.output / m["files"]["kernel"]["evidence_path"]).read_bytes(), blob)
                spacemit.validate_template(m, template=template(m), artifact_root=self.output)

    def test_fit_crc_and_payload_version_must_match(self):
        self.select("bpi-cm6")
        raw = self.files["/boot/Image"]
        self.files["/boot/Image"] = self.files["/boot/vmlinuz-" + RELEASE] = fit(raw, bad_hash=True)
        self.blocked("kernel_fit_hash")
        self.files["/boot/Image"] = self.files["/boot/vmlinuz-" + RELEASE] = fit(raw.replace(RELEASE.encode(), b"6.18.9-c3"))
        self.blocked("kernel_version")

    def test_fit_rejects_unimplemented_security_and_component_semantics(self):
        self.select("bpi-cm6")
        raw = self.files["/boot/Image"]
        for options in ({"config_extra": 'fdt = "fdt";'}, {"config_extra": 'ramdisk = "initrd";'},
                        {"config_extra": 'loadables = "sbi";'}, {"image_extra": 'signature { algo = "sha256,rsa2048"; };'},
                        {"image_extra": 'data-offset = <0>;'}, {"root_extra": 'signature { required = "conf"; };'},
                        {"compression": "gzip"}, {"algo": "sha1"}):
            with self.subTest(options=options):
                self.files["/boot/Image"] = self.files["/boot/vmlinuz-" + RELEASE] = fit(raw, **options)
                self.blocked("kernel_fit")

    def test_fit_rejects_truncation_external_tail_and_missing_default(self):
        self.select("bpi-cm6")
        raw = fit(self.files["/boot/Image"])
        for value in (raw[:-1], raw + b"x", raw.replace(b"conf-default\0", b"conf-missing\0", 1)):
            with self.subTest(length=len(value)), self.assertRaises(core.Error):
                core.kernel(value, "riscv64", RELEASE, self.files["/boot/config-" + RELEASE])

    def test_fit_does_not_relax_arm64_or_nested_payload(self):
        self.select("bpi-cm6")
        blob = fit(self.files["/boot/Image"])
        with self.assertRaises(core.Error):
            core.kernel(blob, "arm64", RELEASE, b"CONFIG_ARM64=y\n")
        for data in (blob, gzip.compress(self.files["/boot/Image"])):
            with self.subTest(length=len(data)), self.assertRaises(core.Error):
                core.kernel(fit(data), "riscv64", RELEASE, self.files["/boot/config-" + RELEASE])


if __name__ == "__main__":
    unittest.main()

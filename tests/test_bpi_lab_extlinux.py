#!/usr/bin/env python3
"""C3 共用解析與測資；合成內容不代表實板或原廠韌體已核定。"""

import copy
import gzip
import json
import lzma
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import bpi_lab_extlinux as core
from tools import bpi_lab_rockchip as rockchip
from tools import bpi_lab_mediatek as mediatek
from tools import bpi_lab_spacemit as spacemit

UUID = "12345678-1234-1234-1234-123456789abc"
RELEASE = "6.18.1-c3"


def legacy(data, arch="arm64", script=False):
    if script:
        data = struct.pack(">II", len(data), 0) + data
    header = struct.pack(">7I4B32s", 0x27051956, 0, 0, len(data), 0, 0, zlib.crc32(data),
                         5, 2 if script else {"arm32": 2, "arm64": 22, "riscv64": 26}[arch],
                         6 if script else 3, 0 if script else 1, b"BPI")
    return header[:4] + struct.pack(">I", zlib.crc32(header)) + header[8:] + data


def cpio(release=RELEASE):
    data = bytearray()
    for name, payload in (("init", b"BPI"), ("usr/lib/modules/" + release + "/kernel/test.ko", b"BPI"), ("TRAILER!!!", b"")):
        name = name.encode() + b"\0"
        fields = [1, 0o100755, 0, 0, 1, 0, len(payload), 0, 0, 0, 0, len(name), 0]
        data += b"070701" + "".join(f"{n:08x}" for n in fields).encode() + name
        data += bytes(-len(data) % 4)
        data += payload
        data += bytes(-len(data) % 4)
    return gzip.compress(bytes(data), mtime=0)


def dtc(text):
    return subprocess.run(["/usr/bin/dtc", "-@", "-I", "dts", "-O", "dtb"], input=text.encode(),
                          capture_output=True, check=True, timeout=10).stdout


def tree(policy, extra=""):
    return dtc('/dts-v1/; / { model = ' + json.dumps(policy["model"]) + "; compatible = "
               + ", ".join(json.dumps(x) for x in policy["compatible"])
               + '; lab: lab-node { status = "disabled"; }; ' + extra + " };")


def overlay(status="okay"):
    return dtc('/dts-v1/; /plugin/; / { fragment@0 { target = <&lab>; __overlay__ { status = "'
               + status + '"; }; }; };')


def fixture(board="bpi-r3"):
    module = next(m for m in (rockchip, mediatek, spacemit) if board in m.POLICIES)
    matrix = json.loads((core.ROOT / "config/bpi-lab/platforms.json").read_text())
    row = next(r for r in matrix["boards"] if r["board"] == board)
    policy = json.loads((core.ROOT / ("config/validation/bananapi-" + module.POLICIES[board] + ".json")).read_text())["boards"][row["artifact_board"]]
    arch = row["architecture"]
    image = bytearray(4096)
    banner = b"Linux version " + RELEASE.encode() + b" (BPI)\0"
    if arch == "arm32":
        image += gzip.compress(banner + bytes(4096), mtime=0)
        struct.pack_into("<3I", image, 36, 0x016f2818, 0, len(image))
    else:
        struct.pack_into("<3Q", image, 8, 0x200000 if arch == "riscv64" else 0x80000, len(image), 0)
        image[128:128 + len(banner)] = banner
        if arch == "riscv64":
            struct.pack_into("<I", image, 32, 2)
            image[48:60] = b"RISCV\0\0\0RSC\x05"
        else:
            image[56:60] = b"ARM\x64"
    kernel_name = "zImage" if arch == "arm32" else "Image"
    initrd_arch = {"arm32": "arm", "arm64": "arm64", "riscv64": "riscv"}[arch]
    raw = cpio()
    files = {
        "/etc/armbian-release": (f'BOARD={row["artifact_board"]}\nBOARDFAMILY={row["family"]}\n'
                                 f'KERNEL_IMAGE_TYPE={kernel_name}\nINITRD_ARCH={initrd_arch}\n').encode(),
        "/boot/" + kernel_name: bytes(image), "/boot/vmlinuz-" + RELEASE: bytes(image),
        "/boot/uInitrd": legacy(raw, arch), "/boot/initrd.img-" + RELEASE: raw,
        "/boot/dtb/" + policy["dtb"]: tree(policy),
        "/boot/config-" + RELEASE: ({"arm32": "CONFIG_ARM", "arm64": "CONFIG_ARM64", "riscv64": "CONFIG_RISCV"}[arch]
                                    + '=y\nCONFIG_CMDLINE=""\n').encode(),
    }
    if "extlinux" in row["boot_profile"]:
        files["/boot/extlinux/extlinux.conf"] = (
            "LABEL Armbian\n KERNEL /boot/Image\n INITRD /boot/uInitrd\n FDT /boot/dtb/" + policy["dtb"]
            + "\n APPEND root=UUID=" + UUID + " rootwait rw console=ttyS0,115200\n").encode()
    elif board == "bpi-sm10":
        files["/boot/env_k3.txt"] = (core.ROOT / "packages/blobs/riscv64/spacemit-k3/bpi-sm10/env_k3.txt").read_bytes()
        files["/boot/initramfs-generic.img"] = files["/boot/uInitrd"]
        files["/etc/fstab"] = ("UUID=" + UUID + " / ext4 defaults 0 1\n").encode()
    else:
        name = "boot-mt7623.cmd" if board == "bpi-r2" else rockchip.SCRIPTS[row["boot_profile"]][0]
        cmd = (core.ROOT / "config/bootscripts" / name).read_bytes()
        files.update({"/boot/boot.cmd": cmd, "/boot/boot.scr": legacy(cmd, arch, True),
                      "/boot/armbianEnv.txt": ("rootdev=UUID=" + UUID + "\nfdtfile=" + policy["dtb"]
                                               + "\noverlay_prefix=" + policy["overlay_prefix"] + "\n").encode()})
    return files, module, policy


def template(manifest):
    result = {key: copy.deepcopy(manifest[key]) for key in (
        "board", "kernel_release", "root_uuid", "entry", "runtime_requirements", "bootargs_template")}
    result.update(schema=core.TEMPLATE_SCHEMA, pairing_sha256="a" * 64, firmware_review_sha256="b" * 64,
                  dtb_checks={key: copy.deepcopy(manifest["checks"][key]) for key in ("dtb", "overlay_application")})
    return result


class FixtureCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.counter = 0
        self.select("bpi-r3")

    def select(self, board):
        self.board = board
        self.files, self.module, self.policy = fixture(board)

    def read(self, path):
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path]

    def prepare(self):
        self.counter += 1
        self.output = self.root / str(self.counter)
        return self.module.prepare(self.read, board=self.board, kernel_release=RELEASE, output=self.output)

    def blocked(self, code):
        manifest = self.prepare()
        self.assertEqual(manifest["status"], "blocked", manifest)
        self.assertIn(code, [x["code"] for x in manifest["blockers"]], manifest["blockers"])
        self.assertFalse(manifest["hardware_validated"])
        self.assertFalse(manifest["execution_ready"])
        return manifest


class ExtlinuxTests(FixtureCase):
    def config(self, value):
        self.files["/boot/extlinux/extlinux.conf"] = value.encode()

    def test_prepared_and_template(self):
        m = self.prepare()
        self.assertEqual(m["status"], "prepared", m["blockers"])
        self.assertEqual(m["root_uuid"], UUID)
        result = core.validate_template(m, template=template(m), artifact_root=self.output)
        self.assertTrue(result["artifact_contract_verified"])
        self.assertFalse(result["execution_ready"])
        self.assertEqual(json.loads((self.output / "manifest.json").read_text()), m)
        self.assertEqual(set(m["checks"]["initrd_container"]), {"bytes", "sha256"})

    def test_uppercase_alias_and_menu(self):
        parsed = core.parse(b"MENU TITLE BPI\nDEFAULT b\nLABEL a\n LINUX /boot/Image\nLABEL b\n MENU DEFAULT\n DEVICETREE /boot/dtb/b.dtb\n")
        self.assertEqual(parsed["selected_label"], "b")
        self.assertEqual(parsed["labels"][0]["directives"]["KERNEL"], "/boot/Image")
        self.assertEqual(parsed["labels"][1]["directives"]["FDT"], "/boot/dtb/b.dtb")

    def test_invalid_directives_defaults_duplicates(self):
        for data in (b"APPEND root=UUID=x\nLABEL a\n", b"INCLUDE x\n", b"LABEL a\n COM32 x\n",
                     b"LABEL a\n IPAPPEND 2\n", b"DEFAULT b\nLABEL a\n", b"LABEL a\nLABEL a\n",
                     b"LABEL a\n APPEND a\n APPEND b\n", b"DEFAULT a\nLABEL a\nLABEL b\n MENU DEFAULT\n"):
            with self.subTest(data=data), self.assertRaises((core.Error, ValueError)):
                core.parse(data)

    def test_multilabel_preserved_but_fallback_blocked(self):
        self.files["/boot/extlinux/extlinux.conf"] += b"LABEL rescue\n LINUX /rescue\n"
        m = self.blocked("extlinux_fallback")
        self.assertEqual(len(m["extlinux"]["labels"]), 2)
        self.assertEqual(m["entry"]["selected_label"], "Armbian")

    def test_boot_partition_absolute_paths(self):
        self.files["/boot/extlinux/extlinux.conf"] = self.files["/boot/extlinux/extlinux.conf"].replace(b"/boot/", b"/")
        m = self.prepare()
        self.assertEqual(m["status"], "prepared", m["blockers"])
        self.assertEqual(m["runtime_requirements"]["boot_mount"], "/boot")

    def test_mixed_namespace_blocked(self):
        self.files["/boot/extlinux/extlinux.conf"] = self.files["/boot/extlinux/extlinux.conf"].replace(b"KERNEL /boot/Image", b"KERNEL /Image")
        self.blocked("boot_mount")

    def test_path_traversal_no_read(self):
        self.files["/boot/extlinux/extlinux.conf"] = self.files["/boot/extlinux/extlinux.conf"].replace(b"KERNEL /boot/Image", b"KERNEL ../Image")
        m = self.blocked("image_path")
        self.assertFalse(any(".." in x["path"] for x in m["reads"]))

    def test_fdtdir_requires_explicit_runtime_contract(self):
        text = self.files["/boot/extlinux/extlinux.conf"].decode()
        self.config(text.replace("FDT /boot/dtb/" + self.policy["dtb"], "FDTDIR /boot/dtb/"))
        m = self.prepare()
        self.assertEqual(m["status"], "prepared", m["blockers"])
        self.assertEqual(m["runtime_requirements"]["fdtfile"], self.policy["dtb"])
        t = template(m)
        t["runtime_requirements"]["fdtfile"] = "wrong.dtb"
        with self.assertRaises(core.Error):
            core.validate_template(m, template=t, artifact_root=self.output)

    def test_fdt_and_fdtdir_conflict(self):
        self.files["/boot/extlinux/extlinux.conf"] += b" FDTDIR /boot/dtb\n"
        self.blocked("extlinux_fdt")

    def test_append_not_silently_rewritten(self):
        original = self.files["/boot/extlinux/extlinux.conf"]
        for replacement, code in ((b"${bootargs}", "bootargs"), (b"root=/dev/mmcblk0p1", "root_uuid"),
                                  (("root=UUID=" + UUID + " root=UUID=" + UUID).encode(), "root_uuid"),
                                  (("root=UUID=" + UUID + " initrd=/boot/uInitrd").encode(), "append_initrd")):
            with self.subTest(code=code):
                self.files["/boot/extlinux/extlinux.conf"] = original.replace(("root=UUID=" + UUID).encode(), replacement)
                self.blocked(code)

    def test_real_overlay_order_and_identity(self):
        self.files["/boot/extlinux/extlinux.conf"] += b" FDTOVERLAYS /boot/first.dtbo /boot/second.dtbo\n"
        self.files["/boot/first.dtbo"] = overlay()
        self.files["/boot/second.dtbo"] = overlay("disabled")
        m = self.prepare()
        self.assertEqual(m["status"], "prepared", m["blockers"])
        result = subprocess.check_output(["/usr/bin/fdtget", "-t", "s", str(self.output / "files/effective.dtb"), "/lab-node", "status"])
        self.assertEqual(result.strip(), b"disabled")

    def test_missing_and_invalid_overlay(self):
        self.files["/boot/extlinux/extlinux.conf"] += b" FDTOVERLAYS /boot/first.dtbo\n"
        self.blocked("overlay_missing")
        self.files["/boot/first.dtbo"] = b"BPI"
        self.blocked("host_tool")

    def test_overlay_cannot_change_identity(self):
        self.files["/boot/extlinux/extlinux.conf"] += b" FDTOVERLAYS /boot/first.dtbo\n"
        self.files["/boot/first.dtbo"] = dtc('/dts-v1/; /plugin/; / { fragment@0 { target-path = "/"; __overlay__ { model = "BPI"; }; }; };')
        self.blocked("dtb_identity")

    def test_reserved_memory_is_preserved(self):
        self.files["/boot/dtb/" + self.policy["dtb"]] = tree(self.policy, 'reserved-memory { #address-cells = <2>; #size-cells = <2>; ranges; area@100000 { reg = <0 0x100000 0 0x1000>; no-map; }; };')
        m = self.prepare()
        self.assertEqual(m["status"], "prepared", m["blockers"])
        self.assertIn("/reserved-memory/area@100000", m["checks"]["dtb"]["reserved_memory"])
        t = template(m)
        t["dtb_checks"]["dtb"]["reserved_memory"] = {}
        with self.assertRaises(core.Error):
            core.validate_template(m, template=t, artifact_root=self.output)

    def test_kernel_and_initrd_version_mismatch(self):
        self.files["/boot/Image"] = self.files["/boot/Image"].replace(RELEASE.encode(), b"6.18.2-c3")
        self.blocked("kernel_version")
        self.select("bpi-r3")
        self.files["/boot/uInitrd"] = legacy(cpio("6.18.2-c3"))
        self.blocked("initrd_version")

    def test_kernel_config_required_and_architecture_checked(self):
        del self.files["/boot/config-" + RELEASE]
        self.blocked("missing_file")
        self.files["/boot/config-" + RELEASE] = b"CONFIG_RISCV=y\n"
        self.blocked("kernel_config")
        self.files["/boot/config-" + RELEASE] = b'CONFIG_ARM64=y\nCONFIG_CMDLINE="root=/dev/ram"\n'
        self.blocked("kernel_cmdline")

    def test_embedded_kernel_config_checked(self):
        data = bytearray(self.files["/boot/Image"])
        embedded = b"IKCFG_ST" + gzip.compress(self.files["/boot/config-" + RELEASE], mtime=0) + b"IKCFG_ED"
        data[512:512 + len(embedded)] = embedded
        self.files["/boot/Image"] = self.files["/boot/vmlinuz-" + RELEASE] = bytes(data)
        m = self.prepare()
        self.assertEqual(m["status"], "prepared", m["blockers"])
        self.files["/boot/config-" + RELEASE] += b"CONFIG_TEST=y\n"
        self.blocked("kernel_config")

    def test_bytes_check_contract_is_blocked_not_serialized(self):
        with mock.patch.object(core, "kernel_config", return_value=b"BPI"):
            self.blocked("check_contract")

    def test_raw_initrd_and_alias(self):
        self.files["/boot/uInitrd"] = self.files["/boot/initrd.img-" + RELEASE]
        m = self.prepare()
        self.assertEqual(m["status"], "prepared", m["blockers"])
        self.assertEqual(m["initrd_format"], "raw")
        self.files["/boot/initrd.img-" + RELEASE] = b"wrong"
        self.blocked("initrd_alias")

    def test_release_mismatch_and_missing_components(self):
        self.files["/etc/armbian-release"] = self.files["/etc/armbian-release"].replace(b"filogic", b"spacemit")
        m = self.blocked("release_identity")
        self.assertIn("dtb", m["files"])
        del self.files["/boot/uInitrd"]
        self.blocked("missing_file")

    def test_legacy_crc_and_dtb_identity(self):
        self.files["/boot/uInitrd"] = self.files["/boot/uInitrd"][:-1] + b"x"
        self.blocked("legacy_crc")
        self.select("bpi-r3")
        self.files["/boot/dtb/" + self.policy["dtb"]] = tree({**self.policy, "model": "BPI"})
        self.blocked("dtb_identity")

    def test_unknown_reader_error_is_not_absence(self):
        original = self.read
        def read(path):
            if path == "/boot/boot.scr":
                raise ValueError("測試讀取失敗")
            return original(path)
        with mock.patch.object(self, "read", read):
            self.blocked("reader_failed")

    def test_evidence_and_manifest_tamper(self):
        m = self.prepare()
        t = template(m)
        changed = copy.deepcopy(m)
        changed["root_uuid"] = "0" * 36
        with self.assertRaises(core.Error):
            core.validate_template(changed, template=t, artifact_root=self.output)
        (self.output / m["files"]["kernel"]["evidence_path"]).write_bytes(b"changed")
        with self.assertRaises(core.Error):
            core.validate_template(m, template=t, artifact_root=self.output)

    def test_alternate_entry_and_existing_output_rejected(self):
        self.files["/boot/boot.scr"] = b"BPI"
        self.blocked("alternate_entry")
        with self.assertRaises(FileExistsError):
            self.module.prepare(self.read, board=self.board, kernel_release=RELEASE, output=self.output)

    def test_generic_extlinux_entry(self):
        m = core.prepare(self.read, board=self.board, kernel_release=RELEASE, output=self.root / "entry")
        self.assertEqual(m["status"], "prepared", m["blockers"])


class Arm32KernelTests(FixtureCase):
    def setUp(self):
        super().setUp()
        self.select("bpi-forge1")

    def configured(self, command="user_debug=31", flags=b"CONFIG_CMDLINE_EXTEND=y\n"):
        value = (b"CONFIG_ARM=y\nCONFIG_USE_OF=y\nCONFIG_IKCONFIG=y\nCONFIG_KERNEL_LZ4=y\n"
                 + b"CONFIG_CMDLINE=" + json.dumps(command).encode() + b"\n" + flags)
        self.files["/boot/config-" + RELEASE] = value
        return value

    def payload(self, config=None, release=RELEASE):
        return (b"Linux version " + release.encode() + b" (BPI)\0" + bytes(4096)
                + (b"IKCFG_ST" + gzip.compress(config, mtime=0) + b"IKCFG_ED" if config is not None else b""))

    def compressed(self, payload):
        return (subprocess.run(["/usr/bin/lz4", "-l", "-c", "-1"], input=payload,
                               capture_output=True, check=True, timeout=10).stdout
                + struct.pack("<I", len(payload)))

    def image(self, compressed):
        data = bytearray(4096) + compressed
        struct.pack_into("<3I", data, 36, 0x016f2818, 0, len(data))
        self.files["/boot/zImage"] = self.files["/boot/vmlinuz-" + RELEASE] = bytes(data)

    def test_lz4_and_extend_preserve_original_bootargs(self):
        config = self.configured()
        payload = self.payload(config)
        self.image(self.compressed(payload))
        m = self.prepare()
        self.assertEqual(m["status"], "prepared", m["blockers"])
        k = m["checks"]["kernel"]
        self.assertEqual(k["payload_compression"], "lz4")
        self.assertEqual(k["compressed_offset"], 4096)
        self.assertEqual(k["expanded"], core.digest(payload))
        self.assertEqual(k["embedded_config"], core.digest(config))
        self.assertNotIn("kernel_decompression", m["runtime_requirements"])
        self.assertNotIn("user_debug=31", m["bootargs_template"])
        contract = m["runtime_requirements"]["kernel_cmdline"]
        self.assertEqual(contract["effective_bootargs_template"], m["bootargs_template"] + ["user_debug=31"])
        self.assertEqual(contract, m["checks"]["kernel_command_line"])
        t = template(m)
        core.validate_template(m, template=t, artifact_root=self.output)
        del t["runtime_requirements"]["kernel_cmdline"]
        with self.assertRaises(core.Error):
            core.validate_template(m, template=t, artifact_root=self.output)

    def test_lz4_multiple_blocks_and_exact_block(self):
        for size in (8 * 1024**2, 8 * 1024**2 + 4096):
            with self.subTest(size=size):
                payload = b"BPI\0" * (size // 4)
                result, tail = core._lz4_kernel(self.compressed(payload) + b"BPI")
                self.assertEqual(result, payload)
                self.assertEqual(tail, b"BPI")

    def test_lz4_invalid_size_truncation_corruption_and_limit(self):
        compressed = self.compressed(self.payload())
        cases = (compressed[:3], compressed[:6], compressed[:-1],
                 compressed[:-4] + struct.pack("<I", 1),
                 compressed[:4] + struct.pack("<I", 0),
                 compressed[:4] + struct.pack("<I", 0xffffffff),
                 compressed[:4] + struct.pack("<I", 2) + b"\xff\xff" + compressed[-4:])
        for blob in cases:
            with self.subTest(blob=blob[:12]), self.assertRaises(core.Error):
                core._lz4_kernel(blob)
        with mock.patch.object(core.binary, "MAX_EXPANDED", 16), self.assertRaises(core.Error):
            core._lz4_kernel(compressed)

    def test_lz4_absent_library_is_explicit(self):
        with mock.patch.object(core.ctypes.util, "find_library", return_value=None):
            with self.assertRaisesRegex(core.Error, "liblz4"):
                core._lz4_kernel(self.compressed(self.payload()))

    def test_lz4_wrong_version_or_multiple_candidates(self):
        config = self.configured()
        self.image(self.compressed(self.payload(config, "6.1.0-other")))
        self.blocked("kernel_version")
        self.image(self.compressed(self.payload(config)) * 2)
        self.blocked("kernel_version")

    def test_lz4_config_mismatch(self):
        config = self.configured()
        self.image(self.compressed(self.payload(config)))
        self.files["/boot/config-" + RELEASE] += b"CONFIG_TEST=y\n"
        self.blocked("kernel_config")

    def test_lz4_codec_mismatch(self):
        config = self.configured().replace(b"CONFIG_KERNEL_LZ4=y", b"CONFIG_KERNEL_GZIP=y")
        self.files["/boot/config-" + RELEASE] = config
        self.image(self.compressed(self.payload(config)))
        self.blocked("kernel_compression")

    def test_extend_requires_embedded_config_and_dt(self):
        config = self.configured()
        self.image(self.compressed(self.payload()))
        self.blocked("kernel_cmdline")
        config = config.replace(b"CONFIG_USE_OF=y", b"CONFIG_USE_OF=n")
        self.files["/boot/config-" + RELEASE] = config
        self.image(self.compressed(self.payload(config)))
        self.blocked("kernel_cmdline")

    def test_extend_conflicts_force_unknown_and_truncation(self):
        for command in ("root=UUID=" + UUID, "console=ttyS0", "cma=256M", "rdinit=/bin/sh", "ro",
                        "user_debug=1 user_debug=31", "loglevel=7", "${bootargs}", "--", "x=" + "a" * 1020):
            with self.subTest(command=command):
                config = self.configured(command)
                self.image(self.compressed(self.payload(config)))
                self.blocked("kernel_cmdline")
        for flags in (b"CONFIG_CMDLINE_FORCE=y\n", b"CONFIG_CMDLINE_OVERRIDE=y\n", b"",
                      b"CONFIG_CMDLINE_EXTEND=y\nCONFIG_CMDLINE_FROM_BOOTLOADER=y\n"):
            with self.subTest(flags=flags):
                config = self.configured(flags=flags)
                self.image(self.compressed(self.payload(config)))
                self.blocked("kernel_cmdline")

    def test_xz_zimage_still_supported(self):
        config = b'CONFIG_ARM=y\nCONFIG_KERNEL_XZ=y\nCONFIG_CMDLINE=""\n'
        self.files["/boot/config-" + RELEASE] = config
        self.image(lzma.compress(self.payload(config)))
        m = self.prepare()
        self.assertEqual(m["status"], "prepared", m["blockers"])
        self.assertEqual(m["checks"]["kernel"]["payload_compression"], "xz")

    def test_arm64_cmdline_policy_not_relaxed(self):
        for values in (b'CONFIG_CMDLINE="user_debug=31"\nCONFIG_CMDLINE_EXTEND=y\n',
                       b'CONFIG_CMDLINE=""\nCONFIG_CMDLINE_EXTEND=y\n'):
            with self.subTest(values=values), self.assertRaises(core.Error):
                core.kernel_config(b"CONFIG_ARM64=y\n" + values, "arm64")


if __name__ == "__main__":
    unittest.main()

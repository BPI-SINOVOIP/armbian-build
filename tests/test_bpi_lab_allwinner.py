#!/usr/bin/env python3
"""原配組件離線回歸；使用合成映像及真實 libfdt 工具，不操作硬體。"""

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
from tools import bpi_lab_allwinner as lab
from tools import bpi_lab_image as image
from tools import bpi_lab_uboot as uboot


UUID = "12345678-1234-1234-1234-123456789abc"
RELEASE = "6.18.49-current-sunxi"


def kernel_config(arch="arm32"):
    return (("CONFIG_ARM=y\n" if arch == "arm32" else "CONFIG_ARM64=y\n")
            + 'CONFIG_CMA=y\nCONFIG_DMA_CMA=y\nCONFIG_OF_RESERVED_MEM=y\nCONFIG_CMDLINE=""\n'
            + 'CONFIG_PAGE_SHIFT=12\nCONFIG_PAGE_BLOCK_MAX_ORDER=11\nCONFIG_ARCH_FORCE_MAX_ORDER=11\n'
            + 'CONFIG_CMA_AREAS=7\nCONFIG_CMA_ALIGNMENT=8\n').encode()


def kernel_image(arch, release, config, compression="gzip"):
    kernel = bytearray(4096)
    body = b"Linux version " + release.encode() + b" (BPI)\x00"
    if config is not None:
        body += b"IKCFG_ST" + gzip.compress(config, mtime=0) + b"IKCFG_ED"
    if arch == "arm32":
        body += bytes(4096)
        kernel += gzip.compress(body, mtime=0) if compression == "gzip" else lzma.compress(body)
        struct.pack_into("<3I", kernel, 36, 0x016f2818, 0, len(kernel))
    else:
        struct.pack_into("<3Q", kernel, 8, 0x80000, len(kernel), 8)
        kernel[56:60] = b"ARM\x64"
        kernel[128:128 + len(body)] = body
    return bytes(kernel)


def reserved_pool(*, size="0x6000000", extra="", ranges="alloc-ranges = <0x40000000 0x10000000>;", fixed=""):
    return ('reserved-memory { #address-cells = <1>; #size-cells = <1>; ranges; '
            + 'default-pool { compatible = "shared-dma-pool"; reusable; linux,cma-default; '
            + f'size = <{size}>; {ranges} {extra}' + ' }; ' + fixed + ' };')


def legacy(payload, *, script=False, arch="arm32", compression=None):
    if script:
        payload = struct.pack(">II", len(payload), 0) + payload
    compression = (0 if script else 1) if compression is None else compression
    header = struct.pack(">7I4B32s", 0x27051956, 0, 0, len(payload), 0, 0,
                         zlib.crc32(payload), 5, 2 if arch == "arm32" else 22,
                         6 if script else 3, compression, b"BPI")
    return header[:4] + struct.pack(">I", zlib.crc32(header)) + header[8:] + payload


def cpio(entries):
    data = bytearray()
    for name, payload in [*entries, ("TRAILER!!!", b"")]:
        name = name.encode() + b"\x00"
        values = [1, 0o100755, 0, 0, 1, 0, len(payload), 0, 0, 0, 0, len(name), 0]
        data += b"070701" + "".join(f"{n:08x}" for n in values).encode() + name
        data += bytes(-len(data) % 4)
        data += payload
        data += bytes(-len(data) % 4)
    return bytes(data)


def dtc(source):
    result = subprocess.run(["/usr/bin/dtc", "-@", "-I", "dts", "-O", "dtb"],
                            input=source.encode(), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            check=True, timeout=10)
    return result.stdout


def tree(policy, *, reservations="", children=""):
    compatible = ", ".join(json.dumps(v) for v in policy["compatible"])
    return dtc('/dts-v1/; ' + reservations + ' / { model = ' + json.dumps(policy["model"]) + "; compatible = " + compatible
               + '; #address-cells = <1>; #size-cells = <1>; lab: lab-node { status = "disabled"; }; ' + children + ' };')


def overlay(value="okay"):
    return dtc('/dts-v1/; /plugin/; / { fragment@0 { target = <&lab>; __overlay__ { status = "'
               + value + '"; }; }; };')


def image_fixture(board="bpi-m1", compression="gzip", *, embedded_config=True):
    registry = json.loads((lab.ROOT / "docs/evidence/bpi-multiboard-lab-20260917/board-registry.json").read_text())
    row = next(row for row in registry["boards"] if row["board"] == board)
    if board in lab.H618_PROFILES:
        policy = {**lab.H618_PROFILES[board], "overlay_prefix": "sun50i-h616",
                  "dtb": "allwinner/" + lab.H618_PROFILES[board]["dtb"]}
    else:
        policy = json.loads((lab.ROOT / f"config/validation/bananapi-sunxi-{lab.POLICIES[board]}.json").read_text())["boards"][row["artifact_board"]]
    arch = row["architecture"]
    release = RELEASE + ("64" if arch == "arm64" else "")
    script = "boot-sunxi.cmd" if arch == "arm32" else "boot-sun50i-next.cmd"
    cmd = (lab.ROOT / "config/bootscripts" / script).read_bytes()
    kernel = kernel_image(arch, release, kernel_config(arch) if embedded_config else None, compression)
    archive = cpio([("init", b"BPI"), (f"usr/lib/modules/{release}/kernel/test.ko", b"BPI")])
    raw = gzip.compress(archive, mtime=0)
    env = {"rootdev": "UUID=" + UUID, "rootfstype": "ext4", "fdtfile": policy["dtb"],
           "overlay_prefix": policy["overlay_prefix"], "console": "both"}
    directory = "/boot/dtb/allwinner"
    files = {"/etc/armbian-release": (f'BOARD={row["artifact_board"]}\nBOARDFAMILY={row["family"]}\n'
                                     f'KERNEL_IMAGE_TYPE={"zImage" if arch == "arm32" else "Image"}\n'
                                     f'INITRD_ARCH={"arm" if arch == "arm32" else "arm64"}\n').encode(),
             "/boot/armbianEnv.txt": "".join(f"{k}={v}\n" for k, v in env.items()).encode(),
             "/boot/boot.cmd": cmd, "/boot/boot.scr": legacy(cmd, script=True),
             "/boot/" + ("zImage" if arch == "arm32" else "Image"): bytes(kernel),
             f"/boot/vmlinuz-{release}": bytes(kernel), "/boot/uInitrd": legacy(raw, arch=arch),
             f"/boot/initrd.img-{release}": raw, "/boot/.next": b"",
             f"/boot/config-{release}": kernel_config(arch),
             "/boot/dtb/" + policy["dtb"]: tree(policy)}
    if policy["overlay_prefix"] in lab.FIXUPS:
        folder = lab.FIXUPS[policy["overlay_prefix"]][0]
        fixup = (lab.ROOT / f'patch/kernel/archive/sunxi-6.18/{folder}/{policy["overlay_prefix"]}-fixup.scr-cmd').read_bytes()
        files[f'{directory}/overlay/{policy["overlay_prefix"]}-fixup.scr'] = legacy(fixup, script=True, arch=arch)
    return files, row, policy, release


def external_template(manifest):
    """僅離線測資；RAM 位址、媒體與摘要都不是任何實板的核定值。"""
    arch = manifest["arch"]
    addr = 0x81280000 if arch == "arm64" else 0x81200000
    files = {}
    for role, address in (("kernel", addr), ("initrd", 0x84000000), ("dtb", 0x85000000)):
        files[role] = {"address": address, "capacity": 0x200000, "path": "placeholder",
                       "bytes": 64, "sha256": "0" * 64, "format": "placeholder"}
    files["kernel"]["entry"] = addr if arch == "arm64" else 0x81400000
    return {"schema": uboot.SCHEMA, "arch": arch, "kernel_release": manifest["kernel_release"],
            "uboot": {"prompt": "BPI=> ", "version": "U-Boot 2025.01 (BPI)",
                      "address_bits": 32 if arch == "arm32" else 64, "line_limit": 4096,
                      "pairing_sha256": "a" * 64, "qualification_sha256": "b" * 64, "abi": "mainline-v2025.01"},
            "ram": {"banks": [{"start": 0x80000000, "size": 0x10000000}],
                    "reserved": [{"start": 0x88000000, "size": 0x100000}],
                    "kernel_work": {"start": 0x81000000, "size": 0x1000000},
                    "boot": {"start": 0x80000000, "size": 0x8000000}},
            "source": {"type": "mmc", "device": 3, "partition": 2, "partuuid": "12345678-02"},
            "files": files, "fdt_extra": 65536,
            "bootargs": [s.replace("${partuuid}", "12345678-01").replace("${devtype}", "mmc")
                         for s in manifest["bootargs_template"]]}


def cma_template(manifest):
    """純合成空間配置；位於 M1 DT 的搜尋範圍，但不是實板核定值。"""
    template = external_template(manifest)
    for area in template["ram"]["banks"] + template["ram"]["reserved"] + [template["ram"]["kernel_work"], template["ram"]["boot"]]:
        area["start"] -= 0x40000000
    for item in template["files"].values():
        item["address"] -= 0x40000000
        if "entry" in item:
            item["entry"] -= 0x40000000
    template["allwinner_cma"] = {
        "requirements": copy.deepcopy(manifest["checks"]["overlay_application"]["dynamic_cma"]),
        "qualification_sha256": "c" * 64,
        "kernel_config_sha256": manifest["files"]["kernel_config"]["sha256"],
        "effective_dtb_sha256": manifest["files"]["effective_dtb"]["sha256"]}
    return template


class AllwinnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="bpi-allwinner-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.files, self.row, self.policy, self.release = image_fixture()
        self.calls = []
        self.counter = 0

    def reader(self, path):
        self.calls.append(path)
        self.assertTrue(path.startswith("/"))
        try:
            return self.files[path]
        except KeyError as exc:
            raise FileNotFoundError(path) from exc

    def prepare(self, board=None):
        self.counter += 1
        self.output = self.root / str(self.counter)
        result = lab.prepare(self.reader, board=board or self.row["board"],
                             kernel_release=self.release, output=self.output)
        self.assertEqual(result, json.loads((self.output / "manifest.json").read_text()))
        self.assertIs(result["hardware_validated"], False)
        return result

    def env(self, **values):
        old = lab._env(self.files["/boot/armbianEnv.txt"])
        old.update(values)
        self.files["/boot/armbianEnv.txt"] = "".join(f"{key}={value}\n" for key, value in old.items()).encode()

    def blocked(self, code, board=None):
        result = self.prepare(board)
        self.assertEqual(result["status"], "blocked", result)
        self.assertIs(result["ready"], False)
        self.assertIn(code, [item["code"] for item in result["blockers"]], result["blockers"])
        return result

    def test_all_sixteen_boards(self):
        for board in lab.POLICIES:
            with self.subTest(board=board):
                self.files, self.row, self.policy, self.release = image_fixture(board)
                result = self.prepare()
                self.assertEqual(result["status"], "prepared", result["blockers"])
                self.assertIs(result["ready"], True)
                self.assertEqual(result["root_uuid"], UUID)
                self.assertEqual(result["checks"]["kernel"]["kernel_release"], self.release)

    def test_source_artifact_board_alias(self):
        self.assertEqual(self.prepare("bananapi")["status"], "prepared")

    def test_unknown_board_rejected(self):
        for board in ("bpi-r1", "m1", "bpi-m4", "0845"):
            with self.subTest(board=board):
                self.blocked("board", board)

    def test_h618_profiles_are_components_only_and_accept_artifact_alias(self):
        for board in lab.H618_PROFILES:
            with self.subTest(board=board):
                self.files, self.row, self.policy, self.release = image_fixture(board)
                manifest = self.prepare(self.row["artifact_board"])
                self.assertEqual(manifest["status"], "prepared", manifest["blockers"])
                self.assertEqual(manifest["profile"]["script"], "config/bootscripts/boot-sun50i-next.cmd")
                self.assertEqual(manifest["profile"]["family"], "sun50iw9-bpi")
                self.assertFalse(manifest["hardware_validated"])
                self.assertFalse(manifest["ddr_validated"])
                self.assertFalse(manifest["boot_chain_validated"])
                self.assertNotIn("qualification_sha256", manifest)
                self.assertEqual(manifest["checks"]["kernel"]["format"], "Image")
                self.assertFalse(manifest["checks"]["fixup"]["executed"])

    def test_h618_wrong_dtb_identity_and_fixup_pwm_rejected(self):
        self.files, self.row, self.policy, self.release = image_fixture("bpi-m4z-emac")
        path = "/boot/dtb/" + self.policy["dtb"]
        original = self.files[path]
        self.files[path] = tree(lab.H618_PROFILES["bpi-m4z"])
        self.blocked("dtb_identity")
        self.files[path] = original
        self.env(overlays="pwm34")
        self.files["/boot/dtb/allwinner/overlay/sun50i-h616-pwm34.dtbo"] = overlay()
        self.blocked("fixup_pwm")

    def test_h618_dts_source_change_is_not_silently_accepted(self):
        self.files, self.row, self.policy, self.release = image_fixture("bpi-m4b")
        source = lab._Evidence.source

        def changed(evidence, path):
            data = source(evidence, path)
            return data + b"\n" if path.endswith("sun50i-h618-bananapi-m4-berry.dts") else data

        with mock.patch.object(lab._Evidence, "source", new=changed):
            self.blocked("source_mapping")

    def test_h618_active_literals_accept_quote_forms(self):
        for board in lab.H618_PROFILES:
            for quote in ("'", '"', ""):
                with self.subTest(board=board, quote=quote):
                    evidence = mock.Mock()
                    evidence.manifest = {"kernel_release": RELEASE + "64"}

                    def source(path):
                        blob = (lab.ROOT / path).read_bytes()
                        if path.startswith("config/boards/") or path.endswith("sun50iw9-bpi.conf"):
                            for field in ("BOARDFAMILY", "BOOT_FDT_FILE", "OVERLAY_PREFIX", "BOOTSCRIPT"):
                                values, _ = lab._source_literals(blob, (field,))
                                if field in values:
                                    value = values[field].encode()
                                    replacement = field.encode() + b"=" + quote.encode() + value + quote.encode()
                                    for original in (b"'" + value + b"'", b'"' + value + b'"'):
                                        blob = blob.replace(field.encode() + b"=" + original, replacement)
                        return blob

                    evidence.source.side_effect = source
                    profile, _ = lab._profile(evidence, board)
                    self.assertEqual(profile["board"], board)

    def test_h618_inactive_ambiguous_and_wrong_board_sources_rejected(self):
        family_path = "config/sources/families/sun50iw9-bpi.conf"
        family = (lab.ROOT / family_path).read_bytes()
        assignment = b"declare -g BOOTSCRIPT='boot-sun50i-next.cmd:boot.cmd'"
        cases = [
            (family_path, family.replace(assignment, b"# " + assignment)),
            (family_path, family.replace(assignment, b"# " + assignment + b"\n\t\tBOOTSCRIPT='other.cmd:boot.cmd'")),
            (family_path, family.replace(b"current | edge)", b"legacy)")),
            (family_path, family.replace(assignment, b"if false; then\n" + assignment + b"\nfi")),
            (family_path, family + b"\nBOOTSCRIPT='other.cmd:boot.cmd'\n"),
            (family_path, family.replace(assignment, b"declare -g BOOTSCRIPT=\"${OTHER_SCRIPT}\"")),
        ]
        board_path = "config/boards/bananapim4zero.conf"
        original = (lab.ROOT / board_path).read_bytes()
        for field, value in (("BOOT_FDT_FILE", "sun50i-h618-bananapi-m4-zero.dtb"),
                             ("BOARDFAMILY", "sun50iw9-bpi"), ("OVERLAY_PREFIX", "sun50i-h616")):
            assignment = f'{field}="{value}"'.encode()
            for changed in (f"{field}='wrong'", f"{field}=wrong", f"# {field}=\"{value}\"", "",
                            f'{field}="${{OTHER}}"', f'{field}="{value}"; true',
                            f"{field}='{value}'#wrong", f"{field}={value}#wrong",
                            f'{field}="{value}"\n{field}=wrong',
                            f'if false; then\n{field}="{value}"\nfi',
                            f'function deferred() {{\n{field}="{value}"\n}}'):
                cases.append((board_path, original.replace(assignment, changed.encode())))
        for path, changed in cases:
            with self.subTest(path=path, changed=changed):
                evidence = mock.Mock()
                evidence.manifest = {"kernel_release": RELEASE + "64"}
                evidence.source.side_effect = lambda name: changed if name == path else (lab.ROOT / name).read_bytes()
                with self.assertRaises(lab.AllwinnerError) as caught:
                    lab._profile(evidence, "bpi-m4z")
                self.assertEqual(caught.exception.code, "source_mapping")

    def test_h618_only_accepts_active_supported_source_branches(self):
        for board, branch, accepted in (("bpi-m4z", "edge", True), ("bpi-m4z", "legacy", False),
                                        ("bpi-m4z", "vendor", False), ("bpi-m4z-emac", "edge", False)):
            with self.subTest(board=board, branch=branch):
                evidence = mock.Mock()
                evidence.source.side_effect = lambda path: (lab.ROOT / path).read_bytes()
                evidence.manifest = {"kernel_release": "6.18.49-" + branch + "-sunxi64"}
                if accepted:
                    self.assertEqual(lab._profile(evidence, board)[0]["board"], board)
                else:
                    with self.assertRaises(lab.AllwinnerError) as caught:
                        lab._profile(evidence, board)
                    self.assertEqual(caught.exception.code, "source_mapping")

    def test_xz_kernel_version(self):
        self.files, self.row, self.policy, self.release = image_fixture(compression="xz")
        self.assertEqual(self.prepare()["status"], "prepared")

    def test_real_overlay_applied(self):
        self.env(overlays="test")
        self.files["/boot/dtb/allwinner/overlay/sun7i-a20-test.dtbo"] = overlay()
        result = self.prepare()
        self.assertEqual(result["status"], "prepared", result["blockers"])
        effective = self.output / result["files"]["effective_dtb"]["evidence_path"]
        status = subprocess.check_output(["/usr/bin/fdtget", "-t", "s", str(effective), "/lab-node", "status"])
        self.assertEqual(status.strip(), b"okay")
        self.assertTrue(any(cmd["argv"][0] == "/usr/bin/fdtoverlay" and cmd["returncode"] == 0 for cmd in result["commands"]))
        self.assertNotEqual(result["files"]["dtb"]["sha256"], result["files"]["effective_dtb"]["sha256"])

    def test_user_overlay_after_kernel_overlay(self):
        self.env(overlays="first", user_overlays="second")
        self.files["/boot/dtb/allwinner/overlay/sun7i-a20-first.dtbo"] = overlay("okay")
        self.files["/boot/overlay-user/second.dtbo"] = overlay("disabled")
        result = self.prepare()
        self.assertEqual(result["status"], "prepared", result["blockers"])
        status = subprocess.check_output(["/usr/bin/fdtget", "-t", "s", str(self.output / "files/effective.dtb"), "/lab-node", "status"])
        self.assertEqual(status.strip(), b"disabled")

    def test_overlay_failure_keeps_original(self):
        self.env(overlays="bad")
        path = "/boot/dtb/allwinner/overlay/sun7i-a20-bad.dtbo"
        self.files[path] = dtc('/dts-v1/; /plugin/; / { fragment@0 { target = <&absent>; __overlay__ { status = "okay"; }; }; };')
        result = self.blocked("host_tool")
        self.assertIn("dtb", result["files"])
        self.assertEqual((self.output / result["files"]["overlay_00"]["evidence_path"]).read_bytes(), self.files[path])
        self.assertTrue(any(c["returncode"] not in (0, None) for c in result["commands"]))

    def test_missing_overlay_not_silently_skipped(self):
        self.env(overlays="absent")
        self.blocked("missing_file")

    def test_overlay_cannot_change_board_identity(self):
        self.env(overlays="wrong")
        self.files["/boot/dtb/allwinner/overlay/sun7i-a20-wrong.dtbo"] = dtc(
            '/dts-v1/; /plugin/; / { fragment@0 { target-path = "/"; __overlay__ { model = "BPI"; }; }; };')
        self.blocked("dtb_identity")

    def test_board_release_mismatch_keeps_components(self):
        self.files["/etc/armbian-release"] = self.files["/etc/armbian-release"].replace(b"BOARD=bananapi\n", b"BOARD=bananapipro\n")
        result = self.blocked("release_identity")
        self.assertTrue({"kernel", "initrd", "dtb", "boot_cmd", "boot_scr"} <= set(result["files"]))

    def test_release_family_and_arch_mismatch(self):
        for key, replacement in ((b"BOARDFAMILY=sun7i", b"BOARDFAMILY=sun8i"),
                                 (b"INITRD_ARCH=arm", b"INITRD_ARCH=arm64"),
                                 (b"KERNEL_IMAGE_TYPE=zImage", b"KERNEL_IMAGE_TYPE=Image")):
            with self.subTest(key=key):
                saved = self.files["/etc/armbian-release"]
                self.files["/etc/armbian-release"] = saved.replace(key, replacement)
                self.blocked("release_identity")
                self.files["/etc/armbian-release"] = saved

    def test_release_quoted_literals_do_not_execute(self):
        self.files["/etc/armbian-release"] += b'VENDORCOLOR="247;16;0"\nBOARD_NAME="Banana Pi"\nEMPTY=""\n'
        result = self.prepare()
        self.assertEqual(result["status"], "prepared", result["blockers"])
        self.assertEqual(result["original_release"]["VENDORCOLOR"], "247;16;0")
        self.assertEqual(result["original_release"]["EMPTY"], "")

    def test_release_substitution_and_unquoted_operators_rejected(self):
        saved = self.files["/etc/armbian-release"]
        for value in (b'"$(id)"', b'"`id`"', b'"${BOARD}"', b"247;16;0", b'"BPI";id', b"'$(id)'"):
            with self.subTest(value=value):
                self.files["/etc/armbian-release"] = saved + b"VENDORCOLOR=" + value + b"\n"
                self.blocked("env_syntax")

    def test_dtb_identity_mismatch(self):
        wrong = copy.deepcopy(self.policy)
        wrong["compatible"] = ["lemaker,bananapro", "allwinner,sun7i-a20"]
        self.files["/boot/dtb/" + self.policy["dtb"]] = tree(wrong)
        self.blocked("dtb_identity")

    def test_wrong_fdtfile_is_preserved_not_corrected(self):
        self.env(fdtfile="sun7i-a20-bananapro.dtb")
        self.files["/boot/dtb/allwinner/sun7i-a20-bananapro.dtb"] = self.files["/boot/dtb/" + self.policy["dtb"]]
        result = self.blocked("dtb_name")
        self.assertTrue(result["files"]["dtb"]["path"].endswith("bananapro.dtb"))

    def test_arm32_dtb_directory_priority(self):
        expected = self.files["/boot/dtb/" + self.policy["dtb"]]
        path = "/boot/dtb/" + Path(self.policy["dtb"]).name
        self.files[path] = expected
        result = self.prepare()
        self.assertEqual(result["status"], "prepared", result["blockers"])
        self.assertEqual(result["files"]["dtb"]["path"], path)

    def test_a64_dtb_directory_priority_and_fallback(self):
        self.files, self.row, self.policy, self.release = image_fixture("bpi-m64")
        vendor = "/boot/dtb/" + self.policy["dtb"]
        plain = "/boot/dtb/" + Path(self.policy["dtb"]).name
        self.files[plain] = self.files[vendor]
        self.assertEqual(self.prepare()["files"]["dtb"]["path"], vendor)
        del self.files[vendor]
        self.assertEqual(self.prepare()["files"]["dtb"]["path"], plain)

    def test_explicit_fdtdir_controls_overlay_path(self):
        self.env(fdtdir="/boot/dtb-selected", overlays="test")
        base = self.files["/boot/dtb/" + self.policy["dtb"]]
        self.files["/boot/dtb-selected/" + Path(self.policy["dtb"]).name] = base
        self.files["/boot/dtb-selected/overlay/sun7i-a20-test.dtbo"] = overlay()
        self.assertEqual(self.prepare()["status"], "prepared")
        self.assertIn("/boot/dtb-selected/overlay/sun7i-a20-test.dtbo", self.calls)

    def test_unknown_environment_and_parameters_block(self):
        original = self.files["/boot/armbianEnv.txt"]
        for key, value in (("unknown", "1"), ("param_spidev_spi_bus", "0"), ("param_unused", ""),
                           ("bootcmd", "reset"), ("kernel_addr_r", "0x1234")):
            with self.subTest(key=key):
                self.files["/boot/armbianEnv.txt"] = original
                self.env(**{key: value})
                result = self.blocked("unsupported_env")
                self.assertIn("kernel_fixup", result["files"])
                self.assertIn("dtb", result["files"])

    def test_shell_environment_never_executed(self):
        self.env(extraargs="$(touch /tmp/bpi-allwinner-must-not-run)")
        self.blocked("env_syntax")

    def test_duplicate_env_block(self):
        self.files["/boot/armbianEnv.txt"] += b"rootdev=UUID=" + UUID.encode() + b"\n"
        self.blocked("env_syntax")

    def test_unknown_env_value(self):
        for key, value in (("console", "none"), ("bootlogo", "on"), ("verbosity", "9"),
                           ("earlycon", "yes"), ("rootfstype", "mystery")):
            with self.subTest(key=key):
                old = self.files["/boot/armbianEnv.txt"]
                self.env(**{key: value})
                self.blocked("env_value")
                self.files["/boot/armbianEnv.txt"] = old

    def test_no_root_uuid_guess(self):
        self.env(rootdev="/dev/mmcblk0p1")
        self.assertIsNone(self.blocked("rootdev")["root_uuid"])

    def test_extraargs_cannot_override_identity(self):
        self.env(extraargs="root=UUID=" + UUID)
        self.blocked("bootargs")

    def test_traversal_never_reaches_reader(self):
        for key, value in (("fdtdir", "/boot/../etc"), ("fdtfile", "../secret.dtb"),
                           ("overlays", "../../secret"), ("user_overlays", "/etc/shadow"),
                           ("overlay_prefix", "../../etc")):
            with self.subTest(key=key):
                saved = self.files["/boot/armbianEnv.txt"]
                self.env(**{key: value})
                self.prepare()
                self.assertFalse(any(".." in p.split("/") or "shadow" in p for p in self.calls))
                self.files["/boot/armbianEnv.txt"] = saved

    def test_legacy_fex_branch_keeps_script(self):
        del self.files["/boot/.next"]
        self.files["/boot/script.bin"] = b"BPI"
        result = self.blocked("legacy_fex")
        self.assertIn("script_bin", result["files"])

    def test_scripts_must_match_each_other(self):
        self.files["/boot/boot.scr"] = legacy(b"reset\n", script=True)
        self.blocked("boot_script")

    def test_matching_custom_scripts_still_block(self):
        self.files["/boot/boot.cmd"] += b"\nreset\n"
        self.files["/boot/boot.scr"] = legacy(self.files["/boot/boot.cmd"], script=True)
        self.blocked("boot_script")

    def test_crc_header_and_payload_rejection(self):
        for path in ("/boot/boot.scr", "/boot/uInitrd", "/boot/dtb/allwinner/overlay/sun7i-a20-fixup.scr"):
            for offset in (4, -1):
                with self.subTest(path=path, offset=offset):
                    saved = self.files[path]
                    data = bytearray(saved)
                    data[offset] ^= 1
                    self.files[path] = bytes(data)
                    self.blocked("legacy_crc")
                    self.files[path] = saved

    def test_initrd_arch_mismatch(self):
        self.files["/boot/uInitrd"] = legacy(self.files[f"/boot/initrd.img-{self.release}"], arch="arm64")
        self.blocked("legacy_type")

    def test_fixup_wrong_architecture_rejected(self):
        for board in ("bpi-m1", "bpi-m64"):
            with self.subTest(board=board):
                self.files, self.row, self.policy, self.release = image_fixture(board)
                path = f'/boot/dtb/allwinner/overlay/{self.policy["overlay_prefix"]}-fixup.scr'
                wrong_arch = "arm64" if self.row["architecture"] == "arm32" else "arm32"
                self.files[path] = legacy(self.files[path][72:], script=True, arch=wrong_arch)
                self.blocked("legacy_type")

    def test_boot_script_still_requires_arm_header_on_a64(self):
        self.files, self.row, self.policy, self.release = image_fixture("bpi-m64")
        self.files["/boot/boot.scr"] = legacy(self.files["/boot/boot.cmd"], script=True, arch="arm64")
        self.blocked("legacy_type")

    def test_arm64_fixup_header_and_payload_crc_rejected(self):
        self.files, self.row, self.policy, self.release = image_fixture("bpi-m64")
        path = "/boot/dtb/allwinner/overlay/sun50i-a64-fixup.scr"
        saved = self.files[path]
        self.assertEqual(saved[29], 22)
        self.assertEqual(self.files["/boot/boot.scr"][29], 2)
        for offset in (4, -1):
            with self.subTest(offset=offset):
                data = bytearray(saved)
                data[offset] ^= 1
                self.files[path] = bytes(data)
                self.blocked("legacy_crc")

    def test_custom_fixup_even_empty_is_blocked(self):
        for blob in (b"", legacy(b"reset\n", script=True)):
            with self.subTest(size=len(blob)):
                self.files["/boot/fixup.scr"] = blob
                result = self.blocked("custom_fixup")
                self.assertIn("user_fixup", result["files"])

    def test_unknown_kernel_fixup_blocked(self):
        self.files["/boot/dtb/allwinner/overlay/sun7i-a20-fixup.scr"] = legacy(b"reset\n", script=True)
        self.blocked("fixup_unknown")

    def test_h3_pwm_fixup_not_ignored(self):
        self.files, self.row, self.policy, self.release = image_fixture("bpi-m2p")
        self.env(overlays="pwm")
        self.files["/boot/dtb/allwinner/overlay/sun8i-h3-pwm.dtbo"] = overlay()
        self.blocked("fixup_pwm")

    def test_kernel_format_and_version(self):
        original = self.files["/boot/zImage"]
        data = bytearray(original)
        data[36] ^= 1
        self.files["/boot/zImage"] = bytes(data)
        self.blocked("kernel_format")
        body = gzip.compress(b"Linux version 1.0-wrong (BPI)\x00", mtime=0)
        data = bytearray(original[:4096] + body)
        struct.pack_into("<3I", data, 36, 0x016f2818, 0, len(data))
        self.files["/boot/zImage"] = bytes(data)
        self.files[f"/boot/vmlinuz-{self.release}"] = bytes(data)
        self.blocked("kernel_version")

    def test_versioned_kernel_must_equal_loaded_kernel(self):
        self.files[f"/boot/vmlinuz-{self.release}"] += b"BPI"
        self.blocked("kernel_alias")

    def test_versioned_initrd_must_equal_payload(self):
        self.files[f"/boot/initrd.img-{self.release}"] += b"BPI"
        self.blocked("initrd_alias")

    def test_initramfs_mismatched_module_version(self):
        raw = gzip.compress(cpio([("init", b"BPI"), ("lib/modules/1.0-wrong/kernel/a.ko", b"BPI")]), mtime=0)
        self.files[f"/boot/initrd.img-{self.release}"] = raw
        self.files["/boot/uInitrd"] = legacy(raw)
        self.blocked("initrd_version")

    def test_early_cpio_and_compressed_main_archive(self):
        raw = cpio([("early", b"BPI")]) + bytes(64) + self.files[f"/boot/initrd.img-{self.release}"]
        self.files[f"/boot/initrd.img-{self.release}"] = raw
        self.files["/boot/uInitrd"] = legacy(raw)
        self.assertEqual(self.prepare()["status"], "prepared")

    def test_unknown_initramfs_format_blocked(self):
        raw = b"\x28\xb5\x2f\xfd" + bytes(100)
        self.files[f"/boot/initrd.img-{self.release}"] = raw
        self.files["/boot/uInitrd"] = legacy(raw)
        self.blocked("compression")

    def test_expansion_limit(self):
        with mock.patch.object(lab, "MAX_EXPANDED", 64):
            self.blocked("decompression")

    def test_missing_required_file_keeps_other_components(self):
        del self.files["/boot/uInitrd"]
        result = self.blocked("missing_file")
        self.assertIn("kernel", result["files"])
        self.assertIn("dtb", result["files"])

    def test_callback_error_is_not_absence(self):
        original = self.reader
        def reader(path):
            if path == "/boot/fixup.scr":
                raise OSError("讀取失敗")
            return original(path)
        with mock.patch.object(self, "reader", reader):
            self.blocked("reader_failed")

    def test_symlink_resolution_delegated_to_callback(self):
        original = self.reader
        links = {"/boot/zImage": f"/boot/vmlinuz-{self.release}",
                 "/boot/uInitrd": f"/boot/uInitrd-{self.release}"}
        self.files[links["/boot/uInitrd"]] = self.files["/boot/uInitrd"]
        del self.files["/boot/zImage"]
        del self.files["/boot/uInitrd"]
        with mock.patch.object(self, "reader", lambda path: original(links.get(path, path))):
            self.assertEqual(self.prepare()["status"], "prepared")

    def test_existing_output_and_symlink_parent_rejected(self):
        with self.assertRaises(FileExistsError):
            lab.prepare(self.reader, board="bpi-m1", kernel_release=self.release, output=self.root)
        link = self.root / "link"
        link.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(OSError):
            lab.prepare(self.reader, board="bpi-m1", kernel_release=self.release, output=link / "new")

    def test_uboot_bridge_for_both_architectures(self):
        for board in ("bpi-m1", "bpi-m64"):
            with self.subTest(board=board):
                self.files, self.row, self.policy, self.release = image_fixture(board)
                manifest = self.prepare()
                template = external_template(manifest)
                original = copy.deepcopy(template)
                with mock.patch.object(uboot, "validate_config", wraps=uboot.validate_config) as config_check, \
                        mock.patch.object(uboot, "validate_artifacts", wraps=uboot.validate_artifacts) as artifact_check:
                    config = lab.build_uboot_config(manifest, template=template, artifact_root=self.output)
                    self.assertTrue(config_check.called)
                    self.assertTrue(artifact_check.called)
                self.assertEqual(template, original)
                self.assertEqual(config["files"]["dtb"]["path"], "files/effective.dtb")
                self.assertEqual(uboot.validate_artifacts(config, self.output)["hardware_verified"], False)

    def test_blocked_manifest_cannot_bind(self):
        self.env(unknown="1")
        manifest = self.prepare()
        with self.assertRaises(lab.AllwinnerError):
            lab.build_uboot_config(manifest, template={}, artifact_root=self.output)

    def test_manifest_and_original_evidence_tamper_rejected(self):
        manifest = self.prepare()
        template = external_template(manifest)
        altered = copy.deepcopy(manifest)
        altered["root_uuid"] = "0" * 36
        with self.assertRaisesRegex(lab.AllwinnerError, "manifest"):
            lab.build_uboot_config(altered, template=template, artifact_root=self.output)
        path = self.output / manifest["files"]["boot_cmd"]["evidence_path"]
        path.write_bytes(path.read_bytes() + b"BPI")
        with self.assertRaisesRegex(lab.AllwinnerError, "變動"):
            lab.build_uboot_config(manifest, template=template, artifact_root=self.output)

    def test_external_template_identity_and_bootargs_mismatch(self):
        manifest = self.prepare()
        template = external_template(manifest)
        for field, value in (("arch", "arm64"), ("kernel_release", "wrong"), ("bootargs", ["root=/dev/mmcblk0p1"])):
            with self.subTest(field=field):
                bad = copy.deepcopy(template)
                bad[field] = value
                with self.assertRaises(lab.AllwinnerError):
                    lab.build_uboot_config(manifest, template=bad, artifact_root=self.output)

    def test_external_ram_overlap_rejected(self):
        manifest = self.prepare()
        template = external_template(manifest)
        template["files"]["initrd"]["address"] = template["files"]["kernel"]["address"]
        with self.assertRaises(uboot.UBootError):
            lab.build_uboot_config(manifest, template=template, artifact_root=self.output)

    def test_dtb_memreserve_requires_full_external_ram_coverage(self):
        path = "/boot/dtb/" + self.policy["dtb"]
        self.files[path] = tree(self.policy, reservations="/memreserve/ 0x88000000 0x2000;")
        self.env(overlays="test")
        self.files["/boot/dtb/allwinner/overlay/sun7i-a20-test.dtbo"] = overlay()
        manifest = self.prepare()
        self.assertEqual(manifest["status"], "prepared", manifest["blockers"])
        for stage in ("dtb", "overlay_application"):
            self.assertEqual(manifest["checks"][stage]["memreserve"], [{"start": 0x88000000, "size": 0x2000}])
        template = external_template(manifest)
        lab.build_uboot_config(manifest, template=template, artifact_root=self.output)
        for area in ({"start": 0x88000000, "size": 0x1000}, {"start": 0x88100000, "size": 0x1000}):
            with self.subTest(area=area):
                bad = copy.deepcopy(template)
                bad["ram"]["reserved"] = [area]
                with self.assertRaisesRegex(lab.AllwinnerError, "ram.reserved"):
                    lab.build_uboot_config(manifest, template=bad, artifact_root=self.output)

    def test_dtb_memreserve_malformed_tables_block(self):
        path = "/boot/dtb/" + self.policy["dtb"]
        original = tree(self.policy, reservations="/memreserve/ 0x88000000 0x2000;")
        reserve_offset = struct.unpack_from(">I", original, 16)[0]
        for mode in ("overflow", "zero_size", "unterminated"):
            with self.subTest(mode=mode):
                data = bytearray(original)
                if mode == "overflow":
                    struct.pack_into(">QQ", data, reserve_offset, (1 << 64) - 1, 2)
                elif mode == "zero_size":
                    struct.pack_into(">QQ", data, reserve_offset, 0x88000000, 0)
                else:
                    struct.pack_into(">QQ", data, reserve_offset + 16, 0x88002000, 0x1000)
                self.files[path] = bytes(data)
                self.blocked("dtb_memreserve")

    def test_reserved_memory_children_block_base_and_effective_dtb(self):
        path = "/boot/dtb/" + self.policy["dtb"]
        self.files[path] = tree(self.policy, children="reserved-memory {};")
        self.assertEqual(self.prepare()["status"], "prepared")
        child = 'firmware@88000000 { reg = <0x88000000 0x2000>; no-map; };'
        self.files[path] = tree(self.policy, children="reserved-memory { " + child + " };")
        self.blocked("reserved_memory")
        self.files[path] = tree(self.policy)
        self.env(overlays="reserved")
        self.files["/boot/dtb/allwinner/overlay/sun7i-a20-reserved.dtbo"] = dtc(
            '/dts-v1/; /plugin/; / { fragment@0 { target-path = "/"; __overlay__ {'
            ' reserved-memory { cma { compatible = "shared-dma-pool"; size = <0x2000>; }; }; }; }; };')
        result = self.blocked("reserved_memory")
        self.assertIn("effective_dtb", result["files"])
        self.assertTrue(any(c["argv"][0] == "/usr/bin/fdtoverlay" and c["returncode"] == 0 for c in result["commands"]))

    def test_original_kernel_config_is_required_and_preserved(self):
        path = f"/boot/config-{self.release}"
        manifest = self.prepare()
        self.assertIn(path, self.calls)
        self.assertEqual(manifest["files"]["kernel_config"]["sha256"], lab.digest(self.files[path])["sha256"])
        del self.files[path]
        result = self.blocked("missing_file")
        self.assertIn({"path": path, "status": "missing"}, result["reads"])

    def test_snapshot_unqueried_config_is_failed_not_missing(self):
        reader = object.__new__(image.SnapshotReader)
        reader.original = {"files": {}}
        reader.missing = set()
        reader.check = lambda: None
        path = f"/boot/config-{self.release}"
        original = self.reader

        def replay(name):
            return reader.read_file(name) if name == path else original(name)

        with mock.patch.object(self, "reader", side_effect=replay):
            result = self.blocked("reader_failed")
        self.assertIn({"path": path, "status": "failed"}, result["reads"])
        self.assertNotIn({"path": path, "status": "missing"}, result["reads"])
        self.assertNotIn("missing_file", [item["code"] for item in result["blockers"]])

    def test_kernel_embedded_config_must_match_original_file(self):
        config = kernel_config()
        body = b"Linux version " + self.release.encode() + b"\0IKCFG_ST" + gzip.compress(config, mtime=0) + b"IKCFG_ED"
        kernel = bytearray(4096) + gzip.compress(body, mtime=0)
        struct.pack_into("<3I", kernel, 36, 0x016f2818, 0, len(kernel))
        self.files["/boot/zImage"] = self.files[f"/boot/vmlinuz-{self.release}"] = bytes(kernel)
        self.assertEqual(self.prepare()["checks"]["kernel"]["embedded_config"], lab.digest(config))
        self.files[f"/boot/config-{self.release}"] += b"CONFIG_HIGHMEM=y\n"
        self.blocked("kernel_config")

    def test_shared_kernel_helper_without_config_is_explicitly_unverified(self):
        config = kernel_config()
        body = b"Linux version " + self.release.encode() + b"\0IKCFG_ST" + gzip.compress(config, mtime=0) + b"IKCFG_ED"
        kernel = bytearray(4096) + gzip.compress(body, mtime=0)
        struct.pack_into("<3I", kernel, 36, 0x016f2818, 0, len(kernel))
        checked = lab._kernel(bytes(kernel), "arm32", self.release)
        self.assertFalse(checked["kernel_config_verified"])
        self.assertNotIn("embedded_config", checked)
        self.assertTrue(lab._kernel(bytes(kernel), "arm32", self.release, config)["kernel_config_verified"])
        raw = bytearray(4096)
        struct.pack_into("<3Q", raw, 8, 0x80000, len(raw), 8)
        raw[56:60] = b"ARM\x64"
        raw[128:128 + len(body)] = body
        self.assertFalse(lab._kernel(bytes(raw), "arm64", self.release)["kernel_config_verified"])
        self.files["/boot/zImage"] = self.files[f"/boot/vmlinuz-{self.release}"] = bytes(kernel)
        del self.files[f"/boot/config-{self.release}"]
        self.blocked("missing_file")

    def test_m1_dynamic_cma_without_alignment(self):
        path = "/boot/dtb/" + self.policy["dtb"]
        self.files[path] = tree(self.policy, children=reserved_pool())
        manifest = self.prepare()
        self.assertEqual(manifest["status"], "prepared", manifest["blockers"])
        checked = manifest["checks"]["overlay_application"]
        self.assertEqual(checked["reserved_memory"], [])
        self.assertEqual(checked["memreserve"], [])
        requirement, = checked["dynamic_cma"]
        self.assertEqual(requirement["node"], "/reserved-memory/default-pool")
        self.assertEqual(requirement["size"], 96 * 1024**2)
        self.assertEqual(requirement["alignment"], 8 * 1024**2)
        self.assertIsNone(requirement["declared_alignment"])
        self.assertEqual(requirement["alloc_ranges"], [{"start": 0x40000000, "size": 0x10000000}])
        template = cma_template(manifest)
        before = copy.deepcopy(template)
        config = lab.build_uboot_config(manifest, template=template, artifact_root=self.output)
        self.assertEqual(template, before)
        self.assertNotIn("allwinner_cma", config)
        self.assertEqual(config["ram"], template["ram"])
        self.assertEqual((self.output / "files/effective.dtb").read_bytes(), self.files[path])

    def test_cma_approval_missing_stale_or_extra(self):
        self.files["/boot/dtb/" + self.policy["dtb"]] = tree(self.policy, children=reserved_pool())
        manifest = self.prepare()
        template = cma_template(manifest)
        for field in (None, "requirements", "qualification_sha256", "kernel_config_sha256", "effective_dtb_sha256"):
            with self.subTest(field=field):
                bad = copy.deepcopy(template)
                if field is None:
                    del bad["allwinner_cma"]
                else:
                    bad["allwinner_cma"][field] = [] if field == "requirements" else "x" * 64
                with self.assertRaises(lab.AllwinnerError) as caught:
                    lab.build_uboot_config(manifest, template=bad, artifact_root=self.output)
                self.assertEqual(caught.exception.code, "cma_approval")
        self.files["/boot/dtb/" + self.policy["dtb"]] = tree(self.policy)
        manifest = self.prepare()
        with self.assertRaises(lab.AllwinnerError) as caught:
            lab.build_uboot_config(manifest, template=cma_template(manifest), artifact_root=self.output)
        self.assertEqual(caught.exception.code, "cma_approval")

    def test_cma_wrong_ram_family_and_full_range_reservation_rejected(self):
        self.files["/boot/dtb/" + self.policy["dtb"]] = tree(self.policy, children=reserved_pool())
        manifest = self.prepare()
        bad = external_template(manifest)
        bad["allwinner_cma"] = cma_template(manifest)["allwinner_cma"]
        with self.assertRaises(lab.AllwinnerError) as caught:
            lab.build_uboot_config(manifest, template=bad, artifact_root=self.output)
        self.assertEqual(caught.exception.code, "cma_space")
        bad = cma_template(manifest)
        bad["ram"]["reserved"] = [{"start": 0x40000000, "size": 0x10000000}]
        with self.assertRaises(uboot.UBootError):
            lab.build_uboot_config(manifest, template=bad, artifact_root=self.output)

    def test_cma_fixed_reservation_requires_full_coverage(self):
        fixed = 'firmware@48000000 { reg = <0x48000000 0x2000>; no-map; };'
        self.files["/boot/dtb/" + self.policy["dtb"]] = tree(self.policy, children=reserved_pool(fixed=fixed))
        manifest = self.prepare()
        self.assertEqual(manifest["status"], "prepared", manifest["blockers"])
        template = cma_template(manifest)
        lab.build_uboot_config(manifest, template=template, artifact_root=self.output)
        template["ram"]["reserved"][0]["size"] = 0x1000
        with self.assertRaisesRegex(lab.AllwinnerError, "ram.reserved"):
            lab.build_uboot_config(manifest, template=template, artifact_root=self.output)

    def test_cma_bootargs_cannot_override_kernel_semantics(self):
        self.files["/boot/dtb/" + self.policy["dtb"]] = tree(self.policy, children=reserved_pool())
        for arg in ("cma=0", "mem=256M", "memmap=16M@0x40000000", "crashkernel=64M", "movablecore=128M"):
            with self.subTest(arg=arg):
                self.env(extraargs=arg)
                manifest = self.prepare()
                self.assertEqual(manifest["status"], "prepared", manifest["blockers"])
                with self.assertRaises(lab.AllwinnerError) as caught:
                    lab.build_uboot_config(manifest, template=cma_template(manifest), artifact_root=self.output)
                self.assertEqual(caught.exception.code, "cma_bootargs")

    def test_cma_overlay_requirements_are_effective_not_original(self):
        self.files["/boot/dtb/" + self.policy["dtb"]] = tree(self.policy, children=reserved_pool())
        self.env(overlays="resize")
        self.files["/boot/dtb/allwinner/overlay/sun7i-a20-resize.dtbo"] = dtc(
            '/dts-v1/; /plugin/; / { fragment@0 { target-path = "/reserved-memory/default-pool"; '
            '__overlay__ { size = <0x4000000>; }; }; };')
        manifest = self.prepare()
        self.assertEqual(manifest["status"], "prepared", manifest["blockers"])
        template = cma_template(manifest)
        lab.build_uboot_config(manifest, template=template, artifact_root=self.output)
        template["allwinner_cma"]["requirements"] = manifest["checks"]["dtb"]["dynamic_cma"]
        with self.assertRaises(lab.AllwinnerError) as caught:
            lab.build_uboot_config(manifest, template=template, artifact_root=self.output)
        self.assertEqual(caught.exception.code, "cma_approval")

    def test_cma_unknown_kernel_features_and_missing_original_config_block(self):
        self.files["/boot/dtb/" + self.policy["dtb"]] = tree(self.policy, children=reserved_pool())
        path = f"/boot/config-{self.release}"
        for before, after in ((b"CONFIG_CMA=y", b"CONFIG_CMA=n"),
                              (b"CONFIG_DMA_CMA=y", b"CONFIG_DMA_CMA=n"),
                              (b"CONFIG_PAGE_BLOCK_MAX_ORDER=11\n", b""),
                              (b"CONFIG_CMDLINE=\"\"", b"CONFIG_CMDLINE=\"cma=0\"")):
            with self.subTest(before=before):
                self.files[path] = kernel_config().replace(before, after)
                self.files["/boot/zImage"] = self.files[f"/boot/vmlinuz-{self.release}"] = kernel_image(
                    "arm32", self.release, self.files[path])
                self.blocked("cma_bootargs" if b"CMDLINE" in before else "cma_kernel")
        del self.files[path]
        self.blocked("kernel_config")

    def test_cma_malformed_nodes_rejected(self):
        path = "/boot/dtb/" + self.policy["dtb"]
        cases = [(reserved_pool(extra="no-map;"), "reserved_memory"),
                 (reserved_pool(extra="alignment = <3>;"), "cma_alignment"),
                 (reserved_pool(size="0x6000001"), "cma_alignment"),
                 (reserved_pool(extra="alignment = <0 0x800000>;"), "reserved_memory"),
                 (reserved_pool(ranges="alloc-ranges = <0xf0000000 0x20000000>;"), "reserved_memory"),
                 (reserved_pool(ranges="alloc-ranges;"), "reserved_memory"),
                 (reserved_pool().replace("reusable;", "reusable = <1>;"), "reserved_memory"),
                 (reserved_pool().replace("linux,cma-default;", ""), "reserved_memory"),
                 (reserved_pool(extra='status = "disabled";'), "reserved_memory"),
                 (reserved_pool(extra="reg = <0x40000000 0x6000000>;"), "reserved_memory"),
                 (reserved_pool().replace("#address-cells = <1>", "#address-cells = <2>"), "reserved_memory")]
        for children, code in cases:
            with self.subTest(children=children):
                self.files[path] = tree(self.policy, children=children)
                self.blocked(code)

    def test_cma_kernel_config_tamper_rejected(self):
        self.files["/boot/dtb/" + self.policy["dtb"]] = tree(self.policy, children=reserved_pool())
        manifest = self.prepare()
        path = self.output / manifest["files"]["kernel_config"]["evidence_path"]
        path.write_bytes(path.read_bytes().replace(b"ORDER=11", b"ORDER=10"))
        with self.assertRaisesRegex(lab.AllwinnerError, "變動"):
            lab.build_uboot_config(manifest, template=cma_template(manifest), artifact_root=self.output)

    def test_cma_requires_embedded_config_for_prepare(self):
        for board in ("bpi-m1", "bpi-m64"):
            with self.subTest(board=board):
                self.files, self.row, self.policy, self.release = image_fixture(board, embedded_config=False)
                ordinary = self.prepare()
                self.assertEqual(ordinary["status"], "prepared", ordinary["blockers"])
                self.assertFalse(ordinary["checks"]["kernel"]["kernel_config_verified"])
                lab.build_uboot_config(ordinary, template=external_template(ordinary), artifact_root=self.output)
                self.files["/boot/dtb/" + self.policy["dtb"]] = tree(self.policy, children=reserved_pool())
                result = self.blocked("kernel_config")
                self.assertFalse(result["checks"]["kernel"]["kernel_config_verified"])

    def test_cma_coordinated_config_and_manifest_forgery_rejected(self):
        self.files["/boot/dtb/" + self.policy["dtb"]] = tree(self.policy, children=reserved_pool())
        manifest = self.prepare()
        original_kernel = manifest["files"]["kernel"]["sha256"]
        config = kernel_config().replace(b"CONFIG_PAGE_BLOCK_MAX_ORDER=11", b"CONFIG_PAGE_BLOCK_MAX_ORDER=9")
        record = manifest["files"]["kernel_config"]
        (self.output / record["evidence_path"]).write_bytes(config)
        record.update(lab.digest(config))
        manifest["checks"]["kernel_config"] = lab.cma.config_evidence(config, "arm32")
        manifest["checks"]["kernel"]["embedded_config"] = lab.digest(config)
        for stage in ("dtb", "overlay_application"):
            requirement = manifest["checks"][stage]["dynamic_cma"][0]
            requirement["alignment"] = 2 * 1024**2
            requirement["kernel"] = lab.cma.kernel_policy(
                manifest["checks"]["kernel_config"]["values"], self.release, record["sha256"])
        (self.output / "manifest.json").write_bytes(lab._json(manifest))
        with self.assertRaises(lab.AllwinnerError) as caught:
            lab.build_uboot_config(manifest, template=cma_template(manifest), artifact_root=self.output)
        self.assertEqual(caught.exception.code, "kernel_config")
        self.assertEqual(manifest["files"]["kernel"]["sha256"], original_kernel)

    def test_cma_binding_rechecks_config_verification_and_kernel_version(self):
        self.files["/boot/dtb/" + self.policy["dtb"]] = tree(self.policy, children=reserved_pool())
        for release, embedded, forged_verified in ((self.release, None, True), (self.release, None, False),
                                                   ("6.18.48-current-sunxi", kernel_config(), True)):
            with self.subTest(release=release, embedded=embedded, forged_verified=forged_verified):
                manifest = self.prepare()
                kernel = kernel_image("arm32", release, embedded)
                for role in ("kernel", "kernel_versioned"):
                    record = manifest["files"][role]
                    (self.output / record["evidence_path"]).write_bytes(kernel)
                    record.update(lab.digest(kernel))
                manifest["checks"]["kernel"] = lab._kernel(kernel, "arm32", release, kernel_config())
                manifest["checks"]["kernel"]["kernel_release"] = self.release
                manifest["checks"]["kernel"]["kernel_config_verified"] = forged_verified
                (self.output / "manifest.json").write_bytes(lab._json(manifest))
                with self.assertRaises(lab.AllwinnerError) as caught:
                    lab.build_uboot_config(manifest, template=cma_template(manifest), artifact_root=self.output)
                expected = "kernel_version" if release != self.release else "memory_evidence" if forged_verified else "kernel_config"
                self.assertEqual(caught.exception.code, expected)

    def test_cma_manifest_claims_are_reparsed_before_binding(self):
        self.files["/boot/dtb/" + self.policy["dtb"]] = tree(self.policy, children=reserved_pool())
        original = self.prepare()
        for stage in ("dtb", "overlay_application", "kernel_config"):
            with self.subTest(stage=stage):
                manifest = copy.deepcopy(original)
                if stage == "kernel_config":
                    manifest["checks"][stage]["values"]["CONFIG_CMA"] = "n"
                else:
                    manifest["checks"][stage]["dynamic_cma"][0]["size"] = 0x800000
                (self.output / "manifest.json").write_bytes(lab._json(manifest))
                with self.assertRaises(lab.AllwinnerError) as caught:
                    lab.build_uboot_config(manifest, template=cma_template(manifest), artifact_root=self.output)
                self.assertEqual(caught.exception.code, "memory_evidence")

    def test_old_manifest_missing_memory_fields_requires_replay(self):
        original = self.prepare()
        for key in ("memreserve", "reserved_memory", "dynamic_cma"):
            with self.subTest(key=key):
                manifest = copy.deepcopy(original)
                del manifest["checks"]["dtb"][key]
                (self.output / "manifest.json").write_bytes(lab._json(manifest))
                with self.assertRaises(lab.AllwinnerError):
                    lab.build_uboot_config(manifest, template=external_template(manifest), artifact_root=self.output)


if __name__ == "__main__":
    unittest.main()

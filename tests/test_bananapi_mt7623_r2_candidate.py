#!/usr/bin/env python3
"""BPI-R2 MT7623 候選來源與守門回歸測試。"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tarfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config/validation/bananapi-mt7623-r2-current.json"
BOARD_PATH = ROOT / "config/boards/bananapir2.csc"
BOOT_SCRIPT = ROOT / "config/bootscripts/boot-mt7623.cmd"
UBOOT_PATCH = ROOT / "patch/u-boot/v2026.07/board_bananapir2/enable-boot-from-ext4.patch"
GENERIC_VERIFIER = ROOT / "tools/verify-bananapi-sunxi-candidates.sh"
SOURCE_POLICY = (
    ROOT
    / "docs/evidence/bananapi-family-optimization"
    / "D-mt7623-r2-source-policy-20260827.md"
)


class BananaPiMT7623R2CandidateTests(unittest.TestCase):
    """驗證 R2 的固定來源、啟動載荷與安全網路預設。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.config = json.loads(CONFIG_PATH.read_text())
        cls.policy = cls.config["boards"]["bananapir2"]

    def shell_config(self, branch: str) -> dict[str, str]:
        harness = r'''
set -eu
SRC=$1
BRANCH=$2
source "$SRC/config/boards/bananapir2.csc"
source "$SRC/config/sources/families/mt7623.conf"
before_fdt=$BOOT_FDT_FILE
before_kernel=$KERNEL_MAJOR_MINOR
for hook in $(compgen -A function "post_family_config_branch_${BRANCH}__"); do
    "$hook"
done
printf '%s\0' "$before_fdt" "$before_kernel" "$BOOT_FDT_FILE" \
    "$KERNEL_MAJOR_MINOR" "${KERNELSOURCE-}" "${KERNELBRANCH-}" \
    "$BOOTBRANCH" "${ARMBIAN_FIRMWARE_GIT_REF-}" "$BOOTSCRIPT"
'''
        result = subprocess.run(
            ["bash", "--noprofile", "--norc", "-c", harness, "r2", str(ROOT), branch],
            env={"PATH": os.defpath, "LC_ALL": "C"},
            capture_output=True, text=True, check=True, timeout=10,
        )
        fields = (
            "before_fdt", "before_kernel", "fdtfile", "kernel", "source",
            "branch", "uboot", "firmware", "bootscript",
        )
        values = result.stdout.split("\0")
        self.assertEqual(values.pop(), "")
        self.assertEqual(len(values), len(fields))
        return dict(zip(fields, values))

    def boot_loads(self, fdtfile: str) -> list[str]:
        # 僅執行已知腳本的控制流程；載入、環境匯入與引導命令均為無硬體副作用的替身。
        harness = r'''
set -eu
bootenv=$(printf 'rootdev=UUID=48136f54-b977-4c35-a2a9-25267664570a\nfdtfile=%s' "$1")
devnum=1
mmcpart=1
kernel_addr_r=0x82000000
fdt_addr_r=0x86000000
ramdisk_addr_r=0x86080000
loaded=no
loads=()
setenv() { local key=$1; shift; printf -v "$key" '%s' "$*"; }
part() {
    [[ "$*" == 'uuid mmc 1:1 rootuuid' ]] || exit 90
    rootuuid=6f9b5821-01
}
test() {
    if [[ "$1" == -e ]]; then
        [[ "$*" == '-e mmc 1:1 boot/armbianEnv.txt' ]]
    else
        builtin test "$@"
    fi
}
load() {
    [[ "$*" == 'mmc 1:1 0x82000000 boot/armbianEnv.txt' ]] || exit 91
    loaded=yes
    filesize=${#bootenv}
}
env() {
    [[ "$loaded" == yes && "$*" == "import -t 0x82000000 $filesize" ]] || exit 92
    local key value
    while IFS='=' read -r key value; do setenv "$key" "$value"; done <<< "$bootenv"
}
ext4load() {
    [[ "$#" == 4 && "$1 $2" == 'mmc 1:1' ]] || exit 93
    loads+=("$3 $4")
}
bootz() {
    [[ "$*" == '0x82000000 0x86080000 0x86000000' ]] || exit 94
    printf 'loaded:%s\n' "${loads[@]}"
}
source "$2"
'''
        result = subprocess.run(
            ["bash", "--noprofile", "--norc", "-c", harness, "r2", fdtfile, str(BOOT_SCRIPT)],
            env={"PATH": os.defpath, "LC_ALL": "C"},
            capture_output=True, text=True, check=True, timeout=10,
        )
        return [line.removeprefix("loaded:") for line in result.stdout.splitlines()
                if line.startswith("loaded:")]

    def test_current_effective_shell_config_and_boot_path(self) -> None:
        config = self.shell_config("current")
        self.assertEqual(config["before_fdt"], "mediatek/mt7623n-bananapi-bpi-r2")
        self.assertEqual(config["fdtfile"], self.policy["dtb"])
        self.assertEqual(config["kernel"], "6.6")
        self.assertEqual(config["source"], self.config["linux_source"])
        self.assertEqual(config["branch"], self.config["linux_ref"])
        self.assertEqual(config["uboot"], self.policy["uboot_git_ref"])
        self.assertEqual(config["firmware"], "commit:" + self.config["firmware_commit"])
        self.assertEqual(config["bootscript"], "boot-mt7623.cmd:boot.cmd")
        self.assertEqual(self.boot_loads(config["fdtfile"]), [
            "0x86000000 boot/dtb/mt7623n-bananapi-bpi-r2.dtb",
            "0x86080000 boot/uInitrd", "0x82000000 boot/zImage",
        ])

    def test_edge_effective_shell_config_is_unchanged(self) -> None:
        config = self.shell_config("edge")
        self.assertEqual(config["fdtfile"], "mediatek/mt7623n-bananapi-bpi-r2")
        self.assertEqual(config["fdtfile"], config["before_fdt"])
        self.assertEqual(config["kernel"], config["before_kernel"])
        self.assertEqual(config["branch"], "")
        self.assertEqual(config["firmware"], "")

    def test_boot_script_does_not_repair_environment_paths(self) -> None:
        for name in ("mediatek/mt7623n-bananapi-bpi-r2",
                     "mediatek/mt7623n-bananapi-bpi-r2.dtb"):
            with self.subTest(fdtfile=name):
                self.assertEqual(self.boot_loads(name), [
                    "0x86000000 boot/dtb/" + name,
                    "0x86080000 boot/uInitrd", "0x82000000 boot/zImage",
                ])

    def test_sources_and_media_contract_are_fixed(self) -> None:
        self.assertEqual(self.config["candidate_branch"], "current")
        self.assertEqual(self.config["kernel_family"], "mt7623")
        self.assertEqual(
            self.config["linux_commit"],
            "dc6160265ffc795a1832bc1424f58291d152c7bb",
        )
        self.assertEqual(
            self.policy["uboot_revision"],
            "ece349ade2973e220f524ce59e59711cc919263f",
        )
        self.assertEqual(self.policy["partition_table"], "msdos")
        self.assertEqual(self.policy["partition_start_sector"], 8192)
        self.assertEqual(
            self.policy["dtb_sha256"],
            "55151de1694bb279e759498eb5f86253e0e90700408044c546b4310a2a81c796",
        )
        self.assertEqual(
            self.policy["uboot_payloads"],
            [
                "BPI-R2-HEAD440-0k.img@0",
                "BPI-R2-HEAD1-512b.img@512",
                "BPI-R2-preloader-2k.img@2048",
                "u-boot.bin@327680",
            ],
        )

    def test_static_boot_payload_hashes_match_repository(self) -> None:
        expected = dict(
            item.split("=", 1) for item in self.policy["uboot_payload_sha256"]
        )
        for name, digest in expected.items():
            with self.subTest(name=name):
                payload = ROOT / "packages/blobs/mt7623n" / name
                self.assertEqual(hashlib.sha256(payload.read_bytes()).hexdigest(), digest)

    def test_boot_blob_provenance_blocks_external_release(self) -> None:
        self.assertFalse(self.config["boot_blob_redistribution_authorized"])
        self.assertEqual(
            self.config["boot_blob_source_repository"],
            "https://github.com/BPI-SINOVOIP/BPI-files.git",
        )
        expected = dict(
            item.split("=", 1) for item in self.policy["uboot_payload_sha256"]
        )
        sources = self.config["boot_blob_sources"]
        self.assertEqual(set(sources), set(expected))
        for name, metadata in sources.items():
            with self.subTest(name=name):
                self.assertTrue(metadata["source_path"].endswith(".img.gz"))
                self.assertRegex(metadata["fixed_commit"], r"^[0-9a-f]{40}$")
                self.assertEqual(metadata["decompressed_sha256"], expected[name])
        policy = SOURCE_POLICY.read_text()
        self.assertIn("不得把包含這些載荷的映像標示為可對外發布版本", policy)
        self.assertIn("boot_blob_redistribution_authorized", policy)

    def test_boot_script_uses_partition_uuid_and_correct_dtb(self) -> None:
        text = BOOT_SCRIPT.read_text()
        self.assertIn("part uuid ${devtype} ${devnum}:${mmcpart} rootuuid", text)
        self.assertIn('setenv rootdev "PARTUUID=${rootuuid}"', text)
        self.assertIn(
            'setenv fdtfile "mt7623n-bananapi-bpi-r2.dtb"', text
        )
        self.assertNotIn("mediatek/mt7623n-bananapi-bpi-r2.dtb", text)
        self.assertNotIn("/dev/mmcblk", text)

    def test_uboot_patch_has_deterministic_environment(self) -> None:
        text = UBOOT_PATCH.read_text()
        for required in (
            "+CONFIG_ENV_IS_NOWHERE=y",
            "+# CONFIG_ENV_IS_IN_MMC is not set",
            "+CONFIG_CMD_BOOTZ=y",
            "+CONFIG_CMD_EXT4=y",
            "mmcinitrdfile=boot/uInitrd",
            "boot/dtb/mt7623n-bananapi-bpi-r2.dtb",
        ):
            self.assertIn(required, text)
        self.assertNotIn("boot/dtb/mediatek/mt7623n-bananapi-bpi-r2.dtb", text)
        self.assertNotIn("#define CONFIG_BOOTCOMMAND", text)
        self.assertNotIn("index 111111111111..222222222222", text)
        self.assertIn(
            "index aeef3bebe96254b1ffb3f23a8e7a0f529bdac507"
            "..8c13942f00b707bbcad3a3402bdbc19b939eceaa",
            text,
        )
        self.assertIn(
            "index 6f42cd32d80fbfc4e8923826d448da40e7f49b5c"
            "..b9dda4d9b9c1213f70315601bf6ee0988b7e574d",
            text,
        )
        for header in ("@@ -33,7 +35,8 @@", "@@ -17,9 +17,27 @@", "@@ -32,8 +50,22 @@"):
            self.assertIn(header, text)

    def test_board_enables_otg_gpio_and_fixed_firmware(self) -> None:
        text = BOARD_PATH.read_text()
        for symbol in (
            "GPIO_CDEV",
            "USB_GADGET",
            "USB_MUSB_HDRC",
            "USB_MUSB_DUAL_ROLE",
            "USB_MUSB_MEDIATEK",
            "USB_ROLE_SWITCH",
        ):
            self.assertIn(symbol, text)
        self.assertIn(self.config["firmware_commit"], text)
        self.assertIn(f'BOOTBRANCH_BOARD="commit:{self.policy["uboot_revision"]}"', text)
        self.assertEqual(self.policy["dtb"], "mt7623n-bananapi-bpi-r2.dtb")
        package_line = next(
            line for line in text.splitlines()
            if line.startswith('PACKAGE_LIST_BOARD="')
        )
        packages = set(package_line.split('"', 2)[1].split())
        self.assertTrue(set(self.config["common_packages"]) <= packages)

    def test_network_default_uses_the_mainline_netplan_contract(self) -> None:
        path = ROOT / "extensions/network/config-networkd/netplan/10-dhcp-all-interfaces.yaml"
        text = path.read_text()
        self.assertIn('name: "e*"', text)
        self.assertIn('name: "lan*"', text)
        self.assertIn('name: "wan*"', text)
        self.assertNotIn("Bridge=br0", text)
        self.assertEqual(
            self.config["installed_file_sha256"],
            {
                "/etc/netplan/10-dhcp-all-interfaces.yaml": hashlib.sha256(
                    path.read_bytes()
                ).hexdigest()
            },
        )

    def test_generic_verifier_checks_exact_payload_and_installed_file_hashes(self) -> None:
        text = GENERIC_VERIFIER.read_text()
        self.assertIn("uboot_payload_sha256", text)
        self.assertIn("installed_file_sha256", text)
        self.assertIn(
            'sudo sha256sum "${mount_dir}${installed_path}"',
            text,
        )
        self.assertIn('sudo md5sum "${checked_payload}"', text)
        self.assertIn('sudo sha256sum "${checked_payload}"', text)
        self.assertIn(
            'sudo cmp --silent --ignore-initial="0:${offset}"',
            text,
        )
        self.assertIn("payload SHA-256 不符", text)

    def test_shell_entrypoints_are_valid(self) -> None:
        for name in (
            "build-bananapi-mt7623-r2-candidate.sh",
            "verify-bananapi-mt7623-r2-candidate.sh",
            "run-bananapi-mt7623-r2-candidate-isolated-cache.sh",
        ):
            self.assertTrue((ROOT / "tools" / name).stat().st_mode & 0o111)
            subprocess.run(
                ["bash", "-n", str(ROOT / "tools" / name)],
                check=True,
            )

    def deb_files(self, package: Path, paths: tuple[str, ...]) -> dict[str, bytes]:
        files = {}
        with subprocess.Popen(
            ["dpkg-deb", "--fsys-tarfile", str(package)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        ) as process:
            with tarfile.open(fileobj=process.stdout, mode="r|") as archive:
                for member in archive:
                    if member.name in paths:
                        self.assertNotIn(member.name, files)
                        self.assertTrue(member.isfile())
                        self.assertLessEqual(member.size, 32 * 1024**2)
                        files[member.name] = archive.extractfile(member).read()
            process.stdout.read()
            error = process.stderr.read().decode()
            self.assertEqual(process.wait(timeout=60), 0, error)
        self.assertEqual(set(files), set(paths))
        return files

    @unittest.skipUnless(os.environ.get("BPI_R2_DTB_REAL") == "1", "須明示啟用本機原證據與套件核對")
    def test_real_snapshot_and_matching_kernel_dtb_packages(self) -> None:
        from tools.bpi_lab_allwinner import _env, _legacy

        evidence = ROOT / "output/evidence/bpi-multiboard-integrate-20260917-allboard-bpi-r2-004/extraction"
        manifest = (evidence / "extraction.json").read_bytes()
        self.assertEqual(hashlib.sha256(manifest).hexdigest(),
                         "aa5f8b065ee20e178bfed723f85e589b283ba984feb94bdb57b309fcae30c274")
        extraction = json.loads(manifest)
        self.assertEqual(extraction["source_digest"]["sha256"],
                         "38a736cbc41c21cb7969e18d0f7954a5ea217af70f347beadb0883ef6e2d58e5")
        release = "6.6.153-current-mt7623"
        blobs = {}
        for path in ("/boot/boot.cmd", "/boot/boot.scr", "/boot/armbianEnv.txt",
                     "/boot/zImage", "/boot/config-" + release):
            record = extraction["files"][path]
            blob = (evidence / record["file"]).read_bytes()
            self.assertEqual(len(blob), record["digest"]["bytes"])
            self.assertEqual(hashlib.sha256(blob).hexdigest(), record["digest"]["sha256"])
            blobs[path] = blob
        self.assertEqual(_legacy(blobs["/boot/boot.scr"], script=True), blobs["/boot/boot.cmd"])
        self.assertEqual(blobs["/boot/boot.cmd"], BOOT_SCRIPT.read_bytes())
        old_name = _env(blobs["/boot/armbianEnv.txt"])["fdtfile"]
        self.assertEqual(old_name, "mediatek/mt7623n-bananapi-bpi-r2")
        self.assertEqual(self.boot_loads(old_name)[0], "0x86000000 boot/dtb/" + old_name)
        query = next(q for q in extraction["queries"] if q["lookup_path"] == "/boot/dtb/" + old_name)
        self.assertEqual(query["command"], "stat /boot/dtb-" + release + "/mediatek")
        error = (evidence / query["stderr_file"]).read_bytes()
        self.assertEqual(hashlib.sha256(error).hexdigest(), query["stderr"]["sha256"])
        self.assertEqual(query["stdout"]["bytes"], 0)
        self.assertIn(b"File not found by ext2_lookup", error)

        debs = ROOT.parent / "bpi-v26.2.1-bananapi-parallel/output/debs"
        suffix = "_26.11.0-trunk_armhf__6.6.153-Sdc61-D0000-P0000-Cdcf3-H8075-HK01ba-V014b-Bf00c-R448a.deb"
        packages = {}
        for kind, expected in (
            ("image", "b6ee041c3854a2a151411c25c5acccd5d7fdca80da5737ecfeaca2e9dfc1988e"),
            ("dtb", "ce5280141c393fddb7251102085b745ab41696ec47d94ff10d1ee98174f23a9c"),
        ):
            package = debs / ("linux-" + kind + "-current-mt7623" + suffix)
            digest = hashlib.sha256()
            with package.open("rb") as stream:
                for block in iter(lambda: stream.read(1024**2), b""):
                    digest.update(block)
            self.assertEqual(digest.hexdigest(), expected)
            packages[kind] = package
        name = self.shell_config("current")["fdtfile"]
        dtb_path = "./boot/dtb-" + release + "/" + name
        dtb = self.deb_files(packages["dtb"], (dtb_path,))[dtb_path]
        self.assertEqual(len(dtb), 34525)
        self.assertEqual(hashlib.sha256(dtb).hexdigest(), self.policy["dtb_sha256"])
        identity = subprocess.run(
            ["fdtget", "-t", "s", "/dev/stdin", "/", "model", "/", "compatible"],
            input=dtb, capture_output=True, check=True, timeout=10,
        )
        self.assertEqual(identity.stdout.decode().splitlines(), [
            self.policy["model"], " ".join(self.policy["compatible"]),
        ])
        kernel_path = "./boot/vmlinuz-" + release
        config_path = "./boot/config-" + release
        embedded_dtb = "./usr/lib/linux-image-" + release + "/" + name
        files = self.deb_files(packages["image"], (kernel_path, config_path, embedded_dtb))
        self.assertEqual(files[kernel_path], blobs["/boot/zImage"])
        self.assertEqual(files[config_path], blobs["/boot/config-" + release])
        self.assertEqual(files[embedded_dtb], dtb)
        self.assertIn(b"CONFIG_ARCH_WANT_FLAT_DTB_INSTALL=y\n", files[config_path])


if __name__ == "__main__":
    unittest.main()
